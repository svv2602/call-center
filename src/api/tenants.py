"""Admin API for tenant management.

Tenants represent retail networks (Prokoleso, Tvoya Shina, Technoopttorg)
sharing the same Asterisk/AI infrastructure. Each tenant has its own
Store API config, enabled tools, greeting, and prompt customization.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from uuid import UUID  # noqa: TC003 - FastAPI needs UUID at runtime for path params

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.agent.tools import ALL_TOOLS
from src.api.auth import require_permission
from src.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/tenants", tags=["tenants"])

_engine: AsyncEngine | None = None

# Module-level dependencies to satisfy B008 lint rule
_perm_r = Depends(require_permission("tenants:read"))
_perm_w = Depends(require_permission("tenants:write"))
_perm_d = Depends(require_permission("tenants:delete"))

# Canonical tool names for validation
_VALID_TOOL_NAMES = frozenset(t["name"] for t in ALL_TOOLS)


async def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database.url, pool_pre_ping=True)
    return _engine


# ─── Pydantic models ──────────────────────────────────────


class TenantCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=50, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=200)
    network_id: str = Field(min_length=1, max_length=50)
    agent_name: str = Field(default="Олена", max_length=100)
    greeting: str | None = None
    enabled_tools: list[str] = []
    extensions: list[str] = []
    prompt_suffix: str | None = None
    config: dict[str, Any] = {}
    is_active: bool = True


class TenantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    network_id: str | None = Field(default=None, min_length=1, max_length=50)
    agent_name: str | None = Field(default=None, max_length=100)
    greeting: str | None = None
    enabled_tools: list[str] | None = None
    extensions: list[str] | None = None
    prompt_suffix: str | None = None
    config: dict[str, Any] | None = None
    is_active: bool | None = None


# ─── Validation helpers ───────────────────────────────────


def _validate_tools(tools: list[str]) -> None:
    """Raise 400 if any tool name is not in the canonical list."""
    invalid = [t for t in tools if t not in _VALID_TOOL_NAMES]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid tool names: {', '.join(invalid)}. "
            f"Valid tools: {', '.join(sorted(_VALID_TOOL_NAMES))}",
        )


_EXTENSION_RE = re.compile(r"^\d{1,10}$")


def _validate_extensions(extensions: list[str]) -> None:
    """Raise 400 if any extension is not a numeric string (1-10 digits)."""
    invalid = [e for e in extensions if not _EXTENSION_RE.match(e)]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid extensions: {', '.join(invalid)}. "
            "Extensions must be numeric strings (1-10 digits).",
        )


async def _check_extension_uniqueness(
    conn: Any, extensions: list[str], exclude_tenant_id: str | None = None
) -> None:
    """Raise 409 if any extension is already assigned to another tenant."""
    if not extensions:
        return
    params: dict[str, Any] = {"extensions": extensions}
    exclude_clause = ""
    if exclude_tenant_id:
        exclude_clause = "AND id != :exclude_id"
        params["exclude_id"] = exclude_tenant_id
    result = await conn.execute(
        text(f"""
            SELECT slug, extensions FROM tenants
            WHERE extensions && CAST(:extensions AS text[])
              AND is_active = true {exclude_clause}
        """),
        params,
    )
    conflict = result.first()
    if conflict:
        raise HTTPException(
            status_code=409,
            detail=f"Extension(s) already assigned to tenant '{conflict._mapping['slug']}'",
        )


# ─── CRUD endpoints ──────────────────────────────────────


@router.get("")
async def list_tenants(
    is_active: bool | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: dict[str, Any] = _perm_r,
) -> dict[str, Any]:
    """List all tenants with optional active filter and pagination."""
    engine = await _get_engine()

    conditions = ["1=1"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    if is_active is not None:
        conditions.append("is_active = :is_active")
        params["is_active"] = is_active

    where_clause = " AND ".join(conditions)

    async with engine.begin() as conn:
        count_result = await conn.execute(
            text(f"SELECT COUNT(*) FROM tenants WHERE {where_clause}"),
            params,
        )
        total = count_result.scalar() or 0

        result = await conn.execute(
            text(f"""
                SELECT id, slug, name, network_id, agent_name, greeting,
                       enabled_tools, extensions, prompt_suffix, config,
                       is_active, created_at, updated_at
                FROM tenants
                WHERE {where_clause}
                ORDER BY created_at
                LIMIT :limit OFFSET :offset
            """),
            params,
        )
        tenants = [dict(row._mapping) for row in result]

    return {"tenants": tenants, "total": total}


@router.get("/{tenant_id}")
async def get_tenant(tenant_id: UUID, _: dict[str, Any] = _perm_r) -> dict[str, Any]:
    """Get a single tenant by ID."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT id, slug, name, network_id, agent_name, greeting,
                       enabled_tools, extensions, prompt_suffix, config,
                       is_active, created_at, updated_at
                FROM tenants WHERE id = :id
            """),
            {"id": str(tenant_id)},
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Tenant not found")

    return {"tenant": dict(row._mapping)}


@router.post("", status_code=201)
async def create_tenant(request: TenantCreate, _: dict[str, Any] = _perm_w) -> dict[str, Any]:
    """Create a new tenant."""
    if request.enabled_tools:
        _validate_tools(request.enabled_tools)
    if request.extensions:
        _validate_extensions(request.extensions)

    engine = await _get_engine()

    try:
        async with engine.begin() as conn:
            if request.extensions:
                await _check_extension_uniqueness(conn, request.extensions)
            result = await conn.execute(
                text("""
                    INSERT INTO tenants
                        (slug, name, network_id, agent_name, greeting,
                         enabled_tools, extensions, prompt_suffix, config, is_active)
                    VALUES (:slug, :name, :network_id, :agent_name, :greeting,
                            CAST(:enabled_tools AS text[]), CAST(:extensions AS text[]),
                            :prompt_suffix, CAST(:config AS jsonb), :is_active)
                    RETURNING id
                """),
                {
                    "slug": request.slug,
                    "name": request.name,
                    "network_id": request.network_id,
                    "agent_name": request.agent_name,
                    "greeting": request.greeting,
                    "enabled_tools": request.enabled_tools,
                    "extensions": request.extensions,
                    "prompt_suffix": request.prompt_suffix,
                    "config": json.dumps(request.config),
                    "is_active": request.is_active,
                },
            )
            row = result.first()
            tenant_id = str(row.id) if row else None
    except Exception as exc:
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise HTTPException(
                status_code=409, detail=f"Tenant with slug '{request.slug}' already exists"
            ) from exc
        raise

    logger.info("Created tenant: %s (slug=%s)", tenant_id, request.slug)
    return {"message": "Tenant created", "id": tenant_id}


@router.patch("/{tenant_id}")
async def update_tenant(
    tenant_id: UUID, request: TenantUpdate, _: dict[str, Any] = _perm_w
) -> dict[str, Any]:
    """Update a tenant (partial update)."""
    if request.enabled_tools is not None:
        _validate_tools(request.enabled_tools)
    if request.extensions is not None:
        _validate_extensions(request.extensions)

    engine = await _get_engine()

    # Build dynamic SET clause
    updates: list[str] = []
    params: dict[str, Any] = {"id": str(tenant_id)}

    for field_name in ("name", "network_id", "agent_name", "is_active"):
        value = getattr(request, field_name, None)
        if value is not None:
            updates.append(f"{field_name} = :{field_name}")
            params[field_name] = value

    # Nullable text fields: distinguish "set to None" from "not provided"
    # Since these use the same None default, we check model_fields_set
    provided = request.model_fields_set
    if "greeting" in provided:
        updates.append("greeting = :greeting")
        params["greeting"] = request.greeting
    if "prompt_suffix" in provided:
        updates.append("prompt_suffix = :prompt_suffix")
        params["prompt_suffix"] = request.prompt_suffix

    if request.enabled_tools is not None:
        updates.append("enabled_tools = CAST(:enabled_tools AS text[])")
        params["enabled_tools"] = request.enabled_tools

    if request.extensions is not None:
        updates.append("extensions = CAST(:extensions AS text[])")
        params["extensions"] = request.extensions

    if request.config is not None:
        updates.append("config = CAST(:config AS jsonb)")
        params["config"] = json.dumps(request.config)

    if not updates:
        return {"message": "No changes"}

    updates.append("updated_at = now()")
    set_clause = ", ".join(updates)

    async with engine.begin() as conn:
        if request.extensions is not None:
            await _check_extension_uniqueness(conn, request.extensions, str(tenant_id))
        result = await conn.execute(
            text(f"""
                UPDATE tenants
                SET {set_clause}
                WHERE id = :id
                RETURNING id
            """),
            params,
        )
        if not result.first():
            raise HTTPException(status_code=404, detail="Tenant not found")

    logger.info("Updated tenant %s", tenant_id)
    return {"message": "Tenant updated"}


@router.delete("/{tenant_id}")
async def delete_tenant(tenant_id: UUID, _: dict[str, Any] = _perm_d) -> dict[str, Any]:
    """Soft-delete a tenant (set is_active=false)."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                UPDATE tenants
                SET is_active = false, updated_at = now()
                WHERE id = :id
                RETURNING id
            """),
            {"id": str(tenant_id)},
        )
        if not result.first():
            raise HTTPException(status_code=404, detail="Tenant not found")

    logger.info("Soft-deleted tenant %s", tenant_id)
    return {"message": "Tenant deactivated"}
