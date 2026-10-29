"""The clarify phase: the agent asks before it writes.

A senior engineer handed "make a login form" does not start typing. They ask
which language, which styling approach, how auth is stored, and so on. This
module gives the agent exactly that move: instead of ``propose_change``, the
first thing it may call is ``ask_questions``, which records a small batch of
structured questions and stops the run in ``CLARIFYING``.

Design notes:

* The phase is *bounded* -- one batch per run, at most
  ``MAX_QUESTIONS_PER_ROUND`` questions -- so a vague task cannot turn into an
  interrogation, and the token budget is never spent on clarifications that
  only lead to more clarifications.
* Questions are structured, not free text: a choice question carries its
  options, so the mobile UI can render tappable answers and the model receives
  a stable ``value`` rather than prose it has to reinterpret.
* ``skip`` lets the model decide the task is already unambiguous -- the run
  then proceeds straight to explore/propose, so this phase never *blocks* a
  well-specified task.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from apps.agent.budget import Budget
from apps.agent.graph import (
    DEFAULT_MAX_TOOL_CALLS,
    ExecOutcome,
    build_agent_graph,
    initial_state,
)
from apps.agent.providers.base import Provider, ToolCall
from apps.agent.tools import ToolResult, ToolSpec

__all__: list[str] = [
    "MAX_QUESTIONS_PER_ROUND",
    "ClarificationDraft",
    "ClarifyController",
    "ClarifyResult",
    "run_clarify",
]

#: Upper bound on questions in one batch. Three is enough to pin down the
#: language, the styling approach, and the one behavioural choice that matters,
#: which is what a first round is for.
MAX_QUESTIONS_PER_ROUND = 3

#: Question kinds the controller accepts, mirroring the model enum.
_KINDS = frozenset({"single_choice", "multi_choice", "text"})

_ASK = "ask_questions"
_SKIP = "skip"

#: A key is used as a ``SlugField`` and injected into prompt text, so it is
#: normalised rather than trusted.
_KEY_SAFE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class ClarificationDraft:
    """One validated question, ready to be stored as a Clarification row."""

    key: str
    question: str
    kind: str
    options: tuple[dict[str, str], ...] = ()
    allow_custom: bool = True
    rationale: str = ""


@dataclass(frozen=True, slots=True)
class ClarifyResult:
    """Outcome of the clarify phase for one run."""

    questions: tuple[ClarificationDraft, ...] = ()
    skipped: bool = False
    messages: list[BaseMessage] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)
    stopped_reason: str = ""

    @property
    def should_pause(self) -> bool:
        """True when the run must stop and wait for the user's answers."""
        return bool(self.questions) and not self.skipped


