"""The propose phase: turn the model's intent into validated, single-file diffs.

The model cannot touch the filesystem -- it can only call ``propose_change``
once per file and ``finish`` when it is done. Patching is therefore *asked
for*, not *done*, and every proposal passes gate before it becomes a
:class:`~apps.agent_tasks.models.ProposedChange` row:

1. the declared path must be inside the worktree and must not match the secret
   policy (a credential is not something you propose a diff to);
2. the patch must be structurally valid for ``git apply``
   (:func:`apps.git.patch.validate_patch` -- non-empty, bounded, hunks present,
   not a silent file-blanking);
3. the patch must touch exactly the one file the model claims it changes.

Failures come back as *tool errors* the model can read and repair, with a
bounded repair budget. A structural error in a diff is a model bug, not a
reason to fail the whole run.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from apps.agent.budget import Budget
from apps.agent.errors import GuardViolationError
from apps.agent.graph import (
    DEFAULT_MAX_TOOL_CALLS,
    ExecOutcome,
    ProposedPatch,
    build_agent_graph,
    initial_state,
)
from apps.agent.guards import guard_change_path
from apps.agent.providers.base import Provider, ToolCall
from apps.agent.tools import ToolResult, ToolSpec
from apps.git.errors import GitCommandError
from apps.git.patch import patch_files, validate_patch

__all__: list[str] = [
    "CHANGE_TYPES",
    "ProposeController",
    "ProposeResult",
    "run_propose",
]

CHANGE_TYPES = frozenset({"create", "modify", "delete"})

_NAME = "propose_change"


@dataclass(frozen=True, slots=True)
class ProposeResult:
    """What the propose phase produced, ready to be persisted as rows."""

    proposals: tuple[ProposedPatch, ...]
    summary: str
    messages: list[BaseMessage]
    reasoning: list[str]
    stopped_reason: str


class ProposeController:
    """Validates and collects one task's proposed changes.

    ``worktree`` is the checkout the diffs will eventually be applied to; it is
    used only for sandbox checks, never written. ``max_repairs`` bounds how
    many times the model may correct its own output during one run.
    """

    def __init__(self, worktree: Path, *, max_repairs: int = 3) -> None:
        self.worktree = worktree.resolve()
        self.max_repairs = max_repairs
        self._repairs_remaining = max_repairs
        self._proposals: list[ProposedPatch] = []

    # ── tool surface ────────────────────────────────────────────────────────

    def schemas(self) -> list[dict[str, Any]]:
        """The tools advertised during the propose phase, OpenAI-shaped."""
        propose = ToolSpec(
            name=_NAME,
            description=(
                "Propose a change to exactly one file as a unified diff. Call "
                "once per file, read the file first, and make each diff apply "
                "to the file's current contents. Valid change types are "
                "create, modify, delete. When every change is proposed, call "
                "finish with a two-sentence summary written for the reviewer."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Repo-relative path of the file this diff changes.",
                    },
                    "change_type": {
                        "type": "string",
                        "enum": sorted(CHANGE_TYPES),
                        "description": "What the diff does to the file.",
                    },
                    "patch": {
                        "type": "string",
                        "description": (
                            "A single-file unified diff (--- a/… / +++ b/… plus "
                            "@@ hunks) that applies to the file's current contents."
                        ),
                    },
                    "rationale": {
                        "type": "string",
                        "description": "Why this change is needed, in one or two sentences.",
                    },
                },
                "required": ["path", "change_type", "patch", "rationale"],
                "additionalProperties": False,
            },
        )
        finish = ToolSpec(
            name="finish",
            description=(
                "Signal that every change for the task has been proposed. "
                "Provide a two-sentence summary written for the person who "
                "will review the pull request."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Reviewer-facing summary of the proposed changes.",
                    }
                },
                "required": ["summary"],
                "additionalProperties": False,
            },
        )
        return [finish.to_openai(), propose.to_openai()]

    def run(self, call: ToolCall) -> ExecOutcome:
        """Execute one propose-phase tool call."""
        try:
            if call.name == "finish":
                return self._finish(call.arguments)
            if call.name == _NAME:
                return self._propose(call.arguments)
            return ExecOutcome(
                result=ToolResult(
                    name=call.name,
                    content=f"Unknown tool {call.name!r}; only {_NAME} and finish are available.",
                    is_error=True,
                )
            )
        except _MalformedArgumentsError as exc:
            # A protocol violation, not a model lapse: malformed arguments get
            # fed back as a tool error the model can react to instead of a
            # crash in the middle of the loop.
            return ExecOutcome(result=ToolResult(name=call.name, content=str(exc), is_error=True))

    @property
    def proposals(self) -> tuple[ProposedPatch, ...]:
        return tuple(self._proposals)

    # ── handlers ────────────────────────────────────────────────────────────

    def _propose(self, args: dict[str, Any]) -> ExecOutcome:
        path = _expect_str(args, "path", _NAME)
        change_type = str(args.get("change_type") or "")
        rationale = _expect_str(args, "rationale", _NAME)
        patch = _expect_str(args, "patch", _NAME)

        if change_type not in CHANGE_TYPES:
            return self._reject(
                f"change_type must be one of {sorted(CHANGE_TYPES)}, got {change_type!r}"
            )
        if not rationale.strip():
            return self._reject("rationale must be a non-empty string")

        try:
            declared = _guard_proposed_path(self.worktree, path)
        except GuardViolationError as exc:
            return self._reject(str(exc))

        try:
            validate_patch(patch)
        except GitCommandError as exc:
            return self._reject(f"patch failed validation: {exc}")

        touched = patch_files(patch)
        if len(touched) != 1:
            return self._reject(
                f"patch must change exactly one file, it names {len(touched)}: {', '.join(touched)}"
            )
        if touched[0] != declared:
            return self._reject(
                f"patch changes {touched[0]!r} but path is {declared!r}; they must match"
            )

        proposal = ProposedPatch(
            path=declared,
            change_type=change_type,
            patch=patch.strip(),
            rationale=rationale.strip(),
        )
        self._proposals.append(proposal)
        return ExecOutcome(
            result=ToolResult(
                name=_NAME,
                content=(
                    f"Stored proposal for {declared} "
                    f"({len(self._proposals)} so far). Call finish once every "
                    "change is proposed."
                ),
            ),
            proposal=proposal,
        )

    def _finish(self, args: dict[str, Any]) -> ExecOutcome:
        summary = _expect_str(args, "summary", "finish").strip()
        if not summary:
            return ExecOutcome(
                result=ToolResult(
                    name="finish",
                    content="summary must be a non-empty string",
                    is_error=True,
                )
            )
        return ExecOutcome(
            result=ToolResult(name="finish", content="Proposal set complete."),
            summary=summary,
        )

    def _reject(self, message: str) -> ExecOutcome:
        self._repairs_remaining -= 1
        left = self._repairs_remaining
        hint = f" ({left} repair attempt(s) left)" if left > 0 else ""
        return ExecOutcome(
            result=ToolResult(name=_NAME, content=f"{message}.{hint}", is_error=True)
        )


def run_propose(
    *,
    provider: Provider,
    controller: ProposeController,
    system: SystemMessage,
    task: HumanMessage,
    budget: Budget,
    reasoning_effort: str = "high",
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
) -> ProposeResult:
    """Run the propose phase to completion (proposal set or loop cap)."""
    graph = build_agent_graph(
        provider=provider,
        tool_schemas=controller.schemas(),
        executor=controller.run,
        budget=budget,
        reasoning_effort=reasoning_effort,
        max_tool_calls=max_tool_calls,
    )
    final = graph.invoke(
        initial_state(system=system, task=task, max_tool_calls=max_tool_calls),
        config={"recursion_limit": max_tool_calls * 2 + 8},
    )
    return ProposeResult(
        proposals=tuple(final.get("proposals", [])),
        summary=final.get("final_summary", ""),
        messages=final["messages"],
        reasoning=final.get("reasoning", []),
        stopped_reason=final.get("stopped_reason", ""),
    )


# ── helpers ──────────────────────────────────────────────────────────────────


def _guard_proposed_path(worktree: Path, raw: str) -> str:
    """Sandbox and policy check for a path the model wants to diff.

    Delegates to the shared :func:`apps.agent.guards.guard_change_path` so the
    propose and repair phases apply exactly the same policy.
    """
    return guard_change_path(worktree, raw)


class _MalformedArgumentsError(Exception):
    """A tool call whose argument values have the wrong shape for the tool."""


def _expect_str(args: dict[str, Any], name: str, tool: str) -> str:
    value = args.get(name)
    if not isinstance(value, str):
        raise _MalformedArgumentsError(f"{tool}: {name} must be a string")
    return value
