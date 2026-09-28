"""Bring existing ``vehicle_aliases`` auto_* rows in line with the generator.

Dry-run by default: prints which rows would be deleted and which added, and
which intra-brand collisions (one ``alias_normalized`` → several models of a
brand) remain afterwards. ``--apply`` writes the diff in one transaction.

What changes against the rows on prod (2026-09-28):
- letter-code models keep letter sounds: ``Глк-клас`` no longer points at
  GLC-Class (it becomes ``Глц-клас``), so GLK-Class owns ``глк-клас``;
- an alias two models of one brand would share is dropped (or kept for the
  one with the strongest claim — own name > hand-curated > translit);
- same-name duplicates (two ``GLA-Class`` rows) get aliases only on the one
  with the most kits, so ``Гла-клас`` resolves instead of being ambiguous.

``manual`` rows are never touched. Usage::

    python -m scripts.fix_vehicle_aliases            # dry-run
    python -m scripts.fix_vehicle_aliases --apply    # write (runner, at deploy)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from scripts.generate_aliases import (
    _AUTO_SOURCES,
    _build_brand_alias_rows,
    _build_model_alias_rows,
    _insert_alias_rows,
    _load_brands,
    _load_models,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

AliasKey = tuple[str, int, int | None]


def _key(row: dict[str, Any]) -> AliasKey:
    return (row["alias_normalized"], row["brand_id"], row["model_id"])


def plan_alias_fix(
    current: Iterable[dict[str, Any]], desired: Iterable[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Diff current rows against generated ones.

    ``current`` rows carry ``id`` and ``source``; only auto_* rows are ever
    deleted. A desired row whose key already exists (any source, manual
    included) is not added. Returns ``(to_delete, to_add)``.
    """
    current = list(current)
    desired_keys = {_key(r) for r in desired}
    current_keys = {_key(r) for r in current}
    to_delete = [r for r in current if r["source"] in _AUTO_SOURCES and _key(r) not in desired_keys]
    seen: set[AliasKey] = set()
    to_add: list[dict[str, Any]] = []
    for r in desired:
        k = _key(r)
        if k in current_keys or k in seen:
            continue
        seen.add(k)
        to_add.append(r)
    return to_delete, to_add


def intra_brand_collisions(rows: Iterable[dict[str, Any]]) -> dict[tuple[int, str], set[int]]:
    """(brand_id, alias_normalized) → model ids, where more than one."""
    groups: dict[tuple[int, str], set[int]] = {}
    for r in rows:
        if r["model_id"] is None:
            continue
        groups.setdefault((r["brand_id"], r["alias_normalized"]), set()).add(r["model_id"])
    return {k: v for k, v in groups.items() if len(v) > 1}


def _label(row: dict[str, Any], brands: dict[int, str], models: dict[int, str]) -> str:
    model = models.get(row["model_id"], "—") if row["model_id"] is not None else "(марка)"
    return f"{brands.get(row['brand_id'], row['brand_id'])} | {model} | {row['alias_normalized']}"


async def _load_current(conn: Any) -> list[dict[str, Any]]:
    result = await conn.execute(
        text("SELECT id, alias, alias_normalized, brand_id, model_id, source FROM vehicle_aliases")
    )
    return [dict(row._mapping) for row in result]


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--apply", action="store_true", help="write the diff (default: dry-run)")
    args = parser.parse_args()

    from sqlalchemy.ext.asyncio import create_async_engine

    from src.config import get_settings

    engine = create_async_engine(get_settings().database.url, pool_size=2)
    try:
        async with engine.connect() as conn:
            brands = await _load_brands(conn)
            models = await _load_models(conn)
            current = await _load_current(conn)

        brand_names = {b["id"]: b["name"] for b in brands}
        model_names = {m["id"]: m["name"] for m in models}
        desired = _build_brand_alias_rows(brands) + _build_model_alias_rows(models)
        to_delete, to_add = plan_alias_fix(current, desired)

        for r in sorted(to_delete, key=lambda r: _label(r, brand_names, model_names)):
            print(f"DELETE id={r['id']} {_label(r, brand_names, model_names)}")
        for r in sorted(to_add, key=lambda r: _label(r, brand_names, model_names)):
            print(f"ADD    {_label(r, brand_names, model_names)} [{r['source']}]")

        delete_ids = {r["id"] for r in to_delete}
        after = [r for r in current if r["id"] not in delete_ids] + to_add
        left = intra_brand_collisions(after)
        for (bid, alias), mids in sorted(left.items(), key=lambda kv: kv[0][1]):
            names = " | ".join(sorted(model_names.get(m, str(m)) for m in mids))
            print(f"COLLISION-LEFT {brand_names.get(bid, bid)} | {alias} → {names}")
        print(
            f"Итого: удалить {len(to_delete)}, добавить {len(to_add)}, "
            f"коллизий до {len(intra_brand_collisions(current))} → после {len(left)}"
        )

        if not args.apply:
            print("[DRY-RUN] ничего не записано; --apply для записи")
            return
        async with engine.begin() as conn:
            if delete_ids:
                await conn.execute(
                    text("DELETE FROM vehicle_aliases WHERE id = ANY(:ids)"),
                    {"ids": sorted(delete_ids)},
                )
            await _insert_alias_rows(conn, to_add)
        print(f"[APPLY] удалено {len(delete_ids)}, добавлено {len(to_add)}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
