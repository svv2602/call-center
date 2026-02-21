"""Vehicle database browser API.

Read-only endpoints for browsing the vehicle tire database
(brands, models, kits, tire sizes) imported via migration 014.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.api.auth import require_role
from src.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/vehicles", tags=["vehicles"])

_engine: AsyncEngine | None = None

# Module-level dependencies to satisfy B008 lint rule
_analyst_dep = Depends(require_role("admin", "analyst"))
_admin_dep = Depends(require_role("admin"))

EXPECTED_CSV_FILES = [
    "test_table_car2_brand.csv",
    "test_table_car2_model.csv",
    "test_table_car2_kit.csv",
    "test_table_car2_kit_tyre_size.csv",
]


class VehicleImportRequest(BaseModel):
    csv_dir: str


async def _get_engine() -> AsyncEngine:
    """Lazily create and cache the async database engine."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database.url, pool_pre_ping=True)
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


@router.post("/import")
async def import_vehicle_db(
    body: VehicleImportRequest,
    _: dict[str, Any] = _admin_dep,
) -> dict[str, Any]:
    """Re-import vehicle DB from CSV files on the server.

    Admin places 4 CSV files in a directory and provides the path.
    Import truncates existing data and re-inserts from CSVs (~30-60s).
    """
    csv_path = Path(body.csv_dir).resolve()

    # Restrict to safe directories to prevent path traversal
    allowed_parents = ("/tmp", "/data")
    if not any(str(csv_path).startswith(p) for p in allowed_parents):
        raise HTTPException(
            status_code=400,
            detail=f"csv_dir must be under {' or '.join(allowed_parents)}",
        )

    if not csv_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Directory not found: {body.csv_dir}")

    missing = [f for f in EXPECTED_CSV_FILES if not (csv_path / f).is_file()]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Missing CSV files: {', '.join(missing)}",
        )

    from scripts.import_vehicle_db import import_data

    engine = await _get_engine()
    try:
        await import_data(engine, csv_path)
    except Exception as exc:
        logger.exception("Vehicle DB import failed")
        raise HTTPException(status_code=500, detail="Import failed. Check server logs.") from exc

    # Read freshly-written metadata
    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT brand_count, model_count, kit_count, tire_size_count,
                       imported_at, source_path
                FROM vehicle_db_metadata
                ORDER BY imported_at DESC
                LIMIT 1
            """)
        )
        meta = result.first()

    return dict(meta._mapping) if meta else {"status": "ok"}
