"""Anthropic native SDK provider."""

from __future__ import annotations

import logging
from typing import Any

import anthropic

from src.llm.models import LLMResponse, ToolCall, Usage
from src.llm.providers.base import AbstractProvider

logger = logging.getLogger(__name__)


class AnthropicProvider(AbstractProvider):
    """LLM provider using the Anthropic Python SDK (AsyncAnthropic)."""

    def __init__(self, api_key: str, model: str, provider_key: str = "") -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._provider_key = provider_key

    async def complete(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system

        response = await self._client.messages.create(**kwargs)
        return self._parse_response(response)

    async def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 300,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": messages,
            "tools": tools,
        }
        if system:
            kwargs["system"] = system

        response = await self._client.messages.create(**kwargs)
        return self._parse_response(response)

    async def health_check(self) -> bool:
        try:
            await self._client.models.list(limit=1)
            return True
        except Exception:
            logger.warning("Anthropic health check failed", exc_info=True)
            return False

    async def close(self) -> None:
        await self._client.close()

    def _parse_response(self, response: Any) -> LLMResponse:
        """Parse Anthropic SDK response into LLMResponse."""
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=block.input,
                ))

        stop_reason = response.stop_reason or "end_turn"

        return LLMResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            usage=Usage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            ),
            provider=self._provider_key,
            model=self._model,
        )
