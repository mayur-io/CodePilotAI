"""Tests for token pricing, including the Groq prompt-cache discount."""

from __future__ import annotations

from decimal import Decimal

from apps.agent_tasks.pricing import (
    CACHED_INPUT_DISCOUNT,
    INPUT_PRICE_PER_M,
    OUTPUT_PRICE_PER_M,
    cost_usd,
)

_MILLION = Decimal(1_000_000)


def test_cost_matches_the_locked_rates_without_a_cache() -> None:
    assert cost_usd(input_tokens=1_000_000, output_tokens=0) == INPUT_PRICE_PER_M
    assert cost_usd(input_tokens=0, output_tokens=1_000_000) == OUTPUT_PRICE_PER_M
    # Reasoning tokens bill at the input rate.
    expected = (Decimal(10 + 5) * INPUT_PRICE_PER_M + Decimal(20) * OUTPUT_PRICE_PER_M) / _MILLION
    assert cost_usd(input_tokens=10, output_tokens=20, reasoning_tokens=5) == expected


def test_a_full_cache_hit_halves_the_input_cost() -> None:
    full = cost_usd(input_tokens=1000, output_tokens=0)
    cached = cost_usd(input_tokens=1000, output_tokens=0, cached_tokens=1000)
    assert cached == full * CACHED_INPUT_DISCOUNT
    assert cached == Decimal("0.000075")


def test_a_partial_cache_hit_discounts_only_the_cached_tokens() -> None:
    expected = (Decimal(500) + Decimal(500) * CACHED_INPUT_DISCOUNT) * INPUT_PRICE_PER_M / _MILLION
    assert cost_usd(input_tokens=1000, output_tokens=0, cached_tokens=500) == expected


def test_cached_tokens_are_clamped_to_the_input_count() -> None:
    # A malformed payload cannot discount more than was billed...
    assert cost_usd(input_tokens=100, output_tokens=0, cached_tokens=10_000) == cost_usd(
        input_tokens=100, output_tokens=0, cached_tokens=100
    )
    # ...and a negative count is treated as no cache rather than a surcharge.
    assert cost_usd(input_tokens=100, output_tokens=0, cached_tokens=-5) == cost_usd(
        input_tokens=100, output_tokens=0
    )


def test_cache_never_touches_output_or_reasoning_pricing() -> None:
    without_cache = cost_usd(input_tokens=0, output_tokens=50, reasoning_tokens=7)
    with_cache = cost_usd(input_tokens=0, output_tokens=50, reasoning_tokens=7, cached_tokens=999)
    assert with_cache == without_cache
