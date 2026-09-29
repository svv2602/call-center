"""tshina sync: parsing, page application, watermark, snapshot guard, switches.

The DB is a hand-written in-memory fake that interprets the exact SQL the
module sends (unknown SQL fails the test) and keeps transactions honest:
effects of a ``begin()`` block land only when the block exits cleanly.
WHERE scopes (``source <> 'manual'``, ``source = 'tshina'``, ``id > 0``) are
evaluated from the SQL text, so dropping one from the code changes behaviour
here too.
"""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from src.integrations import tshina_sync as ts
from src.integrations.tshina_api import Page, TshinaApiClient, TshinaApiError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

OLD_WM = datetime(2026, 9, 20, 2, 0, tzinfo=UTC)
ST1 = "2026-09-29T02:00:00Z"
ST2 = "2026-09-29T02:00:05Z"


# ── fake DB ──────────────────────────────────────────────────────────────────


class Row(SimpleNamespace):
    """Attribute access plus ``_mapping`` like a SQLAlchemy Row."""

    @property
    def _mapping(self) -> dict[str, Any]:
        return dict(vars(self))


class Result:
    def __init__(self, rows: list[dict[str, Any]] | None = None, rowcount: int = 0) -> None:
        self._rows = [Row(**r) for r in rows or []]
        self.rowcount = rowcount

    def __iter__(self) -> Any:
        return iter(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalar(self) -> Any:
        return next(iter(vars(self._rows[0]).values())) if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)


#: The brand sweep's guard: no manual model under the brand (tshina_sync._BRAND_SWEEPABLE).
_MANUAL_CHILD = re.compile(
    r" AND NOT EXISTS \(SELECT 1 FROM vehicle_models vm WHERE vm\.brand_id = "
    r"vehicle_brands\.id AND vm\.source = 'manual'\)"
)


def _scope_ok(
    row: dict[str, Any], scope: str, tables: dict[str, dict[Any, dict[str, Any]]] | None = None
) -> bool:
    if _MANUAL_CHILD.search(scope):
        scope = _MANUAL_CHILD.sub("", scope)
        models = (tables or {}).get("vehicle_models", {}).values()
        if any(m.get("brand_id") == row.get("id") and m.get("source") == "manual" for m in models):
            return False
    for clause in scope.split(" AND "):
        clause = clause.strip()
        if clause == "TRUE":
            continue
        m = re.fullmatch(r"(\w+) (=|<>|!=|>) '?([^']*)'?", clause)
        assert m, f"unmodelled clause {clause!r}"
        col, op, val = m.groups()
        cur = row.get(col)
        if op == ">":
            ok = cur is not None and cur > int(val)
        elif op == "=":
            ok = str(cur) == val
        else:
            ok = str(cur) != val
        if not ok:
            return False
    return True


KEYS = {"tire_eu_labels": "sku"}


class FakeDb:
    def __init__(self) -> None:
        self.t: dict[str, dict[Any, dict[str, Any]]] = {
            name: {}
            for name in (
                "tire_eu_labels",
                "tire_tests",
                "tire_test_placements",
                "vehicle_brands",
                "vehicle_models",
                "vehicle_kits",
                "vehicle_tire_sizes",
                "vehicle_disk_sizes",
                "data_sync_state",
                "vehicle_aliases",
            )
        }
        self.lock_free = True
        self.fail_on: str | None = None
        self.fail_after = 0  # let N matching statements pass first
        self.log: list[str] = []
        self.commits = 0
        self.rollbacks = 0

    def add(self, table: str, **row: Any) -> None:
        key = row[KEYS.get(table, "id")]
        self.t[table][key] = row

    # engine API
    @asynccontextmanager
    async def begin(self) -> AsyncIterator[FakeConn]:
        conn = FakeConn(self)
        try:
            yield conn
        except BaseException:
            self.rollbacks += 1
            raise
        for effect in conn.effects:
            effect()
        self.commits += 1

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[FakeConn]:
        yield FakeConn(self, autocommit=True)


