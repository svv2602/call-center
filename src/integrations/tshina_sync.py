"""Nightly sync of tshina Data API resources into our tables.

Resources and targets:

- ``eu-labels``  → ``tire_eu_labels`` (``source='tshina'``; a row with any
  value off the EU scale is rejected whole — ``scripts/import_tire_labels.parse_row``);
- ``tire-tests`` → ``tire_tests`` + ``tire_test_placements`` (places rewritten
  whole with the test; the source object kept as sent, ``higher_is_better``
  included — it is null for every source today and nothing here reads a
  scale direction from it);
- ``vehicles/brands → models → modifications → tire-sizes → disk-sizes`` →
  ``vehicle_brands/models/kits/tire_sizes/disk_sizes`` by car2 id, with the
  rules of ``scripts/import_vehicle_db.py``: ``source='manual'`` brands and
  models are never overwritten, Hongqi (229) and KG Mobility (231) and all
  their descendants are skipped, a kit-less model named like a brand is
  skipped, a child whose parent is not in our table is skipped and counted.

One transaction per page. A page that fails rolls back alone; the resource
stops, ``data_sync_state.last_error`` is written, the error is counted and
logged at ERROR, and the watermark stays where it was — the next run walks
the same range again (every write is an idempotent upsert). The watermark
moves only after the whole walk is applied, to ``server_time`` of its first
page.

Full snapshot (weekly): everything is upserted, then rows we hold that the
snapshot lacks are deleted — only ``tire_eu_labels.source='tshina'``, tyre
tests and non-manual directory rows — and only when the snapshot has at
least 90 % of our rows; otherwise nothing is deleted, the resource is marked
failed and an ERROR is logged (a truncated answer must not wipe our data).
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from src.integrations.tshina_api import RESOURCES, TshinaApiError, parse_server_time

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from src.integrations.tshina_api import TshinaApiClient

logger = logging.getLogger(__name__)

#: Hongqi duplicates Red Flag, KG Mobility duplicates Ssang Yong: with them
#: Tivoli/Rexton/Torres become ambiguous across brands.
SKIP_BRAND_IDS = frozenset({229, 231})
#: A full snapshot smaller than this share of our rows deletes nothing.
SNAPSHOT_MIN_SHARE = 0.9
SEASONS = frozenset({"summer", "winter", "all_season", "off_road"})
ADVISORY_LOCK_KEY = 72_409_291  # pg_try_advisory_lock: one sync at a time
DELETE_BATCH = 5000
VEHICLE_RESOURCES = tuple(r for r in RESOURCES if r.startswith("vehicles/"))


class RowRejectedError(ValueError):
    """An item that breaks the contract; the whole row is dropped."""


class SnapshotGuardError(RuntimeError):
    """A full snapshot too small to delete by."""


# ── value checks ────────────────────────────────────────────────────────────


def _int(value: Any, lo: int, hi: int, name: str) -> int:
    if isinstance(value, bool):
        raise RowRejectedError(f"{name}: bool")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int) or not lo <= value <= hi:
        raise RowRejectedError(f"{name}: {value!r}")
    return value


def _opt_int(value: Any, lo: int, hi: int, name: str) -> int | None:
    return None if value is None else _int(value, lo, hi, name)


def _num(value: Any, lo: float, hi: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise RowRejectedError(f"{name}: {value!r}")
    if not math.isfinite(value) or not lo <= value <= hi:
        raise RowRejectedError(f"{name}: {value!r}")
    return float(value)


def _dec(value: float) -> Any:
    from decimal import Decimal

    return Decimal(str(value))


def _opt_num(value: Any, lo: float, hi: float, name: str) -> Any:
    return None if value is None else _dec(_num(value, lo, hi, name))


def _str(value: Any, max_len: int, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_len or "\x00" in value:
        raise RowRejectedError(f"{name}: {value!r}")
    return value.strip()


def _opt_str(value: Any, max_len: int, name: str) -> str | None:
    if value is None or value == "":
        return None
    return _str(value, max_len, name)


def _id(value: Any, name: str = "id") -> int:
    return _int(value, 1, 2_147_483_647, name)


def _json_obj(value: Any, name: str, *, allow_str: bool = False) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict) or (allow_str and isinstance(value, str)):
        return json.dumps(value, ensure_ascii=False)
    raise RowRejectedError(f"{name}: {value!r}")


def _opt_datetime(value: Any, name: str) -> datetime | None:
    if value is None:
        return None
    try:
        return parse_server_time(value)
    except TshinaApiError as exc:
        raise RowRejectedError(f"{name}: {value!r}") from exc


# ── item parsers: API item → DB row (or RowRejectedError) ────────────────────────


def parse_label(item: dict[str, Any]) -> dict[str, Any]:
    """EU label; the EU-scale rules are those of ``import_tire_labels.parse_row``."""
    from scripts.import_tire_labels import parse_row

    sku = _str(item.get("sku"), 50, "sku")
    noise = item.get("noise_db")
    if isinstance(noise, bool) or not isinstance(noise, int):
        raise RowRejectedError(f"noise_db: {noise!r}")
    classes = {k: item.get(k) for k in ("energy_class", "wet_grip_class", "noise_class")}
    if not all(isinstance(v, str) for v in classes.values()):
        raise RowRejectedError(f"classes: {classes!r}")
    eprel = item.get("eprel_number")
    if eprel is not None and not isinstance(eprel, str):
        raise RowRejectedError(f"eprel_number: {eprel!r}")
    label = parse_row(
        {
            "sku": sku,
            "energy_class": classes["energy_class"],
            "wet_grip_class": classes["wet_grip_class"],
            "external_rolling_noise_value": str(noise),
            "external_rolling_noise_class": classes["noise_class"],
            "eprel_registration_number": eprel or "",
        }
    )
    if label is None:
        raise RowRejectedError(f"label off the EU scale: {item!r}")
    row = dict(label.__dict__)
    row["eprel_number"] = row["eprel_number"] or None
    return row


def _placement(p: Any, test_id: int) -> dict[str, Any]:
    if not isinstance(p, dict):
        raise RowRejectedError(f"placement: {p!r}")
    model_1c_id = _opt_str(p.get("model_1c_id"), 50, "model_1c_id")
    brand = _opt_str(p.get("brand"), 100, "brand")
    model = _opt_str(p.get("model"), 200, "model")
    if model_1c_id is None and (brand is None or model is None):
        raise RowRejectedError("placement without model_1c_id needs brand and model")
    criteria = p.get("criteria")
    if criteria is None:
        criteria = []
    if not isinstance(criteria, list):
        raise RowRejectedError(f"criteria: {criteria!r}")
    return {
        "test_id": test_id,
        "place": _opt_int(p.get("place"), 0, 32767, "place"),
        "brand": brand,
        "model": model,
        "model_1c_id": model_1c_id,
        "rating": _json_obj(p.get("rating"), "rating"),
        "notes": _json_obj(p.get("notes"), "notes", allow_str=True),
        "criteria": json.dumps(criteria, ensure_ascii=False),
    }


def parse_tire_test(item: dict[str, Any]) -> dict[str, Any]:
    """One tyre test with all its places; any bad place rejects the whole test."""
    test_id = _id(item.get("id"))
    source = item.get("source")
    if not isinstance(source, dict):
        raise RowRejectedError(f"source: {source!r}")
    season = item.get("season")
    if season is not None and season not in SEASONS:
        raise RowRejectedError(f"season: {season!r}")
    raw_date = item.get("test_date")
    test_date: date | None = None
    if raw_date is not None:
        try:
            test_date = date.fromisoformat(raw_date) if isinstance(raw_date, str) else None
        except ValueError:
            test_date = None
        if test_date is None:
            raise RowRejectedError(f"test_date: {raw_date!r}")
    sizes = item.get("sizes")
    if sizes is None and isinstance(item.get("size"), dict):
        sizes = [item["size"]]  # the pre-contract request shape
    if sizes is None:
        sizes = []
    if not isinstance(sizes, list) or not all(isinstance(s, dict) for s in sizes):
        raise RowRejectedError(f"sizes: {sizes!r}")
    placements = item.get("placements") or []
    if not isinstance(placements, list):
        raise RowRejectedError(f"placements: {placements!r}")
    return {
        "id": test_id,
        "source_key": _str(source.get("key"), 64, "source.key"),
        "source_title": _opt_str(source.get("title"), 200, "source.title"),
        # kept as sent: scale_min / scale_max / higher_is_better (null = direction unknown)
        "source": json.dumps(source, ensure_ascii=False),
        "year": _opt_int(item.get("year"), 1900, 2100, "year"),
        "season": season,
        "test_date": test_date,
        "sizes": json.dumps(sizes, ensure_ascii=False),
        "notes": _json_obj(item.get("notes"), "notes", allow_str=True),
        "updated_at": _opt_datetime(item.get("updated_at"), "updated_at"),
        "placements": [_placement(p, test_id) for p in placements],
    }


def parse_brand(item: dict[str, Any]) -> dict[str, Any]:
    return {"id": _id(item.get("id")), "name": _str(item.get("name"), 100, "name")}


def parse_model(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _id(item.get("id")),
        "brand_id": _id(item.get("brand_id"), "brand_id"),
        "name": _str(item.get("name"), 200, "name"),
    }


def parse_modification(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _id(item.get("id")),
        "model_id": _id(item.get("model_id"), "model_id"),
        "year": _int(item.get("year"), 1900, 2100, "year"),
        "name": _opt_str(item.get("name"), 200, "name"),
        "pcd": _opt_num(item.get("pcd"), 0, 9999, "pcd"),
        "bolt_count": _opt_int(item.get("bolt_count"), 0, 100, "bolt_count"),
        "dia": _opt_num(item.get("dia"), 0, 9999, "dia"),
        "bolt_size": _opt_str(item.get("bolt_size"), 50, "bolt_size"),
    }


def _axles(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": _int(item.get("type"), 0, 32767, "type"),
        "axle": _int(item.get("axle"), 0, 32767, "axle"),
        # as sent: car2 keeps 0 for «no group or group 0», the API does the same
        "axle_group": _opt_int(item.get("axle_group"), 0, 32767, "axle_group"),
    }


def parse_tire_size(item: dict[str, Any]) -> dict[str, Any]:
    height = item.get("height")
    return {
        "id": _id(item.get("id")),
        "kit_id": _id(item.get("modification_id"), "modification_id"),
        "width": _int(item.get("width"), 1, 999, "width"),
        # full-profile sizes come as null; our column is NOT NULL and holds 0 for them
        "height": 0 if height is None else _int(height, 0, 999, "height"),
        "diameter": _dec(_num(item.get("diameter"), 1, 99, "diameter")),
        **_axles(item),
    }


def parse_disk_size(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _id(item.get("id")),
        "kit_id": _id(item.get("modification_id"), "modification_id"),
        "width": _dec(_num(item.get("width"), 0.5, 99, "width")),
        "diameter": _dec(_num(item.get("diameter"), 1, 99, "diameter")),
        "et": _opt_num(item.get("et"), -999, 999, "et"),
        **_axles(item),
    }


# ── page parsing ─────────────────────────────────────────────────────────────


@dataclass
class ParsedPage:
    upserts: list[dict[str, Any]] = field(default_factory=list)
    deletes: list[Any] = field(default_factory=list)
    rejected: int = 0
    #: every live key in the page, rejected rows included — the snapshot view
    live_keys: set[Any] = field(default_factory=set)


def _item_key(item: dict[str, Any], key: str) -> Any:
    value = item.get(key)
    if key == "sku":
        return _str(value, 50, "sku")
    return _id(value, key)


def parse_page(spec: ResourceSpec, items: Iterable[dict[str, Any]]) -> ParsedPage:
    """Split a page into upserts / deletes / rejected; last item wins per key."""
    out = ParsedPage()
    by_key: dict[Any, dict[str, Any]] = {}
    deleted: dict[Any, None] = {}
    for item in items:
        try:
            key = _item_key(item, spec.key)
        except RowRejectedError:
            out.rejected += 1
            logger.warning("tshina %s: item without a valid key rejected: %r", spec.name, item)
            continue
        flag = item.get("deleted", False)
        if flag is True:
            by_key.pop(key, None)
            deleted[key] = None
            continue
        if flag not in (False, None):
            out.rejected += 1
            continue
        out.live_keys.add(key)
        deleted.pop(key, None)
        try:
            by_key[key] = spec.parse(item)
        except RowRejectedError as exc:
            by_key.pop(key, None)
            out.rejected += 1
            logger.warning("tshina %s: row %r rejected: %s", spec.name, key, exc)
    out.upserts = list(by_key.values())
    out.deletes = list(deleted)
    return out


# ── appliers: one page into the DB (inside the page transaction) ─────────────


@dataclass
class Applied:
    upserted: int = 0
    skipped: int = 0
    #: brand/model rows added, renamed or deleted — aliases need a rebuild
    changed: int = 0


LABEL_UPSERT = """
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
        updated_at = now()
    WHERE tire_eu_labels.source = 'tshina'
