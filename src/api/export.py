"""Export API endpoints — CSV and PDF downloads.

Provides CSV export for calls and daily statistics,
and PDF report download for weekly summaries.
"""

from __future__ import annotations

import csv
import io
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.api.auth import require_role
from src.config import get_settings
from src.logging.pii_sanitizer import sanitize_phone

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/analytics", tags=["analytics"])

_engine: AsyncEngine | None = None

_MAX_EXPORT_ROWS = 10000

# Module-level dependency to satisfy B008 lint rule
_analyst_dep = Depends(require_role("admin", "analyst"))


async def _get_engine() -> AsyncEngine:
    """Lazily create and cache the async database engine."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database.url)
    return _engine


def _csv_streaming_response(
    rows: list[dict[str, Any]],
    columns: list[str],
    filename: str,
) -> StreamingResponse:
    """Build a StreamingResponse that yields CSV lines."""

    def _generate() -> Any:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=columns)
        writer.writeheader()
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
        for row in rows:
            writer.writerow(row)
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    return StreamingResponse(
        _generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/calls/export")
async def export_calls_csv(
    date_from: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: str | None = Query(None, description="End date (YYYY-MM-DD)"),
    scenario: str | None = Query(None, description="Filter by scenario"),
    transferred: bool | None = Query(None, description="Filter transferred calls"),
    min_quality: float | None = Query(None, description="Minimum quality score"),
    _: dict[str, Any] = _analyst_dep,
) -> StreamingResponse:
    """Export calls as CSV with masked PII."""
    engine = await _get_engine()

    conditions = ["1=1"]
    params: dict[str, Any] = {"limit": _MAX_EXPORT_ROWS}

    if date_from:
        conditions.append("started_at >= :date_from")
        params["date_from"] = date_from
    if date_to:
        conditions.append("started_at < CAST(:date_to AS date) + interval '1 day'")
        params["date_to"] = date_to
    if scenario:
        conditions.append("scenario = :scenario")
        params["scenario"] = scenario
    if transferred is not None:
        conditions.append("transferred_to_operator = :transferred")
        params["transferred"] = transferred
    if min_quality is not None:
        conditions.append("quality_score >= :min_quality")
        params["min_quality"] = min_quality

    where_clause = " AND ".join(conditions)

    async with engine.begin() as conn:
        result = await conn.execute(
            text(f"""
                SELECT
                    id, started_at, duration_seconds, caller_id,
                    scenario, transferred_to_operator, transfer_reason,
                    quality_score, total_cost_usd
                FROM calls
                WHERE {where_clause}
                ORDER BY started_at DESC
                LIMIT :limit
            """),
            params,
        )
        rows_raw = [dict(row._mapping) for row in result]

    # Mask PII and format rows
    rows: list[dict[str, Any]] = []
    for r in rows_raw:
        rows.append(
            {
                "call_id": str(r["id"]),
                "started_at": str(r["started_at"] or ""),
                "duration_sec": r["duration_seconds"] or 0,
                "caller_id": sanitize_phone(str(r["caller_id"] or "")),
                "scenario": r["scenario"] or "",
                "transferred": r["transferred_to_operator"] or False,
                "transfer_reason": r["transfer_reason"] or "",
                "quality_score": r["quality_score"] if r["quality_score"] is not None else "",
                "total_cost": r["total_cost_usd"] if r["total_cost_usd"] is not None else "",
            }
        )

    columns = [
        "call_id",
        "started_at",
        "duration_sec",
        "caller_id",
        "scenario",
        "transferred",
        "transfer_reason",
        "quality_score",
        "total_cost",
    ]

    # Build filename from date range
    d_from = date_from or "all"
    d_to = date_to or "all"
    filename = f"calls_{d_from}_{d_to}.csv"

    return _csv_streaming_response(rows, columns, filename)


@router.get("/summary/export")
async def export_summary_csv(
    date_from: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: str | None = Query(None, description="End date (YYYY-MM-DD)"),
    _: dict[str, Any] = _analyst_dep,
) -> StreamingResponse:
    """Export daily statistics as CSV."""
    engine = await _get_engine()

    conditions = ["1=1"]
    params: dict[str, Any] = {}

    if date_from:
        conditions.append("stat_date >= :date_from")
        params["date_from"] = date_from
    if date_to:
        conditions.append("stat_date <= :date_to")
        params["date_to"] = date_to

    where_clause = " AND ".join(conditions)

    async with engine.begin() as conn:
        result = await conn.execute(
            text(f"""
                SELECT
                    stat_date, total_calls, resolved_by_bot, transferred,
                    avg_duration_seconds, avg_quality_score, total_cost_usd,
                    scenario_breakdown
                FROM daily_stats
                WHERE {where_clause}
                ORDER BY stat_date DESC
                LIMIT :limit
            """),
            {**params, "limit": _MAX_EXPORT_ROWS},
        )
        rows_raw = [dict(row._mapping) for row in result]

    import json

    rows: list[dict[str, Any]] = []
    for r in rows_raw:
        # Extract top scenario from scenario_breakdown JSON
        breakdown = r.get("scenario_breakdown")
        top_scenario = ""
        if breakdown:
            if isinstance(breakdown, str):
                breakdown = json.loads(breakdown)
            if isinstance(breakdown, dict) and breakdown:
                top_scenario = max(breakdown, key=breakdown.get)  # type: ignore[arg-type]

        rows.append(
            {
                "date": str(r["stat_date"]),
                "total_calls": r["total_calls"] or 0,
                "resolved_by_bot": r["resolved_by_bot"] or 0,
                "transferred": r["transferred"] or 0,
                "avg_duration_sec": round(float(r["avg_duration_seconds"] or 0), 1),
                "avg_quality": round(float(r["avg_quality_score"] or 0), 3),
                "total_cost": round(float(r["total_cost_usd"] or 0), 4),
                "top_scenario": top_scenario,
            }
        )

    columns = [
        "date",
        "total_calls",
        "resolved_by_bot",
        "transferred",
        "avg_duration_sec",
        "avg_quality",
        "total_cost",
        "top_scenario",
    ]

    d_from = date_from or "all"
    d_to = date_to or "all"
    filename = f"daily_stats_{d_from}_{d_to}.csv"

    return _csv_streaming_response(rows, columns, filename)


@router.get("/report/pdf")
async def download_report_pdf(
    date_from: str = Query(..., description="Start date (YYYY-MM-DD)"),
    date_to: str = Query(..., description="End date (YYYY-MM-DD)"),
    _: dict[str, Any] = _analyst_dep,
) -> Response:
    """Generate and download a PDF report for the given date range."""
    from src.reports.generator import generate_weekly_report

    engine = await _get_engine()
    pdf_bytes = await generate_weekly_report(date_from, date_to, engine=engine)
    filename = f"report_{date_from}_{date_to}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
