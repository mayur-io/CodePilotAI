"""Append-only event emitter for agent task runs.

Every state change a client needs to see goes through :func:`emit_event`, which
writes a :class:`~apps.agent_tasks.models.RunEvent` row. The SSE endpoint simply
tails this table, so the stream is replayable: a client that was offline can
reconnect with ``?after_seq=N`` and receive the entire history.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction

from apps.agent_tasks.models import AgentTask, RunEvent
from config.observability import get_logger

logger = get_logger(__name__)


def emit_event(task: AgentTask, kind: str, payload: dict[str, Any] | None = None) -> RunEvent:
    """Append an event to a task's stream and return it.

    Safe to call outside an existing transaction: the sequence lookup takes its
    own row lock on the task.
    """
    seq = RunEvent.next_seq(task)
    event = RunEvent.objects.create(
        task=task,
        seq=seq,
        kind=kind,
        payload=payload or {},
    )
    logger.debug("run_event_emitted", task_id=task.pk, seq=seq, kind=kind)
    return event


def set_phase(task: AgentTask, phase: str, *, note: str = "") -> AgentTask:
    """Advance the task to ``phase`` and announce it on the event stream."""
    task.phase = phase
    if phase == AgentTask.Phase.EXPLORE and task.started_at is None:
        task.started_at = _now()
    task.save(update_fields=["phase", "started_at", "updated_at"])
    payload: dict[str, Any] = {"phase": phase}
    if note:
        payload["note"] = note
    emit_event(task, RunEvent.Kind.PHASE, payload)
    return task


def set_status(
    task: AgentTask,
    status: str,
    *,
    error_code: str = "",
    error_message: str = "",
) -> AgentTask:
    """Move the task to ``status`` and announce it on the event stream."""
    task.status = status
    fields = ["status", "updated_at"]

    if status in AgentTask.TERMINAL_STATUSES:
        task.finished_at = _now()
        fields.append("finished_at")
    if error_code:
        task.error_code = error_code
        fields.append("error_code")
    if error_message:
        task.error_message = error_message
        fields.append("error_message")

    task.save(update_fields=fields)
    payload: dict[str, Any] = {"status": status}
    if error_code:
        payload["error_code"] = error_code
    if error_message:
        payload["error_message"] = error_message
    emit_event(task, RunEvent.Kind.STATUS, payload)

    if status in AgentTask.TERMINAL_STATUSES:
        emit_event(task, RunEvent.Kind.DONE, {"status": status})

    _dispatch_push(task.pk, status)

    return task


def _dispatch_push(task_id: int, status: str) -> None:
    """Best-effort push for a notify-worthy status change.

    Push must never affect the run it announces, so the whole thing is fire and
    forget, and it defers until commit when called inside a transaction (task
    planning raises ``awaiting_approval`` inside one and must not hold the
    transaction open across an HTTP call).
    """
    from apps.notifications.push import PUSH_NOTIFY_STATUSES, notify_task_status

    if status not in PUSH_NOTIFY_STATUSES:
        return
    if transaction.get_connection().in_atomic_block:
        transaction.on_commit(lambda: notify_task_status(task_id, status))
    else:
        notify_task_status(task_id, status)


def announce_change(task: AgentTask, change: Any) -> None:
    """Announce a proposed change so the approval UI can render a card."""
    payload = {
        "change_id": change.pk,
        "path": change.path,
        "change_type": change.change_type,
        "additions": change.additions,
        "deletions": change.deletions,
        "rationale": change.rationale,
    }
    with transaction.atomic():
        emit_event(task, RunEvent.Kind.CHANGE_PROPOSED, payload)


def announce_clarification(task: AgentTask, question: Any) -> None:
    """Announce one clarifying question so the ask UI can render it."""
    payload = {
        "clarification_id": question.pk,
        "round": question.round,
        "key": question.key,
        "question": question.question,
        "kind": question.kind,
        "options": question.options,
        "allow_custom": question.allow_custom,
        "rationale": question.rationale,
    }
    with transaction.atomic():
        emit_event(task, RunEvent.Kind.CLARIFICATION, payload)


def _now() -> Any:
    from django.utils import timezone

    return timezone.now()


__all__ = [
    "announce_change",
    "announce_clarification",
    "emit_event",
    "set_phase",
    "set_status",
]
