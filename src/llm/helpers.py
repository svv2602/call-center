"""Unified LLM helper: try router → fallback to direct Anthropic SDK.

All non-streaming LLM calls across the codebase should use ``llm_complete``
instead of duplicating router-check + Anthropic-fallback logic.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import anthropic

from src.config import get_settings

if TYPE_CHECKING:
    from src.llm.models import LLMTask

logger = logging.getLogger(__name__)

_SENTINEL = object()


async def llm_complete(
    task: LLMTask,
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    max_tokens: int = 1024,
    router: object = _SENTINEL,
    provider_override: str | None = None,
) -> str:
    """Complete an LLM request via router with Anthropic SDK fallback.

    Args:
        task: Logical LLM task type for routing.
        messages: Anthropic-format message list.
        system: Optional system prompt.
        max_tokens: Maximum tokens in the response.
        router: LLM router instance. Default (sentinel) uses ``get_router()``.
            Pass ``None`` explicitly to skip the router.
        provider_override: Force a specific provider (passed to router).

    Returns:
        Stripped text response, or empty string if all paths fail.
    """
    # Resolve router
    if router is _SENTINEL:
        from src.llm import get_router

        router = get_router()

    # 1. Try router
    if router is not None:
        try:
            kwargs: dict[str, Any] = {"max_tokens": max_tokens}
            if system is not None:
                kwargs["system"] = system
            if provider_override is not None:
                kwargs["provider_override"] = provider_override

            resp = await router.complete(task, messages, **kwargs)  # type: ignore[union-attr]
            text = (resp.text or "").strip()
            if text:
                return text
        except Exception:
            logger.debug("LLM router failed for task=%s, falling back to Anthropic SDK", task)

    # 2. Fallback: direct Anthropic SDK
    try:
        settings = get_settings()
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic.api_key)
        kwargs_sdk: dict[str, Any] = {
            "model": settings.anthropic.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system is not None:
            kwargs_sdk["system"] = system

        response = await client.messages.create(**kwargs_sdk)
        block = response.content[0] if response.content else None
        return (block.text if block and hasattr(block, "text") else "").strip()
    except Exception:
        logger.exception("Anthropic SDK fallback failed for task=%s", task)
        return ""
