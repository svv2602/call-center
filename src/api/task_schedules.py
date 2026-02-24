"""Admin API for configurable Celery task schedules.

Manages execution schedules (daily/weekly, hour, day_of_week) stored in Redis.
Celery Beat fires tasks frequently; each task checks its schedule via should_run_now().
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from redis.asyncio import Redis

from src.api.auth import require_permission
from src.config import get_settings
from src.tasks.schedule_utils import REDIS_KEY, TASK_DEFAULTS, load_schedules

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/task-schedules", tags=["task-schedules"])

_redis: Redis | None = None

_perm_r = Depends(require_permission("configuration:read"))
_perm_w = Depends(require_permission("configuration:write"))


async def _get_redis() -> Redis:
    global _redis
    if _redis is None:
        settings = get_settings()
        _redis = Redis.from_url(settings.redis.url, decode_responses=True)
    return _redis


class SchedulePatch(BaseModel):
    enabled: bool | None = None
    frequency: str | None = None
    hour: int | None = None
    day_of_week: int | None = None

    @field_validator("frequency")
    @classmethod
    def validate_frequency(cls, v: str | None) -> str | None:
        if v is not None and v not in ("daily", "weekly"):
            raise ValueError("frequency must be 'daily' or 'weekly'")
        return v

    @field_validator("hour")
    @classmethod
    def validate_hour(cls, v: int | None) -> int | None:
        if v is not None and not (0 <= v <= 23):
            raise ValueError("hour must be between 0 and 23")
        return v

    @field_validator("day_of_week")
    @classmethod
    def validate_day_of_week(cls, v: int | None) -> int | None:
        if v is not None and not (0 <= v <= 6):
            raise ValueError("day_of_week must be between 0 (Mon) and 6 (Sun)")
        return v


@router.get("")
async def get_task_schedules(_: dict[str, Any] = _perm_r) -> dict[str, Any]:
    """Get all task schedules (Redis overrides merged with defaults)."""
    redis = await _get_redis()
    schedules = await load_schedules(redis)
    return {"schedules": schedules}


@router.patch("")
async def update_task_schedules(
    body: dict[str, SchedulePatch],
    _: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Update one or more task schedules. Only provided fields are changed."""
    redis = await _get_redis()

    # Load current overrides from Redis (not merged with defaults)
    overrides: dict[str, Any] = {}
    try:
        raw = await redis.get(REDIS_KEY)
        if raw:
            overrides = json.loads(raw)
    except Exception:
        pass

    for task_key, patch in body.items():
        if task_key not in TASK_DEFAULTS:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown task: {task_key}. Valid: {list(TASK_DEFAULTS.keys())}",
            )
        if task_key not in overrides:
            overrides[task_key] = {}
        patch_data = patch.model_dump(exclude_none=True)
        overrides[task_key].update(patch_data)

    await redis.set(REDIS_KEY, json.dumps(overrides, ensure_ascii=False))
    logger.info("Task schedules updated: %s", list(body.keys()))

    # Return merged view
    schedules = await load_schedules(redis)
    return {"schedules": schedules}


@router.post("/{task_key}/run")
async def run_task(task_key: str, _: dict[str, Any] = _perm_w) -> dict[str, Any]:
    """Manually trigger a scheduled task."""
    if task_key not in TASK_DEFAULTS:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown task: {task_key}",
        )

    from src.tasks.celery_app import app as celery_app

    task_map = {
        "catalog-full-sync": "src.tasks.catalog_sync_tasks.catalog_full_sync",
        "refresh-stt-hints": "src.tasks.stt_hints_tasks.refresh_stt_hints",
    }

    task_name = task_map.get(task_key)
    if not task_name:
        raise HTTPException(status_code=404, detail=f"No Celery task for: {task_key}")

    result = celery_app.send_task(task_name, kwargs={"triggered_by": "manual"})
    logger.info("Task %s queued manually: %s", task_key, result.id)
    return {"status": "queued", "task_id": result.id, "task_key": task_key}


@router.post("/reset")
async def reset_schedules(_: dict[str, Any] = _perm_w) -> dict[str, Any]:
    """Reset all schedules to defaults (delete Redis overrides)."""
    redis = await _get_redis()
    await redis.delete(REDIS_KEY)
    logger.info("Task schedules reset to defaults")
    return {"schedules": {k: {**v} for k, v in TASK_DEFAULTS.items()}}