class FakeConn:
    def __init__(self, db: FakeDb, autocommit: bool = False) -> None:
        self.db = db
        self.effects: list[Any] = []
        self.autocommit = autocommit

    async def commit(self) -> None:
        return None

    def _effect(self, fn: Any) -> None:
        if self.autocommit:
            fn()
        else:
            self.effects.append(fn)

    async def execute(self, stmt: Any, params: Any = None) -> Result:
        sql = " ".join(str(stmt).split())
        self.db.log.append(sql)
        if self.db.fail_on and self.db.fail_on in sql:
            if self.db.fail_after <= 0:
                raise RuntimeError(f"db failure on {self.db.fail_on}")
            self.db.fail_after -= 1
        t = self.db.t
        many = params if isinstance(params, list) else [params or {}]
        p = many[0]

        if "pg_try_advisory_lock" in sql:
            return Result([{"v": self.db.lock_free}])
        if "pg_advisory_unlock" in sql:
            return Result([{"v": True}])
        if sql.startswith("SELECT watermark FROM data_sync_state"):
            row = t["data_sync_state"].get(p["resource"])
            return Result([{"watermark": row.get("watermark")}] if row else [])
        if sql.startswith("INSERT INTO data_sync_state"):
            self._effect(
                lambda: (
                    t["data_sync_state"]
                    .setdefault(p["resource"], {"resource": p["resource"]})
                    .update(last_attempt_at="now")
                )
            )
            return Result()
        if sql.startswith("UPDATE data_sync_state SET watermark"):
            full = "full_sync_at" in sql
            self._effect(
                lambda: t["data_sync_state"][p["resource"]].update(
                    watermark=p["watermark"],
                    last_error=None,
                    last_success_at="now",
                    upserted=p["upserted"],
                    deleted=p["deleted"],
                    **({"full_sync_at": "now"} if full else {}),
                )
            )
            return Result()
        if sql.startswith("UPDATE data_sync_state SET last_error"):
            self._effect(lambda: t["data_sync_state"][p["resource"]].update(last_error=p["error"]))
            return Result()

        m = re.fullmatch(r"SELECT DISTINCT (\w+) AS id FROM (\w+) WHERE \w+ = ANY\(:ids\)", sql)
        if m:
            col, table = m.groups()
            vals = {r.get(col, k) for k, r in t[table].items()}
            return Result([{"id": i} for i in p["ids"] if i in vals])
        m = re.fullmatch(r"SELECT (\w+) AS key FROM (\w+) WHERE (.+)", sql)
        if m:
            _key, table, scope = m.groups()
            return Result([{"key": k} for k, r in t[table].items() if _scope_ok(r, scope, t)])
        m = re.fullmatch(r"DELETE FROM (\w+) WHERE (\w+) = ANY\(:keys\) AND (.+)", sql)
        if m:
            table, _key, scope = m.groups()
            hit = [k for k in p["keys"] if k in t[table] and _scope_ok(t[table][k], scope, t)]

            def _del() -> None:
                for k in hit:
                    t[table].pop(k, None)
                    if table == "tire_tests":
                        for pk in [
                            pk for pk, pr in t["tire_test_placements"].items() if pr["test_id"] == k
                        ]:
                            del t["tire_test_placements"][pk]

            self._effect(_del)
            return Result(rowcount=len(hit))
        if sql == "SELECT name FROM vehicle_brands":
            return Result([{"name": r["name"]} for r in t["vehicle_brands"].values()])
        if sql.startswith("SELECT id, name, source FROM vehicle_brands WHERE id = ANY"):
            return Result(
                [{"id": i, **t["vehicle_brands"][i]} for i in p["ids"] if i in t["vehicle_brands"]]
            )
        if sql.startswith("SELECT id, brand_id, name, source FROM vehicle_models WHERE id = ANY"):
            return Result(
                [{"id": i, **t["vehicle_models"][i]} for i in p["ids"] if i in t["vehicle_models"]]
            )
        m = re.match(r"INSERT INTO (vehicle_brands|vehicle_models) \(", sql)
        if m:
            table = m.group(1)
            self._effect(
                lambda: [
                    t[table].__setitem__(r["id"], {**r, "source": "auto_import"}) for r in many
                ]
            )
            return Result()
        m = re.match(r"UPDATE (vehicle_brands|vehicle_models) SET name = :name", sql)
        if m:
            table = m.group(1)
            guarded = "source != 'manual'" in sql

            def _upd() -> None:
                for r in many:
                    cur = t[table].get(r["id"])
                    if cur and not (guarded and cur["source"] == "manual"):
                        cur.update({k: v for k, v in r.items() if k != "id"})

            self._effect(_upd)
            return Result()
        m = re.match(
            r"INSERT INTO (vehicle_kits|vehicle_tire_sizes|vehicle_disk_sizes|tire_tests) \(", sql
        )
        if m:
            table = m.group(1)
            self._effect(lambda: [t[table].__setitem__(r["id"], dict(r)) for r in many])
            return Result()
        if sql.startswith("INSERT INTO tire_eu_labels"):
            guarded = "WHERE tire_eu_labels.source = 'tshina'" in sql

            def _lab() -> None:
                for r in many:
                    cur = t["tire_eu_labels"].get(r["sku"])
                    if cur and guarded and cur["source"] != "tshina":
                        continue
                    src = (
                        cur["source"] if cur and "source = EXCLUDED.source" not in sql else "tshina"
                    )
                    t["tire_eu_labels"][r["sku"]] = {**r, "source": src}

            self._effect(_lab)
            return Result()
        if sql.startswith("DELETE FROM tire_test_placements WHERE test_id = ANY"):
            ids = set(p["ids"])
            self._effect(
                lambda: [
                    t["tire_test_placements"].pop(k)
                    for k in [
                        k for k, r in t["tire_test_placements"].items() if r["test_id"] in ids
                    ]
                ]
            )
            return Result()
        if sql.startswith("INSERT INTO tire_test_placements"):

            def _pl() -> None:
                for r in many:
                    t["tire_test_placements"][
                        len(t["tire_test_placements"]) + 1000 * len(self.db.log)
                    ] = dict(r)

            self._effect(_pl)
            return Result()
        if sql.startswith(
            "SELECT id, alias, alias_normalized, brand_id, model_id, source FROM vehicle_aliases"
        ):
            return Result(list(t["vehicle_aliases"].values()))
        raise AssertionError(f"unmodelled SQL: {sql}")


