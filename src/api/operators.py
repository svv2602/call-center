"""Operator management API endpoints.

CRUD for operators, queue monitoring, transfer history, and operator stats.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.api.auth import require_role
from src.config import get_settings
from src.events.publisher import publish_event

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/operators", tags=["operators"])

_engine: AsyncEngine | None = None

# Module-level dependencies to satisfy B008 lint rule
_admin_dep = Depends(require_role("admin"))
_admin_or_operator_dep = Depends(require_role("admin", "operator"))


async def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database.url, pool_pre_ping=True)
    return _engine


# --- Request models ---
_TIME_PATTERN = r"^\d{2}:\d{2}$"


class CreateOperatorRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    extension: str = Field(min_length=1, max_length=20)
    skills: list[str] = []
    shift_start: str = Field(default="09:00", pattern=_TIME_PATTERN)
    shift_end: str = Field(default="18:00", pattern=_TIME_PATTERN)


class UpdateOperatorRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    extension: str | None = Field(default=None, min_length=1, max_length=20)
    is_active: bool | None = None
    skills: list[str] | None = None
    shift_start: str | None = Field(default=None, pattern=_TIME_PATTERN)
    shift_end: str | None = Field(default=None, pattern=_TIME_PATTERN)


OperatorStatus = Literal["online", "offline", "busy", "break"]


class StatusChangeRequest(BaseModel):
    status: OperatorStatus


# --- CRUD ---


@router.get("")
async def list_operators(
    _: dict[str, Any] = _admin_dep,
) -> dict[str, Any]:
    """List all operators with their current status."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT o.id, o.name, o.extension, o.is_active, o.skills,
                       o.shift_start, o.shift_end, o.created_at, o.updated_at,
                       (SELECT status FROM operator_status_log
                        WHERE operator_id = o.id
                        ORDER BY changed_at DESC LIMIT 1) AS current_status
                FROM operators o
                ORDER BY o.name
            """)
        )
        operators = [dict(row._mapping) for row in result]

    return {"operators": operators}


@router.post("")
async def create_operator(
    req: CreateOperatorRequest,
    _: dict[str, Any] = _admin_dep,
) -> dict[str, Any]:
    """Create a new operator."""
    engine = await _get_engine()
    import json

    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    INSERT INTO operators (name, extension, skills, shift_start, shift_end)
                    VALUES (:name, :extension, CAST(:skills AS jsonb), CAST(:shift_start AS time), CAST(:shift_end AS time))
                    RETURNING id, name, extension, is_active, skills, shift_start, shift_end, created_at
                """),
                {
                    "name": req.name,
                    "extension": req.extension,
                    "skills": json.dumps(req.skills),
                    "shift_start": req.shift_start,
                    "shift_end": req.shift_end,
                },
            )
            row = result.first()
            if row is None:
                msg = "Expected row from INSERT RETURNING"
                raise RuntimeError(msg)
            operator = dict(row._mapping)

            # Create initial status log entry
            await conn.execute(
                text("""
                    INSERT INTO operator_status_log (operator_id, status)
                    VALUES (:op_id, 'offline')
                """),
                {"op_id": str(operator["id"])},
            )
    except Exception as e:
        if "unique" in str(e).lower():
            raise HTTPException(status_code=409, detail="Extension already exists") from e
        raise

    return {"operator": operator}


@router.patch("/{operator_id}")
async def update_operator(
    operator_id: str,
    req: UpdateOperatorRequest,
    _: dict[str, Any] = _admin_dep,
) -> dict[str, Any]:
    """Update operator details."""
    engine = await _get_engine()
    updates: list[str] = []
    params: dict[str, Any] = {"op_id": operator_id}

    if req.name is not None:
        updates.append("name = :name")
        params["name"] = req.name
    if req.extension is not None:
        updates.append("extension = :extension")
        params["extension"] = req.extension
    if req.is_active is not None:
        updates.append("is_active = :is_active")
        params["is_active"] = req.is_active
    if req.skills is not None:
        import json

        updates.append("skills = CAST(:skills AS jsonb)")
        params["skills"] = json.dumps(req.skills)
    if req.shift_start is not None:
        updates.append("shift_start = CAST(:shift_start AS time)")
        params["shift_start"] = req.shift_start
    if req.shift_end is not None:
        updates.append("shift_end = CAST(:shift_end AS time)")
        params["shift_end"] = req.shift_end

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates.append("updated_at = now()")
    set_clause = ", ".join(updates)

    async with engine.begin() as conn:
        result = await conn.execute(
            text(f"""
                UPDATE operators
                SET {set_clause}
                WHERE id = :op_id
                RETURNING id, name, extension, is_active, skills
            """),
            params,
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Operator not found")

    return {"operator": dict(row._mapping)}


@router.delete("/{operator_id}")
async def deactivate_operator(
    operator_id: str,
    _: dict[str, Any] = _admin_dep,
) -> dict[str, Any]:
    """Deactivate an operator (soft delete)."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                UPDATE operators
                SET is_active = false, updated_at = now()
                WHERE id = :op_id
                RETURNING id, name
            """),
            {"op_id": operator_id},
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Operator not found")

    return {"message": f"Operator '{row.name}' deactivated"}


