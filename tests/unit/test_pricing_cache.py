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
    yield
    pricing_cache._cache.clear()


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
            MagicMock(provider_key="gemini-2.5-flash", input_price_per_1m=0.30, output_price_per_1m=2.50),
            MagicMock(provider_key="anthropic-sonnet", input_price_per_1m=3.00, output_price_per_1m=15.00),
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
            MagicMock(provider_key="new-provider", input_price_per_1m=0.50, output_price_per_1m=1.00),
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
