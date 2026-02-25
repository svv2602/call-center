"""Admin API for point hints (district/landmark descriptions).

Content managers can add per-station and per-pickup-point hints (district, landmarks,
description) so the LLM agent can match customer requests ("правий берег") to the
right location.  Hints are stored in PostgreSQL (primary) with Redis as write-through
cache for fast reads during calls.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.api.auth import require_permission
from src.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/fitting", tags=["fitting-hints"])

REDIS_KEY = "fitting:station_hints"
PICKUP_HINTS_KEY = "pickup:point_hints"

_redis: Redis | None = None
_engine: AsyncEngine | None = None

_perm_r = Depends(require_permission("point_hints:read"))
_perm_w = Depends(require_permission("point_hints:write"))


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


async def _load_hints_from_pg(
    conn: Any, point_type: str
) -> dict[str, dict[str, str]]:
    """Load all hints for a point_type from PostgreSQL, return as dict."""
    result = await conn.execute(
        text(
            "SELECT point_id, district, landmarks, description "
            "FROM point_hints WHERE point_type = :point_type"
        ),
        {"point_type": point_type},
    )
    hints: dict[str, dict[str, str]] = {}
    for row in result.mappings():
        hints[row["point_id"]] = {
            "district": row["district"],
            "landmarks": row["landmarks"],
            "description": row["description"],
        }
    return hints


async def _sync_hints_to_redis(redis_key: str, hints: dict[str, Any]) -> None:
    """Best-effort sync of full hints dict to Redis cache."""
    try:
        redis = await _get_redis()
        await redis.set(redis_key, json.dumps(hints, ensure_ascii=False))
    except Exception:
        logger.warning("Redis sync failed for %s", redis_key, exc_info=True)


def _redis_key_for_type(point_type: str) -> str:
    return REDIS_KEY if point_type == "fitting_station" else PICKUP_HINTS_KEY


class StationHint(BaseModel):
    district: str = ""
    landmarks: str = ""
    description: str = ""


# ── Station Hints ─────────────────────────────────────────


@router.get("/station-hints")
async def get_station_hints(_: dict[str, Any] = _perm_r) -> dict[str, Any]:
    """Return all station hints merged with station info.

    Returns {hints: {station_id: {district, landmarks, description}},
             stations: [...]} where stations list is from Redis/1C cache.
    Hints are always from PostgreSQL (source of truth).
    """
    engine = await _get_engine()
    async with engine.begin() as conn:
        hints = await _load_hints_from_pg(conn, "fitting_station")
    # Warm Redis cache
    await _sync_hints_to_redis(REDIS_KEY, hints)

    # Also return stations list for UI convenience
    stations: list[dict[str, Any]] = []
    try:
        redis = await _get_redis()
        raw = await redis.get("onec:fitting_stations")
        if raw:
            stations = json.loads(raw)
    except Exception:
        pass
    return {"hints": hints, "stations": stations}


@router.put("/station-hints/{station_id}")
async def upsert_station_hint(
    station_id: str,
    hint: StationHint,
    _: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Create or update hints for a single station."""
    engine = await _get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO point_hints (point_type, point_id, district, landmarks, description) "
                "VALUES (:point_type, :point_id, :district, :landmarks, :description) "
                "ON CONFLICT (point_type, point_id) DO UPDATE SET "
                "district = EXCLUDED.district, landmarks = EXCLUDED.landmarks, "
                "description = EXCLUDED.description, updated_at = now()"
            ),
            {
                "point_type": "fitting_station",
                "point_id": station_id,
                "district": hint.district,
                "landmarks": hint.landmarks,
                "description": hint.description,
            },
        )
        hints = await _load_hints_from_pg(conn, "fitting_station")
    # PG committed — sync Redis best-effort
    await _sync_hints_to_redis(REDIS_KEY, hints)
    logger.info("Station hint upserted: %s", station_id)
    return {"station_id": station_id, "hint": hint.model_dump()}


@router.delete("/station-hints/{station_id}")
async def delete_station_hint(
    station_id: str,
    _: dict[str, Any] = _perm_w,
) -> dict[str, str]:
    """Remove hints for a single station."""
    engine = await _get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                "DELETE FROM point_hints "
                "WHERE point_type = 'fitting_station' AND point_id = :point_id"
            ),
            {"point_id": station_id},
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Station hint not found")
        hints = await _load_hints_from_pg(conn, "fitting_station")
    # PG committed — sync Redis best-effort
    await _sync_hints_to_redis(REDIS_KEY, hints)
    logger.info("Station hint deleted: %s", station_id)
    return {"status": "deleted", "station_id": station_id}


