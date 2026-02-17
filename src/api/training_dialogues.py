"""Training dialogue examples CRUD API endpoints.

Manage example conversations for evaluation and few-shot prompting.
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
router = APIRouter(prefix="/training/dialogues", tags=["training"])

_engine: AsyncEngine | None = None

_admin_dep = Depends(require_role("admin"))
_analyst_dep = Depends(require_role("admin", "analyst"))

SCENARIO_TYPES = [
    "tire_search",
    "availability_check",
    "order_creation",
    "order_status",
    "fitting_booking",
    "expert_consultation",
    "operator_transfer",
]
PHASES = ["mvp", "orders", "services"]


async def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database.url)
    return _engine


class DialogueCreateRequest(BaseModel):
    title: str
    scenario_type: str
    phase: str
    dialogue: list[dict[str, Any]]
    tools_used: list[str] | None = None
    description: str | None = None
    sort_order: int = 0


class DialogueUpdateRequest(BaseModel):
    title: str | None = None
    scenario_type: str | None = None
    phase: str | None = None
    dialogue: list[dict[str, Any]] | None = None
    tools_used: list[str] | None = None
    description: str | None = None
    is_active: bool | None = None
    sort_order: int | None = None


@router.get("/")
async def list_dialogues(
    scenario_type: str | None = Query(None),
    phase: str | None = Query(None),
    is_active: bool | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: dict[str, Any] = _analyst_dep,
) -> dict[str, Any]:
    """List dialogue examples with filters."""
    engine = await _get_engine()

    conditions = ["1=1"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    if scenario_type:
        conditions.append("scenario_type = :scenario_type")
        params["scenario_type"] = scenario_type
    if phase:
        conditions.append("phase = :phase")
        params["phase"] = phase
    if is_active is not None:
        conditions.append("is_active = :is_active")
        params["is_active"] = is_active

    where_clause = " AND ".join(conditions)

    async with engine.begin() as conn:
        count_result = await conn.execute(
            text(f"SELECT COUNT(*) FROM dialogue_examples WHERE {where_clause}"),
            params,
        )
        total = count_result.scalar()

        result = await conn.execute(
            text(f"""
                SELECT id, title, scenario_type, phase, tools_used, description,
                       is_active, sort_order, created_at, updated_at
                FROM dialogue_examples
                WHERE {where_clause}
                ORDER BY sort_order, created_at DESC
                LIMIT :limit OFFSET :offset
            """),
            params,
        )
        items = [dict(row._mapping) for row in result]

    return {"total": total, "items": items}


@router.get("/{dialogue_id}")
async def get_dialogue(dialogue_id: UUID, _: dict[str, Any] = _analyst_dep) -> dict[str, Any]:
    """Get a specific dialogue example with full content."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT id, title, scenario_type, phase, dialogue, tools_used,
                       description, is_active, sort_order, created_at, updated_at
                FROM dialogue_examples
                WHERE id = :id
            """),
            {"id": str(dialogue_id)},
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Dialogue example not found")

    return {"item": dict(row._mapping)}


@router.post("/")
async def create_dialogue(
    request: DialogueCreateRequest, _: dict[str, Any] = _admin_dep
) -> dict[str, Any]:
    """Create a new dialogue example."""
    if request.scenario_type not in SCENARIO_TYPES:
        raise HTTPException(
            status_code=400, detail=f"Invalid scenario_type. Must be one of: {SCENARIO_TYPES}"
        )
    if request.phase not in PHASES:
        raise HTTPException(status_code=400, detail=f"Invalid phase. Must be one of: {PHASES}")

    import json

    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                INSERT INTO dialogue_examples (title, scenario_type, phase, dialogue, tools_used, description, sort_order)
                VALUES (:title, :scenario_type, :phase, :dialogue, :tools_used, :description, :sort_order)
                RETURNING id, title, scenario_type, phase, is_active, sort_order, created_at
            """),
            {
                "title": request.title,
                "scenario_type": request.scenario_type,
                "phase": request.phase,
                "dialogue": json.dumps(request.dialogue),
                "tools_used": request.tools_used,
                "description": request.description,
                "sort_order": request.sort_order,
            },
        )
        row = result.first()
        if row is None:
            msg = "Expected row from INSERT RETURNING"
            raise RuntimeError(msg)

    return {"item": dict(row._mapping), "message": "Dialogue example created"}


