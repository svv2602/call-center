"""Tests for the LLM pricing in-memory cache."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.monitoring import pricing_cache


@pytest.fixture(autouse=True)
def _clean_cache():
    """Ensure cache is empty before and after each test."""
    pricing_cache._cache.clear()
    pricing_cache._cached_input.clear()
    yield
    pricing_cache._cache.clear()
    pricing_cache._cached_input.clear()


def _pricing_row(provider_key, input_price, output_price, cached_price=None):
    """A pricing row carrying exactly the columns the refresh query selects.

    The spec is load-bearing. A bare MagicMock answers to
    ``cached_input_price_per_1m`` with another MagicMock, which is not None and
    whose ``float()`` is 1.0 — so every provider would quietly acquire a
    fabricated cache rate of $1 per 1M tokens and the test would stay green.
    """
    row = MagicMock(
        spec=[
            "provider_key",
            "input_price_per_1m",
            "output_price_per_1m",
            "cached_input_price_per_1m",
        ]
    )
    row.provider_key = provider_key
    row.input_price_per_1m = input_price
    row.output_price_per_1m = output_price
    row.cached_input_price_per_1m = cached_price
    return row


def _make_engine(rows):
    """Build a mock engine whose begin() yields a conn with execute returning rows."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=rows)

    @asynccontextmanager
    async def _begin():
        yield conn

    engine = MagicMock()
    engine.begin = _begin
    return engine


class TestGetPricing:
    """Tests for synchronous get_pricing()."""

    def test_fallback_when_empty(self) -> None:
        result = pricing_cache.get_pricing("unknown-provider")
        assert result == pricing_cache._FALLBACK

    def test_returns_cached_value(self) -> None:
        pricing_cache._cache["gemini-2.5-flash"] = (0.30, 2.50)
        assert pricing_cache.get_pricing("gemini-2.5-flash") == (0.30, 2.50)

    def test_fallback_for_missing_key(self) -> None:
        pricing_cache._cache["gemini-2.5-flash"] = (0.30, 2.50)
        assert pricing_cache.get_pricing("nonexistent") == pricing_cache._FALLBACK

    def test_multiple_providers(self) -> None:
        pricing_cache._cache["anthropic-sonnet"] = (3.0, 15.0)
        pricing_cache._cache["deepseek-chat"] = (0.27, 1.10)
        assert pricing_cache.get_pricing("anthropic-sonnet") == (3.0, 15.0)
        assert pricing_cache.get_pricing("deepseek-chat") == (0.27, 1.10)


class TestInvalidate:
    """Tests for cache invalidation."""

    def test_invalidate_clears_cache(self) -> None:
        pricing_cache._cache["test"] = (1.0, 2.0)
        pricing_cache.invalidate()
        assert len(pricing_cache._cache) == 0

    def test_get_pricing_returns_fallback_after_invalidate(self) -> None:
        pricing_cache._cache["gemini-2.5-flash"] = (0.30, 2.50)
        pricing_cache.invalidate()
        assert pricing_cache.get_pricing("gemini-2.5-flash") == pricing_cache._FALLBACK


class TestRefreshFromDb:
    """Tests for async refresh_from_db()."""

    @pytest.mark.asyncio
    async def test_refresh_populates_cache(self) -> None:
        rows = [
            _pricing_row("gemini-2.5-flash", 0.30, 2.50, cached_price=0.03),
            _pricing_row("anthropic-sonnet", 3.00, 15.00, cached_price=0.30),
        ]
        engine = _make_engine(rows)

        await pricing_cache.refresh_from_db(engine)

        assert pricing_cache.get_pricing("gemini-2.5-flash") == (0.30, 2.50)
        assert pricing_cache.get_pricing("anthropic-sonnet") == (3.00, 15.00)
        assert len(pricing_cache._cache) == 2

    @pytest.mark.asyncio
    async def test_refresh_replaces_old_entries(self) -> None:
        pricing_cache._cache["old-provider"] = (1.0, 2.0)

        rows = [
            _pricing_row("new-provider", 0.50, 1.00),
        ]
        engine = _make_engine(rows)

        await pricing_cache.refresh_from_db(engine)

        assert "old-provider" not in pricing_cache._cache
        assert pricing_cache.get_pricing("new-provider") == (0.50, 1.00)

    @pytest.mark.asyncio
    async def test_refresh_handles_db_error_gracefully(self) -> None:
        pricing_cache._cache["existing"] = (1.0, 2.0)

        engine = MagicMock()
        engine.begin.side_effect = Exception("DB connection failed")

        await pricing_cache.refresh_from_db(engine)

        # Cache should remain unchanged on error
        assert pricing_cache.get_pricing("existing") == (1.0, 2.0)

    @pytest.mark.asyncio
    async def test_refresh_empty_table(self) -> None:
        pricing_cache._cache["old"] = (1.0, 2.0)

        engine = _make_engine([])

        await pricing_cache.refresh_from_db(engine)

        assert len(pricing_cache._cache) == 0
        assert pricing_cache.get_pricing("old") == pricing_cache._FALLBACK


class TestCachedInputPrice:
    """Tests for the cache-read rate, which is optional per model."""

    @pytest.mark.asyncio
    async def test_refresh_loads_the_rate(self) -> None:
        engine = _make_engine([_pricing_row("openai-gpt41-mini", 0.40, 1.60, cached_price=0.10)])

        await pricing_cache.refresh_from_db(engine)

        assert pricing_cache.get_cached_input_price("openai-gpt41-mini") == 0.10

    @pytest.mark.asyncio
    async def test_a_null_column_stays_unknown(self) -> None:
        """No rate on record must not become a rate of zero.

        Zero would make cached tokens free, which is the opposite error from the
        0.5 multiplier this replaced but just as invented. The caller is meant to
        see None and fall back to the full input price.
        """
        engine = _make_engine([_pricing_row("some-provider", 0.40, 1.60, cached_price=None)])

        await pricing_cache.refresh_from_db(engine)

        assert pricing_cache.get_pricing("some-provider") == (0.40, 1.60)
        assert pricing_cache.get_cached_input_price("some-provider") is None

    def test_unknown_provider_has_no_rate(self) -> None:
        assert pricing_cache.get_cached_input_price("never-heard-of-it") is None

    @pytest.mark.asyncio
    async def test_refresh_drops_a_rate_that_is_gone(self) -> None:
        pricing_cache._cached_input["stale-provider"] = 0.05

        engine = _make_engine([_pricing_row("new-provider", 0.50, 1.00, cached_price=0.05)])

        await pricing_cache.refresh_from_db(engine)

        assert pricing_cache.get_cached_input_price("stale-provider") is None

    def test_invalidate_clears_the_rate_too(self) -> None:
        pricing_cache._cache["p"] = (1.0, 2.0)
        pricing_cache._cached_input["p"] = 0.1

        pricing_cache.invalidate()

        assert pricing_cache.get_cached_input_price("p") is None
