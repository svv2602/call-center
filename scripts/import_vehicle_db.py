"""Import vehicle tire size database from CSV files.

Reads brand/model/kit/tire-size CSVs from the external db_size_auto directory
and bulk-inserts into PostgreSQL tables created by migration 014.

Usage: python -m scripts.import_vehicle_db [--csv-dir /path/to/csvs]
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import re
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_CSV_DIR = "/home/snisar/RubyProjects/db_size_auto"
BATCH_SIZE = 10_000


def _clean_control_chars(value: str) -> str:
    """Strip control characters (0x00-0x1f) from a string."""
    return re.sub(r"[\x00-\x1f]", "", value)


def _int_from_csv(value: str) -> int:
    """Convert CSV numeric string like '235.00' to int."""
    return int(float(value))


def _decimal_from_csv(value: str) -> Decimal | None:
    """Convert CSV numeric string to Decimal, or None if empty/invalid."""
    if not value or value == "NULL":
        return None
    try:
        return Decimal(value)
    except Exception:
        return None


def _smallint_or_none(value: str) -> int | None:
    """Convert CSV value to smallint or None."""
    if not value or value == "NULL":
        return None
    return int(float(value))


def read_brands(csv_dir: Path) -> list[dict[str, Any]]:
    """Read vehicle brands CSV."""
    rows = []
    path = csv_dir / "test_table_car2_brand.csv"
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({"id": int(row["id"]), "name": row["name"]})
    logger.info("Read %d brands from %s", len(rows), path.name)
    return rows


def _normalize_name(name: str) -> str:
    """Normalize a name for fuzzy comparison: lowercase, strip spaces/hyphens/punctuation."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _is_brand_name(name: str, brand_names_lower: set[str], brand_names_norm: set[str]) -> bool:
    """Check if a model name is actually a brand name (data quality issue).

    Uses exact case-insensitive match, normalized match (strip spaces/hyphens),
    and substring containment for longer brand names (≥5 chars) to catch
    variants like "Mercedes-Benz" when brand is "Mercedes".
    """
    if not name.strip():
        return True  # empty names are bogus
    name_lower = name.lower()
    if name_lower in brand_names_lower:
        return True
    if _normalize_name(name) in brand_names_norm:
        return True
    # Substring check: brand name (≥5 chars) contained in model name
    return any(len(bn) >= 5 and bn in name_lower for bn in brand_names_lower)


def _read_kit_model_ids(csv_dir: Path) -> set[int]:
    """Read kit CSV and return set of model IDs that have at least one kit."""
    model_ids: set[int] = set()
    path = csv_dir / "test_table_car2_kit.csv"
    with open(path, encoding="latin-1") as f:
        reader = csv.DictReader(f)
        for row in reader:
            model_ids.add(int(row["model"]))
    return model_ids


def read_models(csv_dir: Path, brand_names: set[str]) -> list[dict[str, Any]]:
    """Read vehicle models CSV, filtering out bogus rows.

    The source CSV contains ~167 rows where brand names are erroneously
    listed as models (all under brand_id=138/Alpine, with no kits).

    Two-pass filtering:
    1. Remove orphan models (no kits) whose name matches a brand name.
    2. If a brand lost ≥10 orphans in pass 1, remove ALL remaining orphans
       for that brand — they're part of the same data corruption
       (catches GAZ, UAZ, VAZ, Citroën encoding variant, etc.).
    """
    path = csv_dir / "test_table_car2_model.csv"
    brand_names_lower = {n.lower() for n in brand_names}
    brand_names_norm = {_normalize_name(n) for n in brand_names}
    kit_model_ids = _read_kit_model_ids(csv_dir)

    # Pass 1: read all rows, mark orphan brand-name matches
    all_rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            model_id = int(row["id"])
            brand_id = int(row["brand"])
            name = row["name"]
            is_orphan = model_id not in kit_model_ids
            is_brand = is_orphan and _is_brand_name(name, brand_names_lower, brand_names_norm)
            all_rows.append({
                "id": model_id,
                "brand_id": brand_id,
                "name": name,
                "_orphan": is_orphan,
                "_brand_match": is_brand,
            })

    # Count brand-name orphans per brand_id
    brand_match_counts: dict[int, int] = {}
    for r in all_rows:
        if r["_brand_match"]:
            brand_match_counts[r["brand_id"]] = brand_match_counts.get(r["brand_id"], 0) + 1

    # Brands with ≥10 brand-name orphans are corrupted
    corrupted_brands = {bid for bid, cnt in brand_match_counts.items() if cnt >= 10}

    # Pass 2: filter
    rows = []
    skipped = 0
    for r in all_rows:
        if r["_brand_match"]:
            skipped += 1
            continue
        if r["_orphan"] and r["brand_id"] in corrupted_brands:
            skipped += 1
            continue
        rows.append({"id": r["id"], "brand_id": r["brand_id"], "name": r["name"]})

    if skipped:
        logger.warning(
            "Skipped %d orphan model rows (brand-name matches + corrupted brand cleanup)", skipped
        )
    logger.info("Read %d models from %s", len(rows), path.name)
    return rows