# ── fake client (a real TshinaApiClient subclass: no invented methods) ───────


class FakeClient(TshinaApiClient):
    def __init__(self, pages: dict[str, list[Any]]) -> None:
        super().__init__("http://tshina.test", "tok")
        self.pages = pages
        self.calls: list[tuple[str, datetime | None]] = []

    async def iter_pages(  # type: ignore[override]
        self, resource: str, *, updated_since: datetime | None = None, limit: int = 1000
    ) -> AsyncIterator[Page]:
        self.calls.append((resource, updated_since))
        for i, entry in enumerate(self.pages.get(resource, [[]])):
            if isinstance(entry, Exception):
                raise entry
            yield Page(items=entry, next_cursor=None, server_time=ST1 if i == 0 else ST2)


def label(sku: str, **kw: Any) -> dict[str, Any]:
    base = {
        "sku": sku,
        "energy_class": "B",
        "wet_grip_class": "C",
        "noise_db": 71,
        "noise_class": "B",
        "eprel_number": "2487145",
        "updated_at": ST1,
        "deleted": False,
    }
    return base | kw


# ── parsing: a row is accepted whole or rejected ─────────────────────────────


def test_label_off_scale_or_wrong_type_is_rejected_whole() -> None:
    assert ts.parse_label(label("1"))["noise_db"] == 71
    for bad in (
        label("1", wet_grip_class="H"),
        label("1", noise_class="D"),
        label("1", noise_db="71"),
        label("1", noise_db=True),
        label("1", noise_db=95),
        label("1", energy_class=None),
    ):
        with pytest.raises(ts.RowRejectedError):
            ts.parse_label(bad)
    assert ts.parse_label(label("1", eprel_number=None))["eprel_number"] is None


