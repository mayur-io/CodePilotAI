"""ApplyChanges: the approval-gated write to the task worktree.

Phase 3 proved the model can only *ask* for changes. This module is the
executor half: approved :class:`~apps.agent_tasks.models.ProposedChange` rows
become real edits in the worktree, one patch at a time, through the same
``apps.git.patch.apply_patch`` escalation chain (check -> ``--3way`` ->
``--reject``) that validated them structurally at propose time.

Contract:

* only ``APPROVED`` changes are applied; ``PENDING`` (undecided) and
  ``REJECTED`` rows stay untouched;
* an already-applied patch is detected with ``git apply --reverse --check`` and
  skipped, so re-running after a crashed worker is a no-op instead of a
  duplicate (Task.md §5 idempotency);
* a patch that conflicts is fully reverted -- including its ``.rej`` files --
  and the change marked ``FAILED``, so the tree is never left half-patched;
* additions/deletions come from :func:`apps.git.patch.diffstat`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apps.agent_tasks.models import AgentTask, ProposedChange
from apps.git.errors import GitCommandError, PatchConflictError
from apps.git.patch import ApplyOutcome, apply_patch, diffstat, revert_conflicts, revert_files
from apps.git.runner import run_git
from config.observability import get_logger

logger = get_logger(__name__)

__all__: list[str] = [
    "AppliedChange",
    "ApplySummary",
    "apply_approved_changes",
]

#: How much rejected-hunk text is kept on a failed change's error_message.
_MAX_DETAIL_CHARS = 2000


@dataclass(frozen=True, slots=True)
class AppliedChange:
    """One approved change's outcome after the apply pass."""

    change_id: int
    path: str
    status: str  # ProposedChange.Status.APPLIED | FAILED
    additions: int = 0
    deletions: int = 0
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ApplySummary:
    """The outcome of applying every approved change to one task's worktree."""

    outcomes: tuple[AppliedChange, ...]

    @property
    def applied(self) -> tuple[AppliedChange, ...]:
        return tuple(o for o in self.outcomes if o.status == ProposedChange.Status.APPLIED)

    @property
    def failed(self) -> tuple[AppliedChange, ...]:
        return tuple(o for o in self.outcomes if o.status == ProposedChange.Status.FAILED)

    @property
    def success(self) -> bool:
        """True when no approved change failed to apply."""
        return not self.failed

    def as_dict(self) -> dict[str, Any]:
        """Compact form for the run event and the task's diff_stat."""
        return {
            "applied": [o.path for o in self.applied],
            "failed": [o.path for o in self.failed],
            "total": len(self.outcomes),
        }


def apply_approved_changes(task: AgentTask, worktree: Path) -> ApplySummary:
    """Apply every APPROVED change to ``worktree``, idempotently.

    Updates each row's status in place (``applied``/``failed``), records its
    diffstat, and reports the outcome as a summary. Runs inside one transaction
    so a mid-loop crash leaves every row consistent with the tree.
    """
    changes = list(
        task.proposed_changes.filter(status=ProposedChange.Status.APPROVED).order_by(
            "order_index", "id"
        )
    )
    outcomes: list[AppliedChange] = []
    for change in changes:
        outcome, status = _apply_one(worktree, change)
        if status == ProposedChange.Status.APPLIED:
            change.additions, change.deletions = diffstat(change.patch)
            if change.error_message:
                change.error_message = ""
        else:
            change.error_message = outcome.detail
        change.status = status
        change.save(
            update_fields=["status", "additions", "deletions", "error_message", "updated_at"]
        )
        outcomes.append(outcome)

    summary = ApplySummary(tuple(outcomes))
    logger.info(
        "apply_changes_finished",
        task_id=task.pk,
        applied=len(summary.applied),
        failed=len(summary.failed),
    )
    return summary


# ── internals ─────────────────────────────────────────────────────────────────


def _apply_one(worktree: Path, change: ProposedChange) -> tuple[AppliedChange, str]:
    """Apply one change, returning its recorded outcome and target status."""
    if _patch_already_applied(worktree, change.patch):
        logger.info("apply_skipped_already_applied", change_id=change.pk, path=change.path)
        return (
            AppliedChange(change.pk, change.path, ProposedChange.Status.APPLIED),
            ProposedChange.Status.APPLIED,
        )

    try:
        outcome: ApplyOutcome = apply_patch(worktree, change.patch)
    except (GitCommandError, PatchConflictError) as exc:
        logger.info("apply_failed", change_id=change.pk, path=change.path, reason="conflict")
        return (
            AppliedChange(
                change.pk,
                change.path,
                ProposedChange.Status.FAILED,
                detail=_clip(str(exc)),
            ),
            ProposedChange.Status.FAILED,
        )

    if outcome.needs_attention:
        # A partial application is a conflict, not a success: undo everything
        # and surface the rejected hunks so the repair phase has exact text.
        revert_files(worktree, outcome.files)
        revert_conflicts(worktree, outcome)
        logger.info("apply_partial_reverted", change_id=change.pk, path=change.path)
        return (
            AppliedChange(
                change.pk,
                change.path,
                ProposedChange.Status.FAILED,
                detail=_clip(_conflict_detail(outcome)),
            ),
            ProposedChange.Status.FAILED,
        )

    return (
        AppliedChange(
            change.pk,
            change.path,
            ProposedChange.Status.APPLIED,
            additions=change.additions,
            deletions=change.deletions,
        ),
        ProposedChange.Status.APPLIED,
    )


def _patch_already_applied(worktree: Path, patch: str) -> bool:
    """True when ``patch`` is already present in the working tree.

    ``git apply --reverse --check`` exits 0 exactly when applying the patch in
    reverse would succeed -- i.e. the change is already there. This is what
    makes a crashed-worker retry a no-op instead of a duplicate conflict.
    """
    result = run_git(
        ["apply", "--reverse", "--check", "--whitespace=nowarn", "-"],
        cwd=worktree,
        input_text=patch,
        check=False,
    )
    return result.ok


def _conflict_detail(outcome: ApplyOutcome) -> str:
    """Render the rejected hunks per file for the change's error_message."""
    lines = [f"hunks could not be applied to {', '.join(outcome.files)}"]
    for target, hunks in outcome.rejected:
        lines.append(f"{target}: {len(hunks)} rejected, first hunks follow")
        lines.extend(hunks[:2])
    return "\n".join(lines)


def _clip(text: str) -> str:
    if len(text) <= _MAX_DETAIL_CHARS:
        return text
    return text[:_MAX_DETAIL_CHARS] + "\n… (truncated)"
