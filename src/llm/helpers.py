"""Unified LLM helper: try router → fallback to direct Anthropic SDK.

All non-streaming LLM calls across the codebase should use ``llm_complete``
instead of duplicating router-check + Anthropic-fallback logic.

In Celery worker context, the global router is not initialized (main() doesn't
run). This module lazily creates and caches a router on first use so that
Celery tasks route through the same provider chain as the main process.
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

# Lazy-initialized router for Celery worker context
_lazy_router: Any = None
_lazy_router_attempted = False


async def _ensure_router() -> Any:
    """Lazily initialize an LLM router for Celery workers.

    When get_router() returns None (no main() startup), creates and caches
    a router from Redis config + env vars. Called once per worker process.
    """
    global _lazy_router, _lazy_router_attempted

    if _lazy_router is not None:
        return _lazy_router
    if _lazy_router_attempted:
        return None

    _lazy_router_attempted = True

    settings = get_settings()
    if not settings.feature_flags.llm_routing_enabled:
        return None

    try:
        from redis.asyncio import Redis

        from src.llm.router import LLMRouter

        router = LLMRouter()
        redis = Redis.from_url(settings.redis.url, decode_responses=False)
        try:
            await router.initialize(redis=redis)
        finally:
            await redis.aclose()

        if router._providers:
            _lazy_router = router
            logger.info(
                "Lazy LLM router initialized for worker: %d providers", len(router._providers)
            )
            return router

        logger.warning("Lazy LLM router has no providers, falling back to Anthropic SDK")
        return None
    except Exception:
        logger.warning("Lazy LLM router init failed, falling back to Anthropic SDK", exc_info=True)
        return None


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
    # Resolve router: global → lazy-init for Celery workers
    if router is _SENTINEL:
        from src.llm import get_router

        router = get_router()
        if router is None:
            router = await _ensure_router()

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
