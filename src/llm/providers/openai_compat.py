"""OpenAI-compatible provider using raw aiohttp.

Supports OpenAI, DeepSeek, and Gemini (all expose OpenAI-compatible endpoints).
No openai SDK dependency — consistent with src/knowledge/embeddings.py pattern.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import aiohttp

from src.llm.format_converter import (
    anthropic_messages_to_openai,
    anthropic_tools_to_openai,
    openai_response_to_llm_response,
    openai_stream_chunk_to_events,
)
from src.llm.providers.base import AbstractProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from src.llm.models import LLMResponse, StreamEvent

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=60)


class OpenAICompatProvider(AbstractProvider):
    """LLM provider for OpenAI-compatible APIs (OpenAI, DeepSeek, Gemini).

    Uses raw aiohttp POST to {base_url}/chat/completions.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        provider_key: str = "",
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._provider_key = provider_key
        self._session: aiohttp.ClientSession | None = None
        # Newer OpenAI models (gpt-5, o1, o3) require max_completion_tokens
        # instead of the deprecated max_tokens parameter.
        self._use_max_completion_tokens = "api.openai.com" in self._base_url

    def _max_tokens_param(self, value: int) -> dict[str, int]:
        """Return the correct max tokens parameter for the API."""
        key = "max_completion_tokens" if self._use_max_completion_tokens else "max_tokens"
        return {key: value}

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=_REQUEST_TIMEOUT,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._session

    async def complete(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        openai_messages = anthropic_messages_to_openai(messages, system)

        body: dict[str, Any] = {
            "model": self._model,
            "messages": openai_messages,
            **self._max_tokens_param(max_tokens),
        }

        data = await self._post_chat(body)
        return openai_response_to_llm_response(data, self._provider_key, self._model)

    async def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 300,
    ) -> LLMResponse:
        openai_messages = anthropic_messages_to_openai(messages, system)
        openai_tools = anthropic_tools_to_openai(tools)

        body: dict[str, Any] = {
            "model": self._model,
            "messages": openai_messages,
            "tools": openai_tools,
            **self._max_tokens_param(max_tokens),
        }

        data = await self._post_chat(body)
        return openai_response_to_llm_response(data, self._provider_key, self._model)

    async def stream_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 300,
    ) -> AsyncIterator[StreamEvent]:
        openai_messages = anthropic_messages_to_openai(messages, system)
        openai_tools = anthropic_tools_to_openai(tools)

        body: dict[str, Any] = {
            "model": self._model,
            "messages": openai_messages,
            "tools": openai_tools,
            "stream": True,
            "stream_options": {"include_usage": True},
            **self._max_tokens_param(max_tokens),
        }

        session = await self._get_session()
        url = f"{self._base_url}/chat/completions"

        async with session.post(url, json=body) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise RuntimeError(
                    f"OpenAI-compat streaming error {resp.status}: {error_text[:300]}"
                )
            async for line in resp.content:
                text = line.decode("utf-8").strip()
                if not text or not text.startswith("data: "):
                    continue
                payload = text[6:]  # strip "data: "
                if payload == "[DONE]":
                    break
                chunk = json.loads(payload)
                for event in openai_stream_chunk_to_events(chunk, self._provider_key, self._model):
                    yield event

    async def health_check(self) -> bool:
        try:
            session = await self._get_session()
            # Simple completion with minimal tokens as health check
            body = {
                "model": self._model,
                "messages": [{"role": "user", "content": "hi"}],
                **self._max_tokens_param(1),
            }
            async with session.post(
                f"{self._base_url}/chat/completions",
                json=body,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                return resp.status == 200
        except Exception:
            logger.warning(
                "OpenAI-compat health check failed for %s",
                self._provider_key,
                exc_info=True,
            )
            return False

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _post_chat(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST to /chat/completions and return parsed JSON."""
        session = await self._get_session()
        url = f"{self._base_url}/chat/completions"

        async with session.post(url, json=body) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                logger.error(
                    "OpenAI-compat API error %d from %s: %s",
                    resp.status,
                    self._provider_key,
                    error_text[:300],
                )
                raise RuntimeError(
                    f"OpenAI-compat API error {resp.status} from {self._provider_key}: {error_text[:300]}"
                )
            data: dict[str, Any] = await resp.json()
            return data
