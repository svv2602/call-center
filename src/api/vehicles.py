"""Vehicle database browser + admin API.

Read endpoints browse the vehicle tire database (brands, models, kits, tire
sizes) imported via migration 014. Wave 8 extends the router with:

- CRUD for brands / models (manual entries via admin UI, source='manual').
- CRUD for vehicle_aliases (Cyrillic pronunciations, admin overrides).
- ZIP upload → dry-run → apply workflow for annual CSV refresh, with
  vehicle_import_history audit trail.
- On-demand alias regeneration (scripts/generate_aliases logic exposed
  as an endpoint).
- Lookup endpoint that mirrors the runtime resolver used by the fitting
  flow — lets an admin sanity-check "will Дастер resolve to Renault Duster?"
"""

from __future__ import annotations

import io
import logging
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
)
from pydantic import BaseModel, Field
from sqlalchemy import text

from src.api.auth import require_permission
from src.api.database import get_engine as _get_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/vehicles", tags=["vehicles"])

# Module-level dependencies to satisfy B008 lint rule
_perm_r = Depends(require_permission("vehicles:read"))
_perm_w = Depends(require_permission("vehicles:write"))

EXPECTED_CSV_FILES = [
    "test_table_car2_brand.csv",
    "test_table_car2_model.csv",
    "test_table_car2_kit.csv",
    "test_table_car2_kit_tyre_size.csv",
]
# Optional CSV — present in production archives but not consumed by the
# fitting-flow bot. We accept it during upload validation but don't import.
OPTIONAL_CSV_FILES = ["test_table_car2_kit_disk_size.csv", "README.txt"]

MAX_UPLOAD_BYTES = 30 * 1024 * 1024  # 30 MB — production ZIP is ~14 MB
STAGING_DIR_PREFIX = "vehicle_import_"


class VehicleImportRequest(BaseModel):
    csv_dir: str


# --- Wave 8 request models ---


class BrandCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class BrandUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ModelCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ModelUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    brand_id: int | None = None  # allow moving model between brands


class AliasCreateRequest(BaseModel):
    alias: str = Field(min_length=1, max_length=200)
    brand_id: int
    model_id: int | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class AliasUpdateRequest(BaseModel):
    alias: str | None = Field(default=None, min_length=1, max_length=200)
    brand_id: int | None = None
    model_id: int | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class LookupRequest(BaseModel):
    utterance: str = Field(min_length=1, max_length=200)


