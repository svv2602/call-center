"""Admin users and audit log API endpoints.

CRUD for admin users with RBAC roles.
Audit log viewer with filters.
"""

from __future__ import annotations

import logging
from typing import Any

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.api.auth import require_role
from src.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])

_engine: AsyncEngine | None = None

# Module-level dependency to satisfy B008 lint rule
_admin_dep = Depends(require_role("admin"))


async def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database.url, pool_pre_ping=True)
    return _engine


_VALID_ROLES = ("admin", "analyst", "operator")


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=128)
    role: str = Field(default="operator", pattern=f"^({'|'.join(_VALID_ROLES)})$")


class UpdateUserRequest(BaseModel):
    role: str | None = Field(default=None, pattern=f"^({'|'.join(_VALID_ROLES)})$")
    is_active: bool | None = None


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)


@router.get("/users")
async def list_users(
    _: dict[str, Any] = _admin_dep,
) -> dict[str, Any]:
    """List all admin users."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT id, username, role, is_active, created_at, last_login_at
                FROM admin_users
                ORDER BY created_at
            """)
        )
        users = [dict(row._mapping) for row in result]

    return {"users": users}


@router.post("/users")
async def create_user(
    req: CreateUserRequest,
    _: dict[str, Any] = _admin_dep,
) -> dict[str, Any]:
    """Create a new admin user."""
    if req.role not in ("admin", "analyst", "operator"):
        raise HTTPException(status_code=400, detail="Invalid role")

    password_hash = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt()).decode()
    engine = await _get_engine()

    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    INSERT INTO admin_users (username, password_hash, role)
                    VALUES (:username, :password_hash, :role)
                    RETURNING id, username, role, is_active, created_at
                """),
                {
                    "username": req.username,
                    "password_hash": password_hash,
                    "role": req.role,
                },
            )
            user = dict(result.first()._mapping)  # type: ignore[union-attr]
    except Exception as e:
        if "unique" in str(e).lower():
            raise HTTPException(status_code=409, detail="Username already exists") from e
        raise

    return {"user": user}


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    req: UpdateUserRequest,
    _: dict[str, Any] = _admin_dep,
) -> dict[str, Any]:
    """Update user role or active status."""
    if req.role is not None and req.role not in ("admin", "analyst", "operator"):
        raise HTTPException(status_code=400, detail="Invalid role")

    engine = await _get_engine()
    updates: list[str] = []
    params: dict[str, Any] = {"user_id": user_id}

    if req.role is not None:
        updates.append("role = :role")
        params["role"] = req.role
    if req.is_active is not None:
        updates.append("is_active = :is_active")
        params["is_active"] = req.is_active

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    set_clause = ", ".join(updates)

    async with engine.begin() as conn:
        result = await conn.execute(
            text(f"""
                UPDATE admin_users
                SET {set_clause}
                WHERE id = :user_id
                RETURNING id, username, role, is_active
            """),
            params,
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")

    return {"user": dict(row._mapping)}


@router.post("/users/{user_id}/reset-password")
async def reset_password(
    user_id: str,
    req: ResetPasswordRequest,
    _: dict[str, Any] = _admin_dep,
) -> dict[str, str]:
    """Reset user password."""
    password_hash = bcrypt.hashpw(req.new_password.encode(), bcrypt.gensalt()).decode()
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                UPDATE admin_users
                SET password_hash = :password_hash
                WHERE id = :user_id
                RETURNING id
            """),
            {"user_id": user_id, "password_hash": password_hash},
        )
        if not result.first():
            raise HTTPException(status_code=404, detail="User not found")

    return {"status": "password_reset"}


@router.get("/audit-log")
async def get_audit_log(
    user_id: str | None = Query(None),
    action: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: dict[str, Any] = _admin_dep,
) -> dict[str, Any]:
    """View audit log with filters."""
    engine = await _get_engine()

    conditions = ["1=1"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    if user_id:
        conditions.append("user_id = :user_id")
        params["user_id"] = user_id
    if action:
        conditions.append("action = :action")
        params["action"] = action
    if date_from:
        conditions.append("created_at >= :date_from")
        params["date_from"] = date_from
    if date_to:
        conditions.append("created_at < CAST(:date_to AS date) + interval '1 day'")
        params["date_to"] = date_to

    where_clause = " AND ".join(conditions)

    async with engine.begin() as conn:
        count_result = await conn.execute(
            text(f"SELECT COUNT(*) AS total FROM admin_audit_log WHERE {where_clause}"),
            params,
        )
        total = count_result.scalar()

        result = await conn.execute(
            text(f"""
                SELECT id, user_id, username, action, resource_type,
                       resource_id, details, ip_address, created_at
                FROM admin_audit_log
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
            """),
            params,
        )
        entries = [dict(row._mapping) for row in result]

    return {"total": total, "entries": entries}


async def write_audit_log(
    engine: AsyncEngine,
    *,
    user_id: str | None,
    username: str | None,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> None:
    """Write an entry to the audit log."""
    async with engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO admin_audit_log
                    (user_id, username, action, resource_type, resource_id, details, ip_address)
                VALUES
                    (:user_id, :username, :action, :resource_type, :resource_id, :details, :ip_address)
            """),
            {
                "user_id": user_id,
                "username": username,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "details": None if details is None else str(details),
                "ip_address": ip_address,
            },
        )
