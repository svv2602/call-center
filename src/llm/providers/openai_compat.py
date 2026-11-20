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
    OpenAIStreamParser,
    anthropic_messages_to_openai,
    anthropic_tools_to_openai,
    openai_response_to_llm_response,
)
from src.llm.providers.base import AbstractProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from src.llm.models import LLMResponse, StreamEvent

logger = logging.getLogger(__name__)


def _tool_choice_param(tool_choice: str | None) -> dict[str, Any]:
    """OpenAI ``tool_choice`` forcing one named function, or nothing (``auto``)."""
    if not tool_choice:
        return {}
    return {"tool_choice": {"type": "function", "function": {"name": tool_choice}}}

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=35)

#: Why a provider call failed — the ``reason`` label of
#: ``callcenter_llm_provider_failures_total``. The list is closed: whatever is
#: not recognised is ``other``, so a new shape of failure is still counted.
FAILURE_REASONS: tuple[str, ...] = (
    "timeout",
    "quota",
    "rate_limit",
    "auth",
    "http_5xx",
    "network",
    "other",
)

# ``code`` / ``type`` of an error body that mean "the account is out of money".
# OpenAI answers 429 for this as well as for a rate limit, and only the body
# tells them apart. ``billing_error`` is Anthropic's.
_QUOTA_MARKERS = frozenset(
    {
        "insufficient_quota",
        "billing_hard_limit_reached",
        "billing_not_active",
        "billing_error",
    }
)
# Anthropic reports an empty balance as a plain 400 ``invalid_request_error``.
_QUOTA_MESSAGES = ("credit balance is too low",)


def _error_fields(body: object) -> tuple[frozenset[str], str]:
    """Lower-cased ``code`` / ``type`` / ``status`` and ``message`` of an error body.

    Takes the raw text or already parsed JSON. A body that is not the
    ``{"error": {...}}`` object gives nothing.
    """
    if isinstance(body, (bytes, bytearray)):
        body = body.decode("utf-8", errors="replace")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            return frozenset(), ""
    if not isinstance(body, dict):
        return frozenset(), ""
    error = body.get("error")
    if not isinstance(error, dict):
        return frozenset(), ""
    values = (error.get(key) for key in ("code", "type", "status"))
    markers = frozenset(value.lower() for value in values if isinstance(value, str))
    message = error.get("message")
    return markers, message.lower() if isinstance(message, str) else ""


def classify_http_failure(status: int, body: object) -> str:
    """Reason (one of ``FAILURE_REASONS``) for a non-200 reply of an LLM API.

    ``body`` is the whole reply — text or parsed JSON — not the 300 characters
    that go into the exception message. Never raises.
    """
    markers, message = _error_fields(body)
    if markers & _QUOTA_MARKERS or any(text in message for text in _QUOTA_MESSAGES):
        return "quota"
    if status == 402:  # Payment Required (DeepSeek: "Insufficient Balance")
        return "quota"
    if status == 429:
        return "rate_limit"
    if status in (401, 403):
        return "auth"
    if status == 408:
        return "timeout"
    if 500 <= status <= 599:
        return "http_5xx"
    return "other"


class ProviderHTTPError(RuntimeError):
    """A non-200 reply; ``reason`` was read from the body while it was whole."""

    def __init__(self, message: str, *, status: int, reason: str) -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason


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
        reasoning_effort: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._provider_key = provider_key
        self._session: aiohttp.ClientSession | None = None
        # Newer OpenAI models (gpt-5, o1, o3) require max_completion_tokens
        # instead of the deprecated max_tokens parameter.
        self._use_max_completion_tokens = "api.openai.com" in self._base_url
        # Reasoning effort control for GPT-5 / o1 / o3 series. When set, the
        # model spends fewer "internal reasoning" tokens before producing
        # visible output — critical for voice-agent latency where reasoning
        # tokens don't stream. Default: auto-select "minimal" for gpt-5-* to
        # keep time-to-first-audio comparable to gpt-4.1-mini. Set explicitly
        # via provider config to override ("minimal" | "low" | "medium" | "high").
        if reasoning_effort is None and model.startswith("gpt-5"):
            reasoning_effort = "minimal"
        self._reasoning_effort = reasoning_effort

    def _max_tokens_param(self, value: int) -> dict[str, int]:
        """Return the correct max tokens parameter for the API."""
        key = "max_completion_tokens" if self._use_max_completion_tokens else "max_tokens"
        return {key: value}

    def _reasoning_param(self) -> dict[str, str]:
        """Return {'reasoning_effort': ...} if configured, else empty dict."""
        if self._reasoning_effort:
            return {"reasoning_effort": self._reasoning_effort}
        return {}

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
            **self._reasoning_param(),
        }

        data = await self._post_chat(body)
        return openai_response_to_llm_response(data, self._provider_key, self._model)

    async def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 300,
        *,
        tool_choice: str | None = None,
    ) -> LLMResponse:
        openai_messages = anthropic_messages_to_openai(messages, system)
        openai_tools = anthropic_tools_to_openai(tools)

        body: dict[str, Any] = {
            "model": self._model,
            "messages": openai_messages,
            "tools": openai_tools,
            **self._max_tokens_param(max_tokens),
            **self._reasoning_param(),
            **_tool_choice_param(tool_choice),
        }

        data = await self._post_chat(body)
        return openai_response_to_llm_response(data, self._provider_key, self._model)

    async def stream_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 300,
        *,
        tool_choice: str | None = None,
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
            **self._reasoning_param(),
            **_tool_choice_param(tool_choice),
        }

        session = await self._get_session()
        url = f"{self._base_url}/chat/completions"

        parser = OpenAIStreamParser(self._provider_key, self._model)
        async with session.post(url, json=body) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise ProviderHTTPError(
                    f"OpenAI-compat streaming error {resp.status}: {error_text[:300]}",
                    status=resp.status,
                    reason=classify_http_failure(resp.status, error_text),
                )
            async for line in resp.content:
                text = line.decode("utf-8").strip()
                if not text or not text.startswith("data: "):
                    continue
                payload = text[6:]  # strip "data: "
                if payload == "[DONE]":
                    break
                chunk = json.loads(payload)
                for event in parser.feed(chunk):
                    yield event
            # Flush deferred StreamDone if usage chunk never arrived
            for event in parser.flush():
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
                raise ProviderHTTPError(
                    f"OpenAI-compat API error {resp.status} from {self._provider_key}: {error_text[:300]}",
                    status=resp.status,
                    reason=classify_http_failure(resp.status, error_text),
                )
            data: dict[str, Any] = await resp.json()
            return data
