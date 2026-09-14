"""Tests for per-call LLM cost accounting.

The subject is the split between input tokens the provider served from its
prompt cache and the rest. On the live agent that split is not a detail: over
the fourteen days to 2026-09-14 the agent sent 115.2M input tokens and 106.7M
of them — 92.6% — came back from the cache, so the rate charged for them is
very nearly the whole bill.
"""

from __future__ import annotations

import pytest

from src.monitoring import pricing_cache
from src.monitoring.cost_tracker import CostBreakdown


@pytest.fixture(autouse=True)
def _clean_pricing():
    pricing_cache._cache.clear()
    pricing_cache._cached_input.clear()
    yield
    pricing_cache._cache.clear()
    pricing_cache._cached_input.clear()


def _price(provider: str, inp: float, out: float, cached: float | None = None) -> None:
    pricing_cache._cache[provider] = (inp, out)
    if cached is not None:
        pricing_cache._cached_input[provider] = cached


class TestCachedTokensAreBilledAtTheirOwnRate:
    def test_cached_tokens_use_the_cache_rate(self) -> None:
        # gpt-4.1-mini as LiteLLM prices it: $0.40/1M in, $0.10/1M cache-read.
        _price("openai-gpt41-mini", 0.40, 1.60, cached=0.10)
        cb = CostBreakdown()

        cb.add_llm_usage(
            input_tokens=1_000_000,
            output_tokens=0,
            provider_key="openai-gpt41-mini",
            cached_input_tokens=800_000,
        )

        # 200k uncached at 0.40 + 800k cached at 0.10
        assert cb.llm_cost == pytest.approx(0.08 + 0.08)

    def test_the_old_half_price_assumption_is_gone(self) -> None:
        """A 0.5 multiplier used to stand in for every provider's cache rate.

        It was right for none of the ten models in production — the real ratios
        run 0.25x and 0.10x — so this pins that the number now comes from the
        pricing row and nowhere else.
        """
        _price("openai-gpt5-mini", 0.25, 2.00, cached=0.025)
        cb = CostBreakdown()

        cb.add_llm_usage(
            input_tokens=1_000_000,
            output_tokens=0,
            provider_key="openai-gpt5-mini",
            cached_input_tokens=1_000_000,
        )

        assert cb.llm_cost == pytest.approx(0.025)
        half_price = 1_000_000 / 1_000_000 * 0.25 * 0.5
        assert cb.llm_cost != pytest.approx(half_price)

    def test_no_rate_on_record_means_no_discount(self) -> None:
        """An unknown cache rate must not be guessed in either direction.

        Charging full price is what the usage dashboard already does, so the two
        views agree on such a model instead of diverging by an invented factor.
        """
        _price("mystery-provider", 0.40, 1.60, cached=None)
        cb = CostBreakdown()

        cb.add_llm_usage(
            input_tokens=1_000_000,
            output_tokens=0,
            provider_key="mystery-provider",
            cached_input_tokens=1_000_000,
        )

        assert cb.llm_cost == pytest.approx(0.40)

    def test_a_call_with_no_cache_hits_is_unaffected(self) -> None:
        _price("openai-gpt41-mini", 0.40, 1.60, cached=0.10)
        cb = CostBreakdown()

        cb.add_llm_usage(
            input_tokens=500_000,
            output_tokens=100_000,
            provider_key="openai-gpt41-mini",
            cached_input_tokens=0,
        )

        assert cb.llm_cost == pytest.approx(0.20 + 0.16)

    def test_the_rate_used_is_reported_alongside_the_others(self) -> None:
        """`cost_breakdown` has to carry the third rate or the row cannot be audited.

        With only the input and output prices stored, a past call's cost could
        not be re-derived from its own JSONB — the cache rate would have to be
        looked up as it stands today, which is not what was charged.
        """
        _price("openai-gpt41-mini", 0.40, 1.60, cached=0.10)
        cb = CostBreakdown()

        cb.add_llm_usage(
            input_tokens=1000,
            output_tokens=10,
            provider_key="openai-gpt41-mini",
            cached_input_tokens=900,
        )

        d = cb.to_dict()
        assert d["llm_input_price_per_1m"] == 0.40
        assert d["llm_output_price_per_1m"] == 1.60
        assert d["llm_cached_input_price_per_1m"] == 0.10
        assert d["llm_cached_input_tokens"] == 900

    def test_the_unknown_rate_is_reported_as_the_input_rate(self) -> None:
        _price("mystery-provider", 0.40, 1.60, cached=None)
        cb = CostBreakdown()

        cb.add_llm_usage(
            input_tokens=1000,
            output_tokens=10,
            provider_key="mystery-provider",
            cached_input_tokens=900,
        )

        assert cb.to_dict()["llm_cached_input_price_per_1m"] == 0.40

    def test_more_cached_than_input_does_not_go_negative(self) -> None:
        """Providers have been known to report the two counts inconsistently."""
        _price("openai-gpt41-mini", 0.40, 1.60, cached=0.10)
        cb = CostBreakdown()

        cb.add_llm_usage(
            input_tokens=1000,
            output_tokens=0,
            provider_key="openai-gpt41-mini",
            cached_input_tokens=5000,
        )

        assert cb.llm_cost > 0
