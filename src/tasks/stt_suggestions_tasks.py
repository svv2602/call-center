"""Celery task: scan call_turns for recurring STT errors and upsert them
into stt_correction_suggestions.

Runs weekly (Monday 03:15 UTC), after partition rollover at 02:00. Manual
trigger is also exposed via POST /admin/stt-corrections/suggestions/rescan.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.config import get_settings
from src.tasks.celery_app import app

logger = logging.getLogger(__name__)


@app.task(
    name="src.tasks.stt_suggestions_tasks.rescan_stt_suggestions",
    bind=True,
    max_retries=2,
    time_limit=600,
    soft_time_limit=480,
)  # type: ignore[untyped-decorator]
def rescan_stt_suggestions(
    self: Any,
    days: int = 30,
    min_occurrences: int = 2,
    triggered_by: str = "manual",
) -> dict[str, Any]:
    """Weekly scan of recent call_turns → stt_correction_suggestions."""
    return asyncio.run(_scan_async(self, days, min_occurrences, triggered_by))


async def _scan_async(
    task: Any, days: int, min_occurrences: int, triggered_by: str
) -> dict[str, Any]:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import create_async_engine

    settings = get_settings()
    engine = create_async_engine(
        settings.database.url, pool_size=3, max_overflow=2, pool_pre_ping=True
    )
    redis: Redis | None = None

    try:
        redis = Redis.from_url(settings.redis.url, decode_responses=False)
        try:
            await redis.ping()
        except Exception:
            logger.warning("Redis unavailable for STT suggestion scan")
            await redis.aclose()
            redis = None

        if redis is None:
            return {"status": "skipped", "reason": "redis_unavailable"}

        from src.stt.suggestion_engine import scan_for_suggestions

        stats = await scan_for_suggestions(
            engine, redis, days=days, min_occurrences=min_occurrences
        )
        logger.info(
            "STT suggestions rescan complete (triggered_by=%s): %s",
            triggered_by,
            stats,
        )
        return {"status": "ok", "triggered_by": triggered_by, **stats}

    except Exception as exc:
        logger.exception("STT suggestions rescan failed")
        raise task.retry(countdown=180) from exc
    finally:
        if redis is not None:
            await redis.aclose()
        await engine.dispose()
