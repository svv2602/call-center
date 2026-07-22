"""Admin API for customer profiles.

Customer rows are created/updated automatically by the AI agent via the
`update_customer_profile` tool during calls. Admins can view, edit any
field, or soft-delete a profile (which hides it from the default list
but leaves call-history linkage intact).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID  # noqa: TC003 - FastAPI needs UUID at runtime for path params

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from src.api.auth import require_permission
from src.api.database import get_engine as _get_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/customers", tags=["customers"])

_perm_r = Depends(require_permission("customers:read"))
_perm_w = Depends(require_permission("customers:write"))
_perm_d = Depends(require_permission("customers:delete"))

_SORT_WHITELIST = frozenset(
    ["phone", "name", "city", "total_calls", "first_call_at", "last_call_at"]
)


class CustomerUpdate(BaseModel):
    """Partial customer update — all fields optional."""

    phone: str | None = Field(default=None, max_length=32)
    name: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=100)
    delivery_address: str | None = Field(default=None, max_length=500)
    vehicles: list[dict[str, Any]] | None = None
    tenant_id: UUID | None = None


@router.get("")
async def list_customers(
    search: str = Query("", max_length=200),
    tenant_id: str = Query("", max_length=36),
    include_deleted: bool = Query(False),
    sort_by: str = Query("last_call_at"),
    sort_dir: str = Query("desc", pattern=r"^(asc|desc)$"),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _: dict[str, Any] = _perm_r,
) -> dict[str, Any]:
    """Paginated list of customers with search, tenant filter, and sorting."""
    engine = await _get_engine()

    conditions = ["1=1"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    if not include_deleted:
        conditions.append("c.deleted_at IS NULL")

    if search.strip():
        conditions.append("(phone ILIKE :search OR name ILIKE :search)")
        params["search"] = f"%{search.strip()}%"

    if tenant_id.strip():
        conditions.append("c.tenant_id = CAST(:tenant_id AS uuid)")
        params["tenant_id"] = tenant_id.strip()

    where_clause = " AND ".join(conditions)
    # Co-locate whitelist check with interpolation to prevent divergence on refactor
    if sort_by not in _SORT_WHITELIST:
        raise HTTPException(status_code=400, detail=f"Invalid sort_by: {sort_by}")
    safe_sort = sort_by
    safe_dir = "DESC" if sort_dir.lower() == "desc" else "ASC"
    order_clause = f"c.{safe_sort} {safe_dir} NULLS LAST"

    async with engine.begin() as conn:
        result = await conn.execute(
            text(f"""
                SELECT c.id, c.phone, c.name, c.city, c.vehicles,
                       c.delivery_address, c.total_calls, c.first_call_at,
                       c.last_call_at, c.tenant_id, c.deleted_at,
                       t.name AS tenant_name,
                       COUNT(*) OVER() AS _total
                FROM customers c
                LEFT JOIN tenants t ON t.id = c.tenant_id
                WHERE {where_clause}
                ORDER BY {order_clause}
                LIMIT :limit OFFSET :offset
            """),
            params,
        )
        rows = [dict(row._mapping) for row in result]

    total = rows[0]["_total"] if rows else 0
    for row in rows:
        del row["_total"]

    return {"customers": rows, "total": total}


@router.get("/{customer_id}")
async def get_customer(customer_id: UUID, _: dict[str, Any] = _perm_r) -> dict[str, Any]:
    """Get customer profile with recent calls."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        cust_result = await conn.execute(
            text("""
                SELECT c.id, c.phone, c.name, c.city, c.vehicles,
                       c.delivery_address, c.total_calls, c.first_call_at,
                       c.last_call_at, c.tenant_id, c.deleted_at,
                       t.name AS tenant_name
                FROM customers c
                LEFT JOIN tenants t ON t.id = c.tenant_id
                WHERE c.id = :id
            """),
            {"id": str(customer_id)},
        )
        cust_row = cust_result.first()
        if not cust_row:
            raise HTTPException(status_code=404, detail="Customer not found")

        calls_result = await conn.execute(
            text("""
                SELECT id, started_at, ended_at, duration_seconds,
                       scenario, transferred_to_operator
                FROM calls
                WHERE customer_id = :id
                ORDER BY started_at DESC
                LIMIT 20
            """),
            {"id": str(customer_id)},
        )
        calls = [dict(row._mapping) for row in calls_result]

    return {"customer": dict(cust_row._mapping), "recent_calls": calls}


@router.patch("/{customer_id}")
async def update_customer(
    customer_id: UUID,
    payload: CustomerUpdate,
    _: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Edit any subset of customer fields.

    Enforces the (tenant_id, phone) uniqueness at DB level — the request
    fails with 409 if the change would collide with another profile.
    """
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields provided")

    engine = await _get_engine()

    set_parts: list[str] = []
    params: dict[str, Any] = {"id": str(customer_id)}
    for field, value in updates.items():
        if field == "tenant_id":
            set_parts.append("tenant_id = CAST(:tenant_id AS uuid)")
            params["tenant_id"] = str(value) if value else None
        elif field == "vehicles":
            set_parts.append("vehicles = CAST(:vehicles AS jsonb)")
            params["vehicles"] = json.dumps(value or [])
        else:
            set_parts.append(f"{field} = :{field}")
            params[field] = value

    sql = f"UPDATE customers SET {', '.join(set_parts)} WHERE id = :id"

    async with engine.begin() as conn:
        try:
            result = await conn.execute(text(sql), params)
        except Exception as e:
            msg = str(e).lower()
            if "unique" in msg or "duplicate" in msg:
                raise HTTPException(
                    status_code=409,
                    detail="A customer with this (tenant, phone) already exists",
                ) from e
            raise

        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Customer not found")

        row = (
            await conn.execute(
                text("""
                    SELECT c.id, c.phone, c.name, c.city, c.vehicles,
                           c.delivery_address, c.total_calls, c.first_call_at,
                           c.last_call_at, c.tenant_id, c.deleted_at,
                           t.name AS tenant_name
                    FROM customers c
                    LEFT JOIN tenants t ON t.id = c.tenant_id
                    WHERE c.id = :id
                """),
                {"id": str(customer_id)},
            )
        ).first()

    logger.info(
        "customer.updated",
        extra={"customer_id": str(customer_id), "fields": list(updates.keys())},
    )
    return {"customer": dict(row._mapping)}


@router.delete("/{customer_id}", status_code=204)
async def soft_delete_customer(customer_id: UUID, _: dict[str, Any] = _perm_d) -> None:
    """Soft-delete a customer profile (hides from default list)."""
    engine = await _get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            text("UPDATE customers SET deleted_at = :now WHERE id = :id AND deleted_at IS NULL"),
            {"id": str(customer_id), "now": datetime.now(UTC)},
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Customer not found or already deleted")
    logger.info("customer.soft_deleted", extra={"customer_id": str(customer_id)})


@router.post("/{customer_id}/restore")
async def restore_customer(customer_id: UUID, _: dict[str, Any] = _perm_d) -> dict[str, Any]:
    """Restore a soft-deleted customer profile."""
    engine = await _get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                "UPDATE customers SET deleted_at = NULL WHERE id = :id AND deleted_at IS NOT NULL"
            ),
            {"id": str(customer_id)},
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Customer not found or not deleted")
    logger.info("customer.restored", extra={"customer_id": str(customer_id)})
    return {"status": "restored"}