"""

TEST_UPSERT = """
    INSERT INTO tire_tests
        (id, source_key, source_title, source, year, season, test_date, sizes, notes,
         updated_at, synced_at)
    VALUES (:id, :source_key, :source_title, CAST(:source AS JSONB), :year, :season, :test_date,
            CAST(:sizes AS JSONB), CAST(:notes AS JSONB), :updated_at, now())
    ON CONFLICT (id) DO UPDATE SET
        source_key = EXCLUDED.source_key,
        source_title = EXCLUDED.source_title,
        source = EXCLUDED.source,
        year = EXCLUDED.year,
        season = EXCLUDED.season,
        test_date = EXCLUDED.test_date,
        sizes = EXCLUDED.sizes,
        notes = EXCLUDED.notes,
        updated_at = EXCLUDED.updated_at,
        synced_at = now()
"""

PLACEMENT_INSERT = """
    INSERT INTO tire_test_placements
        (test_id, place, brand, model, model_1c_id, rating, notes, criteria)
    VALUES (:test_id, :place, :brand, :model, :model_1c_id, CAST(:rating AS JSONB),
            CAST(:notes AS JSONB), CAST(:criteria AS JSONB))
"""

KIT_UPSERT = """
    INSERT INTO vehicle_kits (id, model_id, year, name, pcd, bolt_count, dia, bolt_size)
    VALUES (:id, :model_id, :year, :name, :pcd, :bolt_count, :dia, :bolt_size)
    ON CONFLICT (id) DO UPDATE SET
        model_id = EXCLUDED.model_id, year = EXCLUDED.year, name = EXCLUDED.name,
        pcd = EXCLUDED.pcd, bolt_count = EXCLUDED.bolt_count, dia = EXCLUDED.dia,
        bolt_size = EXCLUDED.bolt_size
