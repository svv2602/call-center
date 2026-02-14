"""Celery application configuration.

Uses Redis as broker for background tasks (quality evaluation, daily stats).
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from src.config import get_settings

settings = get_settings()

app = Celery(
    "call_center",
    broker=settings.celery.broker_url,
    backend=settings.celery.result_backend,
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Europe/Kyiv",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "src.tasks.quality_evaluator.*": {"queue": "quality"},
        "src.tasks.daily_stats.*": {"queue": "stats"},
    },
)

app.conf.beat_schedule = {
    "calculate-daily-stats": {
        "task": "src.tasks.daily_stats.calculate_daily_stats",
        "schedule": crontab(hour=1, minute=0),  # Every day at 01:00 Kyiv time
    },
}

app.autodiscover_tasks(["src.tasks"])
