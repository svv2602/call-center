"""Celery application configuration.

Uses Redis as broker for background tasks (quality evaluation, daily stats).
"""

from __future__ import annotations

from celery import Celery  # type: ignore[import-untyped]
from celery.schedules import crontab  # type: ignore[import-untyped]

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
        "src.tasks.data_retention.*": {"queue": "stats"},
        "src.tasks.partition_manager.*": {"queue": "stats"},
        "src.tasks.backup.*": {"queue": "stats"},
        "src.tasks.email_report.*": {"queue": "stats"},
    },
)

app.conf.beat_schedule = {
    "calculate-daily-stats": {
        "task": "src.tasks.daily_stats.calculate_daily_stats",
        "schedule": crontab(hour=1, minute=0),  # Every day at 01:00 Kyiv time
    },
    "cleanup-expired-data": {
        "task": "src.tasks.data_retention.cleanup_expired_data",
        "schedule": crontab(hour=3, minute=0, day_of_week="sunday"),  # Weekly at 03:00 Sunday
    },
    "ensure-partitions": {
        "task": "src.tasks.partition_manager.ensure_partitions",
        "schedule": crontab(hour=2, minute=0, day_of_month="1"),  # 1st of each month at 02:00
    },
    "backup-database": {
        "task": "src.tasks.backup.backup_database",
        "schedule": crontab(hour=4, minute=0),  # Every day at 04:00 Kyiv time
    },
    "verify-latest-backup": {
        "task": "src.tasks.backup.verify_latest_backup",
        "schedule": crontab(hour=4, minute=30),  # 30 min after backup
    },
    "send-weekly-report": {
        "task": "src.tasks.email_report.send_weekly_report",
        "schedule": crontab(hour=9, minute=0, day_of_week="monday"),  # Monday at 09:00 Kyiv time
    },
}

app.autodiscover_tasks(["src.tasks"])
