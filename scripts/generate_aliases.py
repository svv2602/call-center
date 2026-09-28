"""Regenerate auto_* vehicle_aliases rows from current brand/model data.

Wipes all rows with source in ('auto_import', 'auto_translit', 'auto_model_name')
and re-inserts fresh aliases derived from vehicle_brands + vehicle_models via
src.agent.vehicle_translit. Rows with source='manual' are preserved untouched.

Typical flow:
    # After CSV re-import
    python -m scripts.import_vehicle_db --csv-dir /path/to/csv
    python -m scripts.generate_aliases

    # Ad-hoc regeneration (after editing BRAND_CYRILLIC_ALIASES)
    python -m scripts.generate_aliases

Idempotent — running twice is a no-op modulo any new brands/models added
between runs. Manual aliases survive across runs.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.agent.vehicle_translit import (
    MODEL_CYRILLIC_ALIASES,
    generate_brand_aliases,
    generate_model_aliases,
    normalize_alias,
)
from src.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BATCH_SIZE = 5_000

_AUTO_SOURCES = ("auto_import", "auto_translit", "auto_model_name")


async def _load_brands(conn: Any) -> list[dict[str, Any]]:
    result = await conn.execute(text("SELECT id, name FROM vehicle_brands ORDER BY id"))
    return [{"id": row.id, "name": row.name} for row in result]


async def _load_models(conn: Any) -> list[dict[str, Any]]:
    result = await conn.execute(
        text("""
            SELECT m.id, m.brand_id, m.name,
                   (SELECT COUNT(*) FROM vehicle_kits k WHERE k.model_id = m.id) AS kits
            FROM vehicle_models m ORDER BY m.id
        """)
    )
    return [
        {"id": row.id, "brand_id": row.brand_id, "name": row.name, "kits": row.kits}
        for row in result
    ]


async def _wipe_auto_aliases(conn: Any) -> int:
    """Delete all rows with an auto_* source. Manual rows survive.

    Returns the number of rows deleted.
    """
    result = await conn.execute(
        text(
            "DELETE FROM vehicle_aliases "
            "WHERE source = ANY(:sources) RETURNING id"
        ),
        {"sources": list(_AUTO_SOURCES)},
    )
    return len(result.fetchall())


async def _insert_alias_rows(conn: Any, rows: list[dict[str, Any]]) -> int:
    """Batch-insert alias rows with ON CONFLICT DO NOTHING.

    Uses target-less ON CONFLICT so it fires for either of the partial UNIQUE
    indexes (ux_vehicle_aliases_brand_only, ux_vehicle_aliases_brand_model).
    ON CONFLICT ON CONSTRAINT cannot target partial indexes in Postgres.

    Returns number of INSERT statements executed (SQLAlchemy asyncpg doesn't
    surface per-row inserted status for executemany + ON CONFLICT — actual
    inserted rows may be lower if source data has internal duplicates).
    """
    if not rows:
        return 0

    inserted = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        await conn.execute(
            text("""
                INSERT INTO vehicle_aliases
                    (alias, alias_normalized, brand_id, model_id, source, confidence)
                VALUES
                    (:alias, :alias_normalized, :brand_id, :model_id, :source, :confidence)
                ON CONFLICT DO NOTHING
            """),
            batch,
        )
        inserted += len(batch)

    return inserted


def _build_brand_alias_rows(brands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Generate alias rows for all brands. Uses vehicle_translit.generate_brand_aliases."""
    rows: list[dict[str, Any]] = []
    for brand in brands:
        variants = generate_brand_aliases(brand["name"])
        for alias, source in variants:
            rows.append(
                {
                    "alias": alias,
                    "alias_normalized": normalize_alias(alias),
                    "brand_id": brand["id"],
                    "model_id": None,
                    "source": source,
                    "confidence": None,
                }
            )
    return rows


