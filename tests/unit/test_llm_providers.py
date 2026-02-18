"""Tests for LLM providers — mock HTTP for both provider types."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.llm.models import LLMResponse, ToolCall
from src.llm.providers.anthropic_provider import AnthropicProvider
from src.llm.providers.openai_compat import OpenAICompatProvider


class _FakeAiohttpResponse:
    """Fake aiohttp response that works as an async context manager."""

    def __init__(self, status: int = 200, json_data: dict | None = None, text: str = "") -> None:
        self.status = status
        self._json_data = json_data or {}
        self._text = text

    async def json(self) -> dict:
        return self._json_data

    async def text(self) -> str:
        return self._text

    async def __aenter__(self) -> "_FakeAiohttpResponse":
        return self

    async def __aexit__(self, *args: object) -> None:
        pass


class TestAnthropicProvider:
    """Test AnthropicProvider with mocked SDK client."""

    @pytest.fixture()
    def provider(self) -> AnthropicProvider:
        return AnthropicProvider(
            api_key="test-key",
            model="claude-sonnet-4-5-20250929",
            provider_key="anthropic-sonnet",
        )

    def _make_mock_response(
        self,
        text: str = "Hello",
        tool_calls: list[dict[str, Any]] | None = None,
        stop_reason: str = "end_turn",
    ) -> MagicMock:
        """Create a mock Anthropic SDK response."""
        blocks = []
        if text:
            text_block = MagicMock()
            text_block.type = "text"
            text_block.text = text
            blocks.append(text_block)

        for tc in tool_calls or []:
            tool_block = MagicMock()
            tool_block.type = "tool_use"
            tool_block.id = tc["id"]
            tool_block.name = tc["name"]
            tool_block.input = tc["input"]
            blocks.append(tool_block)

        mock_resp = MagicMock()
        mock_resp.content = blocks
        mock_resp.stop_reason = stop_reason
        mock_resp.usage.input_tokens = 100
        mock_resp.usage.output_tokens = 50
        return mock_resp

    @pytest.mark.asyncio()
    async def test_complete_text(self, provider: AnthropicProvider) -> None:
        mock_resp = self._make_mock_response(text="Hi there!")
        provider._client.messages.create = AsyncMock(return_value=mock_resp)

        result = await provider.complete(
            messages=[{"role": "user", "content": "Hello"}],
            system="Be helpful",
            max_tokens=100,
        )
        assert isinstance(result, LLMResponse)
        assert result.text == "Hi there!"
        assert result.tool_calls == []
        assert result.stop_reason == "end_turn"
        assert result.provider == "anthropic-sonnet"
        assert result.usage.input_tokens == 100

    @pytest.mark.asyncio()
    async def test_complete_with_tools(self, provider: AnthropicProvider) -> None:
        mock_resp = self._make_mock_response(
            text="",
            tool_calls=[{"id": "t1", "name": "search", "input": {"q": "tires"}}],
            stop_reason="tool_use",
        )
        provider._client.messages.create = AsyncMock(return_value=mock_resp)

        tools = [{"name": "search", "description": "Search", "input_schema": {"type": "object", "properties": {}}}]
        result = await provider.complete_with_tools(
            messages=[{"role": "user", "content": "Find tires"}],
            tools=tools,
        )
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "search"
        assert result.tool_calls[0].arguments == {"q": "tires"}
        assert result.stop_reason == "tool_use"

    @pytest.mark.asyncio()
    async def test_health_check_success(self, provider: AnthropicProvider) -> None:
        provider._client.models.list = AsyncMock(return_value=[])
        assert await provider.health_check() is True

    @pytest.mark.asyncio()
    async def test_health_check_failure(self, provider: AnthropicProvider) -> None:
        provider._client.models.list = AsyncMock(side_effect=Exception("Network error"))
        assert await provider.health_check() is False


class TestOpenAICompatProvider:
    """Test OpenAICompatProvider with mocked aiohttp."""

    @pytest.fixture()
    def provider(self) -> OpenAICompatProvider:
        return OpenAICompatProvider(
            api_key="test-key",
            model="gpt-4o",
            base_url="https://api.openai.com/v1",
            provider_key="openai-gpt4o",
        )

    def _make_openai_response(
        self,
        content: str = "Hello!",
        tool_calls: list[dict[str, Any]] | None = None,
        finish_reason: str = "stop",
    ) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            message["content"] = None
            message["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc["arguments"]),
                    },
                }
                for tc in tool_calls
            ]
        return {
            "choices": [{"message": message, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 20},
        }

    @pytest.mark.asyncio()
    async def test_complete_text(self, provider: OpenAICompatProvider) -> None:
        response_data = self._make_openai_response(content="Hello from OpenAI!")

        mock_session = MagicMock()
        mock_session.post.return_value = _FakeAiohttpResponse(200, response_data)
        mock_session.closed = False
        provider._session = mock_session

        result = await provider.complete(
            messages=[{"role": "user", "content": "Hello"}],
            system="Be helpful",
        )
        assert result.text == "Hello from OpenAI!"
        assert result.provider == "openai-gpt4o"
        assert result.model == "gpt-4o"

    @pytest.mark.asyncio()
    async def test_complete_with_tools(self, provider: OpenAICompatProvider) -> None:
        response_data = self._make_openai_response(
            tool_calls=[{"id": "c1", "name": "search", "arguments": {"q": "tires"}}],
            finish_reason="tool_calls",
        )

        mock_session = MagicMock()
        mock_session.post.return_value = _FakeAiohttpResponse(200, response_data)
        mock_session.closed = False
        provider._session = mock_session

        tools = [{"name": "search", "description": "Search", "input_schema": {"type": "object", "properties": {}}}]
        result = await provider.complete_with_tools(
            messages=[{"role": "user", "content": "Find tires"}],
            tools=tools,
        )
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "search"
        assert result.stop_reason == "tool_use"

    @pytest.mark.asyncio()
    async def test_api_error(self, provider: OpenAICompatProvider) -> None:
        mock_session = MagicMock()
        mock_session.post.return_value = _FakeAiohttpResponse(500, text="Internal Server Error")
        mock_session.closed = False
        provider._session = mock_session

        with pytest.raises(RuntimeError, match="API error 500"):
            await provider.complete(
                messages=[{"role": "user", "content": "Hello"}],
            )

    @pytest.mark.asyncio()
    async def test_health_check_success(self, provider: OpenAICompatProvider) -> None:
        mock_session = MagicMock()
        mock_session.post.return_value = _FakeAiohttpResponse(200)
        mock_session.closed = False
        provider._session = mock_session

        assert await provider.health_check() is True

    @pytest.mark.asyncio()
    async def test_health_check_failure(self, provider: OpenAICompatProvider) -> None:
        mock_session = MagicMock()
        mock_session.post.side_effect = Exception("Connection refused")
        mock_session.closed = False
        provider._session = mock_session

        assert await provider.health_check() is False