def test_tire_test_with_one_bad_place_is_rejected_whole() -> None:
    good = {
        "id": 17,
        "source": {
            "key": "adac",
            "title": "ADAC",
            "scale_min": 0.5,
            "scale_max": 5.5,
            "higher_is_better": None,
        },
        "year": 2024,
        "season": "off_road",
        "test_date": "2024-02-20",
        "sizes": [{"width": 215, "height": 55, "diameter": 17, "raw": "215/55 R17"}],
        "notes": {"ua": "…", "ru": "…"},
        "placements": [
            {
                "place": 1,
                "brand": None,
                "model": None,
                "model_1c_id": "000012345",
                "rating": None,
                "notes": {"ua": "a", "ru": "b"},
                "criteria": [],
            },
        ],
        "updated_at": ST1,
    }
    row = ts.parse_tire_test(good)
    assert json.loads(row["source"])["higher_is_better"] is None  # kept as sent
    assert len(row["placements"]) == 1
    bad_place = {
        **good,
        "placements": [
            *good["placements"],
            {"place": 2, "brand": "X", "model": None, "model_1c_id": None},
        ],
    }
    with pytest.raises(ts.RowRejectedError):
        ts.parse_tire_test(bad_place)
    with pytest.raises(ts.RowRejectedError):
        ts.parse_tire_test({**good, "season": "spring"})


def test_tire_size_rules() -> None:
    item = {
        "id": 1,
        "modification_id": 2,
        "width": 235,
        "height": None,
        "diameter": 18,
        "type": 1,
        "axle": 0,
        "axle_group": 0,
    }
    row = ts.parse_tire_size(item)
    assert row["height"] == 0 and row["axle_group"] == 0 and row["diameter"] == Decimal("18.0")
    for bad in ({**item, "diameter": "R18"}, {**item, "width": "235"}, {**item, "type": None}):
        with pytest.raises(ts.RowRejectedError):
            ts.parse_tire_size(bad)
    disk = ts.parse_disk_size(
        {
            "id": 1,
            "modification_id": 2,
            "width": 7.0,
            "diameter": 17,
            "et": None,
            "type": 1,
            "axle": 0,
            "axle_group": None,
        }
    )
    assert disk["et"] is None and disk["axle_group"] is None


def test_snapshot_guard_threshold() -> None:
    assert ts.snapshot_allows_delete(90, 100)
    assert not ts.snapshot_allows_delete(89, 100)
    assert ts.snapshot_allows_delete(0, 0)


# ── the walk: watermark, errors, guard ───────────────────────────────────────


async def _sync(db: FakeDb, client: FakeClient, name: str, **kw: Any) -> ts.ResourceResult:
    return await ts.sync_resource(db, client, ts.SPECS[name], **kw)


async def test_success_moves_watermark_to_first_page_server_time() -> None:
    db = FakeDb()
    db.t["data_sync_state"]["eu-labels"] = {"resource": "eu-labels", "watermark": OLD_WM}
    client = FakeClient({"eu-labels": [[label("1")], [label("2")]]})
    res = await _sync(db, client, "eu-labels")
    assert res.status == "ok" and res.upserted == 2
    assert client.calls == [("eu-labels", OLD_WM)]
    state = db.t["data_sync_state"]["eu-labels"]
    assert state["watermark"] == datetime(2026, 9, 29, 2, 0, tzinfo=UTC)  # ST1, not ST2
    assert state["last_error"] is None and "full_sync_at" not in state


async def test_api_error_mid_walk_keeps_watermark() -> None:
    db = FakeDb()
    db.t["data_sync_state"]["eu-labels"] = {"resource": "eu-labels", "watermark": OLD_WM}
    client = FakeClient({"eu-labels": [[label("1")], TshinaApiError("http", "boom", 503)]})
    res = await _sync(db, client, "eu-labels")
    assert res.status == "error"
    state = db.t["data_sync_state"]["eu-labels"]
    assert state["watermark"] == OLD_WM
    assert "http" in state["last_error"]
    assert "1" in db.t["tire_eu_labels"]  # page 1 was applied, a rerun is idempotent


async def test_db_error_rolls_back_the_page_and_keeps_watermark() -> None:
    db = FakeDb()
    db.t["data_sync_state"]["eu-labels"] = {"resource": "eu-labels", "watermark": OLD_WM}
    db.fail_on, db.fail_after = "INSERT INTO tire_eu_labels", 1
    client = FakeClient({"eu-labels": [[label("1")], [label("2")]]})
    res = await _sync(db, client, "eu-labels")
    assert res.status == "error" and res.error.startswith("db:")
    assert set(db.t["tire_eu_labels"]) == {"1"}
    assert db.t["data_sync_state"]["eu-labels"]["watermark"] == OLD_WM
    assert db.rollbacks == 1


