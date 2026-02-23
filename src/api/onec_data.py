"""Read-only 1C data viewer for Admin UI.

Exposes cached pickup points, stock lookups, and connection status
so admins can verify what data the agent sees during calls.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sys
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Query

from src.api.auth import require_permission

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/onec", tags=["onec-data"])

_perm_r = Depends(require_permission("onec_data:read"))


def _get_main_module() -> Any:
    """Get the running __main__ module (avoids __main__ vs src.main issue)."""
    return sys.modules.get("__main__") or sys.modules.get("src.main")


def _get_redis() -> Redis | None:
    main_mod = _get_main_module()
    return getattr(main_mod, "_redis", None) if main_mod else None


def _get_onec_client() -> Any:
    main_mod = _get_main_module()
    return getattr(main_mod, "_onec_client", None) if main_mod else None


@router.get("/status", dependencies=[_perm_r])
async def onec_status() -> dict[str, Any]:
    """1C connection status, cache ages, and cached item counts."""
    onec = _get_onec_client()
    redis = _get_redis()

    result: dict[str, Any] = {
        "onec_configured": onec is not None,
        "status": "not_configured",
        "pickup_cache": {},
        "stock_cache": {},
    }

    if onec is None:
        return result

    # Check 1C reachability — try cache first, then lightweight HTTP HEAD
    try:
        if redis:
            # If we have cached pickup data, 1C was reachable recently
            cached = await redis.get("onec:points:ProKoleso")
            if cached:
                result["status"] = "reachable"
            else:
                # Quick HTTP check with 3s timeout (no heavy data fetch)
                import asyncio

                resp = await asyncio.wait_for(
                    onec.get_pickup_points("ProKoleso"), timeout=3.0,
                )
                result["status"] = "reachable" if resp is not None else "error"
        else:
            import asyncio

            resp = await asyncio.wait_for(
                onec.get_pickup_points("ProKoleso"), timeout=3.0,
            )
            result["status"] = "reachable" if resp is not None else "error"
    except Exception as exc:
        result["status"] = "unreachable"
        result["error"] = str(exc)[:200]

    # Cache info
    if redis:
        for network in ("ProKoleso", "Tshina"):
            # Pickup points cache
            cache_key = f"onec:points:{network}"
            try:
                ttl = await redis.ttl(cache_key)
                if ttl and ttl > 0:
                    raw = await redis.get(cache_key)
                    count = 0
                    if raw:
                        data = json.loads(raw if isinstance(raw, str) else raw.decode())
                        count = len(data) if isinstance(data, list) else 0
                    result["pickup_cache"][network] = {
                        "ttl_seconds": ttl,
                        "cache_age_seconds": max(0, 3600 - ttl),
                        "count": count,
                    }
            except Exception:
                pass

            # Stock cache
            stock_key = f"onec:stock:{network}"
            try:
                stock_type = await redis.type(stock_key)
                stock_type_str = stock_type if isinstance(stock_type, str) else stock_type.decode()
                if stock_type_str == "hash":
                    count = await redis.hlen(stock_key)
                    ttl = await redis.ttl(stock_key)
                    result["stock_cache"][network] = {
                        "ttl_seconds": ttl if ttl and ttl > 0 else None,
                        "count": count,
                    }
            except Exception:
                pass

    # SOAP / REST / AI-orders info (from system settings + Redis)
    try:
        from src.config import get_settings

        settings = get_settings()
        if settings.onec.username:
            result["soap_endpoint"] = f"{settings.onec.url}{settings.onec.soap_wsdl_path}"
            result["soap_timeout"] = settings.onec.soap_timeout
            result["rest_endpoint"] = settings.onec.url

            # SOAP reachability (lightweight check with 3s timeout)
            try:
                import aiohttp

                auth = aiohttp.BasicAuth(settings.onec.username, settings.onec.password)
                timeout = aiohttp.ClientTimeout(total=3)
                async with aiohttp.ClientSession(timeout=timeout, auth=auth) as sess, sess.get(
                    result["soap_endpoint"], params={"wsdl": ""},
                ) as resp:
                    result["soap_status"] = "reachable" if resp.status < 400 else f"error_{resp.status}"
            except Exception:
                result["soap_status"] = "unreachable"
        else:
            result["soap_status"] = "not_configured"

        # AI order counter from Redis
        if redis:
            try:
                ai_order_seq = await redis.get("order:ai_sequence")
                result["ai_orders_total"] = int(ai_order_seq) if ai_order_seq else 0
            except Exception:
                result["ai_orders_total"] = None
    except Exception:
        pass

    return result


@router.get("/pickup-points", dependencies=[_perm_r])
async def pickup_points(
    network: str = Query("ProKoleso", description="Trading network"),
    city: str = Query("", description="Optional city filter"),
) -> dict[str, Any]:
    """Pickup points from Redis cache, fallback to live 1C request."""
    redis = _get_redis()
    onec = _get_onec_client()
    cache_key = f"onec:points:{network}"
    source = "none"
    cache_age: int | None = None
    points: list[dict[str, Any]] = []

    # 1. Try Redis cache
    if redis:
        try:
            raw = await redis.get(cache_key)
            if raw:
                points = json.loads(raw if isinstance(raw, str) else raw.decode())
                source = "cache"
                ttl = await redis.ttl(cache_key)
                if ttl and ttl > 0:
                    cache_age = max(0, 3600 - ttl)
        except Exception:
            logger.debug("Redis cache read failed for %s", cache_key, exc_info=True)

    # 2. Fallback to live 1C
    if not points and onec is not None:
        try:
            data = await onec.get_pickup_points(network)
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
            source = "live"
            # Update cache
            if redis:
                with contextlib.suppress(Exception):
                    await redis.setex(cache_key, 3600, json.dumps(points, ensure_ascii=False))
        except Exception as exc:
            return {
                "points": [],
                "total": 0,
                "source": "error",
                "error": str(exc)[:200],
                "cache_age_seconds": None,
            }

    # Apply city filter
    if city:
        points = [p for p in points if city.lower() in p.get("city", "").lower()]

    return {
        "points": points,
        "total": len(points),
        "source": source,
        "cache_age_seconds": cache_age,
    }


@router.get("/stock-lookup", dependencies=[_perm_r])
async def stock_lookup(
    network: str = Query("ProKoleso", description="Trading network"),
    sku: str = Query(..., description="Product SKU/article"),
) -> dict[str, Any]:
    """Look up stock data for a single SKU from Redis cache.

    Does NOT call 1C live — get_stock returns ALL items which is too heavy.
    """
    redis = _get_redis()

    if not redis:
        return {"found": False, "error": "Redis not available"}

    stock_key = f"onec:stock:{network}"
    try:
        stock_type = await redis.type(stock_key)
        stock_type_str = stock_type if isinstance(stock_type, str) else stock_type.decode()
        if stock_type_str != "hash":
            return {"found": False, "error": "Stock cache not populated"}

        raw = await redis.hget(stock_key, sku)
        if not raw:
            # Try case-insensitive search on first 20 matching keys
            return {"found": False, "sku": sku}

        data = json.loads(raw if isinstance(raw, str) else raw.decode())
        return {
            "found": True,
            "sku": sku,
            "data": data,
        }
    except Exception as exc:
        return {"found": False, "error": str(exc)[:200]}
