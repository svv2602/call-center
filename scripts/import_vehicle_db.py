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


# --- Corrupted Cyrillic decoder ---
# Source CSVs contain names where Cyrillic was encoded as:
#   uppercase: chr(0x10 + position)  А=0x10, Б=0x11, ..., Я=0x2F
#   lowercase: chr(0x30 + position)  а=0x30, б=0x31, ..., я=0x4F
# This overlaps with ASCII printable range (0x20-0x7E), so detection uses
# control chars (0x10-0x1F) as a reliable signal of corruption.

_CYRILLIC_UPPER = "АБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ"  # 32 letters, no Ё
_CYRILLIC_LOWER = "абвгдежзийклмнопрстуфхцчшщъыьэюя"

_DECODE_MAP: dict[int, str] = {}
for _i, _ch in enumerate(_CYRILLIC_UPPER):
    _DECODE_MAP[0x10 + _i] = _ch
for _i, _ch in enumerate(_CYRILLIC_LOWER):
    _DECODE_MAP[0x30 + _i] = _ch

_CYRILLIC_UPPER_SET = set(_CYRILLIC_UPPER)


def _has_corrupted_cyrillic(value: str) -> bool:
    """Detect corrupted Cyrillic: control chars 0x10-0x1F in the string."""
    return any(0x10 <= ord(c) <= 0x1F for c in value)


def _fix_corrupted_cyrillic(value: str) -> str:
    """Decode corrupted Cyrillic in a string.

    If ALL chars are in the decodable range (0x10-0x4F) AND the decoded result
    starts with uppercase Cyrillic, decode everything (brand names ВАЗ, model
    names Калина).  Otherwise decode only control chars (0x10-0x1F) to uppercase
    Cyrillic and clean up CSV quoting artifacts.
    """
    is_fully_encoded = all(0x10 <= ord(c) <= 0x4F for c in value)
    if is_fully_encoded:
        decoded = "".join(_DECODE_MAP[ord(c)] for c in value)
        # Validate: true Cyrillic words start with uppercase letter
        if decoded[0] in _CYRILLIC_UPPER_SET:
            return decoded
        # Not valid Cyrillic — fall through to mixed decode

    # Mixed: only decode control chars, leave ASCII as-is
    result = "".join(_DECODE_MAP.get(ord(c), c) if 0x10 <= ord(c) <= 0x1F else c for c in value)
    # Strip trailing '"' — artifact of Cyrillic Т (0x22) at CSV boundary
    result = result.rstrip('"')
    # Strip corrupted brand name prefix: digits + Cyrillic uppercase + space
    # e.g. "03АЗ Accent" → "Accent" (prefix is corrupted "ТагАЗ" with leading Т eaten)
    m = re.match(r"^[\d]+[А-Я]+\s+(.+)$", result)
    if m:
        result = m.group(1)
    return result


def _try_full_cyrillic_decode(value: str) -> str | None:
    """Try to decode a string as fully-encoded corrupted Cyrillic.

    Some corrupted names use only chars in the 0x20-0x4F range (ASCII printable),
    so _has_corrupted_cyrillic() doesn't detect them (e.g. "!>1>;L" = "Соболь").

    Returns decoded string if ALL chars map to Cyrillic AND the result starts
    with an uppercase letter (valid word pattern). Returns None otherwise,
    meaning the string is likely a legitimate ASCII model code (e.g. "2101").
    """
    if not value or len(value) < 3:
        return None
    if not all(ord(c) in _DECODE_MAP for c in value):
        return None
    decoded = "".join(_DECODE_MAP[ord(c)] for c in value)
    # Valid Cyrillic word starts with uppercase
    if decoded[0] in _CYRILLIC_UPPER_SET:
        return decoded
    return None


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


def read_brands(csv_dir: Path) -> tuple[list[dict[str, Any]], set[int]]:
    """Read vehicle brands CSV.

    Returns (rows, corrupted_brand_ids) where corrupted_brand_ids is the set
    of brand IDs whose names had corrupted Cyrillic encoding.  Models under
    these brands need aggressive Cyrillic decoding (see _try_full_cyrillic_decode).
    """
    rows = []
    corrupted_ids: set[int] = set()
    path = csv_dir / "test_table_car2_brand.csv"
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            brand_id = int(row["id"])
            name = row["name"]
            if _has_corrupted_cyrillic(name):
                name = _fix_corrupted_cyrillic(name)
                corrupted_ids.add(brand_id)
            rows.append({"id": brand_id, "name": name})
    if corrupted_ids:
        logger.info(
            "Fixed %d brand names with corrupted Cyrillic (brand IDs: %s)",
            len(corrupted_ids),
            sorted(corrupted_ids),
        )
    logger.info("Read %d brands from %s", len(rows), path.name)
    return rows, corrupted_ids


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


