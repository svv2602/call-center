"""Vehicle database browser API.

Read-only endpoints for browsing the vehicle tire database
(brands, models, kits, tire sizes) imported via migration 014.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.api.auth import require_role
from src.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/vehicles", tags=["vehicles"])

_engine: AsyncEngine | None = None

# Module-level dependencies to satisfy B008 lint rule
_analyst_dep = Depends(require_role("admin", "analyst"))


async def _get_engine() -> AsyncEngine:
    """Lazily create and cache the async database engine."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database.url)
    return _engine


@router.get("/stats")
async def get_vehicle_stats(
    _: dict[str, Any] = _analyst_dep,
) -> dict[str, Any]:
    """DB metadata: record counts and last import date."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        # Try metadata table first
        meta_result = await conn.execute(
            text("""
                SELECT brand_count, model_count, kit_count, tire_size_count,
                       imported_at, source_path
                FROM vehicle_db_metadata
                ORDER BY imported_at DESC
                LIMIT 1
            """)
        )
        meta_row = meta_result.first()

        if meta_row:
            return dict(meta_row._mapping)

        # Fallback: count from tables directly
        counts_result = await conn.execute(
            text("""
                SELECT
                    (SELECT COUNT(*) FROM vehicle_brands) AS brand_count,
                    (SELECT COUNT(*) FROM vehicle_models) AS model_count,
                    (SELECT COUNT(*) FROM vehicle_kits) AS kit_count,
                    (SELECT COUNT(*) FROM vehicle_tire_sizes) AS tire_size_count
            """)
        )
        row = counts_result.first()
        if row is None:
            return {
                "brand_count": 0,
                "model_count": 0,
                "kit_count": 0,
                "tire_size_count": 0,
                "imported_at": None,
            }
        data = dict(row._mapping)
        data["imported_at"] = None
        return data


@router.get("/brands")
async def list_brands(
    search: str | None = Query(None, description="Filter by name (ILIKE)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: dict[str, Any] = _analyst_dep,
) -> dict[str, Any]:
    """Paginated brand list with optional search."""
    engine = await _get_engine()

    conditions = ["1=1"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    if search:
        conditions.append("LOWER(b.name) LIKE :search")
        params["search"] = f"%{search.lower()}%"

    where_clause = " AND ".join(conditions)

    async with engine.begin() as conn:
        count_result = await conn.execute(
            text(f"""
                SELECT COUNT(*) AS total
                FROM vehicle_brands b
                WHERE {where_clause}
            """),
            params,
        )
        total = count_result.scalar()

        result = await conn.execute(
            text(f"""
                SELECT b.id, b.name,
                       (SELECT COUNT(*) FROM vehicle_models m WHERE m.brand_id = b.id) AS model_count
                FROM vehicle_brands b
                WHERE {where_clause}
                ORDER BY b.name
                LIMIT :limit OFFSET :offset
            """),
            params,
        )
        brands = [dict(row._mapping) for row in result]

    return {"total": total, "limit": limit, "offset": offset, "items": brands}


@router.get("/brands/{brand_id}/models")
async def list_models(
    brand_id: int,
    search: str | None = Query(None, description="Filter by name (ILIKE)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: dict[str, Any] = _analyst_dep,
) -> dict[str, Any]:
    """Models for a given brand, with optional search."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        # Verify brand exists
        brand_result = await conn.execute(
            text("SELECT id, name FROM vehicle_brands WHERE id = :brand_id"),
            {"brand_id": brand_id},
        )
        brand = brand_result.first()
        if not brand:
            raise HTTPException(status_code=404, detail="Brand not found")

        conditions = ["m.brand_id = :brand_id"]
        params: dict[str, Any] = {"brand_id": brand_id, "limit": limit, "offset": offset}

        if search:
            conditions.append("LOWER(m.name) LIKE :search")
            params["search"] = f"%{search.lower()}%"

        where_clause = " AND ".join(conditions)

        count_result = await conn.execute(
            text(f"""
                SELECT COUNT(*) AS total
                FROM vehicle_models m
                WHERE {where_clause}
            """),
            params,
        )
        total = count_result.scalar()

        result = await conn.execute(
            text(f"""
                SELECT m.id, m.name,
                       (SELECT COUNT(*) FROM vehicle_kits k WHERE k.model_id = m.id) AS kit_count
                FROM vehicle_models m
                WHERE {where_clause}
                ORDER BY m.name
                LIMIT :limit OFFSET :offset
            """),
            params,
        )
        models = [dict(row._mapping) for row in result]

    return {
        "brand": dict(brand._mapping),
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": models,
    }


@router.get("/models/{model_id}/kits")
async def list_kits(
    model_id: int,
    year: int | None = Query(None, description="Filter by year"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: dict[str, Any] = _analyst_dep,
) -> dict[str, Any]:
    """Kits for a given model, optionally filtered by year."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        # Verify model exists and get brand info
        model_result = await conn.execute(
            text("""
                SELECT m.id, m.name, m.brand_id, b.name AS brand_name
                FROM vehicle_models m
                JOIN vehicle_brands b ON b.id = m.brand_id
                WHERE m.id = :model_id
            """),
            {"model_id": model_id},
        )
        model = model_result.first()
        if not model:
            raise HTTPException(status_code=404, detail="Model not found")

        conditions = ["k.model_id = :model_id"]
        params: dict[str, Any] = {"model_id": model_id, "limit": limit, "offset": offset}

        if year is not None:
            conditions.append("k.year = :year")
            params["year"] = year

        where_clause = " AND ".join(conditions)

        count_result = await conn.execute(
            text(f"""
                SELECT COUNT(*) AS total
                FROM vehicle_kits k
                WHERE {where_clause}
            """),
            params,
        )
        total = count_result.scalar()

        result = await conn.execute(
            text(f"""
                SELECT k.id, k.year, k.name, k.pcd, k.bolt_count, k.dia, k.bolt_size,
                       (SELECT COUNT(*) FROM vehicle_tire_sizes ts WHERE ts.kit_id = k.id) AS tire_size_count
                FROM vehicle_kits k
                WHERE {where_clause}
                ORDER BY k.year DESC, k.name
                LIMIT :limit OFFSET :offset
            """),
            params,
        )
        kits = [dict(row._mapping) for row in result]

    return {
        "model": dict(model._mapping),
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": kits,
    }


@router.get("/kits/{kit_id}/tire-sizes")
async def list_tire_sizes(
    kit_id: int,
    _: dict[str, Any] = _analyst_dep,
) -> dict[str, Any]:
    """All tire sizes for a given kit."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        # Verify kit exists and get context
        kit_result = await conn.execute(
            text("""
                SELECT k.id, k.year, k.name, k.pcd, k.bolt_count, k.dia, k.bolt_size,
                       m.name AS model_name, m.brand_id, b.name AS brand_name
                FROM vehicle_kits k
                JOIN vehicle_models m ON m.id = k.model_id
                JOIN vehicle_brands b ON b.id = m.brand_id
                WHERE k.id = :kit_id
            """),
            {"kit_id": kit_id},
        )
        kit = kit_result.first()
        if not kit:
            raise HTTPException(status_code=404, detail="Kit not found")

        result = await conn.execute(
            text("""
                SELECT id, width, height, diameter, type, axle, axle_group
                FROM vehicle_tire_sizes
                WHERE kit_id = :kit_id
                ORDER BY type, axle, width, height, diameter
            """),
            {"kit_id": kit_id},
        )
        tire_sizes = [dict(row._mapping) for row in result]

    return {
        "kit": dict(kit._mapping),
        "items": tire_sizes,
    }
