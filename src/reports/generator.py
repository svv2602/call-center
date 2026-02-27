"""Weekly PDF report generator.

Queries daily_stats for a date range, renders an HTML template,
and converts to PDF via WeasyPrint.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import date as date_type
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.config import get_settings

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"


async def _fetch_report_data(
    engine: AsyncEngine,
    date_from: str,
    date_to: str,
) -> dict[str, Any]:
    """Fetch aggregated data for the report period."""
    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT
                    stat_date, total_calls, resolved_by_bot, transferred,
                    avg_duration_seconds, avg_quality_score, total_cost_usd,
                    transfer_reasons
                FROM daily_stats
                WHERE stat_date >= :date_from AND stat_date <= :date_to
                ORDER BY stat_date
            """),
            {"date_from": date_type.fromisoformat(date_from), "date_to": date_type.fromisoformat(date_to)},
        )
        rows = [dict(row._mapping) for row in result]

    # Aggregate totals
    total_calls = sum(r["total_calls"] or 0 for r in rows)
    resolved = sum(r["resolved_by_bot"] or 0 for r in rows)
    transferred = sum(r["transferred"] or 0 for r in rows)
    total_cost = sum(float(r["total_cost_usd"] or 0) for r in rows)
    avg_quality_sum = sum(float(r["avg_quality_score"] or 0) for r in rows)
    avg_duration_sum = sum(float(r["avg_duration_seconds"] or 0) for r in rows)
    n = len(rows) or 1

    resolved_pct = round(resolved / total_calls * 100, 1) if total_calls > 0 else 0

    # Aggregate transfer reasons across all days
    all_reasons: Counter[str] = Counter()
    for r in rows:
        reasons_raw = r.get("transfer_reasons")
        if reasons_raw:
            if isinstance(reasons_raw, str):
                reasons_raw = json.loads(reasons_raw)
            if isinstance(reasons_raw, dict):
                for reason, count in reasons_raw.items():
                    all_reasons[reason] += count

    top_transfer_reasons = [
        {"name": name, "count": count} for name, count in all_reasons.most_common(5)
    ]

    days = [
        {
            "date": str(r["stat_date"]),
            "total_calls": r["total_calls"] or 0,
            "resolved_by_bot": r["resolved_by_bot"] or 0,
            "transferred": r["transferred"] or 0,
            "avg_duration": round(float(r["avg_duration_seconds"] or 0), 0),
            "avg_quality": round(float(r["avg_quality_score"] or 0), 2),
            "total_cost": round(float(r["total_cost_usd"] or 0), 2),
        }
        for r in rows
    ]

    return {
        "date_from": date_from,
        "date_to": date_to,
        "totals": {
            "total_calls": total_calls,
            "resolved_pct": resolved_pct,
            "transferred": transferred,
            "avg_quality": round(avg_quality_sum / n, 2),
            "total_cost": round(total_cost, 2),
            "avg_duration": round(avg_duration_sum / n, 0),
        },
        "days": days,
        "top_transfer_reasons": top_transfer_reasons,
    }


def _render_html(data: dict[str, Any]) -> str:
    """Render the HTML template with report data."""
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=True,
    )
    template = env.get_template("weekly_report.html")
    return str(template.render(**data))


def _html_to_pdf(html: str) -> bytes:
    """Convert HTML string to PDF bytes via WeasyPrint."""
    from weasyprint import HTML

    result: bytes = HTML(string=html).write_pdf()
    return result


async def generate_weekly_report(
    date_from: str,
    date_to: str,
    engine: AsyncEngine | None = None,
) -> bytes:
    """Generate a weekly PDF report for the given date range.

    Args:
        date_from: Start date (YYYY-MM-DD).
        date_to: End date (YYYY-MM-DD).
        engine: Optional async engine (creates one if not provided).

    Returns:
        PDF file as bytes.
    """
    dispose = False
    if engine is None:
        settings = get_settings()
        engine = create_async_engine(settings.database.url, pool_pre_ping=True)
        dispose = True

    try:
        data = await _fetch_report_data(engine, date_from, date_to)
        html = _render_html(data)
        return _html_to_pdf(html)
    finally:
        if dispose:
            await engine.dispose()
