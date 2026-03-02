"""Admin API for test phone number management.

Manage test phone numbers with per-number history mode (with_history / no_history).
Allows clearing call history for specific phone numbers.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.api.auth import require_permission
from src.config import get_settings
from src.utils.phone import normalize_phone_ua

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/test-phones", tags=["test-phones"])

REDIS_KEY = "test:phones"

_redis: Redis | None = None
_engine: AsyncEngine | None = None

_perm_r = Depends(require_permission("configuration:read"))
_perm_w = Depends(require_permission("configuration:write"))


async def _get_redis() -> Redis:
    global _redis
    if _redis is None:
        settings = get_settings()
        _redis = Redis.from_url(settings.redis.url, decode_responses=True)
    return _redis


async def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database.url, pool_pre_ping=True)
    return _engine


class TestPhoneRequest(BaseModel):
    phone: str
    mode: Literal["with_history", "no_history"] = "no_history"
    tenant_id: str | None = None

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        digits = "".join(c for c in v if c.isdigit())
        if len(digits) < 10:
            raise ValueError("Phone number must have at least 10 digits")
        return v


def _normalize_entry(value: Any) -> dict[str, Any]:
    """Normalize a phone entry to dict format (backward compat)."""
    if isinstance(value, str):
        return {"mode": value, "tenant_id": None}
    return value


@router.get("/config")
async def get_test_phones(_: dict[str, Any] = _perm_r) -> dict[str, Any]:
    """Get test phone numbers configuration."""
    redis = await _get_redis()
    raw = await redis.get(REDIS_KEY)
    phones = json.loads(raw) if raw else {}
    # Normalize to dict format for backward compat
    normalized = {phone: _normalize_entry(v) for phone, v in phones.items()}
    return {"phones": normalized}


@router.put("/config")
async def upsert_test_phone(
    request: TestPhoneRequest, _: dict[str, Any] = _perm_w
) -> dict[str, Any]:
    """Add or update a test phone number."""
    redis = await _get_redis()
    raw = await redis.get(REDIS_KEY)
    phones = json.loads(raw) if raw else {}

    normalized = normalize_phone_ua(request.phone)
    entry = {"mode": request.mode, "tenant_id": request.tenant_id}
    phones[normalized] = entry
    await redis.set(REDIS_KEY, json.dumps(phones))

    logger.info("Test phone %s set to mode=%s tenant=%s", normalized, request.mode, request.tenant_id)
    normalized_phones = {p: _normalize_entry(v) for p, v in phones.items()}
    return {"phone": normalized, "mode": request.mode, "tenant_id": request.tenant_id, "phones": normalized_phones}


@router.delete("/config/{phone}")
async def delete_test_phone(phone: str, _: dict[str, Any] = _perm_w) -> dict[str, Any]:
    """Remove a phone number from test config."""
    redis = await _get_redis()
    raw = await redis.get(REDIS_KEY)
    phones = json.loads(raw) if raw else {}

    normalized = normalize_phone_ua(phone)
    if normalized not in phones:
        raise HTTPException(status_code=404, detail=f"Phone {normalized} not in config")

    del phones[normalized]
    await redis.set(REDIS_KEY, json.dumps(phones))

    logger.info("Test phone %s removed", normalized)
    normalized_phones = {p: _normalize_entry(v) for p, v in phones.items()}
    return {"removed": normalized, "phones": normalized_phones}


@router.post("/clear-history/{phone}")
async def clear_phone_history(
    phone: str,
    tenant_id: str | None = Query(None),
    _: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Clear all call history for a phone number.

    Deletes call_tool_calls, call_turns, calls by caller_id,
    and resets customers.total_calls to 0.
    If tenant_id is provided, only deletes calls for that tenant.
    """
    normalized = normalize_phone_ua(phone)
    engine = await _get_engine()

    tenant_filter = ""
    params: dict[str, Any] = {"phone": normalized}
    if tenant_id:
        tenant_filter = " AND tenant_id = CAST(:tid AS uuid)"
        params["tid"] = tenant_id

    async with engine.begin() as conn:
        # Delete tool calls for matching calls
        r1 = await conn.execute(
            text(f"""
                DELETE FROM call_tool_calls
                WHERE call_id IN (SELECT id FROM calls WHERE caller_id = :phone{tenant_filter})
            """),
            params,
        )
        tool_calls_deleted = r1.rowcount

        # Delete turns for matching calls
        r2 = await conn.execute(
            text(f"""
                DELETE FROM call_turns
                WHERE call_id IN (SELECT id FROM calls WHERE caller_id = :phone{tenant_filter})
            """),
            params,
        )
        turns_deleted = r2.rowcount

        # Delete calls
        r3 = await conn.execute(
            text(f"DELETE FROM calls WHERE caller_id = :phone{tenant_filter}"),
            params,
        )
        calls_deleted = r3.rowcount

        # Reset customer total_calls (filter by tenant if provided)
        customer_tenant_filter = ""
        customer_params: dict[str, Any] = {"phone": normalized}
        if tenant_id:
            customer_tenant_filter = " AND tenant_id = CAST(:tid AS uuid)"
            customer_params["tid"] = tenant_id
        await conn.execute(
            text(f"""
                UPDATE customers SET total_calls = 0,
                    first_call_at = NULL, last_call_at = NULL
                WHERE phone = :phone{customer_tenant_filter}
            """),
            customer_params,
        )

    logger.info(
        "Cleared history for %s (tenant=%s): %d calls, %d turns, %d tool_calls",
        normalized,
        tenant_id,
        calls_deleted,
        turns_deleted,
        tool_calls_deleted,
    )
    return {
        "phone": normalized,
        "calls_deleted": calls_deleted,
        "turns_deleted": turns_deleted,
        "tool_calls_deleted": tool_calls_deleted,
    }