"""

TIRE_SIZE_UPSERT = """
    INSERT INTO vehicle_tire_sizes (id, kit_id, width, height, diameter, type, axle, axle_group)
    VALUES (:id, :kit_id, :width, :height, :diameter, :type, :axle, :axle_group)
    ON CONFLICT (id) DO UPDATE SET
        kit_id = EXCLUDED.kit_id, width = EXCLUDED.width, height = EXCLUDED.height,
        diameter = EXCLUDED.diameter, type = EXCLUDED.type, axle = EXCLUDED.axle,
        axle_group = EXCLUDED.axle_group
"""

DISK_SIZE_UPSERT = """
    INSERT INTO vehicle_disk_sizes (id, kit_id, width, diameter, et, type, axle, axle_group)
    VALUES (:id, :kit_id, :width, :diameter, :et, :type, :axle, :axle_group)
    ON CONFLICT (id) DO UPDATE SET
        kit_id = EXCLUDED.kit_id, width = EXCLUDED.width, diameter = EXCLUDED.diameter,
        et = EXCLUDED.et, type = EXCLUDED.type, axle = EXCLUDED.axle,
        axle_group = EXCLUDED.axle_group
"""


async def _present_ids(conn: Any, table: str, column: str, ids: Iterable[int]) -> set[int]:
    wanted = sorted(set(ids))
    if not wanted:
        return set()
    result = await conn.execute(
        text(f"SELECT DISTINCT {column} AS id FROM {table} WHERE {column} = ANY(:ids)"),
        {"ids": wanted},
    )
    return {row.id for row in result}


async def apply_labels(conn: Any, rows: list[dict[str, Any]]) -> Applied:
    if rows:
        await conn.execute(text(LABEL_UPSERT), rows)
    return Applied(upserted=len(rows))


async def apply_tire_tests(conn: Any, rows: list[dict[str, Any]]) -> Applied:
    if not rows:
        return Applied()
    tests = [{k: v for k, v in r.items() if k != "placements"} for r in rows]
    await conn.execute(text(TEST_UPSERT), tests)
    await conn.execute(
        text("DELETE FROM tire_test_placements WHERE test_id = ANY(:ids)"),
        {"ids": [r["id"] for r in rows]},
    )
    places = [p for r in rows for p in r["placements"]]
    if places:
        await conn.execute(text(PLACEMENT_INSERT), places)
    return Applied(upserted=len(rows))


async def apply_brands(conn: Any, rows: list[dict[str, Any]]) -> Applied:
    from scripts.import_vehicle_db import _apply_brand_diff, _diff_brands

    kept = [r for r in rows if r["id"] not in SKIP_BRAND_IDS]
    skipped = len(rows) - len(kept)
    if not kept:
        return Applied(skipped=skipped)
    result = await conn.execute(
        text("SELECT id, name, source FROM vehicle_brands WHERE id = ANY(:ids)"),
        {"ids": [r["id"] for r in kept]},
    )
    existing = {row.id: {"name": row.name, "source": row.source} for row in result}
    diff = _diff_brands(kept, existing)
    await _apply_brand_diff(conn, diff)
    changed = len(diff["added"]) + len(diff["updated"])
    manual = len(diff["skipped_manual"])
    return Applied(upserted=len(kept) - manual, skipped=skipped + manual, changed=changed)


async def apply_models(conn: Any, rows: list[dict[str, Any]]) -> Applied:
    from scripts.import_vehicle_db import (
        _apply_model_diff,
        _diff_models,
        _is_brand_name,
        _normalize_name,
    )

    candidates = [r for r in rows if r["brand_id"] not in SKIP_BRAND_IDS]
    brands = await _present_ids(conn, "vehicle_brands", "id", (r["brand_id"] for r in candidates))
    candidates = [r for r in candidates if r["brand_id"] in brands]
    if candidates:
        names = {row.name for row in await conn.execute(text("SELECT name FROM vehicle_brands"))}
        names_lower = {n.lower() for n in names}
        names_norm = {_normalize_name(n) for n in names}
        with_kits = await _present_ids(
            conn, "vehicle_kits", "model_id", (r["id"] for r in candidates)
        )
        candidates = [
            r
            for r in candidates
            if r["id"] in with_kits or not _is_brand_name(r["name"], names_lower, names_norm)
        ]
    skipped = len(rows) - len(candidates)
    if not candidates:
        return Applied(skipped=skipped)
    result = await conn.execute(
        text("SELECT id, brand_id, name, source FROM vehicle_models WHERE id = ANY(:ids)"),
        {"ids": [r["id"] for r in candidates]},
    )
    existing = {
        row.id: {"brand_id": row.brand_id, "name": row.name, "source": row.source} for row in result
    }
    diff = _diff_models(candidates, existing)
    await _apply_model_diff(conn, diff)
    changed = len(diff["added"]) + len(diff["updated"])
    manual = len(diff["skipped_manual"])
    return Applied(upserted=len(candidates) - manual, skipped=skipped + manual, changed=changed)


def _child_applier(
    sql: str, parent_table: str, parent_key: str
) -> Callable[[Any, list[dict[str, Any]]], Awaitable[Applied]]:
    async def apply(conn: Any, rows: list[dict[str, Any]]) -> Applied:
        parents = await _present_ids(conn, parent_table, "id", (r[parent_key] for r in rows))
        kept = [r for r in rows if r[parent_key] in parents]
        if kept:
            await conn.execute(text(sql), kept)
        return Applied(upserted=len(kept), skipped=len(rows) - len(kept))

    return apply


# ── deletions and "our rows" per resource ────────────────────────────────────


def _deleter(table: str, key: str, scope: str) -> Callable[[Any, list[Any]], Awaitable[int]]:
    async def delete(conn: Any, keys: list[Any]) -> int:
        total = 0
        for start in range(0, len(keys), DELETE_BATCH):
            chunk = keys[start : start + DELETE_BATCH]
            result = await conn.execute(
                text(f"DELETE FROM {table} WHERE {key} = ANY(:keys) AND {scope}"),
                {"keys": chunk},
            )
            total += int(result.rowcount or 0)
        return total

    return delete


def _our_keys(table: str, key: str, scope: str) -> Callable[[Any], Awaitable[set[Any]]]:
    async def load(conn: Any) -> set[Any]:
        result = await conn.execute(text(f"SELECT {key} AS key FROM {table} WHERE {scope}"))
        return {row.key for row in result}

    return load


@dataclass(frozen=True)
class ResourceSpec:
    name: str
    key: str
    parse: Callable[[dict[str, Any]], dict[str, Any]]
    apply: Callable[[Any, list[dict[str, Any]]], Awaitable[Applied]]
    delete: Callable[[Any, list[Any]], Awaitable[int]]
    our_keys: Callable[[Any], Awaitable[set[Any]]]
    #: deletions of this resource change what aliases are generated from
    alias_source: bool = False


_TSHINA = "source = 'tshina'"
_NOT_MANUAL = "id > 0 AND source <> 'manual'"
#: A brand is swept only when no manual model hangs under it: deleting the
#: brand would cascade to that model (vehicle_models.brand_id ON DELETE CASCADE).
_BRAND_SWEEPABLE = (
    _NOT_MANUAL
    + " AND NOT EXISTS (SELECT 1 FROM vehicle_models vm"
    " WHERE vm.brand_id = vehicle_brands.id AND vm.source = 'manual')"
)
_IMPORTED = "id > 0"

SPECS: dict[str, ResourceSpec] = {
    "eu-labels": ResourceSpec(
        "eu-labels",
        "sku",
        parse_label,
        apply_labels,
        _deleter("tire_eu_labels", "sku", _TSHINA),
        _our_keys("tire_eu_labels", "sku", _TSHINA),
    ),
    "tire-tests": ResourceSpec(
        "tire-tests",
        "id",
        parse_tire_test,
        apply_tire_tests,
        _deleter("tire_tests", "id", "TRUE"),
        _our_keys("tire_tests", "id", "TRUE"),
    ),
    "vehicles/brands": ResourceSpec(
        "vehicles/brands",
        "id",
        parse_brand,
        apply_brands,
        _deleter("vehicle_brands", "id", _BRAND_SWEEPABLE),
        _our_keys("vehicle_brands", "id", _BRAND_SWEEPABLE),
        alias_source=True,
    ),
    "vehicles/models": ResourceSpec(
        "vehicles/models",
        "id",
        parse_model,
        apply_models,
        _deleter("vehicle_models", "id", _NOT_MANUAL),
        _our_keys("vehicle_models", "id", _NOT_MANUAL),
        alias_source=True,
    ),
    "vehicles/modifications": ResourceSpec(
        "vehicles/modifications",
        "id",
        parse_modification,
        _child_applier(KIT_UPSERT, "vehicle_models", "model_id"),
        _deleter("vehicle_kits", "id", _IMPORTED),
        _our_keys("vehicle_kits", "id", _IMPORTED),
    ),
    "vehicles/tire-sizes": ResourceSpec(
        "vehicles/tire-sizes",
        "id",
        parse_tire_size,
        _child_applier(TIRE_SIZE_UPSERT, "vehicle_kits", "kit_id"),
        _deleter("vehicle_tire_sizes", "id", _IMPORTED),
        _our_keys("vehicle_tire_sizes", "id", _IMPORTED),
    ),
    "vehicles/disk-sizes": ResourceSpec(
        "vehicles/disk-sizes",
        "id",
        parse_disk_size,
        _child_applier(DISK_SIZE_UPSERT, "vehicle_kits", "kit_id"),
        _deleter("vehicle_disk_sizes", "id", _IMPORTED),
        _our_keys("vehicle_disk_sizes", "id", _IMPORTED),
    ),
}


def snapshot_allows_delete(snapshot_count: int, our_count: int) -> bool:
    """A snapshot may delete only when it holds at least 90 % of our rows."""
    return snapshot_count >= our_count * SNAPSHOT_MIN_SHARE


# ── state ────────────────────────────────────────────────────────────────────

STATE_SELECT = "SELECT watermark FROM data_sync_state WHERE resource = :resource"
STATE_ATTEMPT = """
    INSERT INTO data_sync_state (resource, last_attempt_at) VALUES (:resource, now())
    ON CONFLICT (resource) DO UPDATE SET last_attempt_at = now()
