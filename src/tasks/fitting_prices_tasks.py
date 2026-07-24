"""Celery task: refresh fitting service prices from 1C into Redis cache.

Fitting prices are a small read-mostly reference dataset (used by the LLM tool
`get_fitting_price` during live calls). To avoid a cold-cache HTTP hit to 1C
during a customer conversation, we proactively refresh the Redis cache hourly.

Cache key:  onec:fitting_prices          (JSON list, TTL 7200s = 2h)
Meta key:   onec:fitting_prices:updated_at  (ISO8601 timestamp, no TTL)

The two-hour TTL overlaps the hourly beat so a single failed run does not leave
a cold cache during a call.
"""

from __future__ import annotations

import contextlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

from src.config import get_settings
from src.tasks.celery_app import app

logger = logging.getLogger(__name__)

CACHE_KEY = "onec:fitting_prices"
UPDATED_AT_KEY = "onec:fitting_prices:updated_at"
CACHE_TTL = 7200  # 2h — overlaps hourly refresh
LOCK_KEY = "fitting_prices_sync:lock"
LOCK_TTL = 180  # 3 min max task runtime


@app.task(
    name="src.tasks.fitting_prices_tasks.refresh_fitting_prices",
    bind=True,
    max_retries=2,
    time_limit=180,
    soft_time_limit=120,
)  # type: ignore[untyped-decorator]
def refresh_fitting_prices(self: Any, triggered_by: str = "beat") -> dict[str, Any]:
    """Fetch fitting service prices from 1C REST and cache in Redis."""
    import asyncio

    return asyncio.run(_refresh_async(self, triggered_by))


async def _refresh_async(task: Any, triggered_by: str) -> dict[str, Any]:
    from redis.asyncio import Redis

    from src.onec_client.client import OneCClient

    settings = get_settings()

    if not settings.onec.username:
        logger.info("1C not configured (ONEC_USERNAME empty), skipping fitting prices refresh")
        return {"status": "skipped", "reason": "onec_not_configured"}

    redis: Redis | None = None
    onec_client: OneCClient | None = None
    acquired = False

    try:
        redis = Redis.from_url(settings.redis.url, decode_responses=False)
        try:
            await redis.ping()
        except Exception:
            logger.warning("Redis unavailable for fitting prices refresh")
            await redis.aclose()
            return {"status": "skipped", "reason": "redis_unavailable"}

        acquired = bool(
            await redis.set(LOCK_KEY, triggered_by, nx=True, ex=LOCK_TTL)
        )
        if not acquired:
            logger.info("Fitting prices refresh already running, skipping")
            return {"status": "skipped", "reason": "already_running"}

        onec_client = OneCClient(
            base_url=settings.onec.url,
            username=settings.onec.username,
            password=settings.onec.password,
            timeout=settings.onec.timeout,
        )
        await onec_client.open()

        data = await onec_client.get_fitting_prices()
        raw = data.get("data", data) if isinstance(data, dict) else data
        prices = raw if isinstance(raw, list) else []

        payload = json.dumps(prices, ensure_ascii=False)
        await redis.setex(CACHE_KEY, CACHE_TTL, payload)
        await redis.set(UPDATED_AT_KEY, datetime.now(UTC).isoformat())

        logger.info(
            "Fitting prices refreshed from 1C (triggered_by=%s): %d items",
            triggered_by,
            len(prices),
        )
        return {"status": "ok", "count": len(prices), "triggered_by": triggered_by}

    except Exception as exc:
        logger.exception("Fitting prices refresh failed")
        raise task.retry(countdown=120) from exc
    finally:
        if onec_client is not None:
            await onec_client.close()
        if redis is not None:
            if acquired:
                with contextlib.suppress(Exception):
                    await redis.delete(LOCK_KEY)
            await redis.aclose()
