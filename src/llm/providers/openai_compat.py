"""OpenAI-compatible provider using raw aiohttp.

Supports OpenAI, DeepSeek, and Gemini (all expose OpenAI-compatible endpoints).
No openai SDK dependency — consistent with src/knowledge/embeddings.py pattern.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import aiohttp

from src.llm.format_converter import (
    anthropic_messages_to_openai,
    anthropic_tools_to_openai,
    openai_response_to_llm_response,
)
from src.llm.providers.base import AbstractProvider

if TYPE_CHECKING:
    from src.llm.models import LLMResponse

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
            "max_tokens": max_tokens,
            "messages": openai_messages,
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
            "max_tokens": max_tokens,
            "messages": openai_messages,
            "tools": openai_tools,
        }

        data = await self._post_chat(body)
        return openai_response_to_llm_response(data, self._provider_key, self._model)

    async def health_check(self) -> bool:
        try:
            session = await self._get_session()
            # Simple completion with minimal tokens as health check
            body = {
                "model": self._model,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "hi"}],
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
