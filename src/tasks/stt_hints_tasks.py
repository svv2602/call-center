"""Celery task for refreshing STT phrase hints from the tire catalog.

Extracts manufacturer + model names and rebuilds the phrase hints in Redis.
Can be triggered manually via Admin UI or by Celery Beat (weekly schedule).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.config import get_settings
from src.tasks.celery_app import app

logger = logging.getLogger(__name__)


@app.task(
    name="src.tasks.stt_hints_tasks.refresh_stt_hints",
    bind=True,
    max_retries=2,
    time_limit=300,
    soft_time_limit=240,
)  # type: ignore[untyped-decorator]
def refresh_stt_hints(self: Any, triggered_by: str = "manual") -> dict[str, Any]:
    """Refresh STT phrase hints from the tire catalog.

    When triggered_by="beat", checks the configurable schedule first.
    When triggered_by="manual", runs immediately.
    """
    return asyncio.run(_refresh_async(self, triggered_by))


async def _refresh_async(task: Any, triggered_by: str) -> dict[str, Any]:
    """Async implementation of STT hints refresh."""
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import create_async_engine

    settings = get_settings()

    # Schedule check for beat-triggered runs
    if triggered_by == "beat":
        try:
            redis_check = Redis.from_url(settings.redis.url, decode_responses=True)
            try:
                from src.tasks.schedule_utils import load_schedules, should_run_now

                schedules = await load_schedules(redis_check)
                schedule = schedules.get("refresh-stt-hints", {})
                if not should_run_now(schedule):
                    logger.debug("refresh-stt-hints: not scheduled to run now, skipping")
                    return {"status": "skipped", "reason": "not_scheduled"}
            finally:
                await redis_check.aclose()
        except Exception:
            logger.warning("Failed to check schedule, running anyway", exc_info=True)

    engine = create_async_engine(
        settings.database.url, pool_size=5, max_overflow=5, pool_pre_ping=True
    )
    redis: Redis | None = None

    try:
        redis = Redis.from_url(settings.redis.url, decode_responses=False)
        try:
            await redis.ping()
        except Exception:
            logger.warning("Redis unavailable for STT hints refresh")
            await redis.aclose()
            redis = None

        if redis is None:
            return {"status": "skipped", "reason": "redis_unavailable"}

        from src.stt.phrase_hints import refresh_phrase_hints

        stats = await refresh_phrase_hints(engine, redis)
        logger.info(
            "STT phrase hints refreshed (triggered_by=%s): %s", triggered_by, stats
        )
        return {"status": "ok", "triggered_by": triggered_by, **stats}

    except Exception as exc:
        logger.exception("STT hints refresh failed")
        raise task.retry(countdown=120) from exc
    finally:
        if redis is not None:
            await redis.aclose()
        await engine.dispose()