def read_kits(csv_dir: Path) -> list[dict[str, Any]]:
    """Read vehicle kits CSV (latin-1 encoding, control chars in bolt_size)."""
    rows = []
    path = csv_dir / "test_table_car2_kit.csv"
    with open(path, encoding="latin-1") as f:
        reader = csv.DictReader(f)
        for row in reader:
            bolt_size = _clean_control_chars(row.get("bolt_size", ""))
            rows.append({
                "id": int(row["id"]),
                "model_id": int(row["model"]),
                "year": int(row["year"]),
                "name": row.get("name", ""),
                "pcd": _decimal_from_csv(row.get("pcd", "")),
                "bolt_count": _smallint_or_none(row.get("bolt_count", "")),
                "dia": _decimal_from_csv(row.get("dia", "")),
                "bolt_size": bolt_size if bolt_size else None,
            })
    logger.info("Read %d kits from %s", len(rows), path.name)
    return rows


def read_tire_sizes(csv_dir: Path) -> list[dict[str, Any]]:
    """Read vehicle tire sizes CSV."""
    rows = []
    path = csv_dir / "test_table_car2_kit_tyre_size.csv"
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({
                "id": int(row["id"]),
                "kit_id": int(row["kit"]),
                "width": _int_from_csv(row["width"]),
                "height": _int_from_csv(row["height"]),
                "diameter": Decimal(row["diameter"]),
                "type": int(row["type"]),
                "axle": int(row["axle"]),
                "axle_group": _smallint_or_none(row.get("axle_group", "")),
            })
    logger.info("Read %d tire sizes from %s", len(rows), path.name)
    return rows


async def import_data(engine: AsyncEngine, csv_dir: Path) -> None:
    """Truncate tables and import all CSV data."""
    logger.info("Starting import from %s", csv_dir)

    # Truncate in FK order
    async with engine.begin() as conn:
        await conn.execute(text(
            "TRUNCATE vehicle_tire_sizes, vehicle_kits, vehicle_models, vehicle_brands CASCADE"
        ))
    logger.info("Tables truncated")

    # 1. Brands
    brands = read_brands(csv_dir)
    brand_names = {b["name"] for b in brands}
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO vehicle_brands (id, name) VALUES (:id, :name)"),
            brands,
        )
    logger.info("Inserted %d brands", len(brands))

    # 2. Models (filter out bogus rows where brand names appear as model names)
    models = read_models(csv_dir, brand_names)
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO vehicle_models (id, brand_id, name) VALUES (:id, :brand_id, :name)"),
            models,
        )
    logger.info("Inserted %d models", len(models))

    # 3. Kits
    kits = read_kits(csv_dir)
    for i in range(0, len(kits), BATCH_SIZE):
        batch = kits[i : i + BATCH_SIZE]
        async with engine.begin() as conn:
            await conn.execute(
                text("""
                    INSERT INTO vehicle_kits
                        (id, model_id, year, name, pcd, bolt_count, dia, bolt_size)
                    VALUES
                        (:id, :model_id, :year, :name, :pcd, :bolt_count, :dia, :bolt_size)
                """),
                batch,
            )
        logger.info("Inserted kits batch %d-%d", i, i + len(batch))
    logger.info("Inserted %d kits total", len(kits))

    # 4. Tire sizes (large — batch insert)
    tire_sizes = read_tire_sizes(csv_dir)
    for i in range(0, len(tire_sizes), BATCH_SIZE):
        batch = tire_sizes[i : i + BATCH_SIZE]
        async with engine.begin() as conn:
            await conn.execute(
                text("""
                    INSERT INTO vehicle_tire_sizes
                        (id, kit_id, width, height, diameter, type, axle, axle_group)
                    VALUES
                        (:id, :kit_id, :width, :height, :diameter, :type, :axle, :axle_group)
                """),
                batch,
            )
        if (i // BATCH_SIZE) % 10 == 0:
            logger.info("Inserted tire_sizes batch %d-%d", i, i + len(batch))
    logger.info("Inserted %d tire sizes total", len(tire_sizes))

    # 5. Metadata
    async with engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO vehicle_db_metadata
                    (brand_count, model_count, kit_count, tire_size_count, source_path)
                VALUES (:brands, :models, :kits, :tire_sizes, :source)
            """),
            {
                "brands": len(brands),
                "models": len(models),
                "kits": len(kits),
                "tire_sizes": len(tire_sizes),
                "source": str(csv_dir),
            },
        )
    logger.info(
        "Import complete: %d brands, %d models, %d kits, %d tire sizes",
        len(brands), len(models), len(kits), len(tire_sizes),
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Import vehicle tire size DB from CSV")
    parser.add_argument("--csv-dir", default=DEFAULT_CSV_DIR, help="Path to CSV directory")
    args = parser.parse_args()

    csv_dir = Path(args.csv_dir)
    if not csv_dir.is_dir():
        logger.error("CSV directory not found: %s", csv_dir)
        sys.exit(1)

    settings = get_settings()
    engine = create_async_engine(settings.database.url, pool_size=5)
    try:
        await import_data(engine, csv_dir)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
