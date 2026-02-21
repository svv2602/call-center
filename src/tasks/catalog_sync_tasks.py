"""Celery tasks for 1C catalog synchronization.

Replaces the in-process sync that previously ran at call-processor startup.
Two tasks:
  - catalog_full_sync: daily full catalog upload (05:00 Kyiv time)
  - catalog_incremental_sync: incremental wares + stock + Nova Poshta (every 5 min)
"""

from __future__ import annotations

import logging
from typing import Any

from src.config import get_settings
from src.tasks.celery_app import app

logger = logging.getLogger(__name__)


@app.task(
    name="src.tasks.catalog_sync_tasks.catalog_full_sync",
    bind=True,
    max_retries=3,
    time_limit=1800,
    soft_time_limit=1500,
)  # type: ignore[untyped-decorator]
def catalog_full_sync(self: Any) -> dict[str, Any]:
    """Full catalog sync from 1C (all wares + stock + Nova Poshta).

    Scheduled daily at 05:00 Kyiv time via Celery Beat.
    Uses a separate extended timeout for the large get_wares/?UploadingAll call.
    """
    import asyncio

    return asyncio.run(
        _catalog_full_sync_async(self)
    )


_LOCK_KEY = "catalog_sync:lock"
_LOCK_TTL_FULL = 1800  # 30 min for full sync
_LOCK_TTL_INCR = 300   # 5 min for incremental sync


async def _catalog_full_sync_async(task: Any) -> dict[str, Any]:
    """Async implementation of full catalog sync."""
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import create_async_engine

    from src.onec_client.client import OneCClient
    from src.onec_client.sync import CatalogSyncService

    settings = get_settings()

    if not settings.onec.username:
        logger.info("1C not configured (ONEC_USERNAME empty), skipping full sync")
        return {"status": "skipped", "reason": "onec_not_configured"}

    engine = create_async_engine(settings.database.url, pool_size=5, max_overflow=5, pool_pre_ping=True)
    redis: Redis | None = None
    onec_client: OneCClient | None = None

    try:
        redis = Redis.from_url(settings.redis.url, decode_responses=False)
        try:
            await redis.ping()
        except Exception:
            logger.warning("Redis unavailable for catalog sync")
            await redis.aclose()
            redis = None

        # Distributed lock to prevent concurrent syncs
        if redis is not None:
            acquired = await redis.set(_LOCK_KEY, "full", nx=True, ex=_LOCK_TTL_FULL)
            if not acquired:
                logger.warning("Catalog sync already running, skipping full sync")
                return {"status": "skipped", "reason": "already_running"}

        # Use full_sync_timeout for the heavy UploadingAll call
        onec_client = OneCClient(
            base_url=settings.onec.url,
            username=settings.onec.username,
            password=settings.onec.password,
            timeout=settings.onec.full_sync_timeout,
        )
        await onec_client.open()

        sync_service = CatalogSyncService(
            onec_client=onec_client,
            db_engine=engine,
            redis=redis,
            stock_cache_ttl=settings.onec.stock_cache_ttl,
        )

        await sync_service.full_sync()
        logger.info("Full catalog sync completed successfully")
        return {"status": "ok"}

    except Exception as exc:
        logger.exception("Full catalog sync failed")
        raise task.retry(countdown=300) from exc
    finally:
        if redis is not None:
            await redis.delete(_LOCK_KEY)
        if onec_client is not None:
            await onec_client.close()
        if redis is not None:
            await redis.aclose()
        await engine.dispose()


@app.task(
    name="src.tasks.catalog_sync_tasks.catalog_incremental_sync",
    bind=True,
    max_retries=2,
    time_limit=300,
    soft_time_limit=240,
)  # type: ignore[untyped-decorator]
def catalog_incremental_sync(self: Any) -> dict[str, Any]:
    """Incremental catalog sync: changed wares + stock + Nova Poshta.

    Scheduled every 5 minutes via Celery Beat.
    """
    import asyncio

    return asyncio.run(
        _catalog_incremental_sync_async(self)
    )


async def _catalog_incremental_sync_async(task: Any) -> dict[str, Any]:
    """Async implementation of incremental catalog sync."""
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import create_async_engine

    from src.onec_client.client import OneCClient
    from src.onec_client.sync import CatalogSyncService

    settings = get_settings()

    if not settings.onec.username:
        logger.info("1C not configured (ONEC_USERNAME empty), skipping incremental sync")
        return {"status": "skipped", "reason": "onec_not_configured"}

    engine = create_async_engine(settings.database.url, pool_size=5, max_overflow=5, pool_pre_ping=True)
    redis: Redis | None = None
    onec_client: OneCClient | None = None

    try:
        redis = Redis.from_url(settings.redis.url, decode_responses=False)
        try:
            await redis.ping()
        except Exception:
            logger.warning("Redis unavailable for catalog sync")
            await redis.aclose()
            redis = None

        # Distributed lock to prevent concurrent syncs
        if redis is not None:
            acquired = await redis.set(_LOCK_KEY, "incremental", nx=True, ex=_LOCK_TTL_INCR)
            if not acquired:
                logger.info("Catalog sync already running, skipping incremental sync")
                return {"status": "skipped", "reason": "already_running"}

        onec_client = OneCClient(
            base_url=settings.onec.url,
            username=settings.onec.username,
            password=settings.onec.password,
            timeout=settings.onec.timeout,
        )
        await onec_client.open()

        sync_service = CatalogSyncService(
            onec_client=onec_client,
            db_engine=engine,
            redis=redis,
            stock_cache_ttl=settings.onec.stock_cache_ttl,
        )

        await sync_service.incremental_sync()
        logger.info("Incremental catalog sync completed successfully")
        return {"status": "ok"}

    except Exception as exc:
        logger.exception("Incremental catalog sync failed")
        raise task.retry(countdown=60) from exc
    finally:
        if redis is not None:
            await redis.delete(_LOCK_KEY)
        if onec_client is not None:
            await onec_client.close()
        if redis is not None:
            await redis.aclose()
        await engine.dispose()
