"""Celery tasks: nightly sync from the tshina Data API.

- ``tshina_sync_incremental`` — daily 04:00 Kyiv, ``updated_since`` = the
  stored watermark of each resource;
- ``tshina_sync_full`` — Sunday 04:30 Kyiv, full snapshot with the
  delete-what-is-gone sweep (guarded at 90 %).

Both are off until ``TSHINA_API_BASE_URL`` and ``TSHINA_API_TOKEN`` are
set: they log «disabled» and send no request. Idempotent (upserts); a second
run while one is in progress skips on the pg advisory lock.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.config import get_settings
from src.tasks.celery_app import app

logger = logging.getLogger(__name__)


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