@router.patch("/{dialogue_id}")
async def update_dialogue(
    dialogue_id: UUID, request: DialogueUpdateRequest, _: dict[str, Any] = _admin_dep
) -> dict[str, Any]:
    """Update a dialogue example."""
    import json

    if request.scenario_type is not None and request.scenario_type not in SCENARIO_TYPES:
        raise HTTPException(
            status_code=400, detail=f"Invalid scenario_type. Must be one of: {SCENARIO_TYPES}"
        )
    if request.phase is not None and request.phase not in PHASES:
        raise HTTPException(status_code=400, detail=f"Invalid phase. Must be one of: {PHASES}")

    engine = await _get_engine()

    updates: list[str] = []
    params: dict[str, Any] = {"id": str(dialogue_id)}

    if request.title is not None:
        updates.append("title = :title")
        params["title"] = request.title
    if request.scenario_type is not None:
        updates.append("scenario_type = :scenario_type")
        params["scenario_type"] = request.scenario_type
    if request.phase is not None:
        updates.append("phase = :phase")
        params["phase"] = request.phase
    if request.dialogue is not None:
        updates.append("dialogue = :dialogue")
        params["dialogue"] = json.dumps(request.dialogue)
    if request.tools_used is not None:
        updates.append("tools_used = :tools_used")
        params["tools_used"] = request.tools_used
    if request.description is not None:
        updates.append("description = :description")
        params["description"] = request.description
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
                UPDATE dialogue_examples
                SET {set_clause}
                WHERE id = :id
                RETURNING id, title, scenario_type, phase, is_active, sort_order, updated_at
            """),
            params,
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Dialogue example not found")

    return {"item": dict(row._mapping), "message": "Dialogue example updated"}


@router.delete("/{dialogue_id}")
async def delete_dialogue(dialogue_id: UUID, _: dict[str, Any] = _admin_dep) -> dict[str, Any]:
    """Soft delete a dialogue example."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                UPDATE dialogue_examples
                SET is_active = false, updated_at = now()
                WHERE id = :id
                RETURNING id, title
            """),
            {"id": str(dialogue_id)},
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Dialogue example not found")

    return {"message": f"Dialogue example '{row.title}' deactivated"}


@router.post("/import")
async def import_dialogues(
    items: list[DialogueCreateRequest], _: dict[str, Any] = _admin_dep
) -> dict[str, Any]:
    """Bulk import dialogue examples from a JSON array."""
    import json

    engine = await _get_engine()
    imported = 0
    errors: list[dict[str, str]] = []

    for item in items:
        if item.scenario_type not in SCENARIO_TYPES:
            errors.append(
                {"title": item.title, "error": f"Invalid scenario_type: {item.scenario_type}"}
            )
            continue
        if item.phase not in PHASES:
            errors.append({"title": item.title, "error": f"Invalid phase: {item.phase}"})
            continue

        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text("""
                        INSERT INTO dialogue_examples (title, scenario_type, phase, dialogue, tools_used, description, sort_order)
                        VALUES (:title, :scenario_type, :phase, :dialogue, :tools_used, :description, :sort_order)
                    """),
                    {
                        "title": item.title,
                        "scenario_type": item.scenario_type,
                        "phase": item.phase,
                        "dialogue": json.dumps(item.dialogue),
                        "tools_used": item.tools_used,
                        "description": item.description,
                        "sort_order": item.sort_order,
                    },
                )
            imported += 1
        except Exception as exc:
            errors.append({"title": item.title, "error": str(exc)})

    return {"imported": imported, "errors": len(errors), "error_details": errors}