"""
STATE_SUCCESS = """
    UPDATE data_sync_state
    SET watermark = :watermark, last_success_at = now(), last_error = NULL,
        upserted = :upserted, deleted = :deleted{full}
    WHERE resource = :resource
"""
STATE_ERROR = "UPDATE data_sync_state SET last_error = :error WHERE resource = :resource"


async def load_watermark(conn: Any, resource: str) -> datetime | None:
    result = await conn.execute(text(STATE_SELECT), {"resource": resource})
    row = result.first()
    return None if row is None else row.watermark


# ── the walk ─────────────────────────────────────────────────────────────────


@dataclass
class ResourceResult:
    resource: str
    status: str = "ok"  # ok | error | guarded | skipped | dry_run
    pages: int = 0
    upserted: int = 0
    deleted: int = 0
    rejected: int = 0
    skipped: int = 0
    changed: int = 0
    watermark: datetime | None = None
    error: str | None = None


def _metrics() -> Any:
    from src.monitoring import metrics

    return metrics


def _count(resource: str, op: str, n: int) -> None:
    if n:
        _metrics().data_sync_rows_total.labels(resource=resource, op=op).inc(n)


def _error_kind(exc: BaseException) -> str:
    if isinstance(exc, TshinaApiError):
        return exc.kind
    if isinstance(exc, SnapshotGuardError):
        return "snapshot_guard"
    return "db"


async def sync_resource(
    engine: Any,
    client: TshinaApiClient,
    spec: ResourceSpec,
    *,
    full: bool = False,
    dry_run: bool = False,
    limit: int = 1000,
) -> ResourceResult:
    """Walk one resource and apply it page by page (see the module docstring)."""
    res = ResourceResult(spec.name, status="dry_run" if dry_run else "ok")
    since: datetime | None = None
    if not full:
        async with engine.connect() as conn:
            since = await load_watermark(conn, spec.name)
    if not dry_run:
        async with engine.begin() as conn:
            await conn.execute(text(STATE_ATTEMPT), {"resource": spec.name})

    first_server_time: datetime | None = None
    snapshot: set[Any] = set()
    try:
        async for page in client.iter_pages(spec.name, updated_since=since, limit=limit):
            if first_server_time is None:
                first_server_time = parse_server_time(page.server_time)
            parsed = parse_page(spec, page.items)
            res.pages += 1
            res.rejected += parsed.rejected
            if full:
                snapshot |= parsed.live_keys
            if dry_run:
                res.upserted += len(parsed.upserts)
                res.deleted += len(parsed.deletes)
                continue
            async with engine.begin() as conn:
                applied = await spec.apply(conn, parsed.upserts)
                deleted = await spec.delete(conn, parsed.deletes) if parsed.deletes else 0
            res.upserted += applied.upserted
            res.skipped += applied.skipped
            res.deleted += deleted
            res.changed += applied.changed + (deleted if spec.alias_source else 0)
            _count(spec.name, "upserted", applied.upserted)
            _count(spec.name, "skipped", applied.skipped)
            _count(spec.name, "rejected", parsed.rejected)
            _count(spec.name, "deleted", deleted)

        if full and not dry_run:
            async with engine.begin() as conn:
                ours = await spec.our_keys(conn)
                if not snapshot_allows_delete(len(snapshot), len(ours)):
                    raise SnapshotGuardError(
                        f"snapshot has {len(snapshot)} rows, we hold {len(ours)}: "
                        f"below {SNAPSHOT_MIN_SHARE:.0%}, nothing deleted"
                    )
                stale = sorted(ours - snapshot)
                deleted = await spec.delete(conn, stale) if stale else 0
            res.deleted += deleted
            res.changed += deleted if spec.alias_source else 0
            _count(spec.name, "deleted", deleted)

        if dry_run:
            res.watermark = first_server_time
            return res
        async with engine.begin() as conn:
            await conn.execute(
                text(STATE_SUCCESS.format(full=", full_sync_at = now()" if full else "")),
                {
                    "resource": spec.name,
                    "watermark": first_server_time,
                    "upserted": res.upserted,
                    "deleted": res.deleted,
                },
            )
        res.watermark = first_server_time
        _metrics().data_sync_last_success_timestamp.labels(resource=spec.name).set(time.time())
        return res
    except Exception as exc:
        kind = _error_kind(exc)
        res.status = "guarded" if kind == "snapshot_guard" else "error"
        res.error = f"{kind}: {exc}"[:2000]
        logger.error(
            "tshina sync %s failed after %d pages (%s); watermark kept",
            spec.name,
            res.pages,
            res.error,
            exc_info=kind == "db",
        )
        if not dry_run:
            _metrics().data_sync_errors_total.labels(resource=spec.name, kind=kind).inc()
            async with engine.begin() as conn:
                await conn.execute(text(STATE_ERROR), {"resource": spec.name, "error": res.error})
        return res


async def refresh_aliases(
    engine: Any, regenerate: Callable[[Any], Awaitable[Any]] | None = None
) -> int:
    """Regenerate auto aliases, then count intra-brand collisions (must be 0)."""
    from scripts.fix_vehicle_aliases import _load_current, intra_brand_collisions

    if regenerate is None:
        from scripts.generate_aliases import generate_aliases as regenerate
    await regenerate(engine)
    async with engine.connect() as conn:
        rows = await _load_current(conn)
    collisions = intra_brand_collisions(rows)
    if collisions:
        sample = sorted(collisions.items(), key=lambda kv: kv[0])[:20]
        logger.error("tshina sync: %d intra-brand alias collisions: %s", len(collisions), sample)
        _metrics().data_sync_errors_total.labels(
            resource="vehicles/models", kind="alias_collision"
        ).inc()
    return len(collisions)


async def run_sync(
    engine: Any,
    client: TshinaApiClient,
    resources: Iterable[str] = RESOURCES,
    *,
    full: bool = False,
    dry_run: bool = False,
    limit: int = 1000,
    regenerate: Callable[[Any], Awaitable[Any]] | None = None,
) -> dict[str, Any]:
    """Sync the given resources in contract order under a pg advisory lock.

    A failed ``vehicles/*`` resource stops the rest of the directory chain
    (children of a half-applied parent would only be skipped). Aliases are
    regenerated when brands or models changed.
    """
    wanted = set(resources)
    unknown = wanted - set(RESOURCES)
    if unknown:
        raise ValueError(f"unknown resources: {sorted(unknown)}")
    order = [r for r in RESOURCES if r in wanted]

    async with engine.connect() as lock_conn:
        if not dry_run:
            got = (
                await lock_conn.execute(
                    text("SELECT pg_try_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY}
                )
            ).scalar()
            if not got:
                logger.info("tshina sync: another run holds the lock, skipping")
                return {"status": "locked", "resources": []}
            await lock_conn.commit()  # session lock survives; no idle-in-transaction
        try:
            results: list[ResourceResult] = []
            vehicles_failed = False
            for name in order:
                if vehicles_failed and name in VEHICLE_RESOURCES:
                    results.append(ResourceResult(name, status="skipped", error="parent failed"))
                    continue
                result = await sync_resource(
                    engine, client, SPECS[name], full=full, dry_run=dry_run, limit=limit
                )
                results.append(result)
                if result.status in ("error", "guarded") and name in VEHICLE_RESOURCES:
                    vehicles_failed = True
            changed = sum(r.changed for r in results)
            collisions = None
            if changed and not dry_run:
                collisions = await refresh_aliases(engine, regenerate)
        finally:
            if not dry_run:
                await lock_conn.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY}
                )
                await lock_conn.commit()
    ok = all(r.status in ("ok", "dry_run") for r in results)
    return {
        "status": ("dry_run" if dry_run else "ok") if ok else "error",
        "alias_collisions": collisions,
        "resources": [
            r.__dict__ | {"watermark": r.watermark and r.watermark.isoformat()} for r in results
        ],
    }
