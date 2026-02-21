"""Training response templates CRUD API endpoints.

Manage pre-defined agent responses (greeting, farewell, etc.).
Supports multiple variants per template_key for randomized responses.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID  # noqa: TC003 - FastAPI needs UUID at runtime for path params

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.api.auth import require_role
from src.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/training/templates", tags=["training"])

_engine: AsyncEngine | None = None

_admin_dep = Depends(require_role("admin"))
_analyst_dep = Depends(require_role("admin", "analyst"))

TEMPLATE_KEYS = [
    "greeting",
    "farewell",
    "silence_prompt",
    "transfer",
    "error",
    "wait",
    "order_cancelled",
]


async def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database.url, pool_pre_ping=True)
    return _engine


class TemplateCreateRequest(BaseModel):
    template_key: str = Field(min_length=1, max_length=50)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    description: str | None = Field(default=None, max_length=2000)


class TemplateUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = Field(default=None, min_length=1)
    description: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None


@router.get("/")
async def list_templates(
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: dict[str, Any] = _analyst_dep,
) -> dict[str, Any]:
    """List all response templates, ordered by key and variant."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        count_result = await conn.execute(text("SELECT COUNT(*) FROM response_templates"))
        total = count_result.scalar() or 0

        result = await conn.execute(
            text("""
                SELECT id, template_key, variant_number, title, content, description,
                       is_active, created_at, updated_at
                FROM response_templates
                ORDER BY template_key, variant_number
                LIMIT :limit OFFSET :offset
            """),
            {"limit": limit, "offset": offset},
        )
        items = [dict(row._mapping) for row in result]

    return {"items": items, "total": total, "valid_keys": TEMPLATE_KEYS}


@router.get("/{template_id}")
async def get_template(template_id: UUID, _: dict[str, Any] = _analyst_dep) -> dict[str, Any]:
    """Get a specific response template."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT id, template_key, variant_number, title, content, description,
                       is_active, created_at, updated_at
                FROM response_templates
                WHERE id = :id
            """),
            {"id": str(template_id)},
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Response template not found")

    return {"item": dict(row._mapping)}


@router.post("/")
async def create_template(
    request: TemplateCreateRequest, _: dict[str, Any] = _admin_dep
) -> dict[str, Any]:
    """Create a new response template variant.

    Auto-assigns next variant_number for the given template_key.
    """
    if request.template_key not in TEMPLATE_KEYS:
        raise HTTPException(
            status_code=400, detail=f"Invalid template_key. Must be one of: {TEMPLATE_KEYS}"
        )

    engine = await _get_engine()

    async with engine.begin() as conn:
        # Get next variant number
        max_result = await conn.execute(
            text("""
                SELECT COALESCE(MAX(variant_number), 0) AS max_variant
                FROM response_templates
                WHERE template_key = :key
            """),
            {"key": request.template_key},
        )
        max_row = max_result.first()
        next_variant = (max_row.max_variant if max_row else 0) + 1

        result = await conn.execute(
            text("""
                INSERT INTO response_templates
                    (template_key, variant_number, title, content, description)
                VALUES (:template_key, :variant_number, :title, :content, :description)
                RETURNING id, template_key, variant_number, title, is_active, created_at
            """),
            {
                "template_key": request.template_key,
                "variant_number": next_variant,
                "title": request.title,
                "content": request.content,
                "description": request.description,
            },
        )
        row = result.first()
        if row is None:
            msg = "Expected row from INSERT RETURNING"
            raise RuntimeError(msg)

    return {"item": dict(row._mapping), "message": "Response template created"}


@router.patch("/{template_id}")
async def update_template(
    template_id: UUID, request: TemplateUpdateRequest, _: dict[str, Any] = _admin_dep
) -> dict[str, Any]:
    """Update a response template."""
    engine = await _get_engine()

    updates: list[str] = []
    params: dict[str, Any] = {"id": str(template_id)}

    if request.title is not None:
        updates.append("title = :title")
        params["title"] = request.title
    if request.content is not None:
        updates.append("content = :content")
        params["content"] = request.content
    if request.description is not None:
        updates.append("description = :description")
        params["description"] = request.description
    if request.is_active is not None:
        updates.append("is_active = :is_active")
        params["is_active"] = request.is_active

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates.append("updated_at = now()")
    set_clause = ", ".join(updates)

    async with engine.begin() as conn:
        result = await conn.execute(
            text(f"""
                UPDATE response_templates
                SET {set_clause}
                WHERE id = :id
                RETURNING id, template_key, variant_number, title, content, is_active, updated_at
            """),
            params,
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Response template not found")

    return {"item": dict(row._mapping), "message": "Response template updated"}


@router.delete("/{template_id}")
async def delete_template(template_id: UUID, _: dict[str, Any] = _admin_dep) -> dict[str, Any]:
    """Delete a response template variant.

    Only allows deletion if there is more than one variant for the key.
    """
    engine = await _get_engine()

    async with engine.begin() as conn:
        # Get the template to find its key
        tpl_result = await conn.execute(
            text("SELECT template_key FROM response_templates WHERE id = :id"),
            {"id": str(template_id)},
        )
        tpl_row = tpl_result.first()
        if not tpl_row:
            raise HTTPException(status_code=404, detail="Response template not found")

        # Count variants for this key
        count_result = await conn.execute(
            text("""
                SELECT COUNT(*) AS cnt
                FROM response_templates
                WHERE template_key = :key
            """),
            {"key": tpl_row.template_key},
        )
        count_row = count_result.first()
        if count_row and count_row.cnt <= 1:
            raise HTTPException(
                status_code=400,
                detail="Cannot delete the last variant. Each key must have at least one template.",
            )

        await conn.execute(
            text("DELETE FROM response_templates WHERE id = :id"),
            {"id": str(template_id)},
        )

    return {"message": "Response template variant deleted"}
