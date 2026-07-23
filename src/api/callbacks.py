"""Admin API for after-hours callback requests.

The bot creates callback_requests via the `create_callback_request` tool
when a customer reaches the call center outside working hours. Operators
list them here, mark them as done, and add a note about the outcome.
"""

from __future__ import annotations

import logging
from typing import Any, Literal
from uuid import UUID  # noqa: TC003 - FastAPI needs UUID at runtime for path params

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from src.api.auth import require_permission
from src.api.database import get_engine as _get_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/callbacks", tags=["callbacks"])

# Module-level dependencies to satisfy B008 lint rule
_perm_r = Depends(require_permission("operators:read"))
_perm_w = Depends(require_permission("operators:write"))


CallbackStatus = Literal["pending", "in_progress", "done", "cancelled"]

# Module-level Query defaults (B008 workaround)
_q_status = Query(None)
_q_tenant = Query(None)
_q_limit = Query(100, ge=1, le=500)
_q_offset = Query(0, ge=0)


class CallbackUpdate(BaseModel):
    status: CallbackStatus | None = None
    operator_id: UUID | None = None
    note_result: str | None = Field(default=None, max_length=2000)


@router.get("")
async def list_callbacks(
    status: CallbackStatus | None = _q_status,
    tenant_id: UUID | None = _q_tenant,
    limit: int = _q_limit,
    offset: int = _q_offset,
    _: dict[str, Any] = _perm_r,
) -> dict[str, Any]:
    """List callback requests, newest first."""
    engine = await _get_engine()

    conditions: list[str] = ["1=1"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if status is not None:
        conditions.append("cr.status = :status")
        params["status"] = status
    if tenant_id is not None:
        conditions.append("cr.tenant_id = :tenant_id")
        params["tenant_id"] = str(tenant_id)
    where_clause = " AND ".join(conditions)

    async with engine.begin() as conn:
        count_row = await conn.execute(
            text(f"SELECT COUNT(*) FROM callback_requests cr WHERE {where_clause}"),
            params,
        )
        total = count_row.scalar() or 0

        result = await conn.execute(
            text(f"""
                SELECT cr.id, cr.tenant_id, cr.call_id, cr.phone, cr.preferred_time,
                       cr.note, cr.reason, cr.status, cr.operator_id, cr.created_at,
                       cr.called_back_at, cr.note_result,
                       t.slug AS tenant_slug, t.name AS tenant_name,
                       o.name AS operator_name
                FROM callback_requests cr
                LEFT JOIN tenants t ON t.id = cr.tenant_id
                LEFT JOIN operators o ON o.id = cr.operator_id
                WHERE {where_clause}
                ORDER BY cr.created_at DESC
                LIMIT :limit OFFSET :offset
            """),
            params,
        )
        callbacks = [dict(row._mapping) for row in result]

    return {"callbacks": callbacks, "total": total}


@router.patch("/{callback_id}")
async def update_callback(
    callback_id: UUID,
    request: CallbackUpdate,
    _: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Update a callback status / operator assignment / note.

    Setting status='done' auto-stamps `called_back_at`.
    """
    engine = await _get_engine()

    updates: list[str] = []
    params: dict[str, Any] = {"id": str(callback_id)}

    if request.status is not None:
        updates.append("status = :status")
        params["status"] = request.status
        if request.status == "done":
            updates.append("called_back_at = COALESCE(called_back_at, now())")
    if request.operator_id is not None:
        updates.append("operator_id = :operator_id")
        params["operator_id"] = str(request.operator_id)
    if "note_result" in request.model_fields_set:
        updates.append("note_result = :note_result")
        params["note_result"] = request.note_result

    if not updates:
        return {"message": "No changes"}

    set_clause = ", ".join(updates)

    async with engine.begin() as conn:
        result = await conn.execute(
            text(f"""
                UPDATE callback_requests
                SET {set_clause}
                WHERE id = :id
                RETURNING id, status, called_back_at
            """),
            params,
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Callback not found")

    return {"callback": dict(row._mapping)}