def read_models(
    csv_dir: Path,
    brand_names: set[str],
    corrupted_brand_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Read vehicle models CSV, filtering out bogus rows.

    The source CSV contains ~167 rows where brand names are erroneously
    listed as models (all under brand_id=138/Alpine, with no kits).

    Two-pass filtering:
    1. Remove orphan models (no kits) whose name matches a brand name.
    2. If a brand lost ≥10 orphans in pass 1, remove ALL remaining orphans
       for that brand — they're part of the same data corruption
       (catches GAZ, UAZ, VAZ, Citroën encoding variant, etc.).

    For models under corrupted_brand_ids, applies aggressive Cyrillic decoding
    to catch names without control chars (e.g. "!>1>;L" → "Соболь").
    """
    path = csv_dir / "test_table_car2_model.csv"
    brand_names_lower = {n.lower() for n in brand_names}
    brand_names_norm = {_normalize_name(n) for n in brand_names}
    kit_model_ids = _read_kit_model_ids(csv_dir)
    corrupted_brand_ids = corrupted_brand_ids or set()

    # Pass 1: read all rows, mark orphan brand-name matches
    all_rows: list[dict[str, Any]] = []
    full_decode_count = 0
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            model_id = int(row["id"])
            brand_id = int(row["brand"])
            name = row["name"]
            if _has_corrupted_cyrillic(name):
                name = _fix_corrupted_cyrillic(name)
            elif brand_id in corrupted_brand_ids:
                # Aggressive decode for models under corrupted brands:
                # try full Cyrillic decode even without control chars
                decoded = _try_full_cyrillic_decode(name)
                if decoded:
                    full_decode_count += 1
                    name = decoded
            is_orphan = model_id not in kit_model_ids
            is_brand = is_orphan and _is_brand_name(name, brand_names_lower, brand_names_norm)
            all_rows.append(
                {
                    "id": model_id,
                    "brand_id": brand_id,
                    "name": name,
                    "_orphan": is_orphan,
                    "_brand_match": is_brand,
                }
            )
    if full_decode_count:
        logger.info(
            "Decoded %d model names via full Cyrillic decode (no control chars)", full_decode_count
        )

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
            rows.append(
                {
                    "id": int(row["id"]),
                    "model_id": int(row["model"]),
                    "year": int(row["year"]),
                    "name": _clean_control_chars(row.get("name", "")),
                    "pcd": _decimal_from_csv(row.get("pcd", "")),
                    "bolt_count": _smallint_or_none(row.get("bolt_count", "")),
                    "dia": _decimal_from_csv(row.get("dia", "")),
                    "bolt_size": bolt_size if bolt_size else None,
                }
            )
    logger.info("Read %d kits from %s", len(rows), path.name)
    return rows


def read_tire_sizes(csv_dir: Path) -> list[dict[str, Any]]:
    """Read vehicle tire sizes CSV."""
    rows = []
    path = csv_dir / "test_table_car2_kit_tyre_size.csv"
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(
                {
                    "id": int(row["id"]),
                    "kit_id": int(row["kit"]),
                    "width": _int_from_csv(row["width"]),
                    "height": _int_from_csv(row["height"]),
                    "diameter": Decimal(row["diameter"]),
                    "type": int(row["type"]),
                    "axle": int(row["axle"]),
                    "axle_group": _smallint_or_none(row.get("axle_group", "")),
                }
            )
    logger.info("Read %d tire sizes from %s", len(rows), path.name)
    return rows


async def _load_existing_brands(conn: Any) -> dict[int, dict[str, Any]]:
    """Return {id: {name, source}} for every existing brand."""
    result = await conn.execute(text("SELECT id, name, source FROM vehicle_brands"))
    return {row.id: {"name": row.name, "source": row.source} for row in result}


async def _load_existing_models(conn: Any) -> dict[int, dict[str, Any]]:
    """Return {id: {brand_id, name, source}} for every existing model."""
    result = await conn.execute(text("SELECT id, brand_id, name, source FROM vehicle_models"))
    return {
        row.id: {"brand_id": row.brand_id, "name": row.name, "source": row.source}
        for row in result
    }


def _diff_brands(
    csv_brands: list[dict[str, Any]], existing: dict[int, dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """Compare CSV rows to current DB state.

    Returns dict with keys:
      added            — rows in CSV, not in DB (will INSERT)
      updated          — rows in both, name changed, DB source != 'manual' (will UPDATE)
      skipped_manual   — rows in both, DB source == 'manual' (SKIP — preserve manual edits)
      missing_in_source — DB rows (source='auto_import') absent from CSV — NOT deleted, just reported
    """
    csv_ids: set[int] = set()
    added: list[dict[str, Any]] = []
    updated: list[dict[str, Any]] = []
    skipped_manual: list[dict[str, Any]] = []

    for row in csv_brands:
        bid = row["id"]
        csv_ids.add(bid)
        current = existing.get(bid)
        if current is None:
            added.append(row)
        elif current["source"] == "manual":
            skipped_manual.append(row)
        elif current["name"] != row["name"]:
            updated.append(row)

    missing_in_source = [
        {"id": bid, "name": info["name"]}
        for bid, info in existing.items()
        if bid > 0 and bid not in csv_ids and info["source"] != "manual"
    ]

    return {
        "added": added,
        "updated": updated,
        "skipped_manual": skipped_manual,
        "missing_in_source": missing_in_source,
    }


def _diff_models(
    csv_models: list[dict[str, Any]], existing: dict[int, dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """Compare CSV rows to current DB state (analogous to _diff_brands, adds brand_id compare)."""
    csv_ids: set[int] = set()
    added: list[dict[str, Any]] = []
    updated: list[dict[str, Any]] = []
    skipped_manual: list[dict[str, Any]] = []

    for row in csv_models:
        mid = row["id"]
        csv_ids.add(mid)
        current = existing.get(mid)
        if current is None:
            added.append(row)
        elif current["source"] == "manual":
            skipped_manual.append(row)
        elif current["name"] != row["name"] or current["brand_id"] != row["brand_id"]:
            updated.append(row)

    missing_in_source = [
        {"id": mid, "name": info["name"], "brand_id": info["brand_id"]}
        for mid, info in existing.items()
        if mid > 0 and mid not in csv_ids and info["source"] != "manual"
    ]

    return {
        "added": added,
        "updated": updated,
        "skipped_manual": skipped_manual,
        "missing_in_source": missing_in_source,
    }


async def _apply_brand_diff(conn: Any, diff: dict[str, list[dict[str, Any]]]) -> None:
    """Upsert brands: insert new + update changed. skipped_manual and missing_in_source untouched."""
    if diff["added"]:
        await conn.execute(
            text(
                "INSERT INTO vehicle_brands (id, name, source) "
                "VALUES (:id, :name, 'auto_import')"
            ),
            diff["added"],
        )
    if diff["updated"]:
        await conn.execute(
            text(
                "UPDATE vehicle_brands SET name = :name, updated_at = now() "
                "WHERE id = :id AND source != 'manual'"
            ),
            diff["updated"],
        )


async def _apply_model_diff(conn: Any, diff: dict[str, list[dict[str, Any]]]) -> None:
    """Upsert models: insert new + update changed. skipped_manual and missing_in_source untouched."""
    if diff["added"]:
        await conn.execute(
            text(
                "INSERT INTO vehicle_models (id, brand_id, name, source) "
                "VALUES (:id, :brand_id, :name, 'auto_import')"
            ),
            diff["added"],
        )
    if diff["updated"]:
        await conn.execute(
            text(
                "UPDATE vehicle_models SET name = :name, brand_id = :brand_id, updated_at = now() "
                "WHERE id = :id AND source != 'manual'"
            ),
            diff["updated"],
        )


async def _refresh_kits_and_sizes(
    conn: Any, kits: list[dict[str, Any]], tire_sizes: list[dict[str, Any]]
) -> tuple[int, int]:
    """Delete all source-imported kits (id>0) and reinsert from CSV.

    Kits + tire_sizes have no 'manual' concept in Wave 8 — they're source-only.
    CASCADE on kit_id wipes tire_sizes automatically.

    Returns (kit_count, tire_size_count) inserted.
    """
    # CASCADE deletes tire_sizes tied to these kits
    await conn.execute(text("DELETE FROM vehicle_kits WHERE id > 0"))

    for i in range(0, len(kits), BATCH_SIZE):
        batch = kits[i : i + BATCH_SIZE]
        await conn.execute(
            text("""
                INSERT INTO vehicle_kits
                    (id, model_id, year, name, pcd, bolt_count, dia, bolt_size)
                VALUES
                    (:id, :model_id, :year, :name, :pcd, :bolt_count, :dia, :bolt_size)
            """),
            batch,
        )

    for i in range(0, len(tire_sizes), BATCH_SIZE):
        batch = tire_sizes[i : i + BATCH_SIZE]
        await conn.execute(
            text("""
                INSERT INTO vehicle_tire_sizes
                    (id, kit_id, width, height, diameter, type, axle, axle_group)
                VALUES
                    (:id, :kit_id, :width, :height, :diameter, :type, :axle, :axle_group)
            """),
            batch,
        )

    return len(kits), len(tire_sizes)


async def _record_import_history(
    conn: Any,
    mode: str,
    status: str,
    source_path: str,
    archive_name: str | None,
    triggered_by: str | None,
    counts: dict[str, int],
    error_message: str | None = None,
    diff_report: dict[str, Any] | None = None,
) -> int:
    """Insert vehicle_import_history row and return its id."""
    import json as _json

    result = await conn.execute(
        text("""
            INSERT INTO vehicle_import_history (
                mode, status, source_path, archive_name, triggered_by,
                brand_added, brand_updated, brand_skipped_manual, brand_missing_in_source,
                model_added, model_updated, model_skipped_manual, model_missing_in_source,
                kit_added, kit_deleted, tire_size_added, tire_size_deleted,
                aliases_regenerated, error_message, diff_report_json,
                finished_at
            )
            VALUES (
                :mode, :status, :source_path, :archive_name, :triggered_by,
                :b_add, :b_upd, :b_skip, :b_miss,
                :m_add, :m_upd, :m_skip, :m_miss,
                :k_add, :k_del, :t_add, :t_del,
                :aliases, :err, CAST(:diff AS JSONB),
                CASE WHEN :status = 'running' THEN NULL ELSE now() END
            )
            RETURNING id
        """),
        {
            "mode": mode,
            "status": status,
            "source_path": source_path,
            "archive_name": archive_name,
            "triggered_by": triggered_by,
            "b_add": counts.get("brand_added", 0),
            "b_upd": counts.get("brand_updated", 0),
            "b_skip": counts.get("brand_skipped_manual", 0),
            "b_miss": counts.get("brand_missing_in_source", 0),
            "m_add": counts.get("model_added", 0),
            "m_upd": counts.get("model_updated", 0),
            "m_skip": counts.get("model_skipped_manual", 0),
            "m_miss": counts.get("model_missing_in_source", 0),
            "k_add": counts.get("kit_added", 0),
            "k_del": counts.get("kit_deleted", 0),
            "t_add": counts.get("tire_size_added", 0),
            "t_del": counts.get("tire_size_deleted", 0),
            "aliases": counts.get("aliases_regenerated", 0),
            "err": error_message,
            "diff": _json.dumps(diff_report) if diff_report else None,
        },
    )
    return int(result.scalar_one())


def _summarise_diff(
    brand_diff: dict[str, list[dict[str, Any]]],
    model_diff: dict[str, list[dict[str, Any]]],
    kits_count: int,
    tire_sizes_count: int,
) -> dict[str, Any]:
    """Produce compact JSON-serialisable diff summary (samples, not full lists)."""
    _SAMPLE = 20  # cap examples so history JSON stays small

    return {
        "brands": {
            "added_count": len(brand_diff["added"]),
            "updated_count": len(brand_diff["updated"]),
            "skipped_manual_count": len(brand_diff["skipped_manual"]),
            "missing_in_source_count": len(brand_diff["missing_in_source"]),
            "added_sample": [b["name"] for b in brand_diff["added"][:_SAMPLE]],
            "updated_sample": [
                {"id": b["id"], "new_name": b["name"]} for b in brand_diff["updated"][:_SAMPLE]
            ],
            "missing_in_source_sample": [
                {"id": b["id"], "name": b["name"]}
                for b in brand_diff["missing_in_source"][:_SAMPLE]
            ],
        },
        "models": {
            "added_count": len(model_diff["added"]),
            "updated_count": len(model_diff["updated"]),
            "skipped_manual_count": len(model_diff["skipped_manual"]),
            "missing_in_source_count": len(model_diff["missing_in_source"]),
            "added_sample": [m["name"] for m in model_diff["added"][:_SAMPLE]],
            "updated_sample": [
                {"id": m["id"], "new_name": m["name"]} for m in model_diff["updated"][:_SAMPLE]
            ],
        },
        "kits": {"csv_count": kits_count},
        "tire_sizes": {"csv_count": tire_sizes_count},
    }


async def import_data(
    engine: AsyncEngine,
    csv_dir: Path,
    mode: str = "apply",
    triggered_by: str | None = None,
    archive_name: str | None = None,
    reuse_history_id: int | None = None,
) -> dict[str, Any]:
    """Import (or dry-run) vehicle DB from CSV files.

    Preserves rows with ``source='manual'`` (added or edited via admin UI).
    Kits and tire_sizes have no manual concept — they're always fully refreshed.

    Args:
        engine: Async SQLAlchemy engine.
        csv_dir: Directory with the 4 required CSVs (see EXPECTED_CSV_FILES).
        mode: ``"apply"`` (default) applies changes; ``"dryrun"`` computes diff only.
        triggered_by: UUID of admin user who initiated the import (for audit log).
        archive_name: Original filename of uploaded ZIP (for display in history).
        reuse_history_id: Reuse an existing dryrun history row when mode='apply'
            (upload→dryrun→apply flow). Row is updated in place; no new row.
            Ignored for mode='dryrun'.

    Returns:
        dict with ``history_id``, ``mode``, ``status``, and per-entity ``counts``
        plus a compact ``diff_report`` for UI display.
    """
    if mode not in ("apply", "dryrun"):
        raise ValueError(f"mode must be 'apply' or 'dryrun', got {mode!r}")

    logger.info("Starting %s import from %s", mode, csv_dir)

    # Read CSVs (same helpers as before — Cyrillic decoding untouched)
    brands, corrupted_brand_ids = read_brands(csv_dir)
    brand_names = {b["name"] for b in brands}
    models = read_models(csv_dir, brand_names, corrupted_brand_ids)
    kits = read_kits(csv_dir)
    tire_sizes = read_tire_sizes(csv_dir)

    async with engine.begin() as conn:
        existing_brands = await _load_existing_brands(conn)
        existing_models = await _load_existing_models(conn)

        brand_diff = _diff_brands(brands, existing_brands)
        model_diff = _diff_models(models, existing_models)

        # Count of kit/tire_size rows that would be removed (source-imported only)
        result = await conn.execute(text("SELECT COUNT(*) FROM vehicle_kits WHERE id > 0"))
        kit_to_delete = int(result.scalar_one() or 0)
        result = await conn.execute(
            text("SELECT COUNT(*) FROM vehicle_tire_sizes ts "
                 "WHERE ts.kit_id IN (SELECT id FROM vehicle_kits WHERE id > 0)")
        )
        tire_size_to_delete = int(result.scalar_one() or 0)

        counts = {
            "brand_added": len(brand_diff["added"]),
            "brand_updated": len(brand_diff["updated"]),
            "brand_skipped_manual": len(brand_diff["skipped_manual"]),
            "brand_missing_in_source": len(brand_diff["missing_in_source"]),
            "model_added": len(model_diff["added"]),
            "model_updated": len(model_diff["updated"]),
            "model_skipped_manual": len(model_diff["skipped_manual"]),
            "model_missing_in_source": len(model_diff["missing_in_source"]),
            "kit_added": len(kits) if mode == "apply" else 0,
            "kit_deleted": kit_to_delete if mode == "apply" else 0,
            "tire_size_added": len(tire_sizes) if mode == "apply" else 0,
            "tire_size_deleted": tire_size_to_delete if mode == "apply" else 0,
            "aliases_regenerated": 0,  # populated by generate_aliases.py in Phase 2
        }

        diff_summary = _summarise_diff(brand_diff, model_diff, len(kits), len(tire_sizes))

        if mode == "dryrun":
            history_id = await _record_import_history(
                conn=conn,
                mode="dryrun",
                status="dryrun",
                source_path=str(csv_dir),
                archive_name=archive_name,
                triggered_by=triggered_by,
                counts=counts,
                diff_report=diff_summary,
            )
            logger.info(
                "Dry-run complete: brands +%d/~%d (skip manual %d, missing %d), "
                "models +%d/~%d (skip manual %d, missing %d)",
                counts["brand_added"], counts["brand_updated"],
                counts["brand_skipped_manual"], counts["brand_missing_in_source"],
                counts["model_added"], counts["model_updated"],
                counts["model_skipped_manual"], counts["model_missing_in_source"],
            )
            return {
                "history_id": history_id,
                "mode": "dryrun",
                "status": "dryrun",
                "counts": counts,
                "diff_report": diff_summary,
            }

    # mode == "apply" — record 'running' first, then do work in its own transactions
    async with engine.begin() as conn:
        if reuse_history_id is not None:
            # Upgrade a previous dryrun row → running (upload→dryrun→apply flow)
            await conn.execute(
                text("""
                    UPDATE vehicle_import_history
                    SET mode='apply', status='running', started_at=now(),
                        finished_at=NULL, error_message=NULL,
                        triggered_by=COALESCE(:tb, triggered_by),
                        source_path=:sp, archive_name=COALESCE(:an, archive_name)
                    WHERE id=:id
                """),
                {
                    "id": reuse_history_id,
                    "tb": triggered_by,
                    "sp": str(csv_dir),
                    "an": archive_name,
                },
            )
            history_id = reuse_history_id
        else:
            history_id = await _record_import_history(
                conn=conn,
                mode="apply",
                status="running",
                source_path=str(csv_dir),
                archive_name=archive_name,
                triggered_by=triggered_by,
                counts=counts,
                diff_report=diff_summary,
            )

    try:
        async with engine.begin() as conn:
            await _apply_brand_diff(conn, brand_diff)
            await _apply_model_diff(conn, model_diff)
        logger.info(
            "Applied brands: +%d added, %d updated (skipped %d manual, %d missing in source)",
            counts["brand_added"], counts["brand_updated"],
            counts["brand_skipped_manual"], counts["brand_missing_in_source"],
        )
        logger.info(
            "Applied models: +%d added, %d updated (skipped %d manual, %d missing in source)",
            counts["model_added"], counts["model_updated"],
            counts["model_skipped_manual"], counts["model_missing_in_source"],
        )

        # Kits + tire_sizes: full refresh (source-only, no manual). Own transaction
        # because bulk-delete + 1.2M row insert takes 30-60s.
        async with engine.begin() as conn:
            k_inserted, ts_inserted = await _refresh_kits_and_sizes(conn, kits, tire_sizes)
        logger.info("Refreshed kits: %d, tire_sizes: %d", k_inserted, ts_inserted)

        # Legacy metadata (still read by /admin/vehicles/stats fallback)
        async with engine.begin() as conn:
            await conn.execute(
                text("""
                    INSERT INTO vehicle_db_metadata
                        (brand_count, model_count, kit_count, tire_size_count, source_path)
                    VALUES (:b, :m, :k, :t, :s)
                """),
                {
                    "b": len(brands), "m": len(models),
                    "k": len(kits), "t": len(tire_sizes), "s": str(csv_dir),
                },
            )

            # Mark history complete
            await conn.execute(
                text(
                    "UPDATE vehicle_import_history SET status='completed', finished_at=now() "
                    "WHERE id=:id"
                ),
                {"id": history_id},
            )
    except Exception as exc:
        logger.exception("Import failed after history_id=%s", history_id)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE vehicle_import_history "
                    "SET status='failed', finished_at=now(), error_message=:err "
                    "WHERE id=:id"
                ),
                {"id": history_id, "err": str(exc)[:2000]},
            )
        raise

    logger.info(
        "Import complete (history_id=%d): %d brands, %d models, %d kits, %d tire sizes",
        history_id, len(brands), len(models), len(kits), len(tire_sizes),
    )
    return {
        "history_id": history_id,
        "mode": "apply",
        "status": "completed",
        "counts": counts,
        "diff_report": diff_summary,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Import vehicle tire size DB from CSV")
    parser.add_argument("--csv-dir", default=DEFAULT_CSV_DIR, help="Path to CSV directory")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute diff report without applying changes. Records a 'dryrun' history row.",
    )
    args = parser.parse_args()

    csv_dir = Path(args.csv_dir)
    if not csv_dir.is_dir():
        logger.error("CSV directory not found: %s", csv_dir)
        sys.exit(1)

    settings = get_settings()
    engine = create_async_engine(settings.database.url, pool_size=5)
    try:
        result = await import_data(
            engine, csv_dir, mode="dryrun" if args.dry_run else "apply"
        )
        logger.info("Result: %s", {k: v for k, v in result.items() if k != "diff_report"})
    finally:
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
