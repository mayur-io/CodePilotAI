"""Token pricing: the single source of truth for what a run costs.

Groq bills input tokens at one rate and output tokens at another, and serves
input tokens that hit its prompt cache at half the input rate (Task.md §3/§110).
Reasoning tokens are priced at the input rate in the locked stack (Task.md §2).

Everything that reports a cost -- the per-task ``cost_usd`` column and the daily
budget rollup -- goes through :func:`cost_usd`, so the two can never disagree.
"""

from __future__ import annotations

from decimal import Decimal

__all__ = [
    "CACHED_INPUT_DISCOUNT",
    "INPUT_PRICE_PER_M",
    "OUTPUT_PRICE_PER_M",
    "cost_usd",
]

#: Locked $/1M-token rates (Task.md §2).
INPUT_PRICE_PER_M = Decimal("0.15")
OUTPUT_PRICE_PER_M = Decimal("0.60")

#: Groq serves cached prompt tokens at half the input rate.
CACHED_INPUT_DISCOUNT = Decimal("0.5")

_MILLION = Decimal(1_000_000)


def cost_usd(
    *,
    input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int = 0,
    cached_tokens: int = 0,
) -> Decimal:
    """Billed cost in USD for one usage record.

    ``cached_tokens`` is a subset of ``input_tokens`` (that is how Groq reports
    it), so it is clamped to the input count -- a malformed payload cannot
    yield a negative cost. Cached input tokens are billed at
    ``INPUT_PRICE_PER_M * CACHED_INPUT_DISCOUNT``; reasoning tokens bill at the
    full input rate, exactly as before caching existed.
    """
    cached = min(max(int(cached_tokens), 0), max(int(input_tokens), 0))
    discounted_input = Decimal(int(input_tokens)) - Decimal(cached) * CACHED_INPUT_DISCOUNT
    total = (discounted_input + Decimal(int(reasoning_tokens))) * INPUT_PRICE_PER_M + Decimal(
        int(output_tokens)
    ) * OUTPUT_PRICE_PER_M
    return total / _MILLION