class ClarifyController:
    """Validates and collects the questions the model wants to ask.

    Mirrors :class:`~apps.agent.propose.ProposeController`: the model never
    writes to the database, it only calls a tool, and invalid calls come back as
    readable tool errors rather than crashes.
    """

    def __init__(self, *, max_questions: int = MAX_QUESTIONS_PER_ROUND) -> None:
        self.max_questions = max_questions
        self._questions: list[ClarificationDraft] = []
        self._skipped = False

    # ── tool surface ────────────────────────────────────────────────────────

    def schemas(self) -> list[dict[str, Any]]:
        """The tools advertised during the clarify phase, OpenAI-shaped."""
        ask = ToolSpec(
            name=_ASK,
            description=(
                "Ask the user up to "
                f"{self.max_questions} short questions you genuinely need "
                "answered before you can write the right code. Use this when the "
                "request leaves a material choice open (language, framework, "
                "styling approach, data store, auth strategy, target platform). "
                "Each question must carry concrete options so the user can answer "
                "with one tap; the user may still type a custom answer. Call this "
                "at most once: it ends your turn and pauses the run."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "questions": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": self.max_questions,
                        "description": "The questions to ask, in the order to show them.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "key": {
                                    "type": "string",
                                    "description": (
                                        "Short machine name for the answer, e.g. "
                                        "'language' or 'styling'. Lowercase letters, "
                                        "digits and underscores."
                                    ),
                                },
                                "question": {
                                    "type": "string",
                                    "description": "The question, phrased for a person.",
                                },
                                "kind": {
                                    "type": "string",
                                    "enum": sorted(_KINDS),
                                    "description": (
                                        "single_choice (one option), multi_choice "
                                        "(any number), or text (free answer)."
                                    ),
                                },
                                "options": {
                                    "type": "array",
                                    "description": (
                                        "Choices for a choice question. Give at least "
                                        "two. Required unless kind is 'text'."
                                    ),
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "value": {
                                                "type": "string",
                                                "description": "Stable answer value.",
                                            },
                                            "label": {
                                                "type": "string",
                                                "description": "Text shown to the user.",
                                            },
                                        },
                                        "required": ["value", "label"],
                                    },
                                },
                                "rationale": {
                                    "type": "string",
                                    "description": (
                                        "One sentence on why this choice changes the code."
                                    ),
                                },
                            },
                            "required": ["key", "question", "kind"],
                        },
                    }
                },
                "required": ["questions"],
                "additionalProperties": False,
            },
        )
        skip = ToolSpec(
            name=_SKIP,
            description=(
                "Call this instead of ask_questions when the task is already "
                "unambiguous enough to implement without asking anything. Do not "
                "use it to avoid asking a question that genuinely matters."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Why no clarification is needed.",
                    }
                },
                "required": ["reason"],
                "additionalProperties": False,
            },
        )
        return [skip.to_openai(), ask.to_openai()]

    def run(self, call: ToolCall) -> ExecOutcome:
        """Execute one clarify-phase tool call."""
        if call.name == _SKIP:
            return self._skip(call.arguments)
        if call.name == _ASK:
            return self._ask(call.arguments)
        return ExecOutcome(
            result=ToolResult(
                name=call.name,
                content=f"Unknown tool {call.name!r}; only {_ASK} and {_SKIP} are available.",
                is_error=True,
            )
        )

    @property
    def questions(self) -> tuple[ClarificationDraft, ...]:
        return tuple(self._questions)

    @property
    def skipped(self) -> bool:
        return self._skipped

    # ── handlers ────────────────────────────────────────────────────────────

    def _skip(self, args: dict[str, Any]) -> ExecOutcome:
        self._skipped = True
        reason = str(args.get("reason") or "").strip() or "The task is unambiguous."
        return ExecOutcome(
            result=ToolResult(name=_SKIP, content=f"No questions needed: {reason}"),
            summary=reason,
        )

    def _ask(self, args: dict[str, Any]) -> ExecOutcome:
        raw = args.get("questions")
        if not isinstance(raw, list) or not raw:
            return self._reject("questions must be a non-empty array")

        drafts: list[ClarificationDraft] = []
        seen: set[str] = set()
        for index, item in enumerate(raw[: self.max_questions], start=1):
            if not isinstance(item, dict):
                return self._reject(f"question {index} must be an object")
            draft = self._validate_one(item, index)
            if isinstance(draft, str):
                return self._reject(draft)
            if draft.key in seen:
                return self._reject(f"duplicate question key {draft.key!r}")
            seen.add(draft.key)
            drafts.append(draft)

        self._questions = drafts
        return ExecOutcome(
            result=ToolResult(
                name=_ASK,
                content=(
                    f"Recorded {len(drafts)} question(s). The run is now paused until "
                    "the user answers."
                ),
            ),
            summary=f"{len(drafts)} question(s) awaiting the user.",
        )

    def _validate_one(self, item: dict[str, Any], index: int) -> ClarificationDraft | str:
        key = _slug(str(item.get("key") or ""))
        if not key:
            return f"question {index}: key must contain at least one letter or digit"
        question = str(item.get("question") or "").strip()
        if not question:
            return f"question {index}: question must be a non-empty string"
        kind = str(item.get("kind") or "single_choice")
        if kind not in _KINDS:
            return f"question {index}: kind must be one of {sorted(_KINDS)}, got {kind!r}"

        options = _normalise_options(item.get("options"))
        if kind == "text" and options:
            return f"question {index}: a text question must not carry options"
        if kind != "text" and len(options) < 2:
            return (
                f"question {index}: a {kind} question needs at least two options, "
                f"got {len(options)}"
            )

        return ClarificationDraft(
            key=key,
            question=question,
            kind=kind,
            options=tuple(options),
            allow_custom=bool(item.get("allow_custom", True)),
            rationale=str(item.get("rationale") or "").strip(),
        )

    def _reject(self, message: str) -> ExecOutcome:
        return ExecOutcome(
            result=ToolResult(name=_ASK, content=f"{message}.", is_error=True)
        )


def run_clarify(
    *,
    provider: Provider,
    controller: ClarifyController,
    system: SystemMessage,
    task: HumanMessage,
    budget: Budget,
    reasoning_effort: str = "medium",
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
) -> ClarifyResult:
    """Run the clarify phase to completion (a question batch, or a skip)."""
    graph = build_agent_graph(
        provider=provider,
        tool_schemas=controller.schemas(),
        executor=controller.run,
        budget=budget,
        reasoning_effort=reasoning_effort,
        # The phase ends on its first meaningful call, so a small cap is enough;
        # it exists only to bound a model that loops on malformed arguments.
        max_tool_calls=min(max_tool_calls, 4),
    )
    final = graph.invoke(
        initial_state(system=system, task=task, max_tool_calls=min(max_tool_calls, 4)),
        config={"recursion_limit": 16},
    )
    return ClarifyResult(
        questions=controller.questions,
        skipped=controller.skipped,
        messages=final["messages"],
        reasoning=final.get("reasoning", []),
        stopped_reason=final.get("stopped_reason", ""),
    )


# ── helpers ──────────────────────────────────────────────────────────────────


def _slug(value: str) -> str:
    """Normalise a question key into something safe for a SlugField and a prompt."""
    cleaned = _KEY_SAFE.sub("_", value.strip().lower()).strip("_")
    return cleaned[:64]


def _normalise_options(raw: Any) -> list[dict[str, str]]:
    """Coerce the model's options into ``{'value', 'label'}`` pairs.

    A model that returns plain strings (``["Python", "JavaScript"]``) is common
    enough to accept: the string becomes both value and label.
    """
    if not isinstance(raw, list):
        return []
    options: list[dict[str, str]] = []
    for entry in raw:
        if isinstance(entry, str):
            value = label = entry.strip()
        elif isinstance(entry, dict):
            label = str(entry.get("label") or entry.get("value") or "").strip()
            value = str(entry.get("value") or label).strip()
        else:
            continue
        if value and label:
            options.append({"value": value[:100], "label": label[:200]})
    return options
