"""Celery tasks: nightly sync from the tshina Data API.

- ``tshina_sync_incremental`` — daily 04:00 Kyiv, ``updated_since`` = the
  stored watermark of each resource;
- ``tshina_sync_full`` — Sunday 04:30 Kyiv, full snapshot with the
  delete-what-is-gone sweep (guarded at 90 %).

Both are off until ``TSHINA_API_BASE_URL`` and ``TSHINA_API_TOKEN`` are
set: they log «disabled» and send no request. Idempotent (upserts); a second
run while one is in progress skips on the pg advisory lock.

``tshina_sync_manual`` — a run started from the admin page
(``POST /admin/vehicles/tshina-sync/run``): ``dry_run`` | ``incremental`` |
``full``. The API claims ``ACTIVE_KEY`` in Redis (SET NX) before it queues
the task, so a second click gets 409 even while the first task still waits in
the queue and holds no pg lock yet; the task frees the key when it ends. The
result (the dry-run report included) is the Celery result — the backend is
already Redis and keeps it for ``result_expires`` (1 day), the same as
``RUN_TTL_S`` of the run record the status endpoint checks task ids against.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.config import get_settings
from src.tasks.celery_app import app

logger = logging.getLogger(__name__)

MANUAL_MODES = ("dry_run", "incremental", "full")
ACTIVE_KEY = "tshina_sync:active"  # value: task id of the manual run in flight
RUN_KEY = "tshina_sync:run:{task_id}"  # value: JSON {mode, user, queued_at}
ACTIVE_TTL_S = 12_000  # > time_limit of the full run: a lost worker frees it
RUN_TTL_S = 86_400  # = Celery result_expires (default 1 day)


async def run_tshina_sync(
    *,
    full: bool,
    resources: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Build engine + client from settings and run the sync (shared with the CLI)."""
    settings = get_settings()
    cfg = settings.tshina_api
    if not cfg.enabled:
        logger.info("tshina sync disabled (TSHINA_API_BASE_URL / TSHINA_API_TOKEN not set)")
        return {"status": "disabled"}

    from sqlalchemy.ext.asyncio import create_async_engine

    from src.integrations.tshina_api import RESOURCES, TshinaApiClient
    from src.integrations.tshina_sync import run_sync

    engine = create_async_engine(settings.database.url, pool_size=3, pool_pre_ping=True)
    try:
        async with TshinaApiClient(
            cfg.base_url,
            cfg.token,
            basic_user=cfg.basic_user,
            basic_password=cfg.basic_password,
            timeout=cfg.timeout,
        ) as client:
            result = await run_sync(
                engine,
                client,
                resources or RESOURCES,
                full=full,
                dry_run=dry_run,
                limit=cfg.page_limit,
            )
    finally:
        await engine.dispose()
    logger.info("tshina sync (%s) finished: %s", "full" if full else "incremental", result)
    return result


@app.task(
    name="src.tasks.tshina_sync_tasks.tshina_sync_incremental",
    soft_time_limit=3600,
    time_limit=3700,
)  # type: ignore[untyped-decorator]
def tshina_sync_incremental() -> dict[str, Any]:
    return asyncio.run(run_tshina_sync(full=False))


@app.task(
    name="src.tasks.tshina_sync_tasks.tshina_sync_full",
    soft_time_limit=10800,
    time_limit=11000,
)  # type: ignore[untyped-decorator]
def tshina_sync_full() -> dict[str, Any]:
    return asyncio.run(run_tshina_sync(full=True))


async def release_active(task_id: str) -> None:
    """Free ``ACTIVE_KEY`` if it still names this task (best effort; TTL backs it)."""
    from redis.asyncio import Redis

    redis = Redis.from_url(get_settings().redis.url, decode_responses=True)
    try:
        if await redis.get(ACTIVE_KEY) == task_id:
            await redis.delete(ACTIVE_KEY)
    except Exception:
        logger.warning("tshina sync: could not free %s for %s", ACTIVE_KEY, task_id, exc_info=True)
    finally:
        await redis.aclose()


async def run_manual_sync(mode: str, task_id: str) -> dict[str, Any]:
    """One manual run from the admin page; always frees the active marker."""
    try:
        if mode not in MANUAL_MODES:
            raise ValueError(f"unknown mode: {mode}")
        result = await run_tshina_sync(full=mode == "full", dry_run=mode == "dry_run")
    finally:
        await release_active(task_id)
    return {"mode": mode, **result}


@app.task(
    name="src.tasks.tshina_sync_tasks.tshina_sync_manual",
    bind=True,
    soft_time_limit=10800,
    time_limit=11000,
)  # type: ignore[untyped-decorator]
def tshina_sync_manual(self: Any, mode: str) -> dict[str, Any]:
    return asyncio.run(run_manual_sync(mode, self.request.id))
