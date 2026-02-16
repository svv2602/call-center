"""Training tool description overrides API endpoints.

Manage DB overrides for tool descriptions used by the LLM agent.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.agent.tools import ALL_TOOLS
from src.api.auth import require_role
from src.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/training/tools", tags=["training"])

_engine: AsyncEngine | None = None

_admin_dep = Depends(require_role("admin"))
_analyst_dep = Depends(require_role("admin", "analyst"))

_TOOL_NAMES = [t["name"] for t in ALL_TOOLS]


async def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database.url)
    return _engine


class ToolOverrideRequest(BaseModel):
    description: str | None = None
    input_schema_override: dict[str, Any] | None = None
    is_active: bool = True


@router.get("/")
async def list_tools(_: dict[str, Any] = _analyst_dep) -> dict[str, Any]:
    """List all tools, merging code defaults with DB overrides."""
    engine = await _get_engine()

    # Load DB overrides
    overrides: dict[str, dict[str, Any]] = {}
    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    SELECT tool_name, description, input_schema_override, is_active,
                           created_at, updated_at
                    FROM tool_description_overrides
                """)
            )
            for row in result:
                overrides[row.tool_name] = dict(row._mapping)
    except Exception:
        logger.warning("Could not load tool overrides from DB, using code defaults only")

    # Merge
    items = []
    for tool in ALL_TOOLS:
        entry: dict[str, Any] = {
            "name": tool["name"],
            "description": tool["description"],
            "input_schema": tool["input_schema"],
            "has_override": False,
        }
        if tool["name"] in overrides:
            override = overrides[tool["name"]]
            entry["has_override"] = True
            entry["override"] = override
            if override.get("description") and override.get("is_active"):
                entry["effective_description"] = override["description"]
            else:
                entry["effective_description"] = tool["description"]
        else:
            entry["effective_description"] = tool["description"]
        items.append(entry)

    return {"items": items, "tool_names": _TOOL_NAMES}


@router.patch("/{tool_name}")
async def update_tool_override(tool_name: str, request: ToolOverrideRequest, _: dict[str, Any] = _admin_dep) -> dict[str, Any]:
    """Create or update a tool description override."""
    if tool_name not in _TOOL_NAMES:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found. Valid tools: {_TOOL_NAMES}")

    import json

    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                INSERT INTO tool_description_overrides (tool_name, description, input_schema_override, is_active)
                VALUES (:tool_name, :description, :input_schema_override, :is_active)
                ON CONFLICT (tool_name) DO UPDATE SET
                    description = EXCLUDED.description,
                    input_schema_override = EXCLUDED.input_schema_override,
                    is_active = EXCLUDED.is_active,
                    updated_at = now()
                RETURNING id, tool_name, description, is_active, updated_at
            """),
            {
                "tool_name": tool_name,
                "description": request.description,
                "input_schema_override": json.dumps(request.input_schema_override) if request.input_schema_override else None,
                "is_active": request.is_active,
            },
        )
        row = result.first()
        if row is None:
            msg = "Expected row from INSERT RETURNING"
            raise RuntimeError(msg)

    return {"item": dict(row._mapping), "message": f"Tool override for '{tool_name}' saved"}


@router.delete("/{tool_name}")
async def delete_tool_override(tool_name: str, _: dict[str, Any] = _admin_dep) -> dict[str, Any]:
    """Remove a tool description override (reset to code default)."""
    if tool_name not in _TOOL_NAMES:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found. Valid tools: {_TOOL_NAMES}")

    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                DELETE FROM tool_description_overrides
                WHERE tool_name = :tool_name
                RETURNING id, tool_name
            """),
            {"tool_name": tool_name},
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail=f"No override found for tool '{tool_name}'")

    return {"message": f"Override for '{tool_name}' removed, using code default"}
