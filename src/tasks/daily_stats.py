"""Daily statistics aggregation task.

Runs daily via Celery Beat to calculate aggregated metrics
and store them in the daily_stats table.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.config import get_settings
from src.tasks.celery_app import app

logger = logging.getLogger(__name__)


@app.task(name="src.tasks.daily_stats.calculate_daily_stats")  # type: ignore[untyped-decorator]
def calculate_daily_stats(target_date: str | None = None) -> dict[str, Any]:
    """Calculate and store daily aggregated statistics.

    Args:
        target_date: Date to calculate stats for (YYYY-MM-DD).
                     Defaults to yesterday.
    """
    import asyncio

    return asyncio.run(_calculate_daily_stats_async(target_date))


async def _calculate_daily_stats_async(target_date: str | None = None) -> dict[str, Any]:
    """Async implementation of daily stats calculation."""
    settings = get_settings()
    engine = create_async_engine(settings.database.url, pool_pre_ping=True)

    stat_date = date.fromisoformat(target_date) if target_date else date.today() - timedelta(days=1)

    try:
        async with engine.begin() as conn:
            # Main call statistics
            result = await conn.execute(
                text("""
                    SELECT
                        COUNT(*) AS total_calls,
                        COUNT(*) FILTER (WHERE NOT transferred_to_operator) AS resolved_by_bot,
                        COUNT(*) FILTER (WHERE transferred_to_operator) AS transferred,
                        COALESCE(AVG(duration_seconds), 0) AS avg_duration_seconds,
                        COALESCE(AVG(quality_score), 0) AS avg_quality_score,
                        COALESCE(SUM(total_cost_usd), 0) AS total_cost_usd
                    FROM calls
                    WHERE started_at::date = :stat_date
                """),
                {"stat_date": stat_date.isoformat()},
            )
            stats_row = result.first()
            if stats_row is None:
                msg = "Expected row from aggregate query"
                raise RuntimeError(msg)
            stats = dict(stats_row._mapping)

            # Scenario breakdown
            scenario_result = await conn.execute(
                text("""
                    SELECT scenario, COUNT(*) AS count
                    FROM calls
                    WHERE started_at::date = :stat_date
                      AND scenario IS NOT NULL
                    GROUP BY scenario
                """),
                {"stat_date": stat_date.isoformat()},
            )
            scenario_breakdown = {row.scenario: row.count for row in scenario_result}

            # Transfer reasons breakdown
            transfer_result = await conn.execute(
                text("""
                    SELECT transfer_reason, COUNT(*) AS count
                    FROM calls
                    WHERE started_at::date = :stat_date
                      AND transferred_to_operator = true
                      AND transfer_reason IS NOT NULL
                    GROUP BY transfer_reason
                """),
                {"stat_date": stat_date.isoformat()},
            )
            transfer_reasons = {row.transfer_reason: row.count for row in transfer_result}

            # Hourly distribution
            hourly_result = await conn.execute(
                text("""
                    SELECT EXTRACT(HOUR FROM started_at) AS hour, COUNT(*) AS count
                    FROM calls
                    WHERE started_at::date = :stat_date
                    GROUP BY EXTRACT(HOUR FROM started_at)
                    ORDER BY hour
                """),
                {"stat_date": stat_date.isoformat()},
            )
            hourly_distribution = {str(int(row.hour)): row.count for row in hourly_result}

            # Upsert into daily_stats
            await conn.execute(
                text("""
                    INSERT INTO daily_stats (
                        stat_date, total_calls, resolved_by_bot, transferred,
                        avg_duration_seconds, avg_quality_score, total_cost_usd,
                        scenario_breakdown, transfer_reasons, hourly_distribution
                    ) VALUES (
                        :stat_date, :total_calls, :resolved_by_bot, :transferred,
                        :avg_duration_seconds, :avg_quality_score, :total_cost_usd,
                        :scenario_breakdown, :transfer_reasons, :hourly_distribution
                    )
                    ON CONFLICT (stat_date) DO UPDATE SET
                        total_calls = EXCLUDED.total_calls,
                        resolved_by_bot = EXCLUDED.resolved_by_bot,
                        transferred = EXCLUDED.transferred,
                        avg_duration_seconds = EXCLUDED.avg_duration_seconds,
                        avg_quality_score = EXCLUDED.avg_quality_score,
                        total_cost_usd = EXCLUDED.total_cost_usd,
                        scenario_breakdown = EXCLUDED.scenario_breakdown,
                        transfer_reasons = EXCLUDED.transfer_reasons,
                        hourly_distribution = EXCLUDED.hourly_distribution
                """),
                {
                    "stat_date": stat_date.isoformat(),
                    "total_calls": stats["total_calls"],
                    "resolved_by_bot": stats["resolved_by_bot"],
                    "transferred": stats["transferred"],
                    "avg_duration_seconds": float(stats["avg_duration_seconds"]),
                    "avg_quality_score": float(stats["avg_quality_score"]),
                    "total_cost_usd": float(stats["total_cost_usd"]),
                    "scenario_breakdown": json.dumps(scenario_breakdown),
                    "transfer_reasons": json.dumps(transfer_reasons),
                    "hourly_distribution": json.dumps(hourly_distribution),
                },
            )

        logger.info(
            "Daily stats calculated: date=%s, calls=%d, resolved=%d, transferred=%d",
            stat_date,
            stats["total_calls"],
            stats["resolved_by_bot"],
            stats["transferred"],
        )

        return {
            "stat_date": stat_date.isoformat(),
            **stats,
            "scenario_breakdown": scenario_breakdown,
            "transfer_reasons": transfer_reasons,
        }

    finally:
        await engine.dispose()
