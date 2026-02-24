"""Admin API for point hints (district/landmark descriptions).

Content managers can add per-station and per-pickup-point hints (district, landmarks,
description) so the LLM agent can match customer requests ("правий берег") to the
right location.  Hints are stored in Redis and merged into get_fitting_stations /
get_pickup_points tool results at runtime.
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
PICKUP_HINTS_KEY = "pickup:point_hints"

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


@router.post("/stations/refresh")
async def refresh_fitting_stations(_: dict[str, Any] = _perm_w) -> dict[str, Any]:
    """Fetch fitting stations from 1C SOAP and cache in Redis.

    Creates a short-lived SOAP client, calls GetStation, stores in Redis.
    """
    from src.onec_client.soap import OneCSOAPClient

    settings = get_settings()
    if not settings.onec.username:
        raise HTTPException(status_code=503, detail="1C credentials not configured")

    client = OneCSOAPClient(
        base_url=settings.onec.url,
        username=settings.onec.username,
        password=settings.onec.password,
        wsdl_path=settings.onec.soap_wsdl_path,
        timeout=settings.onec.soap_timeout,
    )
    try:
        await client.open()
        stations = await client.get_stations()
    except Exception as exc:
        logger.warning("Failed to fetch stations from 1C SOAP: %s", exc)
        raise HTTPException(status_code=502, detail=f"1C SOAP error: {exc}") from exc
    finally:
        await client.close()

    redis = await _get_redis()
    await redis.setex(
        "onec:fitting_stations", 3600, json.dumps(stations, ensure_ascii=False)
    )
    logger.info("Fitting stations refreshed from 1C: %d stations", len(stations))
    return {"stations": stations, "total": len(stations)}


# ── Pickup Point Hints ────────────────────────────────────────


@router.get("/pickup-hints")
async def get_pickup_hints(_: dict[str, Any] = _perm_r) -> dict[str, Any]:
    """Return all pickup point hints {point_id: {district, landmarks, description}}."""
    redis = await _get_redis()
    raw = await redis.get(PICKUP_HINTS_KEY)
    hints = json.loads(raw) if raw else {}
    return {"hints": hints}


@router.put("/pickup-hints/{point_id}")
async def upsert_pickup_hint(
    point_id: str,
    hint: StationHint,
    _: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Create or update hints for a single pickup point."""
    redis = await _get_redis()
    raw = await redis.get(PICKUP_HINTS_KEY)
    hints = json.loads(raw) if raw else {}
    hints[point_id] = hint.model_dump()
    await redis.set(PICKUP_HINTS_KEY, json.dumps(hints, ensure_ascii=False))
    logger.info("Pickup point hint upserted: %s", point_id)
    return {"point_id": point_id, "hint": hints[point_id]}


@router.delete("/pickup-hints/{point_id}")
async def delete_pickup_hint(
    point_id: str,
    _: dict[str, Any] = _perm_w,
) -> dict[str, str]:
    """Remove hints for a single pickup point."""
    redis = await _get_redis()
    raw = await redis.get(PICKUP_HINTS_KEY)
    hints = json.loads(raw) if raw else {}
    if point_id not in hints:
        raise HTTPException(status_code=404, detail="Pickup point hint not found")
    del hints[point_id]
    await redis.set(PICKUP_HINTS_KEY, json.dumps(hints, ensure_ascii=False))
    logger.info("Pickup point hint deleted: %s", point_id)
    return {"status": "deleted", "point_id": point_id}


@router.get("/pickup-points")
async def list_pickup_points(_: dict[str, Any] = _perm_r) -> dict[str, Any]:
    """Return cached pickup points from Redis (read-only proxy).

    Reads from onec:points:ProKoleso (and Tshina) — same cache used by
    the get_pickup_points tool.  Falls back to empty list.
    """
    redis = await _get_redis()
    all_points: list[dict[str, Any]] = []
    for network in ("ProKoleso", "Tshina"):
        raw = await redis.get(f"onec:points:{network}")
        if raw:
            all_points.extend(json.loads(raw))
    return {"points": all_points}


_NETWORKS = ("ProKoleso", "Tshina")


@router.post("/pickup-points/refresh")
async def refresh_pickup_points(_: dict[str, Any] = _perm_w) -> dict[str, Any]:
    """Fetch pickup points from 1C REST for all networks and cache in Redis."""
    from src.onec_client.client import OneCClient

    settings = get_settings()
    if not settings.onec.username:
        raise HTTPException(status_code=503, detail="1C credentials not configured")

    client = OneCClient(
        base_url=settings.onec.url,
        username=settings.onec.username,
        password=settings.onec.password,
        timeout=settings.onec.timeout,
    )
    redis = await _get_redis()
    total = 0

    try:
        await client.open()
        for network in _NETWORKS:
            try:
                data = await client.get_pickup_points(network)
                raw_points = data.get("data", [])
                points = [
                    {
                        "id": p.get("id", ""),
                        "address": p.get("point", ""),
                        "type": p.get("point_type", ""),
                        "city": p.get("City", ""),
                    }
                    for p in raw_points
                ]
                await redis.setex(
                    f"onec:points:{network}",
                    3600,
                    json.dumps(points, ensure_ascii=False),
                )
                total += len(points)
                logger.info(
                    "Pickup points refreshed for %s: %d points", network, len(points)
                )
            except Exception:
                logger.warning(
                    "Failed to fetch pickup points for %s", network, exc_info=True
                )
    except Exception as exc:
        logger.warning("Failed to connect to 1C REST: %s", exc)
        raise HTTPException(status_code=502, detail=f"1C REST error: {exc}") from exc
    finally:
        await client.close()

    # Re-read combined points from cache
    all_points: list[dict[str, Any]] = []
    for network in _NETWORKS:
        raw = await redis.get(f"onec:points:{network}")
        if raw:
            all_points.extend(json.loads(raw))

    return {"points": all_points, "total": total}