def _primary_models(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One model per (brand, name): the catalogue has same-name duplicates
    (Mercedes ``GLA-Class`` ×2 — 6 and 170 kits). Aliases on both make every
    alias of that name ambiguous (``find_model_by_alias`` → None), so only the
    one with the most kits (tie → lowest id) gets aliases."""
    best: dict[tuple[int, str], dict[str, Any]] = {}
    for model in models:
        key = (model["brand_id"], normalize_alias(model["name"]))
        cur = best.get(key)
        rank = (-(model.get("kits") or 0), model["id"])
        if cur is None or rank < (-(cur.get("kits") or 0), cur["id"]):
            best[key] = model
    keep = {m["id"] for m in best.values()}
    return [m for m in models if m["id"] in keep]


def _alias_tier(model_name: str, alias_normalized: str) -> int:
    """0 — the model's own name, 1 — hand-curated, 2 — char-by-char translit."""
    if alias_normalized == normalize_alias(model_name):
        return 0
    hand = {normalize_alias(a) for a in MODEL_CYRILLIC_ALIASES.get(model_name, [])}
    return 1 if alias_normalized in hand else 2


def drop_model_alias_collisions(
    rows: list[dict[str, Any]], names: dict[int, str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Default-deny: an alias pointing at >1 model of one brand is kept only
    for the single model whose claim is strongest (own name > hand-curated >
    translit); a tie at the top drops the alias for every model.

    Returns ``(kept, dropped)``. ``names`` maps model_id → model name.
    """
    groups: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault((row["brand_id"], row["alias_normalized"]), []).append(row)
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for group in groups.values():
        if len({r["model_id"] for r in group}) <= 1:
            kept.extend(group)
            continue
        tiers = {
            r["model_id"]: _alias_tier(names[r["model_id"]], r["alias_normalized"]) for r in group
        }
        top = min(tiers.values())
        winners = {mid for mid, t in tiers.items() if t == top}
        if len(winners) == 1:
            kept.extend(r for r in group if r["model_id"] in winners)
            dropped.extend(r for r in group if r["model_id"] not in winners)
        else:
            dropped.extend(group)
    return kept, dropped


def _build_model_alias_rows(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Generate alias rows for all models. Uses vehicle_translit.generate_model_aliases.

    Same-name duplicates get aliases once (``_primary_models``); an alias
    that two models of a brand would share is resolved by
    ``drop_model_alias_collisions`` (GLC-Class ≠ GLK-Class).
    """
    primary = _primary_models(models)
    rows: list[dict[str, Any]] = []
    for model in primary:
        variants = generate_model_aliases(model["name"])
        for alias, source in variants:
            rows.append(
                {
                    "alias": alias,
                    "alias_normalized": normalize_alias(alias),
                    "brand_id": model["brand_id"],
                    "model_id": model["id"],
                    "source": source,
                    "confidence": None,
                }
            )
    kept, _dropped = drop_model_alias_collisions(rows, {m["id"]: m["name"] for m in primary})
    return kept


async def _update_last_history(conn: Any, count: int) -> None:
    """Stamp aliases_regenerated on the most recent vehicle_import_history row.

    Best-effort: if no history exists yet (fresh install), skip silently.
    Only touches the most recent row so ad-hoc runs after edits update it too.
    """
    await conn.execute(
        text("""
            UPDATE vehicle_import_history
            SET aliases_regenerated = :n
            WHERE id = (SELECT id FROM vehicle_import_history ORDER BY started_at DESC LIMIT 1)
        """),
        {"n": count},
    )


async def generate_aliases(engine: AsyncEngine) -> dict[str, int]:
    """Regenerate auto_* aliases. Manual aliases preserved.

    Returns counts dict: brands_processed, models_processed, aliases_deleted,
    aliases_inserted.
    """
    logger.info("Loading brands and models from DB")
    async with engine.begin() as conn:
        brands = await _load_brands(conn)
        models = await _load_models(conn)

    logger.info("Loaded %d brands, %d models", len(brands), len(models))

    brand_rows = _build_brand_alias_rows(brands)
    model_rows = _build_model_alias_rows(models)
    logger.info(
        "Generated %d brand alias rows and %d model alias rows",
        len(brand_rows), len(model_rows),
    )

    async with engine.begin() as conn:
        deleted = await _wipe_auto_aliases(conn)
        logger.info("Deleted %d existing auto_* alias rows (manual rows preserved)", deleted)

        brand_inserted = await _insert_alias_rows(conn, brand_rows)
        model_inserted = await _insert_alias_rows(conn, model_rows)
        total_inserted = brand_inserted + model_inserted
        logger.info(
            "Inserted %d alias rows (brands: %d, models: %d)",
            total_inserted, brand_inserted, model_inserted,
        )

        await _update_last_history(conn, total_inserted)

    # Post-check: ambiguity report
    async with engine.begin() as conn:
        ambiguity = await conn.execute(
            text("""
                SELECT alias_normalized, COUNT(DISTINCT brand_id) AS brand_count
                FROM vehicle_aliases
                GROUP BY alias_normalized
                HAVING COUNT(DISTINCT brand_id) > 1
                ORDER BY brand_count DESC, alias_normalized
                LIMIT 20
            """)
        )
        ambiguous = [(row.alias_normalized, row.brand_count) for row in ambiguity]

    if ambiguous:
        logger.info(
            "Cross-brand ambiguous aliases (top 20): %s",
            [f"{a}={n}" for a, n in ambiguous],
        )

    return {
        "brands_processed": len(brands),
        "models_processed": len(models),
        "aliases_deleted": deleted,
        "aliases_inserted": total_inserted,
        "cross_brand_ambiguous_top20": len(ambiguous),
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Regenerate auto_* vehicle_aliases rows")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print how many rows would be generated without touching the DB.",
    )
    args = parser.parse_args()

    settings = get_settings()
    engine = create_async_engine(settings.database.url, pool_size=5)

    try:
        if args.dry_run:
            async with engine.begin() as conn:
                brands = await _load_brands(conn)
                models = await _load_models(conn)
            brand_rows = _build_brand_alias_rows(brands)
            model_rows = _build_model_alias_rows(models)
            print(f"[DRY-RUN] Would generate {len(brand_rows)} brand aliases + "
                  f"{len(model_rows)} model aliases from {len(brands)} brands, "
                  f"{len(models)} models")
        else:
            result = await generate_aliases(engine)
            print(f"Result: {result}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