@router.get("/stats")
async def get_vehicle_stats(
    _: dict[str, Any] = _perm_r,
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
    _: dict[str, Any] = _perm_r,
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
    _: dict[str, Any] = _perm_r,
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
    _: dict[str, Any] = _perm_r,
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
    _: dict[str, Any] = _perm_r,
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
    _: dict[str, Any] = _perm_w,
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


# ==================== Wave 8: CRUD for brands ====================


@router.post("/brands", status_code=201)
async def create_brand(
    body: BrandCreateRequest,
    user: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Create a manual brand entry (negative ID via manual sequence)."""
    engine = await _get_engine()
    async with engine.begin() as conn:
        # Check for name collision (case-insensitive)
        existing = await conn.execute(
            text("SELECT id, name FROM vehicle_brands WHERE LOWER(name) = LOWER(:n)"),
            {"n": body.name.strip()},
        )
        if existing.first():
            raise HTTPException(status_code=409, detail=f"Brand '{body.name}' already exists")

        result = await conn.execute(
            text("""
                INSERT INTO vehicle_brands (id, name, source, updated_by)
                VALUES (nextval('vehicle_brands_manual_id_seq'), :name, 'manual', :uid)
                RETURNING id, name, source, created_at, updated_at
            """),
            {"name": body.name.strip(), "uid": user.get("user_id")},
        )
        row = result.first()
    logger.info("Created manual brand id=%s name=%r by user=%s", row.id, row.name, user.get("user_id"))
    return dict(row._mapping)


@router.put("/brands/{brand_id}")
async def update_brand(
    brand_id: int,
    body: BrandUpdateRequest,
    user: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Update brand name. Marks source='manual' if it was 'auto_import'
    (so subsequent CSV re-imports preserve the edit)."""
    engine = await _get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                UPDATE vehicle_brands
                SET name = :name, source = 'manual',
                    updated_by = :uid, updated_at = now()
                WHERE id = :id
                RETURNING id, name, source, created_at, updated_at
            """),
            {"id": brand_id, "name": body.name.strip(), "uid": user.get("user_id")},
        )
        row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Brand not found")
    return dict(row._mapping)


@router.delete("/brands/{brand_id}", status_code=204)
async def delete_brand(
    brand_id: int,
    user: dict[str, Any] = _perm_w,
) -> None:
    """Delete a brand — only allowed for source='manual' entries, and only
    if no models remain under it. Auto-imported brands must go through the
    CSV import diff report."""
    engine = await _get_engine()
    async with engine.begin() as conn:
        row = await conn.execute(
            text("SELECT source FROM vehicle_brands WHERE id = :id"),
            {"id": brand_id},
        )
        current = row.first()
        if not current:
            raise HTTPException(status_code=404, detail="Brand not found")
        if current.source != "manual":
            raise HTTPException(
                status_code=400,
                detail="Cannot delete auto-imported brand. Edit its source CSV instead.",
            )

        model_count = await conn.execute(
            text("SELECT COUNT(*) FROM vehicle_models WHERE brand_id = :id"),
            {"id": brand_id},
        )
        if int(model_count.scalar_one() or 0) > 0:
            raise HTTPException(
                status_code=409,
                detail="Brand has models. Delete or reassign models first.",
            )

        await conn.execute(text("DELETE FROM vehicle_brands WHERE id = :id"), {"id": brand_id})
    logger.info("Deleted manual brand id=%s by user=%s", brand_id, user.get("user_id"))


# ==================== Wave 8: CRUD for models ====================


@router.post("/brands/{brand_id}/models", status_code=201)
async def create_model(
    brand_id: int,
    body: ModelCreateRequest,
    user: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Create a manual model under a brand (negative ID)."""
    engine = await _get_engine()
    async with engine.begin() as conn:
        # Verify brand exists
        brand = await conn.execute(
            text("SELECT id FROM vehicle_brands WHERE id = :id"), {"id": brand_id}
        )
        if not brand.first():
            raise HTTPException(status_code=404, detail="Brand not found")

        # Check for name collision under this brand
        dup = await conn.execute(
            text("SELECT id FROM vehicle_models WHERE brand_id = :bid AND LOWER(name) = LOWER(:n)"),
            {"bid": brand_id, "n": body.name.strip()},
        )
        if dup.first():
            raise HTTPException(status_code=409, detail=f"Model '{body.name}' already under this brand")

        result = await conn.execute(
            text("""
                INSERT INTO vehicle_models (id, brand_id, name, source, updated_by)
                VALUES (nextval('vehicle_models_manual_id_seq'), :bid, :n, 'manual', :uid)
                RETURNING id, brand_id, name, source, created_at, updated_at
            """),
            {"bid": brand_id, "n": body.name.strip(), "uid": user.get("user_id")},
        )
        row = result.first()
    logger.info("Created manual model id=%s brand_id=%s name=%r", row.id, row.brand_id, row.name)
    return dict(row._mapping)


@router.put("/models/{model_id}")
async def update_model(
    model_id: int,
    body: ModelUpdateRequest,
    user: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Update model name, optionally reassign to a different brand."""
    engine = await _get_engine()
    params: dict[str, Any] = {"id": model_id, "n": body.name.strip(), "uid": user.get("user_id")}
    async with engine.begin() as conn:
        if body.brand_id is not None:
            brand_check = await conn.execute(
                text("SELECT id FROM vehicle_brands WHERE id = :id"), {"id": body.brand_id}
            )
            if not brand_check.first():
                raise HTTPException(status_code=404, detail="Target brand not found")
            params["bid"] = body.brand_id
            result = await conn.execute(
                text("""
                    UPDATE vehicle_models
                    SET name = :n, brand_id = :bid, source = 'manual',
                        updated_by = :uid, updated_at = now()
                    WHERE id = :id
                    RETURNING id, brand_id, name, source, created_at, updated_at
                """),
                params,
            )
        else:
            result = await conn.execute(
                text("""
                    UPDATE vehicle_models
                    SET name = :n, source = 'manual',
                        updated_by = :uid, updated_at = now()
                    WHERE id = :id
                    RETURNING id, brand_id, name, source, created_at, updated_at
                """),
                params,
            )
        row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Model not found")
    return dict(row._mapping)


@router.delete("/models/{model_id}", status_code=204)
async def delete_model(
    model_id: int,
    user: dict[str, Any] = _perm_w,
) -> None:
    """Delete a manual model. CASCADE wipes its kits and aliases."""
    engine = await _get_engine()
    async with engine.begin() as conn:
        row = await conn.execute(
            text("SELECT source FROM vehicle_models WHERE id = :id"), {"id": model_id}
        )
        current = row.first()
        if not current:
            raise HTTPException(status_code=404, detail="Model not found")
        if current.source != "manual":
            raise HTTPException(
                status_code=400,
                detail="Cannot delete auto-imported model. Edit its source CSV instead.",
            )
        await conn.execute(text("DELETE FROM vehicle_models WHERE id = :id"), {"id": model_id})
    logger.info("Deleted manual model id=%s by user=%s", model_id, user.get("user_id"))


# ==================== Wave 8: CRUD for aliases ====================


@router.get("/aliases")
async def list_aliases(
    search: str | None = Query(None, description="Filter by alias substring"),
    brand_id: int | None = Query(None),
    model_id: int | None = Query(None),
    source: str | None = Query(None, description="auto_import|auto_translit|auto_model_name|manual"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: dict[str, Any] = _perm_r,
) -> dict[str, Any]:
    """Paginated alias listing with joins to brand + model names."""
    engine = await _get_engine()
    conditions = ["1=1"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    if search:
        conditions.append("LOWER(va.alias) LIKE :search")
        params["search"] = f"%{search.lower()}%"
    if brand_id is not None:
        conditions.append("va.brand_id = :brand_id")
        params["brand_id"] = brand_id
    if model_id is not None:
        conditions.append("va.model_id = :model_id")
        params["model_id"] = model_id
    if source:
        conditions.append("va.source = :source")
        params["source"] = source

    where_clause = " AND ".join(conditions)

    async with engine.begin() as conn:
        total = (await conn.execute(
            text(f"SELECT COUNT(*) FROM vehicle_aliases va WHERE {where_clause}"), params
        )).scalar_one()

        result = await conn.execute(
            text(f"""
                SELECT va.id, va.alias, va.alias_normalized, va.brand_id, b.name AS brand_name,
                       va.model_id, m.name AS model_name, va.source, va.confidence,
                       va.created_at, va.updated_at
                FROM vehicle_aliases va
                JOIN vehicle_brands b ON b.id = va.brand_id
                LEFT JOIN vehicle_models m ON m.id = va.model_id
                WHERE {where_clause}
                ORDER BY b.name, m.name NULLS FIRST, va.alias
                LIMIT :limit OFFSET :offset
            """),
            params,
        )
        items = [dict(row._mapping) for row in result]

    return {"total": total, "limit": limit, "offset": offset, "items": items}


@router.post("/aliases", status_code=201)
async def create_alias(
    body: AliasCreateRequest,
    user: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Manually add an alias (source='manual', preserved across regen)."""
    from src.agent.vehicle_translit import normalize_alias

    engine = await _get_engine()
    normalized = normalize_alias(body.alias)
    if not normalized:
        raise HTTPException(status_code=400, detail="alias normalizes to empty string")

    async with engine.begin() as conn:
        # Validate brand
        brand = await conn.execute(
            text("SELECT id FROM vehicle_brands WHERE id = :id"), {"id": body.brand_id}
        )
        if not brand.first():
            raise HTTPException(status_code=404, detail="brand_id not found")
        # Validate model if given
        if body.model_id is not None:
            model = await conn.execute(
                text("SELECT id, brand_id FROM vehicle_models WHERE id = :id"),
                {"id": body.model_id},
            )
            m = model.first()
            if not m:
                raise HTTPException(status_code=404, detail="model_id not found")
            if m.brand_id != body.brand_id:
                raise HTTPException(
                    status_code=400,
                    detail="model_id does not belong to the specified brand_id",
                )

        try:
            result = await conn.execute(
                text("""
                    INSERT INTO vehicle_aliases
                        (alias, alias_normalized, brand_id, model_id, source,
                         confidence, created_by)
                    VALUES
                        (:alias, :norm, :bid, :mid, 'manual', :conf, :uid)
                    RETURNING id, alias, alias_normalized, brand_id, model_id,
                              source, confidence, created_at, updated_at
                """),
                {
                    "alias": body.alias.strip(),
                    "norm": normalized,
                    "bid": body.brand_id,
                    "mid": body.model_id,
                    "conf": body.confidence,
                    "uid": user.get("user_id"),
                },
            )
        except Exception as exc:
            if "duplicate key" in str(exc).lower():
                raise HTTPException(
                    status_code=409,
                    detail="This alias already exists for this brand+model combination",
                ) from exc
            raise
        row = result.first()
    logger.info(
        "Created manual alias id=%s alias=%r brand_id=%s model_id=%s by user=%s",
        row.id, row.alias, row.brand_id, row.model_id, user.get("user_id"),
    )
    return dict(row._mapping)


@router.put("/aliases/{alias_id}")
async def update_alias(
    alias_id: int,
    body: AliasUpdateRequest,
    user: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Update alias fields. Marks source='manual' unconditionally (edits win)."""
    from src.agent.vehicle_translit import normalize_alias

    engine = await _get_engine()
    async with engine.begin() as conn:
        current = await conn.execute(
            text("""
                SELECT id, alias, brand_id, model_id, confidence
                FROM vehicle_aliases WHERE id = :id
            """),
            {"id": alias_id},
        )
        row = current.first()
        if not row:
            raise HTTPException(status_code=404, detail="Alias not found")

        new_alias = body.alias.strip() if body.alias is not None else row.alias
        new_brand = body.brand_id if body.brand_id is not None else row.brand_id
        new_model = body.model_id if body.model_id is not None else row.model_id
        new_conf = body.confidence if body.confidence is not None else row.confidence

        # Validate FK if changed
        if body.brand_id is not None:
            b_check = await conn.execute(
                text("SELECT id FROM vehicle_brands WHERE id = :id"), {"id": new_brand}
            )
            if not b_check.first():
                raise HTTPException(status_code=404, detail="brand_id not found")
        if body.model_id is not None and new_model is not None:
            m_check = await conn.execute(
                text("SELECT brand_id FROM vehicle_models WHERE id = :id"), {"id": new_model}
            )
            m = m_check.first()
            if not m:
                raise HTTPException(status_code=404, detail="model_id not found")
            if m.brand_id != new_brand:
                raise HTTPException(
                    status_code=400,
                    detail="model_id does not belong to the specified brand_id",
                )

        normalized = normalize_alias(new_alias)
        result = await conn.execute(
            text("""
                UPDATE vehicle_aliases
                SET alias = :alias, alias_normalized = :norm,
                    brand_id = :bid, model_id = :mid,
                    confidence = :conf, source = 'manual', updated_at = now()
                WHERE id = :id
                RETURNING id, alias, alias_normalized, brand_id, model_id,
                          source, confidence, created_at, updated_at
            """),
            {
                "id": alias_id, "alias": new_alias, "norm": normalized,
                "bid": new_brand, "mid": new_model, "conf": new_conf,
            },
        )
        updated = result.first()
    return dict(updated._mapping)


@router.delete("/aliases/{alias_id}", status_code=204)
async def delete_alias(
    alias_id: int,
    user: dict[str, Any] = _perm_w,
) -> None:
    """Delete any alias regardless of source (admin discretion)."""
    engine = await _get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            text("DELETE FROM vehicle_aliases WHERE id = :id RETURNING id"), {"id": alias_id}
        )
        if not result.first():
            raise HTTPException(status_code=404, detail="Alias not found")
    logger.info("Deleted alias id=%s by user=%s", alias_id, user.get("user_id"))


# ==================== Wave 8: alias regeneration ====================


@router.post("/aliases/regenerate")
async def regenerate_aliases(
    background: BackgroundTasks,
    user: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Trigger scripts/generate_aliases in the background. Manual aliases
    are preserved. Frontend polls latest history record for completion."""
    from scripts.generate_aliases import generate_aliases

    engine = await _get_engine()

    async def _task() -> None:
        try:
            result = await generate_aliases(engine)
            logger.info("Regenerated aliases via API: %s (by %s)", result, user.get("user_id"))
        except Exception:
            logger.exception("Alias regeneration failed")

    background.add_task(_task)
    return {"status": "started"}


# ==================== Wave 8: lookup (admin sanity check) ====================


@router.post("/lookup")
async def lookup_alias(
    body: LookupRequest,
    _: dict[str, Any] = _perm_r,
) -> dict[str, Any]:
    """Sanity-check what the runtime resolver would return for a given utterance.

    Mirrors src.agent.vehicle_alias_lookup.resolve_by_alias but returns full
    ambiguous_matches list too (for admin debugging).
    """
    from src.agent.vehicle_alias_lookup import resolve_by_alias

    engine = await _get_engine()
    async with engine.connect() as conn:
        res = await resolve_by_alias(conn, body.utterance)

    return {
        "utterance": body.utterance,
        "brand_id": res.brand_id,
        "brand_name": res.brand_name,
        "model_id": res.model_id,
        "model_name": res.model_name,
        "ambiguous": res.ambiguous,
        "source": res.source,
        "ambiguous_matches": res.ambiguous_matches,
    }


# ==================== Wave 8: import history ====================


@router.get("/import/history")
async def list_import_history(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _: dict[str, Any] = _perm_r,
) -> dict[str, Any]:
    """Paginated list of past import attempts (both dryrun and apply)."""
    engine = await _get_engine()
    async with engine.begin() as conn:
        total = (await conn.execute(
            text("SELECT COUNT(*) FROM vehicle_import_history")
        )).scalar_one()

        result = await conn.execute(
            text("""
                SELECT h.id, h.mode, h.status, h.source_path, h.archive_name,
                       h.started_at, h.finished_at, h.error_message,
                       h.brand_added, h.brand_updated, h.brand_skipped_manual,
                       h.brand_missing_in_source,
                       h.model_added, h.model_updated, h.model_skipped_manual,
                       h.model_missing_in_source,
                       h.kit_added, h.kit_deleted,
                       h.tire_size_added, h.tire_size_deleted,
                       h.aliases_regenerated,
                       u.username AS triggered_by_username
                FROM vehicle_import_history h
                LEFT JOIN admin_users u ON u.id = h.triggered_by
                ORDER BY h.started_at DESC
                LIMIT :limit OFFSET :offset
            """),
            {"limit": limit, "offset": offset},
        )
        items = [dict(row._mapping) for row in result]

    return {"total": total, "limit": limit, "offset": offset, "items": items}


@router.get("/import/history/{history_id}")
async def get_import_history(
    history_id: int,
    _: dict[str, Any] = _perm_r,
) -> dict[str, Any]:
    """Detail view including full diff_report_json."""
    engine = await _get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT h.*, u.username AS triggered_by_username
                FROM vehicle_import_history h
                LEFT JOIN admin_users u ON u.id = h.triggered_by
                WHERE h.id = :id
            """),
            {"id": history_id},
        )
        row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Import history record not found")
    return dict(row._mapping)


# ==================== Wave 8: upload + apply ====================


def _extract_zip_safely(contents: bytes, dest_dir: Path) -> list[str]:
    """Extract CSV files from a ZIP into dest_dir.

    Only extracts the expected file basenames (path traversal defense).
    Silently skips unexpected files. Returns list of extracted basenames.
    """
    allowed = set(EXPECTED_CSV_FILES + OPTIONAL_CSV_FILES)
    extracted: list[str] = []
    with zipfile.ZipFile(io.BytesIO(contents)) as zf:
        for name in zf.namelist():
            basename = os.path.basename(name)
            if basename in allowed:
                dst = dest_dir / basename
                with zf.open(name) as src, open(dst, "wb") as out:
                    shutil.copyfileobj(src, out)
                extracted.append(basename)
    return extracted


@router.post("/import/upload")
async def upload_and_dryrun(
    user: dict[str, Any] = _perm_w,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Accept a ZIP with 4-5 CSVs, extract to a temp staging dir, run dry-run.

    Returns ``history_id`` + diff report. Frontend then POSTs
    /import/apply/{history_id} to commit.
    """
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Expected a .zip file")

    contents = await file.read()
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"ZIP too large ({len(contents)} B); limit {MAX_UPLOAD_BYTES} B",
        )
    if not contents:
        raise HTTPException(status_code=400, detail="Empty file")

    # Extract to a fresh staging dir under /tmp
    staging = Path(tempfile.mkdtemp(prefix=STAGING_DIR_PREFIX))
    try:
        try:
            extracted = _extract_zip_safely(contents, staging)
        except zipfile.BadZipFile as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise HTTPException(status_code=400, detail=f"Invalid ZIP: {exc}") from exc

        missing = [f for f in EXPECTED_CSV_FILES if f not in extracted]
        if missing:
            shutil.rmtree(staging, ignore_errors=True)
            raise HTTPException(
                status_code=400,
                detail=f"ZIP missing required CSVs: {', '.join(missing)}",
            )

        # Dry-run — computes diff, writes history row, does NOT modify data
        from scripts.import_vehicle_db import import_data

        engine = await _get_engine()
        try:
            result = await import_data(
                engine, staging, mode="dryrun",
                triggered_by=user.get("user_id"),
                archive_name=file.filename,
            )
        except Exception as exc:
            shutil.rmtree(staging, ignore_errors=True)
            logger.exception("Dry-run failed after upload")
            raise HTTPException(status_code=500, detail=f"Dry-run failed: {exc}") from exc

        logger.info(
            "Upload dry-run OK: history_id=%s file=%s size=%dB by user=%s",
            result["history_id"], file.filename, len(contents), user.get("user_id"),
        )
        return {
            **result,
            "staging_dir": str(staging),
            "extracted_files": extracted,
        }
    except HTTPException:
        raise
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


@router.post("/import/apply/{history_id}")
async def apply_staged_import(
    history_id: int,
    background: BackgroundTasks,
    user: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Apply a previously staged dry-run. Runs in the background — poll
    /import/history/{history_id} for status transition dryrun → running →
    completed / failed."""
    engine = await _get_engine()
    async with engine.begin() as conn:
        row = await conn.execute(
            text("""
                SELECT source_path, status
                FROM vehicle_import_history
                WHERE id = :id
            """),
            {"id": history_id},
        )
        record = row.first()

    if not record:
        raise HTTPException(status_code=404, detail="History record not found")
    if record.status != "dryrun":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot apply: status is '{record.status}', expected 'dryrun'",
        )

    staging = Path(record.source_path) if record.source_path else None
    if not staging or not staging.is_dir():
        raise HTTPException(
            status_code=410,
            detail="Staging directory missing (may have been cleaned up). Re-upload the ZIP.",
        )

    async def _apply_task() -> None:
        from scripts.generate_aliases import generate_aliases
        from scripts.import_vehicle_db import import_data

        try:
            await import_data(
                engine, staging, mode="apply",
                triggered_by=user.get("user_id"),
                reuse_history_id=history_id,
            )
            # Regenerate aliases after successful import so new brands+models
            # get their auto_* rows immediately.
            try:
                alias_result = await generate_aliases(engine)
                logger.info("Post-import alias regen: %s", alias_result)
            except Exception:
                logger.exception("Post-import alias regen failed (import itself succeeded)")
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    background.add_task(_apply_task)
    return {"status": "started", "history_id": history_id}


@router.delete("/import/staged/{history_id}", status_code=204)
async def discard_staged_import(
    history_id: int,
    user: dict[str, Any] = _perm_w,
) -> None:
    """Discard a staged dry-run — cleanup staging dir and delete the record."""
    engine = await _get_engine()
    async with engine.begin() as conn:
        row = await conn.execute(
            text(
                "SELECT source_path, status FROM vehicle_import_history WHERE id = :id"
            ),
            {"id": history_id},
        )
        record = row.first()

    if not record:
        raise HTTPException(status_code=404, detail="History record not found")
    if record.status != "dryrun":
        raise HTTPException(
            status_code=400,
            detail=f"Can only discard dryrun records; status='{record.status}'",
        )

    if record.source_path:
        staging = Path(record.source_path)
        if staging.is_dir() and str(staging).startswith(f"/tmp/{STAGING_DIR_PREFIX}"):
            shutil.rmtree(staging, ignore_errors=True)

    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM vehicle_import_history WHERE id = :id"), {"id": history_id}
        )
    logger.info("Discarded staged import id=%s by user=%s", history_id, user.get("user_id"))
