"""Tyre test places of the found tyres (``tire_tests`` / ``tire_test_placements``).

The tshina Data API sync (migration 065, ``src/integrations/tshina_sync.py``)
keeps tyre tests (ADAC, Auto Bild, Auto Zeitung, ACE …) with all their
places. ``attach_tire_tests`` puts on each found catalogue item up to
``MAX_TESTS`` tests of its model as ``tests``:
``[{"source", "year", "place", "of", "size"}]`` — ``of`` is the number of
places in the test, ``size`` the test size the place was taken in.

The rule is tshina's ``ShownModelSourceFilter`` (0338abd38): a test in the
item's own size first, then one of the same rim diameter; a test of another
diameter (or with no readable size) is not taken. Within a tier the newer
test goes first. Only the place is kept: ``rating`` is a JSONB word
(«Дуже добре»), not a number, and its scale differs between sources.

The model link (prod 2026-10-26): ``tire_test_placements.model_1c_id`` =
``tire_products.model_id``, by the item's SKU; a place with a NULL
``model_1c_id`` or a NULL ``place`` is skipped. One query per search result,
for all its SKUs at once.

On request only (R08): the compressor never hands ``tests`` to the LLM; the
places are said by the code (``tyre_compare``) when the caller asks which
tyre is better or what to take — and only under sales, the only scope that
keeps a search result there. A database error → a warning and items without
``tests``; an item without a test carries no ``tests`` key at all.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

#: Tests per model — «одна строка на модель, максимум 2 теста» (R08).
MAX_TESTS = 2

_SIZE = re.compile(r"(?<!\d)(\d{3})\s*/\s*(\d{2})\s*Z?R\s*[FC]?\s*(\d{2})(?!\d)", re.IGNORECASE)

_QUERY = """
    SELECT p.sku AS sku, tp.test_id AS test_id, tp.place AS place,
           t.source_title AS source_title, t.source_key AS source_key,
           t.year AS year, t.sizes AS sizes,
           (SELECT COUNT(*) FROM tire_test_placements c
             WHERE c.test_id = tp.test_id AND c.place IS NOT NULL) AS places
    FROM tire_test_placements tp
    JOIN tire_tests t ON t.id = tp.test_id
    JOIN tire_products p ON p.model_id = tp.model_1c_id
    WHERE p.sku IN ({skus}) AND tp.place IS NOT NULL
"""


def parse_size(text: Any) -> tuple[int, int, int] | None:
    """``(width, height, diameter)`` of «225/50 R17 94W» / «225/50ZR17», or ``None``."""
    m = _SIZE.search(str(text or ""))
    if m is None:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _test_sizes(raw: Any) -> list[tuple[tuple[int | None, int | None, int | None], str]]:
    """The sizes of a test row: ``[((w, h, d), "225/50 R17")]`` (JSONB list)."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    if not isinstance(raw, list):
        return []
    out = []
    for s in raw:
        if not isinstance(s, dict):
            continue
        dims = (_int(s.get("width")), _int(s.get("height")), _int(s.get("diameter")))
        label = str(s.get("raw") or "").strip()
        if dims[2] is None and label:
            parsed = parse_size(label)
            if parsed is not None:
                dims = parsed
        if not label and None not in dims:
            label = f"{dims[0]}/{dims[1]} R{dims[2]}"
        out.append((dims, label))
    return out


def _tier(size: tuple[int, int, int], row: dict[str, Any]) -> tuple[int, str] | None:
    """0 — a test in the item's size, 1 — the same diameter; other → ``None``."""
    best: tuple[int, str] | None = None
    for (width, height, diameter), label in _test_sizes(row.get("sizes")):
        if diameter != size[2]:
            continue
        tier = 0 if (width, height) == (size[0], size[1]) else 1
        if best is None or tier < best[0]:
            best = (tier, label)
    return best


def pick_tests(size: tuple[int, int, int], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Up to ``MAX_TESTS`` tests of one item: its size first, then its diameter."""
    ranked: list[tuple[int, int, int, dict[str, Any]]] = []
    seen: set[Any] = set()
    for row in rows:
        place = _int(row.get("place"))
        if place is None or place <= 0 or row.get("test_id") in seen:
            continue
        tier = _tier(size, row)
        if tier is None:
            continue
        seen.add(row.get("test_id"))
        year = _int(row.get("year"))
        test: dict[str, Any] = {
            "source": str(row.get("source_title") or row.get("source_key") or "").strip(),
            "place": place,
        }
        if year:
            test["year"] = year
        places = _int(row.get("places"))
        if places and places >= place:
            test["of"] = places
        if tier[1]:
            test["size"] = tier[1]
        ranked.append((tier[0], -(year or 0), -(_int(row.get("test_id")) or 0), test))
    ranked.sort(key=lambda r: r[:3])
    return [r[3] for r in ranked[:MAX_TESTS]]


async def attach_tire_tests(engine: Any, result: Any) -> None:
    """Put ``tests`` on the items of a catalogue ``search_tires`` result, in place."""
    if engine is None or not isinstance(result, dict):
        return
    items = [i for i in result.get("items") or () if isinstance(i, dict) and i.get("id")]
    if not items:
        return
    from sqlalchemy import text

    skus = sorted({str(i["id"]) for i in items})
    params = {f"sku_{n}": sku for n, sku in enumerate(skus)}
    query = text(_QUERY.format(skus=", ".join(f":{k}" for k in params)))
    try:
        async with engine.connect() as conn:
            rows = list((await conn.execute(query, params)).mappings().all())
    except Exception:
        logger.warning("tire tests: lookup failed, items go without tests", exc_info=True)
        return
    by_sku: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        row = dict(row)
        if row.get("sku") is not None:
            by_sku.setdefault(str(row["sku"]), []).append(row)
    for item in items:
        size = parse_size(item.get("size"))
        own = by_sku.get(str(item["id"]))
        if size is None or not own:
            continue
        tests = pick_tests(size, own)
        if tests:
            item["tests"] = tests