async def test_deleted_items_are_deleted_only_from_tshina_rows() -> None:
    db = FakeDb()
    db.add("tire_eu_labels", sku="1", source="tshina")
    db.add("tire_eu_labels", sku="2", source="manual")
    client = FakeClient(
        {"eu-labels": [[{"sku": "1", "deleted": True}, {"sku": "2", "deleted": True}]]}
    )
    res = await _sync(db, client, "eu-labels")
    assert res.deleted == 1
    assert set(db.t["tire_eu_labels"]) == {"2"}


async def test_label_upsert_does_not_overwrite_other_sources() -> None:
    db = FakeDb()
    db.add("tire_eu_labels", sku="1", source="manual", wet_grip_class="A")
    await _sync(db, FakeClient({"eu-labels": [[label("1", wet_grip_class="E")]]}), "eu-labels")
    assert db.t["tire_eu_labels"]["1"]["wet_grip_class"] == "A"


async def test_full_sweep_deletes_what_the_snapshot_lacks() -> None:
    db = FakeDb()
    for i in range(10):
        db.add("tire_eu_labels", sku=str(i), source="tshina")
    db.add("tire_eu_labels", sku="m", source="manual")
    client = FakeClient({"eu-labels": [[label(str(i)) for i in range(9)]]})
    res = await _sync(db, client, "eu-labels", full=True)
    assert res.status == "ok" and client.calls == [("eu-labels", None)]
    assert "9" not in db.t["tire_eu_labels"] and "m" in db.t["tire_eu_labels"]
    assert db.t["data_sync_state"]["eu-labels"]["full_sync_at"] == "now"


async def test_full_sweep_below_90_percent_deletes_nothing() -> None:
    db = FakeDb()
    db.t["data_sync_state"]["eu-labels"] = {"resource": "eu-labels", "watermark": OLD_WM}
    for i in range(100):
        db.add("tire_eu_labels", sku=str(i), source="tshina")
    client = FakeClient({"eu-labels": [[label(str(i)) for i in range(89)]]})
    res = await _sync(db, client, "eu-labels", full=True)
    assert res.status == "guarded" and res.deleted == 0
    assert len(db.t["tire_eu_labels"]) == 100
    state = db.t["data_sync_state"]["eu-labels"]
    assert state["watermark"] == OLD_WM and "snapshot_guard" in state["last_error"]


async def test_rejected_rows_count_in_the_snapshot_and_are_not_deleted() -> None:
    db = FakeDb()
    for i in range(10):
        db.add("tire_eu_labels", sku=str(i), source="tshina")
    page = [label(str(i)) for i in range(9)] + [label("9", wet_grip_class="Z")]
    res = await _sync(db, FakeClient({"eu-labels": [page]}), "eu-labels", full=True)
    assert res.rejected == 1 and res.deleted == 0 and "9" in db.t["tire_eu_labels"]


async def test_dry_run_writes_nothing() -> None:
    db = FakeDb()
    client = FakeClient({"eu-labels": [[label("1"), {"sku": "2", "deleted": True}]]})
    res = await _sync(db, client, "eu-labels", dry_run=True)
    assert res.status == "dry_run" and res.upserted == 1 and res.deleted == 1
    assert db.t["tire_eu_labels"] == {} and db.t["data_sync_state"] == {}


async def test_tire_test_places_rewritten_whole() -> None:
    db = FakeDb()
    test = {
        "id": 5,
        "source": {"key": "adac", "higher_is_better": None},
        "season": "summer",
        "placements": [
            {"place": 1, "brand": "A", "model": "M1"},
            {"place": 2, "brand": "B", "model": "M2"},
        ],
    }
    await _sync(db, FakeClient({"tire-tests": [[test]]}), "tire-tests")
    assert len(db.t["tire_test_placements"]) == 2
    test2 = {**test, "placements": [{"place": 1, "brand": "C", "model": "M3"}]}
    await _sync(db, FakeClient({"tire-tests": [[test2]]}), "tire-tests")
    assert [p["brand"] for p in db.t["tire_test_placements"].values()] == ["C"]


# ── vehicle directory rules ──────────────────────────────────────────────────


