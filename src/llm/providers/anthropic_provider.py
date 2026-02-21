"""Anthropic native SDK provider."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

import anthropic

from src.llm.models import (
    LLMResponse,
    StreamDone,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
    Usage,
)
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

    async def stream_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 300,
    ) -> AsyncIterator[StreamEvent]:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": messages,
            "tools": tools,
        }
        if system:
            kwargs["system"] = system

        async with self._client.messages.stream(**kwargs) as stream:
            current_tool_id = ""
            async for event in stream:
                if event.type == "content_block_start":
                    block = event.content_block
                    if block.type == "tool_use":
                        current_tool_id = block.id
                        yield ToolCallStart(id=block.id, name=block.name)
                elif event.type == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        yield TextDelta(text=delta.text)
                    elif delta.type == "input_json_delta":
                        yield ToolCallDelta(
                            id=current_tool_id,
                            arguments_chunk=delta.partial_json,
                        )
                elif event.type == "content_block_stop":
                    if current_tool_id:
                        yield ToolCallEnd(id=current_tool_id)
                        current_tool_id = ""
                elif event.type == "message_delta":
                    pass  # handled after loop

            # After stream completes, emit StreamDone
            final = await stream.get_final_message()
            yield StreamDone(
                stop_reason=final.stop_reason or "end_turn",
                usage=Usage(
                    input_tokens=final.usage.input_tokens,
                    output_tokens=final.usage.output_tokens,
                ),
            )

    def _parse_response(self, response: Any) -> LLMResponse:
        """Parse Anthropic SDK response into LLMResponse."""
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=block.input,
                    )
                )

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
