"""Prompt construction: the anti-slop contract and the per-phase briefs.

Prompt structure follows Task.md §3's caching note -- *repo context first,
question last*. The system message carries everything that is stable across a
run (contract, repo map, conventions, stack), so repeated calls share a cached
prefix instead of re-billing the same tokens at $0.15/M.

The contract is deliberately written as prohibitions rather than aspirations.
"Aspire to minimal diffs" is not enforceable; "do not add docstrings to lines
you did not change" is, because the reviewer can see it in the patch.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from apps.agent.context import InstructionFile

__all__: list[str] = [
    "ANTI_SLOP_CONTRACT",
    "PHASE_BRIEFS",
    "REASONING_EFFORT_BY_PHASE",
    "REPO_RULES",
    "SYSTEM_HEADER",
    "PromptContext",
    "build_system_prompt",
    "phase_hint",
    "system_message",
    "user_task_message",
]

#: Phase -> Groq `reasoning_effort`. Only low|medium|high exist on gpt-oss
#: (Task.md §3), and setting it explicitly per phase is cheaper than one global
#: value: exploration is mechanical, writing code is not.
REASONING_EFFORT_BY_PHASE: dict[str, str] = {
    "queued": "low",
    "plan": "medium",
    "clarify": "medium",
    "explore": "low",
    "propose": "high",
    "apply": "low",
    "verify": "low",
    "repair": "high",
    "commit": "low",
    "push": "low",
}

SYSTEM_HEADER = """\
You are CodePilotAI, a senior software engineer working inside a real \
repository on behalf of its owner. You are given a task, a read-only view of \
the codebase, and a way to propose changes. You never edit files directly."""

ANTI_SLOP_CONTRACT = """\
## Non-negotiables

- Ship the smallest change that solves the task. A smaller correct change beats a
  tidier large one.
- Match the surrounding code exactly: indentation, naming, quoting, line length,
  import order, comment density, error-handling style.
- Prefer editing an existing function over adding a new one. Prefer a new file over
  a risky refactor.
- Do not touch a line you are not changing.

## Forbidden

- Reformatting or restyling code outside your change.
- Adding docstrings, comments, type hints, or annotations to lines you did not change.
- Replacing working code while you are in the file.
- Inventing a dependency when an existing one does the job.
- Creating README, changelog, summary, or example files unless the task asks for them.
- Unrelated imports, renames, or "cleanup".
- Explaining the change inside the file. The rationale field is where explanations go.

## Allowed exceptions

A rule above yields only when the task explicitly asks for it, or when the code
literally cannot work otherwise. When you break a rule, say so in the rationale
instead of hiding it."""

REPO_RULES = """\
## How changes are made

- You cannot write files. You propose unified diffs, and a human approves each one.
- Every change is a unified diff. Never emit a whole file, never emit a patch you
  have not verified against the current contents of the target file.
- Read a file before you change it. Guessing context is how patches fail.
- One short rationale per file, stating *why* -- not a summary of what the diff says.
- Keep each proposal to one concern. Separate changes that should be approved
  separately: a dependency bump is not part of a bug fix.
- If you cannot solve the task, say so in your final message rather than proposing a
  change that only looks like progress."""

PHASE_BRIEFS: dict[str, str] = {
    "plan": (
        "Break the task into concrete, repository-aware sub-goals. Look at the repo "
        "map first so the plan refers to files that actually exist. Respond with the "
        "plan only; do not propose changes yet."
    ),
    "clarify": (
        "Before writing any code, decide whether the task leaves a material choice "
        "open that would change what you build -- language, framework, styling "
        "approach, data store, auth strategy, target platform, or a behavioural "
        "detail the user did not specify. If it does, call ask_questions once with "
        "up to three concrete, tappable questions and stop. If the task is already "
        "unambiguous, call skip. Do not ask about anything you can settle yourself "
        "by reading the repository, and do not re-ask a question the user has "
        "already answered."
    ),
    "explore": (
        "Explore until you can answer three things: which files are involved, what "
        "conventions they follow, and what already exists that you should reuse. "
        "Call read_repo_instructions first. Prefer reading real files over "
        "speculating. Do not propose changes yet."
    ),
    "propose": (
        "Read every file you intend to change, in full, before writing a single diff. "
        "Then call propose_change once per file with a unified diff that applies to "
        "the current contents. When every change is proposed, call finish with a "
        "two-sentence summary written for the person who has to review it."
    ),
    "repair": (
        "The previous change failed a check. Fix only what the check reported. Do "
        "not broaden the change, and do not re-propose anything that already passed."
    ),
    "commit": (
        "Write a conventional commit subject and body for the approved changes. "
        "Describe behaviour, not files."
    ),
}


@dataclass(frozen=True, slots=True)
class PromptContext:
    """Everything stable for the length of one run.

    Frozen and value-typed so the same context always renders byte-identical
    text: identical prefixes are what makes Groq's prompt cache hit.
    """

    repository: str
    base_branch: str
    feature_branch: str
    repo_map: str
    stack: dict[str, Any] = field(default_factory=dict)
    instructions: Sequence[InstructionFile] = ()
    toolchain_commands: dict[str, str] = field(default_factory=dict)


def build_system_prompt(context: PromptContext) -> str:
    """Render the stable prefix of the conversation.

    Order matters and is not negotiable: static contract first (rarely changes
    between runs), repository context next (changes once per task), tool list
    last (changes per phase). Everything after this message is cheap to vary.
    """
    sections = [SYSTEM_HEADER, ANTI_SLOP_CONTRACT, REPO_RULES]

    repository = [
        "## Repository",
        f"Name: {context.repository}",
        f"Base branch: {context.base_branch}   Feature branch: {context.feature_branch}",
    ]
    languages = context.stack.get("languages") or []
    if languages:
        repository.append(f"Stack: {', '.join(languages)}")
    managers = context.stack.get("managers") or []
    if managers:
        repository.append(f"Package manager: {', '.join(managers)}")
    if context.toolchain_commands:
        detected = "; ".join(f"{k} = `{v}`" for k, v in sorted(context.toolchain_commands.items()))
        repository.append(f"Detected commands (do not invent others): {detected}")
    if context.repo_map:
        repository += ["", "```", context.repo_map, "```"]
    sections.append("\n".join(repository))

    if context.instructions:
        blocks = ["## Repository conventions", "These override any generic rule above."]
        for item in context.instructions:
            note = " (truncated)" if item.truncated else ""
            blocks += ["", f"### {item.path}{note}", item.content.strip()]
        sections.append("\n".join(blocks))

    return "\n\n".join(sections)


def system_message(context: PromptContext) -> SystemMessage:
    return SystemMessage(content=build_system_prompt(context))


def user_task_message(prompt: str) -> HumanMessage:
    """The task, always last -- it is the part that varies per request."""
    return HumanMessage(content=prompt)


def phase_hint(phase: str) -> str:
    """The instruction that tells the model what this phase is for."""
    return PHASE_BRIEFS.get(phase, PHASE_BRIEFS["explore"])