async def test_hongqi_and_kg_and_their_descendants_are_skipped() -> None:
    db = FakeDb()
    client = FakeClient(
        {
            "vehicles/brands": [
                [
                    {"id": 228, "name": "Avatr"},
                    {"id": 229, "name": "Hongqi"},
                    {"id": 231, "name": "KG Mobility"},
                ]
            ],
            "vehicles/models": [
                [
                    {"id": 1, "brand_id": 228, "name": "11"},
                    {"id": 2, "brand_id": 229, "name": "HS5"},
                    {"id": 3, "brand_id": 231, "name": "Torres"},
                ]
            ],
            "vehicles/modifications": [
                [{"id": 10, "model_id": 1, "year": 2024}, {"id": 20, "model_id": 2, "year": 2024}]
            ],
            "vehicles/tire-sizes": [
                [
                    {
                        "id": 100,
                        "modification_id": 10,
                        "width": 235,
                        "height": 60,
                        "diameter": 18,
                        "type": 1,
                        "axle": 0,
                        "axle_group": None,
                    },
                    {
                        "id": 200,
                        "modification_id": 20,
                        "width": 235,
                        "height": 60,
                        "diameter": 18,
                        "type": 1,
                        "axle": 0,
                        "axle_group": None,
                    },
                ]
            ],
        }
    )
    out = await ts.run_sync(db, client, ts.VEHICLE_RESOURCES, regenerate=_noop_regen)
    assert set(db.t["vehicle_brands"]) == {228}
    assert set(db.t["vehicle_models"]) == {1}
    assert set(db.t["vehicle_kits"]) == {10}
    assert set(db.t["vehicle_tire_sizes"]) == {100}
    by = {r["resource"]: r for r in out["resources"]}
    assert by["vehicles/brands"]["skipped"] == 2
    assert by["vehicles/models"]["skipped"] == 2
    assert by["vehicles/tire-sizes"]["skipped"] == 1


async def test_manual_brand_and_model_are_not_overwritten_or_swept() -> None:
    db = FakeDb()
    db.add("vehicle_brands", id=5, name="Ручна", source="manual")
    db.add("vehicle_models", id=7, brand_id=5, name="Ручна модель", source="manual")
    db.add("vehicle_brands", id=6, name="Old", source="auto_import")
    client = FakeClient(
        {
            "vehicles/brands": [[{"id": 5, "name": "Auto"}, {"id": 6, "name": "New"}]],
            "vehicles/models": [[{"id": 7, "brand_id": 5, "name": "Auto model"}]],
        }
    )
    out = await ts.run_sync(
        db, client, ["vehicles/brands", "vehicles/models"], regenerate=_noop_regen
    )
    assert db.t["vehicle_brands"][5]["name"] == "Ручна"
    assert db.t["vehicle_models"][7]["name"] == "Ручна модель"
    assert db.t["vehicle_brands"][6]["name"] == "New"
    by = {r["resource"]: r for r in out["resources"]}
    assert (by["vehicles/brands"]["skipped"], by["vehicles/brands"]["changed"]) == (1, 1)
    assert (by["vehicles/models"]["skipped"], by["vehicles/models"]["changed"]) == (1, 0)
    # full snapshot with every auto row but without the manual ones: the sweep
    # runs (10 of 10 auto rows present) and still leaves the manual rows alone
    for i in range(20, 29):
        db.add("vehicle_brands", id=i, name=f"B{i}", source="auto_import")
    snapshot = [{"id": 6, "name": "New"}] + [{"id": i, "name": f"B{i}"} for i in range(20, 29)]
    full = FakeClient({"vehicles/brands": [snapshot], "vehicles/models": [[]]})
    out = await ts.run_sync(
        db, full, ["vehicles/brands", "vehicles/models"], full=True, regenerate=_noop_regen
    )
    assert out["resources"][0]["status"] == "ok"
    assert 5 in db.t["vehicle_brands"] and 7 in db.t["vehicle_models"]


