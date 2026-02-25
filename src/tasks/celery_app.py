"""Celery application configuration.

Uses Redis as broker for background tasks (quality evaluation, daily stats).
"""

from __future__ import annotations

from typing import Any

from celery import Celery  # type: ignore[import-untyped]
from celery.schedules import crontab  # type: ignore[import-untyped]
from celery.signals import task_failure  # type: ignore[import-untyped]

from src.config import get_settings

settings = get_settings()

app = Celery(
    "call_center",
    broker=settings.celery.broker_url,
    backend=settings.celery.result_backend,
    include=[
        "src.tasks.quality_evaluator",
        "src.tasks.daily_stats",
        "src.tasks.data_retention",
        "src.tasks.partition_manager",
        "src.tasks.backup",
        "src.tasks.email_report",
        "src.tasks.embedding_tasks",
        "src.tasks.scraper_tasks",
        "src.tasks.catalog_sync_tasks",
        "src.tasks.stt_hints_tasks",
        "src.tasks.prompt_optimizer",
        "src.tasks.promo_summary_tasks",
        "src.tasks.pricing_sync",
    ],
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
    task_soft_time_limit=300,  # 5 min default soft limit (SoftTimeLimitExceeded)
    task_time_limit=360,  # 6 min default hard kill
    task_routes={
        "src.tasks.quality_evaluator.*": {"queue": "quality"},
        "src.tasks.daily_stats.*": {"queue": "stats"},
        "src.tasks.data_retention.*": {"queue": "stats"},
        "src.tasks.partition_manager.*": {"queue": "stats"},
        "src.tasks.backup.*": {"queue": "stats"},
        "src.tasks.email_report.*": {"queue": "stats"},
        "src.tasks.embedding_tasks.*": {"queue": "embeddings"},
        "src.tasks.scraper_tasks.*": {"queue": "scraper"},
        "src.tasks.catalog_sync_tasks.*": {"queue": "catalog"},
        "src.tasks.stt_hints_tasks.*": {"queue": "catalog"},
        "src.tasks.prompt_optimizer.*": {"queue": "quality"},
        "src.tasks.promo_summary_tasks.*": {"queue": "embeddings"},
        "src.tasks.pricing_sync.*": {"queue": "stats"},
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
        "schedule": crontab(
            hour=2, minute=0, day_of_week="monday"
        ),  # Weekly Monday 02:00 (idempotent, 3 months ahead)
    },
    "backup-database": {
        "task": "src.tasks.backup.backup_database",
        "schedule": crontab(hour=4, minute=0),  # Every day at 04:00 Kyiv time
    },
    "verify-latest-backup": {
        "task": "src.tasks.backup.verify_latest_backup",
        "schedule": crontab(hour=4, minute=30),  # 30 min after backup
    },
    "backup-redis": {
        "task": "src.tasks.backup.backup_redis",
        "schedule": crontab(hour=4, minute=15),  # Every day at 04:15 Kyiv time
    },
    "backup-knowledge-base": {
        "task": "src.tasks.backup.backup_knowledge_base",
        "schedule": crontab(hour=1, minute=0, day_of_week="sunday"),  # Weekly Sunday 01:00
    },
    "send-weekly-report": {
        "task": "src.tasks.email_report.send_weekly_report",
        "schedule": crontab(hour=9, minute=0, day_of_week="monday"),  # Monday at 09:00 Kyiv time
    },
    "rescrape-watched-pages": {
        "task": "src.tasks.scraper_tasks.rescrape_watched_pages",
        "schedule": crontab(
            minute=30
        ),  # Every hour at :30; actual timing per-page via next_scrape_at
    },
    "run-all-content-sources": {
        "task": "src.tasks.scraper_tasks.run_all_sources",
        "schedule": crontab(minute=15),  # Every hour at :15; actual day/time per-source in DB
        "kwargs": {"triggered_by": "beat"},
    },
    "catalog-full-sync": {
        "task": "src.tasks.catalog_sync_tasks.catalog_full_sync",
        "schedule": crontab(minute=0),  # Every hour at :00; actual time via Redis schedule
        "kwargs": {"triggered_by": "beat"},
    },
    "refresh-stt-hints": {
        "task": "src.tasks.stt_hints_tasks.refresh_stt_hints",
        "schedule": crontab(hour=0, minute=10),  # Daily at 00:10; actual day/time via Redis schedule
        "kwargs": {"triggered_by": "beat"},
    },
    "catalog-incremental-sync": {
        "task": "src.tasks.catalog_sync_tasks.catalog_incremental_sync",
        "schedule": crontab(minute="*/5"),  # Every 5 minutes
    },
    "analyze-failed-calls": {
        "task": "src.tasks.prompt_optimizer.analyze_failed_calls",
        "schedule": crontab(hour=6, minute=0, day_of_week="sunday"),  # Weekly Sunday 06:00
        "kwargs": {"days": 7, "max_calls": 20, "triggered_by": "beat"},
    },
    "sync-llm-pricing-catalog": {
        "task": "src.tasks.pricing_sync.sync_llm_pricing_catalog",
        "schedule": crontab(hour=5, minute=30),  # Daily at 05:30 Kyiv time
    },
}

# Note: autodiscover_tasks(["src.tasks"]) looks for src/tasks/tasks.py which doesn't exist.
# Task modules are explicitly listed in the Celery `include` parameter above.


# --- Celery signal: count task failures in Prometheus ---


@task_failure.connect  # type: ignore[untyped-decorator]
def _on_task_failure(sender: Any = None, **kwargs: Any) -> None:
    """Increment Prometheus counter on task failure."""
    from src.monitoring.metrics import celery_task_failures_total

    task_name = sender.name if sender else "unknown"
    # Use short name (last segment) for cleaner metrics
    short_name = task_name.rsplit(".", 1)[-1] if task_name else "unknown"
    celery_task_failures_total.labels(task_name=short_name).inc()
