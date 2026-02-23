"""Unit tests for LLM connection warm-up."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.llm.router import LLMRouter


class TestLLMWarmup:
    """Tests for LLMRouter.warmup()."""

    @pytest.mark.asyncio()
    async def test_warmup_calls_health_check(self) -> None:
        """warmup() calls health_check on each provider."""
        router = LLMRouter()
        mock_provider = MagicMock()
        mock_provider.health_check = AsyncMock(return_value=True)
        router._providers = {"test-provider": mock_provider}

        await router.warmup()

        mock_provider.health_check.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_warmup_failure_does_not_raise(self) -> None:
        """warmup() should not raise even if health_check fails."""
        router = LLMRouter()
        mock_provider = MagicMock()
        mock_provider.health_check = AsyncMock(side_effect=ConnectionError("timeout"))
        router._providers = {"failing-provider": mock_provider}

        # Should not raise
        await router.warmup()

    @pytest.mark.asyncio()
    async def test_warmup_no_providers(self) -> None:
        """warmup() with no providers should be a no-op."""
        router = LLMRouter()
        router._providers = {}

        await router.warmup()  # Should not raise
