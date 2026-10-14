"""Celery entry points for the agent pipeline.

The HTTP layer never runs the graph; it only enqueues these tasks. Each task
moves through stages under the worker's ownership, and the database is the
source of truth (Task.md Q9), so a worker crash leaves a task that the next
invocation can resume from either the planning or the execution stage.
"""

from __future__ import annotations

from typing import Any

from celery import shared_task

from apps.agent.runner import run_execution_stage, run_planning_stage
from apps.agent_tasks.models import AgentTask
from config.observability import get_logger

logger = get_logger(__name__)

__all__: list[str] = ["run_agent_task"]

#: Statuses a worker can still run to completion from. Anything that was a
#: mid-flight execution when a worker died is resumed here -- the worktree
#: creation and the apply pass are both idempotent, so rerunning them is safe.
_EXECUTION_STATUSES = frozenset(
    {
        AgentTask.Status.AWAITING_APPROVAL,
        AgentTask.Status.RUNNING,
        AgentTask.Status.APPLYING,
        AgentTask.Status.VERIFYING,
    }
)


@shared_task(bind=True, ignore_result=True, max_retries=0)
def run_agent_task(self: Any, task_id: int) -> str:
    """Advance one agent task to its natural stopping point.

    Dispatch is by current status: ``QUEUED`` runs the planning stage
    (proposals -> ``awaiting_approval``); any post-approval status runs the
    execution stage (apply -> verify -> repair -> commit -> push). Terminal
    statuses (failed, cancelled, pushed, merged) are left alone.
    """
    try:
        task = AgentTask.objects.get(pk=task_id)
    except AgentTask.DoesNotExist:
        logger.warning("agent_task_missing", task_id=task_id)
        return "missing"

    if task.status == AgentTask.Status.QUEUED:
        run_planning_stage(task)
        return "planned"
    if task.status in _EXECUTION_STATUSES:
        run_execution_stage(task)
        return "executed"

    logger.info("agent_task_skipped", task_id=task_id, status=task.status)
    return f"skipped:{task.status}"
