"""Analytics API endpoints.

Provides quality reports, call filtering, and call details
for the admin interface.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID  # noqa: TC003 - FastAPI needs UUID at runtime for path params

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.api.auth import require_role
from src.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/analytics", tags=["analytics"])

_engine: AsyncEngine | None = None

# Module-level dependencies to satisfy B008 lint rule
_analyst_dep = Depends(require_role("admin", "analyst"))


async def _get_engine() -> AsyncEngine:
    """Lazily create and cache the async database engine."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database.url)
    return _engine


@router.get("/quality")
async def get_quality_report(
    date_from: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: str | None = Query(None, description="End date (YYYY-MM-DD)"),
    scenario: str | None = Query(None, description="Filter by scenario"),
    _: dict[str, Any] = _analyst_dep,
) -> dict[str, Any]:
    """Aggregated quality report with averages by criteria."""
    engine = await _get_engine()

    conditions = ["quality_score IS NOT NULL"]
    params: dict[str, Any] = {}

    if date_from:
        conditions.append("started_at >= :date_from")
        params["date_from"] = date_from
    if date_to:
        conditions.append("started_at < :date_to::date + interval '1 day'")
        params["date_to"] = date_to
    if scenario:
        conditions.append("scenario = :scenario")
        params["scenario"] = scenario

    where_clause = " AND ".join(conditions)

    async with engine.begin() as conn:
        result = await conn.execute(
            text(f"""
                SELECT
                    COUNT(*) AS total_evaluated,
                    AVG(quality_score) AS avg_quality_score,
                    COUNT(*) FILTER (WHERE quality_score < 0.5) AS low_quality_count,
                    COUNT(*) FILTER (WHERE quality_score >= 0.8) AS high_quality_count
                FROM calls
                WHERE {where_clause}
            """),
            params,
        )
        summary_row = result.first()
        if summary_row is None:
            msg = "Expected row from aggregate query"
            raise RuntimeError(msg)
        summary = dict(summary_row._mapping)

        # Per-scenario breakdown
        scenario_result = await conn.execute(
            text(f"""
                SELECT
                    scenario,
                    COUNT(*) AS count,
                    AVG(quality_score) AS avg_score
                FROM calls
                WHERE {where_clause} AND scenario IS NOT NULL
                GROUP BY scenario
                ORDER BY avg_score
            """),
            params,
        )
        by_scenario = [dict(row._mapping) for row in scenario_result]

    return {
        "summary": summary,
        "by_scenario": by_scenario,
        "filters": {"date_from": date_from, "date_to": date_to, "scenario": scenario},
    }


@router.get("/calls")
async def get_calls_list(
    quality_below: float | None = Query(None, description="Filter calls with quality below"),
    scenario: str | None = Query(None, description="Filter by scenario"),
    transferred: bool | None = Query(None, description="Filter transferred calls"),
    date_from: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: str | None = Query(None, description="End date (YYYY-MM-DD)"),
    search: str | None = Query(None, description="Full-text search in transcriptions"),
    sort_by: str | None = Query(None, description="Sort by: date, quality, cost"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: dict[str, Any] = _analyst_dep,
) -> dict[str, Any]:
    """List calls with filters for quality, scenario, transfer status."""
    engine = await _get_engine()

    conditions = ["1=1"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    if quality_below is not None:
        conditions.append("quality_score < :quality_below")
        params["quality_below"] = quality_below
    if scenario:
        conditions.append("scenario = :scenario")
        params["scenario"] = scenario
    if transferred is not None:
        conditions.append("transferred_to_operator = :transferred")
        params["transferred"] = transferred
    if date_from:
        conditions.append("started_at >= :date_from")
        params["date_from"] = date_from
    if date_to:
        conditions.append("started_at < :date_to::date + interval '1 day'")
        params["date_to"] = date_to
    if search:
        conditions.append("id IN (SELECT call_id FROM call_turns WHERE text ILIKE :search)")
        params["search"] = f"%{search}%"

    where_clause = " AND ".join(conditions)

    # Sort order
    order_map = {
        "quality": "quality_score ASC NULLS LAST",
        "cost": "total_cost_usd DESC NULLS LAST",
    }
    order_by = order_map.get(sort_by or "", "started_at DESC")

    async with engine.begin() as conn:
        # Get total count
        count_result = await conn.execute(
            text(f"SELECT COUNT(*) AS total FROM calls WHERE {where_clause}"),
            params,
        )
        total = count_result.scalar()

        # Get paginated results
        result = await conn.execute(
            text(f"""
                SELECT
                    id, caller_id, started_at, ended_at, duration_seconds,
                    scenario, transferred_to_operator, transfer_reason,
                    quality_score, total_cost_usd
                FROM calls
                WHERE {where_clause}
                ORDER BY {order_by}
                LIMIT :limit OFFSET :offset
            """),
            params,
        )
        calls = [dict(row._mapping) for row in result]

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "calls": calls,
    }


@router.get("/calls/{call_id}")
async def get_call_details(call_id: UUID, _: dict[str, Any] = _analyst_dep) -> dict[str, Any]:
    """Full call details with transcription, tool calls, and quality breakdown."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        # Call metadata
        call_result = await conn.execute(
            text("""
                SELECT
                    id, caller_id, customer_id, started_at, ended_at,
                    duration_seconds, scenario, transferred_to_operator,
                    transfer_reason, order_id, fitting_booking_id,
                    quality_score, quality_details, cost_breakdown,
                    total_cost_usd, prompt_version
                FROM calls
                WHERE id = :call_id
            """),
            {"call_id": str(call_id)},
        )
        call = call_result.first()
        if not call:
            raise HTTPException(status_code=404, detail="Call not found")

        call_data = dict(call._mapping)

        # Turns (transcription)
        turns_result = await conn.execute(
            text("""
                SELECT
                    turn_index, speaker, text, stt_confidence,
                    stt_latency_ms, llm_latency_ms, tts_latency_ms,
                    created_at
                FROM call_turns
                WHERE call_id = :call_id
                ORDER BY turn_index
            """),
            {"call_id": str(call_id)},
        )
        turns = [dict(row._mapping) for row in turns_result]

        # Tool calls
        tools_result = await conn.execute(
            text("""
                SELECT
                    tool_name, tool_args, tool_result, success,
                    duration_ms, called_at
                FROM call_tool_calls
                WHERE call_id = :call_id
                ORDER BY called_at
            """),
            {"call_id": str(call_id)},
        )
        tool_calls = [dict(row._mapping) for row in tools_result]

    return {
        "call": call_data,
        "turns": turns,
        "tool_calls": tool_calls,
    }


@router.get("/summary")
async def get_summary(
    date_from: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: str | None = Query(None, description="End date (YYYY-MM-DD)"),
    _: dict[str, Any] = _analyst_dep,
) -> dict[str, Any]:
    """Aggregated daily statistics from daily_stats table."""
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
                SELECT *
                FROM daily_stats
                WHERE {where_clause}
                ORDER BY stat_date DESC
                LIMIT 90
            """),
            params,
        )
        stats = [dict(row._mapping) for row in result]

    return {"daily_stats": stats}
