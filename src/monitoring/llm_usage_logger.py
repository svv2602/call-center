"""Fire-and-forget LLM usage logger.

Writes every LLM call (all task types) to ``llm_usage_log`` for cost analysis.
Uses lazy engine creation (same pattern as analytics.py) and
``asyncio.create_task`` so callers never block on the INSERT.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.config import get_settings

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None


def _get_engine() -> AsyncEngine:
    """Lazily create and cache the async database engine."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database.url, pool_pre_ping=True)
    return _engine


async def _insert_usage(
    task_type: str,
    provider_key: str,
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int | None,
    call_id: str | None,
    tenant_id: str | None,
) -> None:
    """Perform the actual INSERT into llm_usage_log."""
    try:
        engine = _get_engine()
        async with engine.begin() as conn:
            await conn.execute(
                text("""
                    INSERT INTO llm_usage_log
                        (task_type, provider_key, model_name,
                         input_tokens, output_tokens, latency_ms,
                         call_id, tenant_id)
                    VALUES
                        (:task_type, :provider_key, :model_name,
                         :input_tokens, :output_tokens, :latency_ms,
                         :call_id, :tenant_id)
                """),
                {
                    "task_type": task_type,
                    "provider_key": provider_key,
                    "model_name": model_name,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "latency_ms": latency_ms,
                    "call_id": call_id,
                    "tenant_id": tenant_id,
                },
            )
    except Exception:
        logger.warning(
            "Failed to log LLM usage: task=%s provider=%s tokens=%d/%d",
            task_type,
            provider_key,
            input_tokens,
            output_tokens,
            exc_info=True,
        )


def log_llm_usage(
    task_type: str,
    provider_key: str,
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int | None = None,
    call_id: str | None = None,
    tenant_id: str | None = None,
) -> None:
    """Schedule a fire-and-forget INSERT into llm_usage_log.

    Safe to call from any async context — errors are logged, never raised.
    """
    try:
        asyncio.get_running_loop().create_task(
            _insert_usage(
                task_type=task_type,
                provider_key=provider_key,
                model_name=model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                call_id=call_id,
                tenant_id=tenant_id,
            )
        )
    except RuntimeError:
        # No running event loop (e.g. sync context) — skip silently
        logger.debug("No event loop for LLM usage logging, skipping")
