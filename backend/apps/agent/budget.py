"""Run budgets: the hard stop that keeps one task from consuming the fleet's quota.

Two independent limits, checked after every model call and every tool call:

* **tokens** -- `AGENT_TOKEN_BUDGET` per task, so a loop that never converges
  fails at a known cost instead of at the end of the month.
* **wall clock** -- `AGENT_WALL_CLOCK_SECONDS`, because a Celery worker holding
  a slot for an hour is worse than a task that fails early.

Both raise :class:`~apps.agent.errors.BudgetError`, which the runner maps to a
graceful ``failed`` state with the work done so far retained (Task.md §5).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from apps.agent.errors import BudgetError

__all__: list[str] = ["Budget", "BudgetSnapshot"]


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    """Immutable view handed to logging and to the task's usage columns."""

    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cached_tokens: int
    llm_calls: int
    tool_calls: int
    elapsed_seconds: float

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.reasoning_tokens


@dataclass(slots=True)
class Budget:
    """Mutable accounting for one agent run."""

    token_limit: int
    wall_clock_seconds: float
    started_at: float = field(default_factory=time.monotonic)
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    llm_calls: int = 0
    tool_calls: int = 0

    # ── recording ────────────────────────────────────────────────────────────

    def record_usage(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        reasoning_tokens: int = 0,
        cached_tokens: int = 0,
    ) -> None:
        self.input_tokens += max(0, input_tokens)
        self.output_tokens += max(0, output_tokens)
        self.reasoning_tokens += max(0, reasoning_tokens)
        self.cached_tokens += max(0, cached_tokens)
        self.llm_calls += 1

    def record_tool_call(self) -> None:
        self.tool_calls += 1

    # ── inspection ───────────────────────────────────────────────────────────

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.reasoning_tokens

    @property
    def tokens_remaining(self) -> int:
        return max(0, self.token_limit - self.total_tokens)

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def seconds_remaining(self) -> float:
        return max(0.0, self.wall_clock_seconds - self.elapsed_seconds)

    @property
    def is_exhausted(self) -> bool:
        return self.total_tokens >= self.token_limit or self.seconds_remaining <= 0

    def snapshot(self) -> BudgetSnapshot:
        return BudgetSnapshot(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            reasoning_tokens=self.reasoning_tokens,
            cached_tokens=self.cached_tokens,
            llm_calls=self.llm_calls,
            tool_calls=self.tool_calls,
            elapsed_seconds=round(self.elapsed_seconds, 3),
        )

    # ── enforcement ──────────────────────────────────────────────────────────

    def check(self) -> None:
        """Raise if either limit is hit. Call before spending, not after."""
        if self.total_tokens >= self.token_limit:
            raise BudgetError("token", self.total_tokens, self.token_limit)
        if self.seconds_remaining <= 0:
            raise BudgetError(
                "wall clock",
                int(self.elapsed_seconds),
                int(self.wall_clock_seconds),
            )

    def clamp_max_tokens(self, requested: int | None) -> int | None:
        """Trim a completion cap so the request cannot exceed the budget.

        Groq bills the *whole* completion, so a generous ``max_tokens`` on the
        final call of a run is how budgets get overshot.
        """
        if requested is None:
            return None
        remaining = self.tokens_remaining
        if remaining <= 0:
            raise BudgetError("token", self.total_tokens, self.token_limit)
        return min(requested, remaining)