async def test_an_auto_brand_with_a_manual_model_is_not_swept() -> None:
    # vehicle_models.brand_id cascades: sweeping the brand would take the
    # manual model with it
    db = FakeDb()
    db.add("vehicle_brands", id=6, name="Old", source="auto_import")
    db.add("vehicle_models", id=8, brand_id=6, name="Ручна модель", source="manual")
    for i in range(20, 29):
        db.add("vehicle_brands", id=i, name=f"B{i}", source="auto_import")
    snapshot = [{"id": i, "name": f"B{i}"} for i in range(20, 29)]  # brand 6 is gone there
    full = FakeClient({"vehicles/brands": [snapshot]})
    out = await ts.run_sync(db, full, ["vehicles/brands"], full=True, regenerate=_noop_regen)
    assert out["resources"][0]["status"] == "ok"
    assert 6 in db.t["vehicle_brands"] and 8 in db.t["vehicle_models"]


async def test_hongqi_models_skipped_even_when_the_brand_is_ours() -> None:
    db = FakeDb()
    db.add("vehicle_brands", id=229, name="Hongqi", source="manual")
    client = FakeClient({"vehicles/models": [[{"id": 2, "brand_id": 229, "name": "HS5"}]]})
    await ts.run_sync(db, client, ["vehicles/models"], regenerate=_noop_regen)
    assert db.t["vehicle_models"] == {}


async def test_brand_named_model_without_kits_is_skipped() -> None:
    db = FakeDb()
    db.add("vehicle_brands", id=138, name="Alpine", source="auto_import")
    db.add("vehicle_brands", id=1, name="Toyota", source="auto_import")
    client = FakeClient(
        {
            "vehicles/models": [
                [
                    {"id": 1, "brand_id": 138, "name": "Toyota"},
                    {"id": 2, "brand_id": 138, "name": "A110"},
                ]
            ]
        }
    )
    await ts.run_sync(db, client, ["vehicles/models"], regenerate=_noop_regen)
    assert set(db.t["vehicle_models"]) == {2}


async def test_aliases_regenerated_only_when_brands_or_models_changed() -> None:
    calls: list[Any] = []

    async def regen(engine: Any) -> None:
        calls.append(engine)

    db = FakeDb()
    db.add("vehicle_brands", id=1, name="Toyota", source="auto_import")
    same = FakeClient({"vehicles/brands": [[{"id": 1, "name": "Toyota"}]]})
    out = await ts.run_sync(db, same, ["vehicles/brands"], regenerate=regen)
    assert calls == [] and out["alias_collisions"] is None
    renamed = FakeClient({"vehicles/brands": [[{"id": 1, "name": "Toyota Motor"}]]})
    await ts.run_sync(db, renamed, ["vehicles/brands"], regenerate=regen)
    assert calls == [db]


async def test_alias_collision_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeDb()
    db.t["vehicle_aliases"] = {
        1: {
            "id": 1,
            "alias": "x",
            "alias_normalized": "x",
            "brand_id": 1,
            "model_id": 10,
            "source": "manual",
        },
        2: {
            "id": 2,
            "alias": "x",
            "alias_normalized": "x",
            "brand_id": 1,
            "model_id": 11,
            "source": "auto_translit",
        },
    }
    errors = _errors_counter(monkeypatch)
    client = FakeClient({"vehicles/brands": [[{"id": 1, "name": "Toyota"}]]})
    out = await ts.run_sync(db, client, ["vehicles/brands"], regenerate=_noop_regen)
    assert out["alias_collisions"] == 1
    assert ("vehicles/models", "alias_collision") in errors


async def test_failed_parent_stops_the_vehicle_chain() -> None:
    db = FakeDb()
    client = FakeClient(
        {
            "vehicles/brands": [TshinaApiError("auth", "bad token", 401)],
            "vehicles/models": [[{"id": 1, "brand_id": 1, "name": "X"}]],
        }
    )
    out = await ts.run_sync(db, client, ts.VEHICLE_RESOURCES, regenerate=_noop_regen)
    assert out["status"] == "error"
    assert [c[0] for c in client.calls] == ["vehicles/brands"]
    assert {r["status"] for r in out["resources"][1:]} == {"skipped"}


async def test_lock_held_elsewhere_skips_without_requests() -> None:
    db = FakeDb()
    db.lock_free = False
    client = FakeClient({"eu-labels": [[label("1")]]})
    out = await ts.run_sync(db, client, ["eu-labels"])
    assert out["status"] == "locked" and client.calls == []