@router.get("/stations")
async def list_fitting_stations(_: dict[str, Any] = _perm_r) -> dict[str, Any]:
    """Return fitting stations: Redis cache → 1C SOAP fallback.

    Admin UI uses this to display station names/addresses alongside hints.
    If Redis cache is expired, fetches fresh data from 1C and re-caches.
    """
    redis = await _get_redis()
    raw = await redis.get("onec:fitting_stations")
    if raw:
        stations = json.loads(raw)
        return {"stations": stations}

    # Cache miss — try fetching from 1C SOAP directly
    from src.onec_client.soap import OneCSOAPClient

    settings = get_settings()
    if not settings.onec.username:
        return {"stations": []}

    try:
        client = OneCSOAPClient(
            base_url=settings.onec.url,
            username=settings.onec.username,
            password=settings.onec.password,
            wsdl_path=settings.onec.soap_wsdl_path,
            timeout=settings.onec.soap_timeout,
        )
        await client.open()
        try:
            stations = await client.get_stations()
        finally:
            await client.close()
        # Re-cache for 1 hour
        await redis.setex(
            "onec:fitting_stations", 3600, json.dumps(stations, ensure_ascii=False)
        )
        logger.info("Stations cache warmed from 1C SOAP: %d stations", len(stations))
        return {"stations": stations}
    except Exception:
        logger.warning("Failed to fetch stations from 1C for cache warm", exc_info=True)
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
    """Return all pickup point hints merged with points info.

    Returns {hints: {point_id: {district, landmarks, description}},
             points: [...]} where points list is from Redis cache.
    Hints are always from PostgreSQL (source of truth).
    """
    engine = await _get_engine()
    async with engine.begin() as conn:
        hints = await _load_hints_from_pg(conn, "pickup_point")
    # Warm Redis cache
    await _sync_hints_to_redis(PICKUP_HINTS_KEY, hints)

    # Also return points list for UI convenience
    all_points: list[dict[str, Any]] = []
    try:
        redis = await _get_redis()
        for network in ("ProKoleso", "Tshina"):
            raw = await redis.get(f"onec:points:{network}")
            if raw:
                all_points.extend(json.loads(raw))
    except Exception:
        pass
    return {"hints": hints, "points": all_points}


@router.put("/pickup-hints/{point_id}")
async def upsert_pickup_hint(
    point_id: str,
    hint: StationHint,
    _: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Create or update hints for a single pickup point."""
    engine = await _get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO point_hints (point_type, point_id, district, landmarks, description) "
                "VALUES (:point_type, :point_id, :district, :landmarks, :description) "
                "ON CONFLICT (point_type, point_id) DO UPDATE SET "
                "district = EXCLUDED.district, landmarks = EXCLUDED.landmarks, "
                "description = EXCLUDED.description, updated_at = now()"
            ),
            {
                "point_type": "pickup_point",
                "point_id": point_id,
                "district": hint.district,
                "landmarks": hint.landmarks,
                "description": hint.description,
            },
        )
        hints = await _load_hints_from_pg(conn, "pickup_point")
    # PG committed — sync Redis best-effort
    await _sync_hints_to_redis(PICKUP_HINTS_KEY, hints)
    logger.info("Pickup point hint upserted: %s", point_id)
    return {"point_id": point_id, "hint": hint.model_dump()}


@router.delete("/pickup-hints/{point_id}")
async def delete_pickup_hint(
    point_id: str,
    _: dict[str, Any] = _perm_w,
) -> dict[str, str]:
    """Remove hints for a single pickup point."""
    engine = await _get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                "DELETE FROM point_hints "
                "WHERE point_type = 'pickup_point' AND point_id = :point_id"
            ),
            {"point_id": point_id},
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Pickup point hint not found")
        hints = await _load_hints_from_pg(conn, "pickup_point")
    # PG committed — sync Redis best-effort
    await _sync_hints_to_redis(PICKUP_HINTS_KEY, hints)
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
