"""Import EU tyre labels into ``tire_eu_labels`` from a tshina export.

The neighbour tshina catalogue keeps the labels (from EPREL) keyed by the
same 1C product code as our ``tire_products.sku``. Export on the machine that
has the tshina database::

    mysql -B -e "SELECT p.sku, l.energy_сlass, l.wet_grip_class,
        l.external_rolling_noise_value, l.external_rolling_noise_class,
        l.eprel_registration_number
      FROM catalog_products p
      JOIN catalog_product_euro_labels l ON l.product_id = p.id" > labels.tsv

then here (dry-run by default)::

    python -m scripts.import_tire_labels labels.tsv            # report only
    python -m scripts.import_tire_labels labels.tsv --apply    # upsert

Rows whose SKU is not in ``tire_products`` are skipped (reported). A row
with a value outside the EU scale is skipped whole, never half-written.
Labels already in the table and absent from the file are kept: a rerun adds
and refreshes, it never deletes.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CLASSES = frozenset("ABCDEFG")
NOISE_CLASSES = frozenset("ABC")
#: The header of the tshina export, in order (see the module docstring).
COLUMNS = (
    "sku",
    "energy_сlass",  # sic: the tshina column name has a Cyrillic «с»
    "wet_grip_class",
    "external_rolling_noise_value",
    "external_rolling_noise_class",
    "eprel_registration_number",
)


@dataclass(frozen=True)
class Label:
    sku: str
    energy_class: str
    wet_grip_class: str
    noise_db: int
    noise_class: str
    eprel_number: str


def parse_row(row: dict[str, str]) -> Label | None:
    """One export row → a label, or ``None`` when any value is off the EU scale."""
    sku = (row.get("sku") or "").strip()
    energy = (row.get("energy_сlass") or row.get("energy_class") or "").strip().upper()
    wet = (row.get("wet_grip_class") or "").strip().upper()
    noise_class = (row.get("external_rolling_noise_class") or "").strip().upper()
    noise_raw = (row.get("external_rolling_noise_value") or "").strip()
    eprel = (row.get("eprel_registration_number") or "").strip()
    if not sku or energy not in CLASSES or wet not in CLASSES or noise_class not in NOISE_CLASSES:
        return None
    if not noise_raw.isdigit() or not 50 <= int(noise_raw) <= 90:
        return None
    return Label(sku, energy, wet, int(noise_raw), noise_class, eprel[:32])


def read_labels(path: Path) -> tuple[list[Label], int]:
    """Parsed labels (last row wins per SKU) and the count of rejected rows."""
    by_sku: dict[str, Label] = {}
    rejected = 0
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            label = parse_row(row)
            if label is None:
                rejected += 1
                continue
            by_sku[label.sku] = label
    return list(by_sku.values()), rejected


UPSERT_SQL = """
    INSERT INTO tire_eu_labels
        (sku, energy_class, wet_grip_class, noise_db, noise_class, eprel_number, source, updated_at)
    VALUES (:sku, :energy_class, :wet_grip_class, :noise_db, :noise_class, :eprel_number,
            'tshina', now())
    ON CONFLICT (sku) DO UPDATE SET
        energy_class = EXCLUDED.energy_class,
        wet_grip_class = EXCLUDED.wet_grip_class,
        noise_db = EXCLUDED.noise_db,
        noise_class = EXCLUDED.noise_class,
        eprel_number = EXCLUDED.eprel_number,
        source = EXCLUDED.source,
        updated_at = now()
"""


async def run(path: Path, apply: bool) -> int:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from src.config import get_settings

    labels, rejected = read_labels(path)
    engine = create_async_engine(get_settings().database.url)
    try:
        async with engine.connect() as conn:
            known = {r.sku for r in await conn.execute(text("SELECT sku FROM tire_products"))}
            existing = {r.sku for r in await conn.execute(text("SELECT sku FROM tire_eu_labels"))}
            ours = [lb for lb in labels if lb.sku in known]
            new = sum(1 for lb in ours if lb.sku not in existing)
            print(
                f"file: {len(labels)} labels ({rejected} rows rejected: off the EU scale); "
                f"in tire_products: {len(ours)}; new: {new}; refreshed: {len(ours) - new}; "
                f"not in our catalogue (skipped): {len(labels) - len(ours)}"
            )
            if not apply:
                print("DRY-RUN: nothing written. Re-run with --apply.")
                return 0
            params: list[dict[str, Any]] = [lb.__dict__ for lb in ours]
            for start in range(0, len(params), 1000):
                await conn.execute(text(UPSERT_SQL), params[start : start + 1000])
            await conn.commit()
            total = (await conn.execute(text("SELECT count(*) FROM tire_eu_labels"))).scalar()
            print(f"APPLIED: upserted {len(ours)}; tire_eu_labels now {total} rows")
    finally:
        await engine.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("path", type=Path, help="tab-separated tshina export (see the docstring)")
    parser.add_argument("--apply", action="store_true", help="write (default: dry-run)")
    args = parser.parse_args(argv)
    return asyncio.run(run(args.path, args.apply))


if __name__ == "__main__":
    sys.exit(main())
