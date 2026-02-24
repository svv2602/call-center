"""Admin API for fitting station hints (district/landmark descriptions).

Content managers can add per-station hints (district, landmarks, description)
so the LLM agent can match customer requests ("правий берег") to the right station.
Hints are stored in Redis and merged into get_fitting_stations tool results at runtime.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from redis.asyncio import Redis

from src.api.auth import require_permission
from src.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/fitting", tags=["fitting-hints"])

REDIS_KEY = "fitting:station_hints"

_redis: Redis | None = None

_perm_r = Depends(require_permission("configuration:read"))
_perm_w = Depends(require_permission("configuration:write"))


async def _get_redis() -> Redis:
    global _redis
    if _redis is None:
        settings = get_settings()
        _redis = Redis.from_url(settings.redis.url, decode_responses=True)
    return _redis


class StationHint(BaseModel):
    district: str = ""
    landmarks: str = ""
    description: str = ""


@router.get("/station-hints")
async def get_station_hints(_: dict[str, Any] = _perm_r) -> dict[str, Any]:
    """Return all station hints {station_id: {district, landmarks, description}}."""
    redis = await _get_redis()
    raw = await redis.get(REDIS_KEY)
    hints = json.loads(raw) if raw else {}
    return {"hints": hints}


@router.put("/station-hints/{station_id}")
async def upsert_station_hint(
    station_id: str,
    hint: StationHint,
    _: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Create or update hints for a single station."""
    redis = await _get_redis()
    raw = await redis.get(REDIS_KEY)
    hints = json.loads(raw) if raw else {}
    hints[station_id] = hint.model_dump()
    await redis.set(REDIS_KEY, json.dumps(hints, ensure_ascii=False))
    logger.info("Station hint upserted: %s", station_id)
    return {"station_id": station_id, "hint": hints[station_id]}


@router.delete("/station-hints/{station_id}")
async def delete_station_hint(
    station_id: str,
    _: dict[str, Any] = _perm_w,
) -> dict[str, str]:
    """Remove hints for a single station."""
    redis = await _get_redis()
    raw = await redis.get(REDIS_KEY)
    hints = json.loads(raw) if raw else {}
    if station_id not in hints:
        raise HTTPException(status_code=404, detail="Station hint not found")
    del hints[station_id]
    await redis.set(REDIS_KEY, json.dumps(hints, ensure_ascii=False))
    logger.info("Station hint deleted: %s", station_id)
    return {"status": "deleted", "station_id": station_id}


@router.get("/stations")
async def list_fitting_stations(_: dict[str, Any] = _perm_r) -> dict[str, Any]:
    """Return cached fitting stations from Redis (read-only proxy).

    Admin UI uses this to display station names/addresses alongside hints.
    Falls back to empty list if no cached data.
    """
    redis = await _get_redis()
    raw = await redis.get("onec:fitting_stations")
    if raw:
        stations = json.loads(raw)
        return {"stations": stations}
    return {"stations": []}
