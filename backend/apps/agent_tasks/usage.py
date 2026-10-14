"""Per-user daily token budget: the fleet-level hard stop.

The per-run :class:`~apps.agent.budget.Budget` stops one runaway task at
``AGENT_TOKEN_BUDGET``; this module caps what a *single user* can consume across
all of their tasks in one UTC day (``AGENT_DAILY_TOKEN_BUDGET``). It is checked
at every place a run can start -- task creation, execution after approval, and
follow-up messages -- so a user who queues task after task hits a clear
``429 daily_budget_exceeded`` instead of silently burning the backend's total
quota.

Attribution rule: a task's token usage counts against the UTC day its run
*started* (``started_at``). A run that starts just before midnight and finishes
after it still bills the day it began, which is the day the user could see the
enqueue succeed. Tasks that never started (draft/queued) contribute zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db.models import Count, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.agent_tasks.models import AgentTask
from apps.agent_tasks.pricing import cost_usd

__all__ = [
    "DailyBudgetExceededError",
    "DailyUsage",
    "assert_daily_budget",
    "daily_usage",
]


class DailyBudgetExceededError(Exception):
    """The user's daily token budget is exhausted; no new run may start."""

    def __init__(self, *, spent: int, limit: int) -> None:
        self.spent = spent
        self.limit = limit
        super().__init__(
            f"Your daily token budget of {limit:,} tokens is used up "
            f"({spent:,} spent today). New runs resume tomorrow."
        )


@dataclass(frozen=True, slots=True)
class DailyUsage:
    """Immutable rollup of one user's token spend for the current UTC day."""

    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cached_tokens: int
    task_count: int

    @property
    def total_tokens(self) -> int:
        """Raw tokens; this is what the hard stop is enforced against."""
        return self.input_tokens + self.output_tokens + self.reasoning_tokens

    @property
    def total_cost_usd(self) -> Decimal:
        """Billed cost at the locked rates, with the prompt-cache discount applied."""
        cost = cost_usd(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            reasoning_tokens=self.reasoning_tokens,
            cached_tokens=self.cached_tokens,
        )
        return cost.quantize(Decimal("0.0001"))


def daily_usage(user: Any) -> DailyUsage:
    """Sum the requesting user's usage across runs started today (UTC)."""
    now = timezone.now()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    rows = AgentTask.objects.filter(
        user=user, started_at__gte=day_start, started_at__lt=day_end
    ).aggregate(
        input_tokens=Coalesce(Sum("input_tokens"), 0),
        output_tokens=Coalesce(Sum("output_tokens"), 0),
        reasoning_tokens=Coalesce(Sum("reasoning_tokens"), 0),
        cached_tokens=Coalesce(Sum("cached_tokens"), 0),
        task_count=Count("id"),
    )
    return DailyUsage(
        input_tokens=rows["input_tokens"],
        output_tokens=rows["output_tokens"],
        reasoning_tokens=rows["reasoning_tokens"],
        cached_tokens=rows["cached_tokens"],
        task_count=rows["task_count"],
    )


def assert_daily_budget(user: Any, *, projected_tokens: int = 0) -> DailyUsage:
    """Raise :class:`DailyBudgetExceededError` when the user's daily spend would exceed the cap."""
    usage = daily_usage(user)
    limit = settings.AGENT_DAILY_TOKEN_BUDGET
    if usage.total_tokens + projected_tokens >= limit:
        raise DailyBudgetExceededError(spent=usage.total_tokens, limit=limit)
    return usage
