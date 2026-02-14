"""Automatic partition management for time-partitioned PostgreSQL tables.

Creates future partitions and drops old ones (for transcription tables).
Runs monthly via Celery Beat on the 1st of each month.

Partitioned tables: calls, call_turns, call_tool_calls.
Partition naming: {table}_{YYYY}_{MM}
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.config import get_settings
from src.tasks.celery_app import app

logger = logging.getLogger(__name__)

PARTITIONED_TABLES = ["calls", "call_turns", "call_tool_calls"]
PARTITION_KEY = {
    "calls": "started_at",
    "call_turns": "created_at",
    "call_tool_calls": "created_at",
}
MONTHS_AHEAD = 3
# Drop old partitions for transcription tables only (calls metadata kept longer)
RETENTION_TABLES = ["call_turns", "call_tool_calls"]
RETENTION_MONTHS = 12


def _months_range(start: date, count: int) -> list[tuple[date, date]]:
    """Generate (start, end) date pairs for `count` months starting from `start`."""
    result = []
    current = start.replace(day=1)
    for _ in range(count):
        year = current.year
        month = current.month
        if month == 12:
            next_month = date(year + 1, 1, 1)
        else:
            next_month = date(year, month + 1, 1)
        result.append((current, next_month))
        current = next_month
    return result


@app.task(name="src.tasks.partition_manager.ensure_partitions")
def ensure_partitions() -> dict[str, Any]:
    """Create future partitions and drop expired ones.

    Idempotent: uses IF NOT EXISTS / IF EXISTS.
    """
    return asyncio.get_event_loop().run_until_complete(_ensure_partitions_async())


async def _ensure_partitions_async() -> dict[str, Any]:
    settings = get_settings()
    engine = create_async_engine(settings.database.url)

    results: dict[str, Any] = {"created": [], "dropped": []}

    try:
        async with engine.begin() as conn:
            # --- Create future partitions ---
            today = date.today()
            months = _months_range(today, MONTHS_AHEAD)

            for table in PARTITIONED_TABLES:
                for start, end in months:
                    partition_name = f"{table}_{start.year}_{start.month:02d}"
                    sql = (
                        f"CREATE TABLE IF NOT EXISTS {partition_name} "
                        f"PARTITION OF {table} "
                        f"FOR VALUES FROM ('{start.isoformat()}') "
                        f"TO ('{end.isoformat()}')"
                    )
                    await conn.execute(text(sql))
                    results["created"].append(partition_name)

            # --- Drop old partitions for transcription tables ---
            cutoff = today.replace(day=1) - timedelta(days=RETENTION_MONTHS * 31)
            cutoff = cutoff.replace(day=1)

            for table in RETENTION_TABLES:
                # Check for partitions older than retention period
                for months_back in range(RETENTION_MONTHS, RETENTION_MONTHS + 6):
                    d = today.replace(day=1)
                    for _ in range(months_back):
                        d = (d - timedelta(days=1)).replace(day=1)
                    partition_name = f"{table}_{d.year}_{d.month:02d}"
                    sql = f"DROP TABLE IF EXISTS {partition_name}"
                    await conn.execute(text(sql))
                    results["dropped"].append(partition_name)

        logger.info(
            "Partition management: created=%d, dropped=%d",
            len(results["created"]),
            len(results["dropped"]),
        )

    except Exception:
        logger.exception("Partition management failed")
        raise
    finally:
        await engine.dispose()

    return results
