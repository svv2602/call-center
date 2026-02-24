"""Admin API for STT phrase hints configuration.

Manage speech recognition phrase hints: base dictionary, auto-extracted catalog terms,
and custom user-defined phrases. Stored in Redis, applied to Google Cloud STT v2
SpeechAdaptation at call start.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from redis.asyncio import Redis

from src.api.auth import require_permission
from src.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/stt", tags=["stt-config"])

_redis: Redis | None = None
_engine: Any = None

_perm_r = Depends(require_permission("configuration:read"))
_perm_w = Depends(require_permission("configuration:write"))


async def _get_redis() -> Redis:
    global _redis
    if _redis is None:
        settings = get_settings()
        _redis = Redis.from_url(settings.redis.url, decode_responses=False)
    return _redis


async def _get_engine() -> Any:
    global _engine
    if _engine is None:
        from sqlalchemy.ext.asyncio import create_async_engine

        settings = get_settings()
        _engine = create_async_engine(
            settings.database.url, pool_size=2, max_overflow=2, pool_pre_ping=True
        )
    return _engine


class CustomPhrasesRequest(BaseModel):
    phrases: list[str]

    @field_validator("phrases")
    @classmethod
    def validate_phrases(cls, v: list[str]) -> list[str]:
        if len(v) > 1000:
            raise ValueError("Maximum 1000 custom phrases allowed")
        for i, phrase in enumerate(v):
            if len(phrase) > 200:
                raise ValueError(f"Phrase #{i + 1} exceeds 200 character limit")
        # Filter empty strings
        return [p.strip() for p in v if p.strip()]


@router.get("/phrase-hints")
async def get_stt_phrase_hints(_: dict[str, Any] = _perm_r) -> dict[str, Any]:
    """Get phrase hints stats and custom phrases list."""
    from src.stt.phrase_hints import get_phrase_hints

    redis = await _get_redis()
    data = await get_phrase_hints(redis)
    return {
        "stats": {
            "base_count": data["base_count"],
            "auto_count": data["auto_count"],
            "custom_count": data["custom_count"],
            "total": data["total"],
            "google_limit": data["google_limit"],
            "updated_at": data.get("updated_at"),
        },
        "custom_phrases": data["custom"],
    }


@router.patch("/phrase-hints/custom")
async def update_stt_custom_phrases(
    request: CustomPhrasesRequest, _: dict[str, Any] = _perm_w
) -> dict[str, Any]:
    """Update custom phrase list."""
    from src.stt.phrase_hints import update_custom_phrases

    redis = await _get_redis()
    stats = await update_custom_phrases(redis, request.phrases)
    logger.info("STT custom phrases updated: %d phrases", stats["custom_count"])
    return {"message": "Custom phrases updated", **stats}


@router.post("/phrase-hints/refresh")
async def refresh_stt_phrase_hints(_: dict[str, Any] = _perm_w) -> dict[str, Any]:
    """Refresh phrase hints from catalog (base + auto + keep custom)."""
    from src.stt.phrase_hints import refresh_phrase_hints

    redis = await _get_redis()
    try:
        engine = await _get_engine()
    except Exception:
        logger.exception("Failed to create DB engine for phrase hints refresh")
        raise HTTPException(status_code=500, detail="Database connection failed") from None

    try:
        stats = await refresh_phrase_hints(engine, redis)
    except Exception:
        logger.exception("Phrase hints refresh failed")
        raise HTTPException(status_code=500, detail="Phrase hints refresh failed") from None

    return {"message": "Phrase hints refreshed", **stats}


@router.post("/phrase-hints/reset")
async def reset_stt_phrase_hints(_: dict[str, Any] = _perm_w) -> dict[str, Any]:
    """Delete Redis key — fall back to base phrases only."""
    from src.stt.phrase_hints import get_base_phrases, invalidate_cache

    redis = await _get_redis()
    await redis.delete("stt:phrase_hints")
    invalidate_cache()

    base = get_base_phrases()
    logger.info("STT phrase hints reset to base only (%d phrases)", len(base))
    return {
        "message": "Phrase hints reset to base only",
        "base_count": len(base),
        "auto_count": 0,
        "custom_count": 0,
        "total": len(base),
        "google_limit": 5000,
    }
