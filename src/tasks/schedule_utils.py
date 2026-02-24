"""Task schedule utilities — Redis-backed configurable schedules.

Each task has a default schedule (daily/weekly + hour + day_of_week).
Admin UI can override schedules via Redis.  Celery Beat fires tasks
frequently (e.g. hourly), and each task calls `should_run_now()` to
decide whether it's actually time to execute.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

KYIV_TZ = ZoneInfo("Europe/Kyiv")
REDIS_KEY = "tasks:schedules"

# Canonical task schedule defaults.
# day_of_week: 0=Monday … 6=Sunday (Python weekday()).
TASK_DEFAULTS: dict[str, dict[str, Any]] = {
    "catalog-full-sync": {
        "enabled": True,
        "frequency": "daily",
        "hour": 8,
        "day_of_week": 0,
        "label": "Полная синхронизация каталога 1С",
    },
    "refresh-stt-hints": {
        "enabled": True,
        "frequency": "weekly",
        "hour": 10,
        "day_of_week": 6,
        "label": "Обновление STT подсказок из каталога",
    },
}


async def load_schedules(redis: Any) -> dict[str, dict[str, Any]]:
    """Load schedules from Redis, merging with TASK_DEFAULTS."""
    result: dict[str, dict[str, Any]] = {}
    overrides: dict[str, Any] = {}

    try:
        raw = await redis.get(REDIS_KEY)
        if raw:
            overrides = json.loads(raw if isinstance(raw, str) else raw.decode())
    except Exception:
        logger.debug("Failed to read task schedules from Redis", exc_info=True)

    for key, defaults in TASK_DEFAULTS.items():
        merged = {**defaults}
        if key in overrides:
            merged.update(overrides[key])
        result[key] = merged

    return result


async def save_schedules(redis: Any, schedules: dict[str, dict[str, Any]]) -> None:
    """Save schedule overrides to Redis."""
    await redis.set(REDIS_KEY, json.dumps(schedules, ensure_ascii=False))


def should_run_now(schedule: dict[str, Any], now: datetime | None = None) -> bool:
    """Check whether a task should run at the current time (Europe/Kyiv).

    Args:
        schedule: Task schedule dict with keys: enabled, frequency, hour, day_of_week.
        now: Override current time (for testing). Must be timezone-aware or naive (Kyiv assumed).

    Returns:
        True if the task should execute now.
    """
    if not schedule.get("enabled", True):
        return False

    if now is None:
        now = datetime.now(KYIV_TZ)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=KYIV_TZ)

    current_hour = now.hour
    target_hour = schedule.get("hour", 0)
    frequency = schedule.get("frequency", "daily")

    if current_hour != target_hour:
        return False

    if frequency == "weekly":
        current_weekday = now.weekday()
        target_weekday = schedule.get("day_of_week", 0)
        if current_weekday != target_weekday:
            return False

    return True
