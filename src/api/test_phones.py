"""Admin API for test phone number management.

Manage test phone numbers with per-number history mode (with_history / no_history).
Allows clearing call history for specific phone numbers.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
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

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        digits = "".join(c for c in v if c.isdigit())
        if len(digits) < 10:
            raise ValueError("Phone number must have at least 10 digits")
        return v


@router.get("/config")
async def get_test_phones(_: dict[str, Any] = _perm_r) -> dict[str, Any]:
    """Get test phone numbers configuration."""
    redis = await _get_redis()
    raw = await redis.get(REDIS_KEY)
    phones = json.loads(raw) if raw else {}
    return {"phones": phones}


@router.put("/config")
async def upsert_test_phone(
    request: TestPhoneRequest, _: dict[str, Any] = _perm_w
) -> dict[str, Any]:
    """Add or update a test phone number."""
    redis = await _get_redis()
    raw = await redis.get(REDIS_KEY)
    phones = json.loads(raw) if raw else {}

    normalized = normalize_phone_ua(request.phone)
    phones[normalized] = request.mode
    await redis.set(REDIS_KEY, json.dumps(phones))

    logger.info("Test phone %s set to mode=%s", normalized, request.mode)
    return {"phone": normalized, "mode": request.mode, "phones": phones}


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
    return {"removed": normalized, "phones": phones}


@router.post("/clear-history/{phone}")
async def clear_phone_history(
    phone: str, _: dict[str, Any] = _perm_w
) -> dict[str, Any]:
    """Clear all call history for a phone number.

    Deletes call_tool_calls, call_turns, calls by caller_id,
    and resets customers.total_calls to 0.
    """
    normalized = normalize_phone_ua(phone)
    engine = await _get_engine()

    async with engine.begin() as conn:
        # Delete tool calls for matching calls
        r1 = await conn.execute(
            text("""
                DELETE FROM call_tool_calls
                WHERE call_id IN (SELECT id FROM calls WHERE caller_id = :phone)
            """),
            {"phone": normalized},
        )
        tool_calls_deleted = r1.rowcount

        # Delete turns for matching calls
        r2 = await conn.execute(
            text("""
                DELETE FROM call_turns
                WHERE call_id IN (SELECT id FROM calls WHERE caller_id = :phone)
            """),
            {"phone": normalized},
        )
        turns_deleted = r2.rowcount

        # Delete calls
        r3 = await conn.execute(
            text("DELETE FROM calls WHERE caller_id = :phone"),
            {"phone": normalized},
        )
        calls_deleted = r3.rowcount

        # Reset customer total_calls
        await conn.execute(
            text("""
                UPDATE customers SET total_calls = 0,
                    first_call_at = NULL, last_call_at = NULL
                WHERE phone = :phone
            """),
            {"phone": normalized},
        )

    logger.info(
        "Cleared history for %s: %d calls, %d turns, %d tool_calls",
        normalized,
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
