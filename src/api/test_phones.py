"""Admin API for test phone number management.

Manage test phone numbers with per-number, per-tenant history mode
(with_history / no_history). Allows clearing call history for specific
phone numbers scoped by tenant.

Redis format (test:phones):
  {
    "+380501234567": [
      {"mode": "no_history", "tenant_id": "bb0e4d02-..."},
      {"mode": "with_history", "tenant_id": "2e47a882-..."}
    ]
  }

Backward compat: old string/dict values are auto-normalized to list on read.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.api.auth import require_permission
from src.config import get_settings
from src.utils.phone import normalize_phone_ua

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/test-phones", tags=["test-phones"])

REDIS_KEY = "test:phones"

_engine: AsyncEngine | None = None

_perm_r = Depends(require_permission("configuration:read"))
_perm_w = Depends(require_permission("configuration:write"))


async def _get_redis() -> Redis:
    from src.core.redis_client import get_redis

    return await get_redis()


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


def _normalize_entries(value: Any) -> list[dict[str, Any]]:
    """Normalize a phone value to list-of-dicts format (backward compat).

    Handles three legacy formats:
      str  "no_history"         → [{"mode": "no_history", "tenant_id": null}]
      dict {"mode":..,"tenant_id":..} → [that dict]
      list [...]                → as-is
    """
    if isinstance(value, str):
        return [{"mode": value, "tenant_id": None}]
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return value
    return [{"mode": "no_history", "tenant_id": None}]


def _normalize_all(phones: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Normalize entire phones dict."""
    return {phone: _normalize_entries(v) for phone, v in phones.items()}


async def _load_phones(redis: Redis) -> dict[str, list[dict[str, Any]]]:
    """Load and normalize phones from Redis."""
    raw = await redis.get(REDIS_KEY)
    phones = json.loads(raw) if raw else {}
    return _normalize_all(phones)


async def _save_phones(redis: Redis, phones: dict[str, list[dict[str, Any]]]) -> None:
    """Save phones to Redis."""
    await redis.set(REDIS_KEY, json.dumps(phones))


@router.get("/config")
async def get_test_phones(_: dict[str, Any] = _perm_r) -> dict[str, Any]:
    """Get test phone numbers configuration."""
    redis = await _get_redis()
    phones = await _load_phones(redis)
    return {"phones": phones}


@router.put("/config")
async def upsert_test_phone(
    request: TestPhoneRequest, _: dict[str, Any] = _perm_w
) -> dict[str, Any]:
    """Add or update a test phone entry for a specific tenant.

    Same phone can have different settings for different tenants.
    """
    redis = await _get_redis()
    phones = await _load_phones(redis)

    normalized = normalize_phone_ua(request.phone)
    new_entry = {"mode": request.mode, "tenant_id": request.tenant_id}

    entries = phones.get(normalized, [])
    # Replace existing entry for same tenant_id, or append new
    replaced = False
    for i, e in enumerate(entries):
        if e.get("tenant_id") == request.tenant_id:
            entries[i] = new_entry
            replaced = True
            break
    if not replaced:
        entries.append(new_entry)

    phones[normalized] = entries
    await _save_phones(redis, phones)

    logger.info(
        "Test phone %s set to mode=%s tenant=%s",
        normalized, request.mode, request.tenant_id,
    )
    return {
        "phone": normalized,
        "mode": request.mode,
        "tenant_id": request.tenant_id,
        "phones": phones,
    }


@router.delete("/config/{phone}")
async def delete_test_phone(
    phone: str,
    tenant_id: str | None = Query(None),
    _: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Remove a test phone entry.

    If tenant_id is provided, removes only that tenant's entry.
    If not provided, removes all entries for the phone.
    """
    redis = await _get_redis()
    phones = await _load_phones(redis)

    normalized = normalize_phone_ua(phone)
    if normalized not in phones:
        raise HTTPException(status_code=404, detail=f"Phone {normalized} not in config")

    if tenant_id is not None:
        # Remove only the entry for this tenant
        entries = phones[normalized]
        phones[normalized] = [e for e in entries if e.get("tenant_id") != tenant_id]
        if not phones[normalized]:
            del phones[normalized]
    else:
        del phones[normalized]

    await _save_phones(redis, phones)

    logger.info("Test phone %s removed (tenant=%s)", normalized, tenant_id)
    return {"removed": normalized, "tenant_id": tenant_id, "phones": phones}


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
