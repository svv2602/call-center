"""Training safety rules CRUD API endpoints.

Manage adversarial test cases and behavioral boundaries for the AI agent.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID  # noqa: TC003 - FastAPI needs UUID at runtime for path params

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.api.auth import require_role
from src.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/training/safety-rules", tags=["training"])

_engine: AsyncEngine | None = None

_admin_dep = Depends(require_role("admin"))
_analyst_dep = Depends(require_role("admin", "analyst"))

RULE_TYPES = [
    "prompt_injection",
    "data_validation",
    "off_topic",
    "language",
    "behavioral",
    "escalation",
]
SEVERITIES = ["low", "medium", "high", "critical"]


async def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database.url)
    return _engine


class SafetyRuleCreateRequest(BaseModel):
    title: str
    rule_type: str
    trigger_input: str
    expected_behavior: str
    severity: str = "medium"
    sort_order: int = 0


class SafetyRuleUpdateRequest(BaseModel):
    title: str | None = None
    rule_type: str | None = None
    trigger_input: str | None = None
    expected_behavior: str | None = None
    severity: str | None = None
    is_active: bool | None = None
    sort_order: int | None = None


@router.get("/")
async def list_safety_rules(
    rule_type: str | None = Query(None),
    severity: str | None = Query(None),
    is_active: bool | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: dict[str, Any] = _analyst_dep,
) -> dict[str, Any]:
    """List safety rules with filters."""
    engine = await _get_engine()

    conditions = ["1=1"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    if rule_type:
        conditions.append("rule_type = :rule_type")
        params["rule_type"] = rule_type
    if severity:
        conditions.append("severity = :severity")
        params["severity"] = severity
    if is_active is not None:
        conditions.append("is_active = :is_active")
        params["is_active"] = is_active

    where_clause = " AND ".join(conditions)

    async with engine.begin() as conn:
        count_result = await conn.execute(
            text(f"SELECT COUNT(*) FROM safety_rules WHERE {where_clause}"),
            params,
        )
        total = count_result.scalar()

        result = await conn.execute(
            text(f"""
                SELECT id, title, rule_type, trigger_input, expected_behavior,
                       severity, is_active, sort_order, created_at, updated_at
                FROM safety_rules
                WHERE {where_clause}
                ORDER BY sort_order, severity DESC, created_at DESC
                LIMIT :limit OFFSET :offset
            """),
            params,
        )
        items = [dict(row._mapping) for row in result]

    return {"total": total, "items": items}


@router.get("/{rule_id}")
async def get_safety_rule(rule_id: UUID, _: dict[str, Any] = _analyst_dep) -> dict[str, Any]:
    """Get a specific safety rule."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT id, title, rule_type, trigger_input, expected_behavior,
                       severity, is_active, sort_order, created_at, updated_at
                FROM safety_rules
                WHERE id = :id
            """),
            {"id": str(rule_id)},
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Safety rule not found")

    return {"item": dict(row._mapping)}


@router.post("/")
async def create_safety_rule(
    request: SafetyRuleCreateRequest, _: dict[str, Any] = _admin_dep
) -> dict[str, Any]:
    """Create a new safety rule."""
    if request.rule_type not in RULE_TYPES:
        raise HTTPException(
            status_code=400, detail=f"Invalid rule_type. Must be one of: {RULE_TYPES}"
        )
    if request.severity not in SEVERITIES:
        raise HTTPException(
            status_code=400, detail=f"Invalid severity. Must be one of: {SEVERITIES}"
        )

    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                INSERT INTO safety_rules (title, rule_type, trigger_input, expected_behavior, severity, sort_order)
                VALUES (:title, :rule_type, :trigger_input, :expected_behavior, :severity, :sort_order)
                RETURNING id, title, rule_type, severity, is_active, sort_order, created_at
            """),
            {
                "title": request.title,
                "rule_type": request.rule_type,
                "trigger_input": request.trigger_input,
                "expected_behavior": request.expected_behavior,
                "severity": request.severity,
                "sort_order": request.sort_order,
            },
        )
        row = result.first()
        if row is None:
            msg = "Expected row from INSERT RETURNING"
            raise RuntimeError(msg)

    return {"item": dict(row._mapping), "message": "Safety rule created"}


@router.patch("/{rule_id}")
async def update_safety_rule(
    rule_id: UUID, request: SafetyRuleUpdateRequest, _: dict[str, Any] = _admin_dep
) -> dict[str, Any]:
    """Update a safety rule."""
    if request.rule_type is not None and request.rule_type not in RULE_TYPES:
        raise HTTPException(
            status_code=400, detail=f"Invalid rule_type. Must be one of: {RULE_TYPES}"
        )
    if request.severity is not None and request.severity not in SEVERITIES:
        raise HTTPException(
            status_code=400, detail=f"Invalid severity. Must be one of: {SEVERITIES}"
        )

    engine = await _get_engine()

    updates: list[str] = []
    params: dict[str, Any] = {"id": str(rule_id)}

    if request.title is not None:
        updates.append("title = :title")
        params["title"] = request.title
    if request.rule_type is not None:
        updates.append("rule_type = :rule_type")
        params["rule_type"] = request.rule_type
    if request.trigger_input is not None:
        updates.append("trigger_input = :trigger_input")
        params["trigger_input"] = request.trigger_input
    if request.expected_behavior is not None:
        updates.append("expected_behavior = :expected_behavior")
        params["expected_behavior"] = request.expected_behavior
    if request.severity is not None:
        updates.append("severity = :severity")
        params["severity"] = request.severity
    if request.is_active is not None:
        updates.append("is_active = :is_active")
        params["is_active"] = request.is_active
    if request.sort_order is not None:
        updates.append("sort_order = :sort_order")
        params["sort_order"] = request.sort_order

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates.append("updated_at = now()")
    set_clause = ", ".join(updates)

    async with engine.begin() as conn:
        result = await conn.execute(
            text(f"""
                UPDATE safety_rules
                SET {set_clause}
                WHERE id = :id
                RETURNING id, title, rule_type, severity, is_active, sort_order, updated_at
            """),
            params,
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Safety rule not found")

    return {"item": dict(row._mapping), "message": "Safety rule updated"}


@router.delete("/{rule_id}")
async def delete_safety_rule(rule_id: UUID, _: dict[str, Any] = _admin_dep) -> dict[str, Any]:
    """Soft delete a safety rule."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                UPDATE safety_rules
                SET is_active = false, updated_at = now()
                WHERE id = :id
                RETURNING id, title
            """),
            {"id": str(rule_id)},
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Safety rule not found")

    return {"message": f"Safety rule '{row.title}' deactivated"}