# ── metrics and the off switch ───────────────────────────────────────────────


async def _noop_regen(engine: Any) -> None:
    return None


def _errors_counter(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    from src.monitoring import metrics

    seen: list[tuple[str, str]] = []
    real = metrics.data_sync_errors_total

    class Spy:
        def labels(self, resource: str, kind: str) -> Any:
            seen.append((resource, kind))
            return real.labels(resource=resource, kind=kind)

    monkeypatch.setattr(metrics, "data_sync_errors_total", Spy())
    return seen


async def test_error_is_metered_by_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    errors = _errors_counter(monkeypatch)
    db = FakeDb()
    await _sync(db, FakeClient({"eu-labels": [TshinaApiError("auth", "x", 403)]}), "eu-labels")
    assert errors == [("eu-labels", "auth")]


async def test_success_sets_last_success_gauge() -> None:
    from src.monitoring import metrics

    gauge = metrics.data_sync_last_success_timestamp.labels(resource="tire-tests")
    gauge.set(0)
    await _sync(FakeDb(), FakeClient({"tire-tests": [[]]}), "tire-tests")
    assert gauge._value.get() > 0


def _settings(**cfg: Any) -> Any:
    from src.config import TshinaApiSettings

    return SimpleNamespace(
        tshina_api=TshinaApiSettings(**cfg),
        database=SimpleNamespace(url="postgresql+asyncpg://x/y"),
    )


@pytest.mark.parametrize(
    "cfg", [{}, {"base_url": "https://t.test"}, {"token": "tok"}, {"base_url": " ", "token": "t"}]
)
async def test_sync_is_off_without_url_or_token(monkeypatch: pytest.MonkeyPatch, cfg: dict) -> None:
    from src.integrations import tshina_api
    from src.tasks import tshina_sync_tasks

    def boom(*a: Any, **k: Any) -> None:
        raise AssertionError("client built while disabled")

    monkeypatch.setattr(tshina_sync_tasks, "get_settings", lambda: _settings(**cfg))
    monkeypatch.setattr(tshina_api, "TshinaApiClient", boom)
    monkeypatch.setattr("sqlalchemy.ext.asyncio.create_async_engine", boom)
    assert await tshina_sync_tasks.run_tshina_sync(full=False) == {"status": "disabled"}


async def test_sync_runs_when_url_and_token_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.integrations import tshina_api, tshina_sync
    from src.tasks import tshina_sync_tasks

    seen: dict[str, Any] = {}

    class Engine:
        async def dispose(self) -> None:
            seen["disposed"] = True

    async def fake_run(engine: Any, client: Any, resources: Any, **kw: Any) -> dict[str, Any]:
        seen["headers"] = client.headers()
        seen["resources"] = list(resources)
        return {"status": "ok"}

    monkeypatch.setattr(
        tshina_sync_tasks,
        "get_settings",
        lambda: _settings(base_url="https://t.test", token="tok"),
    )
    monkeypatch.setattr("sqlalchemy.ext.asyncio.create_async_engine", lambda *a, **k: Engine())
    monkeypatch.setattr(tshina_sync, "run_sync", fake_run)
    out = await tshina_sync_tasks.run_tshina_sync(full=False)
    assert out == {"status": "ok"}
    assert seen["headers"]["Authorization"] == "Bearer tok"
    assert seen["resources"] == list(tshina_api.RESOURCES) and seen["disposed"]


def test_beat_schedule_and_routes() -> None:
    from src.tasks.celery_app import app

    beat = app.conf.beat_schedule
    inc, full = beat["tshina-sync-incremental"], beat["tshina-sync-full"]
    assert inc["task"] == "src.tasks.tshina_sync_tasks.tshina_sync_incremental"
    assert full["task"] == "src.tasks.tshina_sync_tasks.tshina_sync_full"
    # 04:40: clear of backup-database (04:00); Sunday is the full walk's alone
    assert inc["schedule"].hour == {4} and inc["schedule"].minute == {40}
    assert inc["schedule"].day_of_week == {1, 2, 3, 4, 5, 6}
    assert full["schedule"].hour == {4} and full["schedule"].minute == {40}
    assert full["schedule"].day_of_week == {0}
    assert "src.tasks.tshina_sync_tasks" in app.conf.include
