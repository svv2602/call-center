"""Admin API for viewing customer profiles (read-only).

Customers are created/updated automatically by the AI agent via
the `update_customer_profile` tool during calls.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID  # noqa: TC003 - FastAPI needs UUID at runtime for path params

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from src.api.auth import require_permission
from src.api.database import get_engine as _get_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/customers", tags=["customers"])

_perm_r = Depends(require_permission("customers:read"))

_SORT_WHITELIST = frozenset(
    ["phone", "name", "city", "total_calls", "first_call_at", "last_call_at"]
)


@router.get("")
async def list_customers(
    search: str = Query("", max_length=200),
    tenant_id: str = Query("", max_length=36),
    sort_by: str = Query("last_call_at"),
    sort_dir: str = Query("desc", pattern=r"^(asc|desc)$"),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _: dict[str, Any] = _perm_r,
) -> dict[str, Any]:
    """Paginated list of customers with search, tenant filter, and sorting."""
    if sort_by not in _SORT_WHITELIST:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid sort_by: {sort_by}. "
            f"Allowed: {', '.join(sorted(_SORT_WHITELIST))}",
        )

    engine = await _get_engine()

    conditions = ["1=1"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    if search.strip():
        conditions.append("(phone ILIKE :search OR name ILIKE :search)")
        params["search"] = f"%{search.strip()}%"

    if tenant_id.strip():
        conditions.append("c.tenant_id = CAST(:tenant_id AS uuid)")
        params["tenant_id"] = tenant_id.strip()

    where_clause = " AND ".join(conditions)
    order_clause = f"c.{sort_by} {sort_dir} NULLS LAST"

    async with engine.begin() as conn:
        result = await conn.execute(
            text(f"""
                SELECT c.id, c.phone, c.name, c.city, c.vehicles,
                       c.delivery_address, c.total_calls, c.first_call_at,
                       c.last_call_at, c.tenant_id,
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
async def get_customer(
    customer_id: UUID, _: dict[str, Any] = _perm_r
) -> dict[str, Any]:
    """Get customer profile with recent calls."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        cust_result = await conn.execute(
            text("""
                SELECT c.id, c.phone, c.name, c.city, c.vehicles,
                       c.delivery_address, c.total_calls, c.first_call_at,
                       c.last_call_at, c.tenant_id,
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
