"""Training response templates CRUD API endpoints.

Manage pre-defined agent responses (greeting, farewell, etc.).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.api.auth import require_role
from src.config import get_settings

if TYPE_CHECKING:
    from uuid import UUID

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
        _engine = create_async_engine(settings.database.url)
    return _engine


class TemplateCreateRequest(BaseModel):
    template_key: str
    title: str
    content: str
    description: str | None = None


class TemplateUpdateRequest(BaseModel):
    title: str | None = None
    content: str | None = None
    description: str | None = None
    is_active: bool | None = None


@router.get("/")
async def list_templates(_: dict[str, Any] = _analyst_dep) -> dict[str, Any]:
    """List all response templates."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT id, template_key, title, content, description,
                       is_active, created_at, updated_at
                FROM response_templates
                ORDER BY template_key
            """)
        )
        items = [dict(row._mapping) for row in result]

    return {"items": items, "valid_keys": TEMPLATE_KEYS}


@router.get("/{template_id}")
async def get_template(template_id: UUID, _: dict[str, Any] = _analyst_dep) -> dict[str, Any]:
    """Get a specific response template."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT id, template_key, title, content, description,
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
    """Create a new response template."""
    if request.template_key not in TEMPLATE_KEYS:
        raise HTTPException(
            status_code=400, detail=f"Invalid template_key. Must be one of: {TEMPLATE_KEYS}"
        )

    engine = await _get_engine()

    async with engine.begin() as conn:
        # Check uniqueness
        existing = await conn.execute(
            text("SELECT id FROM response_templates WHERE template_key = :key"),
            {"key": request.template_key},
        )
        if existing.first():
            raise HTTPException(
                status_code=409, detail=f"Template with key '{request.template_key}' already exists"
            )

        result = await conn.execute(
            text("""
                INSERT INTO response_templates (template_key, title, content, description)
                VALUES (:template_key, :title, :content, :description)
                RETURNING id, template_key, title, is_active, created_at
            """),
            {
                "template_key": request.template_key,
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
                RETURNING id, template_key, title, content, is_active, updated_at
            """),
            params,
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Response template not found")

    return {"item": dict(row._mapping), "message": "Response template updated"}
