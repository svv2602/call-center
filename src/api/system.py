"""System status, config reload, and Celery health API endpoints.

Provides extended system information, hot-reload for safe config params,
and Celery worker health check.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.api.auth import require_permission
from src.config import get_settings
from src.monitoring.metrics import celery_workers_online

logger = logging.getLogger(__name__)
router = APIRouter(tags=["system"])

_engine: AsyncEngine | None = None
_start_time = time.time()

# Module-level dependencies to satisfy B008 lint rule
_perm_r = Depends(require_permission("system:read"))
_perm_w = Depends(require_permission("system:write"))


async def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database.url, pool_pre_ping=True)
    return _engine


@router.get("/health/celery")
async def celery_health() -> dict[str, Any]:
    """Check Celery worker availability.

    No auth required — used by monitoring systems.
    """
    try:
        from src.tasks.celery_app import app as celery_app

        inspect = celery_app.control.inspect(timeout=5)
        ping_result = inspect.ping()
        workers_online = len(ping_result) if ping_result else 0

        active = inspect.active()
        queue_lengths: dict[str, int] = {}
        if active:
            for worker_name, tasks in active.items():
                queue_lengths[worker_name] = len(tasks)

        celery_workers_online.set(workers_online)
        return {
            "status": "ok" if workers_online > 0 else "degraded",
            "workers_online": workers_online,
            "active_tasks": queue_lengths,
        }
    except Exception as e:
        logger.debug("Celery health check failed: %s", e)
        celery_workers_online.set(0)
        return {
            "status": "unavailable",
            "workers_online": 0,
            "error": str(e),
        }


@router.post("/admin/config/reload")
async def reload_config(
    _: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Reload safe configuration parameters from environment.

    Reloadable: QualitySettings, FeatureFlagSettings, LoggingSettings.
    NOT reloadable: DatabaseSettings, RedisSettings, AudioSocketSettings.
    """
    try:
        from src.config import (
            FeatureFlagSettings,
            LoggingSettings,
            QualitySettings,
            get_settings,
        )

        settings = get_settings()
        old_quality = settings.quality.llm_model
        old_stt = settings.feature_flags.stt_provider
        old_log_level = settings.logging.level

        settings.quality = QualitySettings()
        settings.feature_flags = FeatureFlagSettings()
        settings.logging = LoggingSettings()

        reloaded = {
            "quality.llm_model": f"{old_quality} -> {settings.quality.llm_model}",
            "feature_flags.stt_provider": f"{old_stt} -> {settings.feature_flags.stt_provider}",
            "logging.level": f"{old_log_level} -> {settings.logging.level}",
        }

        logger.info("Configuration reloaded: %s", reloaded)
        return {"status": "reloaded", "changes": reloaded}
    except Exception as e:
        logger.error("Config reload failed: %s", e)
        return {"status": "error", "error": str(e)}


@router.get("/admin/system-status")
async def system_status(
    _: dict[str, Any] = _perm_r,
) -> dict[str, Any]:
    """Full system status for admin dashboard."""
    settings = get_settings()
    uptime_seconds = int(time.time() - _start_time)
    result: dict[str, Any] = {
        "version": "0.1.0",
        "uptime_seconds": uptime_seconds,
    }

    # PostgreSQL status
    try:
        engine = await _get_engine()
        async with engine.begin() as conn:
            db_size = await conn.execute(text("SELECT pg_database_size(current_database())"))
            result["postgres_db_size_bytes"] = db_size.scalar()

            conn_count = await conn.execute(text("SELECT count(*) FROM pg_stat_activity"))
            result["postgres_connections"] = conn_count.scalar()
    except Exception:
        result["postgres"] = "unavailable"

    # Redis status
    try:
        from redis.asyncio import Redis

        r = Redis.from_url(settings.redis.url, decode_responses=True)
        try:
            info = await r.info("memory")
            result["redis_used_memory"] = info.get("used_memory_human", "unknown")
        finally:
            await r.aclose()
    except Exception:
        result["redis"] = "unavailable"

    # Celery status
    try:
        from src.tasks.celery_app import app as celery_app

        inspect = celery_app.control.inspect(timeout=3)
        ping_result = inspect.ping()
        result["celery_workers_online"] = len(ping_result) if ping_result else 0
    except Exception:
        result["celery_workers_online"] = 0

    # Last backup info
    try:
        from pathlib import Path

        backup_dir = Path(settings.backup.backup_dir)
        if backup_dir.exists():
            backups = sorted(
                backup_dir.glob("callcenter_*.sql*"), key=lambda f: f.stat().st_mtime, reverse=True
            )
            if backups:
                latest = backups[0]
                result["last_backup"] = {
                    "file": latest.name,
                    "size_bytes": latest.stat().st_size,
                    "modified": time.strftime(
                        "%Y-%m-%dT%H:%M:%S", time.gmtime(latest.stat().st_mtime)
                    ),
                }
    except Exception:
        pass

    return result
