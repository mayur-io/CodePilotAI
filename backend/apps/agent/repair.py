"""The repair phase: fix verification failures inside the approved boundary.

Post-approval, the user has already consented to these files changing, so the
repair model may *apply* a corrected diff to the worktree immediately -- this
is the one place the pipeline writes outside the approval gate (Task.md §5).

Every repair still passes the same gates as a proposal: the path sandbox, the
secret policy, structural ``validate_patch``, and the strict one-file-one-patch
rule. A repair therefore cannot widen the change, touch a file the user never
approved, or rewrite history; it can only put the already-approved files into a
state that passes the repo's own checks.

The loop is bounded by ``max_repairs`` per repair round, and the runner bounds
the number of rounds itself (:class:`~apps.agent.runner`).
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
from apps.git.errors import GitCommandError, PatchConflictError
from apps.git.patch import apply_patch, patch_files, revert_conflicts, validate_patch
from config.observability import get_logger

logger = get_logger(__name__)

__all__: list[str] = [
    "RepairController",
    "RepairResult",
    "run_repair",
]

_NAME = "repair_file"


@dataclass(frozen=True, slots=True)
class RepairResult:
    """What one repair round produced, ready to be applied to the worktree."""

    applied: tuple[ProposedPatch, ...]
    messages: list[BaseMessage]
    reasoning: list[str]
    stopped_reason: str


class RepairController:
    """Applies validated repair diffs to the worktree, one file per call.

    ``worktree`` is written here -- that is the whole point of the repair
    phase -- but only through the same gates the propose phase uses.
    """

    def __init__(self, worktree: Path, *, max_repairs: int = 2) -> None:
        self.worktree = worktree.resolve()
        self.max_repairs = max_repairs
        self._repairs_remaining = max_repairs
        self._applied: list[ProposedPatch] = []
        #: File bytes taken just before a repair apply, so a failed attempt
        #: restores the *pre-repair* state (approved changes intact) rather
        #: than resetting the file to HEAD and losing the approved work.
        self._snapshots: dict[str, tuple[bool, bytes]] = {}

    # ── tool surface ────────────────────────────────────────────────────────

    def schemas(self) -> list[dict[str, Any]]:
        """The tools advertised during a repair round, OpenAI-shaped."""
        repair = ToolSpec(
            name=_NAME,
            description=(
                "Fix a failing check by applying a corrected unified diff to "
                "exactly one file. Read the file's current contents from the "
                "brief first, and make the diff apply to those contents. The "
                "diff is applied to the worktree immediately. When every "
                "failure is addressed, call finish."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Repo-relative path of the file this diff changes.",
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
                        "description": "Which check this repair addresses, in one sentence.",
                    },
                },
                "required": ["path", "patch", "rationale"],
                "additionalProperties": False,
            },
        )
        finish = ToolSpec(
            name="finish",
            description=(
                "Signal that every reported failure has been addressed. "
                "Verification will be re-run."
            ),
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        )
        return [finish.to_openai(), repair.to_openai()]

    def run(self, call: ToolCall) -> ExecOutcome:
        """Execute one repair-phase tool call."""
        try:
            if call.name == "finish":
                return self._finish()
            if call.name == _NAME:
                return self._repair(call.arguments)
            return ExecOutcome(
                result=ToolResult(
                    name=call.name,
                    content=f"Unknown tool {call.name!r}; only {_NAME} and finish are available.",
                    is_error=True,
                )
            )
        except _MalformedArgumentsError as exc:
            return ExecOutcome(result=ToolResult(name=call.name, content=str(exc), is_error=True))

    @property
    def applied(self) -> tuple[ProposedPatch, ...]:
        return tuple(self._applied)

    # ── handlers ────────────────────────────────────────────────────────────

    def _repair(self, args: dict[str, Any]) -> ExecOutcome:
        path = _expect_str(args, "path", _NAME)
        patch = _expect_str(args, "patch", _NAME)
        rationale = _expect_str(args, "rationale", _NAME)

        try:
            declared = guard_change_path(self.worktree, path)
        except GuardViolationError as exc:
            return self._reject(str(exc))

        if not rationale.strip():
            return self._reject("rationale must be a non-empty string")

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

        self._snapshot_file(declared)
        try:
            outcome = apply_patch(self.worktree, patch)
        except (GitCommandError, PatchConflictError) as exc:
            self._restore_snapshot(declared)
            return self._reject(f"patch did not apply: {exc}")

        if outcome.needs_attention:
            # The tree must not be left half-patched: put the file back the way
            # it was before this repair (the approved change stays) and report.
            self._restore_snapshot(declared)
            revert_conflicts(self.worktree, outcome)
            return self._reject("patch would only apply partially; it was reverted")

        repair = ProposedPatch(
            path=declared,
            change_type="modify",
            patch=patch.strip(),
            rationale=rationale.strip(),
        )
        self._applied.append(repair)
        self._discard_snapshot(declared)
        return ExecOutcome(
            result=ToolResult(
                name=_NAME,
                content=(
                    f"Applied repair to {declared} "
                    f"({len(self._applied)} so far). Call finish when every "
                    "reported failure is addressed."
                ),
            ),
            proposal=repair,
        )

    # ── failure rollback ────────────────────────────────────────────────────

    def _snapshot_file(self, name: str) -> None:
        """Record the file's bytes (or absence) before a repair apply."""
        full = self.worktree / name
        if full.is_file():
            self._snapshots[name] = (True, full.read_bytes())
        else:
            self._snapshots[name] = (False, b"")

    def _restore_snapshot(self, name: str) -> None:
        """Put ``name`` back to its pre-repair state, deleting it if new."""
        snapshot = self._snapshots.pop(name, None)
        if snapshot is None:
            return
        existed, body = snapshot
        full = self.worktree / name
        if existed:
            full.parent.mkdir(parents=True, exist_ok=True)
            try:
                full.write_bytes(body)
            except OSError:
                logger.warning(
                    "repair_snapshot_restore_failed",
                    path=str(full),
                    error="could not restore file after failed repair",
                )
        else:
            full.unlink(missing_ok=True)

    def _discard_snapshot(self, name: str) -> None:
        self._snapshots.pop(name, None)

    def _finish(self) -> ExecOutcome:
        if not self._applied:
            return ExecOutcome(
                result=ToolResult(
                    name="finish",
                    content="No repairs were applied; re-running verification anyway.",
                    is_error=False,
                ),
                summary="repairs_complete",
            )
        return ExecOutcome(
            result=ToolResult(
                name="finish",
                content=f"{len(self._applied)} repair(s) applied.",
            ),
            summary="repairs_complete",
        )

    def _reject(self, message: str) -> ExecOutcome:
        self._repairs_remaining -= 1
        left = self._repairs_remaining
        hint = f" ({left} repair attempt(s) left)" if left > 0 else ""
        return ExecOutcome(
            result=ToolResult(name=_NAME, content=f"{message}.{hint}", is_error=True)
        )


def run_repair(
    *,
    provider: Provider,
    controller: RepairController,
    system: SystemMessage,
    task: HumanMessage,
    budget: Budget,
    reasoning_effort: str = "high",
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
) -> RepairResult:
    """Run one bounded repair round to completion (repairs or loop cap)."""
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
    return RepairResult(
        applied=tuple(final.get("proposals", [])),
        messages=final["messages"],
        reasoning=final.get("reasoning", []),
        stopped_reason=final.get("stopped_reason", ""),
    )


class _MalformedArgumentsError(Exception):
    """A tool call whose argument values have the wrong shape for the tool."""


def _expect_str(args: dict[str, Any], name: str, tool: str) -> str:
    value = args.get(name)
    if not isinstance(value, str):
        raise _MalformedArgumentsError(f"{tool}: {name} must be a string")
    return value