@router.patch("/{operator_id}/status")
async def change_operator_status(
    operator_id: str,
    req: StatusChangeRequest,
    _: dict[str, Any] = _admin_or_operator_dep,
) -> dict[str, Any]:
    """Change operator status (online/offline/busy/break)."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        # Verify operator exists
        check = await conn.execute(
            text("SELECT id FROM operators WHERE id = :op_id"),
            {"op_id": operator_id},
        )
        if not check.first():
            raise HTTPException(status_code=404, detail="Operator not found")

        await conn.execute(
            text("""
                INSERT INTO operator_status_log (operator_id, status)
                VALUES (:op_id, :status)
            """),
            {"op_id": operator_id, "status": req.status},
        )

    await publish_event(
        "operator:status_changed",
        {
            "operator_id": operator_id,
            "status": req.status,
        },
    )

    return {"status": req.status, "operator_id": operator_id}


# --- Queue monitoring ---


@router.get("/queue")
async def get_queue_status(
    _: dict[str, Any] = _admin_dep,
) -> dict[str, Any]:
    """Current operator queue status."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        # Online operators count
        online_result = await conn.execute(
            text("""
                SELECT COUNT(DISTINCT o.id) AS online_count
                FROM operators o
                JOIN operator_status_log osl ON osl.operator_id = o.id
                WHERE o.is_active = true
                  AND osl.status = 'online'
                  AND osl.changed_at = (
                      SELECT MAX(changed_at) FROM operator_status_log
                      WHERE operator_id = o.id
                  )
            """)
        )
        online_count = online_result.scalar() or 0

        # Transfers in last hour
        transfers_result = await conn.execute(
            text("""
                SELECT COUNT(*) AS transfers_last_hour
                FROM calls
                WHERE transferred_to_operator = true
                  AND started_at >= now() - interval '1 hour'
            """)
        )
        transfers_last_hour = transfers_result.scalar() or 0

    return {
        "operators_online": online_count,
        "transfers_last_hour": transfers_last_hour,
    }


@router.get("/transfers")
async def get_transfers(
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    reason: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: dict[str, Any] = _admin_dep,
) -> dict[str, Any]:
    """Transfer history with filters."""
    engine = await _get_engine()

    conditions = ["transferred_to_operator = true"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    if date_from:
        conditions.append("started_at >= :date_from")
        params["date_from"] = date_from
    if date_to:
        conditions.append("started_at < CAST(:date_to AS date) + interval '1 day'")
        params["date_to"] = date_to
    if reason:
        conditions.append("transfer_reason = :reason")
        params["reason"] = reason

    where_clause = " AND ".join(conditions)

    async with engine.begin() as conn:
        count_result = await conn.execute(
            text(f"SELECT COUNT(*) FROM calls WHERE {where_clause}"),
            params,
        )
        total = count_result.scalar()

        result = await conn.execute(
            text(f"""
                SELECT id, caller_id, started_at, duration_seconds,
                       transfer_reason, quality_score
                FROM calls
                WHERE {where_clause}
                ORDER BY started_at DESC
                LIMIT :limit OFFSET :offset
            """),
            params,
        )
        transfers = [dict(row._mapping) for row in result]

    return {"total": total, "transfers": transfers}


# --- Operator stats ---


@router.get("/{operator_id}/stats")
async def get_operator_stats(
    operator_id: str,
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    _: dict[str, Any] = _admin_dep,
) -> dict[str, Any]:
    """Statistics for a specific operator."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        # Verify operator exists
        op_check = await conn.execute(
            text("SELECT id, name FROM operators WHERE id = :op_id"),
            {"op_id": operator_id},
        )
        op_row = op_check.first()
        if not op_row:
            raise HTTPException(status_code=404, detail="Operator not found")

        # Status history
        status_result = await conn.execute(
            text("""
                SELECT status, changed_at
                FROM operator_status_log
                WHERE operator_id = :op_id
                ORDER BY changed_at DESC
                LIMIT 20
            """),
            {"op_id": operator_id},
        )
        status_history = [dict(row._mapping) for row in status_result]

    return {
        "operator_id": operator_id,
        "name": op_row.name,
        "status_history": status_history,
    }
