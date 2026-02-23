"""Unit tests for src.llm.helpers.llm_complete."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.llm.helpers import llm_complete
from src.llm.models import LLMTask


def _mock_router(text: str = "router reply", *, raise_exc: bool = False) -> AsyncMock:
    """Create a mock LLM router."""
    router = AsyncMock()
    if raise_exc:
        router.complete = AsyncMock(side_effect=RuntimeError("router down"))
    else:
        resp = MagicMock()
        resp.text = text
        router.complete = AsyncMock(return_value=resp)
    return router


def _mock_anthropic_response(text: str = "anthropic reply") -> MagicMock:
    block = MagicMock()
    block.text = text
    response = MagicMock()
    response.content = [block]
    return response


class TestLlmComplete:
    """Tests for llm_complete helper."""

    @pytest.mark.asyncio
    async def test_router_returns_text(self) -> None:
        router = _mock_router("hello from router")
        result = await llm_complete(
            LLMTask.AGENT, [{"role": "user", "content": "hi"}], router=router
        )
        assert result == "hello from router"
        router.complete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_router_empty_text_falls_back(self) -> None:
        router = _mock_router("")
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_mock_anthropic_response("fallback")
        )

        with patch("src.llm.helpers.anthropic.AsyncAnthropic", return_value=mock_client):
            result = await llm_complete(
                LLMTask.AGENT, [{"role": "user", "content": "hi"}], router=router
            )
        assert result == "fallback"

    @pytest.mark.asyncio
    async def test_router_exception_falls_back(self) -> None:
        router = _mock_router(raise_exc=True)
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_mock_anthropic_response("sdk fallback")
        )

        with patch("src.llm.helpers.anthropic.AsyncAnthropic", return_value=mock_client):
            result = await llm_complete(
                LLMTask.AGENT, [{"role": "user", "content": "hi"}], router=router
            )
        assert result == "sdk fallback"

    @pytest.mark.asyncio
    async def test_router_none_skips_to_fallback(self) -> None:
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_mock_anthropic_response("direct")
        )

        with patch("src.llm.helpers.anthropic.AsyncAnthropic", return_value=mock_client):
            result = await llm_complete(
                LLMTask.AGENT, [{"role": "user", "content": "hi"}], router=None
            )
        assert result == "direct"

    @pytest.mark.asyncio
    async def test_sentinel_uses_get_router(self) -> None:
        router = _mock_router("from global")

        with patch("src.llm.get_router", return_value=router):
            result = await llm_complete(
                LLMTask.AGENT, [{"role": "user", "content": "hi"}]
            )
        assert result == "from global"

    @pytest.mark.asyncio
    async def test_system_prompt_passed_to_router(self) -> None:
        router = _mock_router("ok")
        await llm_complete(
            LLMTask.AGENT,
            [{"role": "user", "content": "hi"}],
            system="You are helpful.",
            router=router,
        )
        _, kwargs = router.complete.call_args
        assert kwargs["system"] == "You are helpful."

    @pytest.mark.asyncio
    async def test_provider_override_passed_to_router(self) -> None:
        router = _mock_router("ok")
        await llm_complete(
            LLMTask.AGENT,
            [{"role": "user", "content": "hi"}],
            router=router,
            provider_override="gemini-flash",
        )
        _, kwargs = router.complete.call_args
        assert kwargs["provider_override"] == "gemini-flash"

    @pytest.mark.asyncio
    async def test_both_fail_returns_empty(self) -> None:
        router = _mock_router(raise_exc=True)
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=RuntimeError("sdk down"))

        with patch("src.llm.helpers.anthropic.AsyncAnthropic", return_value=mock_client):
            result = await llm_complete(
                LLMTask.AGENT, [{"role": "user", "content": "hi"}], router=router
            )
        assert result == ""

    @pytest.mark.asyncio
    async def test_fallback_uses_settings_model(self) -> None:
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_mock_anthropic_response("ok")
        )

        mock_settings = MagicMock()
        mock_settings.anthropic.api_key = "test-key"
        mock_settings.anthropic.model = "claude-test-model"

        with (
            patch("src.llm.helpers.anthropic.AsyncAnthropic", return_value=mock_client),
            patch("src.llm.helpers.get_settings", return_value=mock_settings),
        ):
            await llm_complete(
                LLMTask.AGENT, [{"role": "user", "content": "hi"}], router=None
            )

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "claude-test-model"

    @pytest.mark.asyncio
    async def test_system_passed_to_sdk_fallback(self) -> None:
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_mock_anthropic_response("ok")
        )

        with patch("src.llm.helpers.anthropic.AsyncAnthropic", return_value=mock_client):
            await llm_complete(
                LLMTask.AGENT,
                [{"role": "user", "content": "hi"}],
                system="Be helpful.",
                router=None,
            )

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["system"] == "Be helpful."

    @pytest.mark.asyncio
    async def test_text_is_stripped(self) -> None:
        router = _mock_router("  spaced reply  \n")
        result = await llm_complete(
            LLMTask.AGENT, [{"role": "user", "content": "hi"}], router=router
        )
        assert result == "spaced reply"
