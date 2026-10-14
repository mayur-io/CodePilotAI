"""Tests for run budgets: token and wall-clock limits enforce a hard stop."""

from __future__ import annotations

import time

import pytest

from apps.agent.budget import Budget
from apps.agent.errors import BudgetError


def test_usage_accumulates() -> None:
    budget = Budget(token_limit=10_000, wall_clock_seconds=60)
    budget.record_usage(input_tokens=100, output_tokens=50, reasoning_tokens=20, cached_tokens=40)
    budget.record_usage(input_tokens=200, output_tokens=30)
    budget.record_tool_call()

    assert budget.total_tokens == 400
    assert budget.input_tokens == 300
    assert budget.output_tokens == 80
    assert budget.reasoning_tokens == 20
    assert budget.cached_tokens == 40
    assert budget.tool_calls == 1
    assert budget.llm_calls == 2
    assert budget.tokens_remaining == 9_600


def test_negative_usage_is_ignored() -> None:
    budget = Budget(token_limit=100, wall_clock_seconds=60)
    budget.record_usage(input_tokens=-5, output_tokens=10)
    assert budget.total_tokens == 10


def test_check_passes_within_limits() -> None:
    Budget(token_limit=1_000, wall_clock_seconds=60).check()  # must not raise


def test_check_raises_when_token_budget_exhausted() -> None:
    budget = Budget(token_limit=100, wall_clock_seconds=60)
    budget.record_usage(input_tokens=100)
    with pytest.raises(BudgetError, match="token budget exhausted"):
        budget.check()


def test_check_raises_when_wall_clock_expired() -> None:
    started = time.monotonic() - 61
    budget = Budget(token_limit=10_000, wall_clock_seconds=60, started_at=started)
    with pytest.raises(BudgetError, match="wall clock"):
        budget.check()


def test_clamp_max_tokens_caps_the_completion_to_remaining() -> None:
    budget = Budget(token_limit=100, wall_clock_seconds=60)
    budget.record_usage(input_tokens=70)
    assert budget.clamp_max_tokens(10_000) == 30
    assert budget.clamp_max_tokens(20) == 20


def test_clamp_rejects_when_nothing_remains() -> None:
    budget = Budget(token_limit=10, wall_clock_seconds=60)
    budget.record_usage(input_tokens=10)
    with pytest.raises(BudgetError):
        budget.clamp_max_tokens(5)


def test_snapshot_is_a_plain_value() -> None:
    budget = Budget(token_limit=1_000, wall_clock_seconds=60)
    budget.record_usage(input_tokens=10, output_tokens=5, reasoning_tokens=1)
    snapshot = budget.snapshot()

    assert snapshot.total_tokens == 16
    assert snapshot.llm_calls == 1
    assert snapshot.elapsed_seconds >= 0
    # The snapshot is frozen; mutating it is a dataclass error.
    with pytest.raises(AttributeError):
        snapshot.input_tokens = 99  # type: ignore[misc]


def test_exhausted_flag_matches_check() -> None:
    budget = Budget(token_limit=50, wall_clock_seconds=60)
    assert not budget.is_exhausted
    budget.record_usage(input_tokens=50)
    assert budget.is_exhausted
