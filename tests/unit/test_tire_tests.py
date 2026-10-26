"""Tyre test places on the found tyres (``src/store_client/tire_tests.py``).

R08 «тільки на запит»: the search puts up to two tests of each tyre's model
on the item (the caller's size first, then the same diameter, another
diameter never), the compressor does not hand them to the model, and the
code says them only on a compare / advice question (`test_tyre_compare`).

No real database: a fake engine records the SQL and answers the catalogue
query and the tests query with rows shaped like the prod tables (prod
2026-10-26: ``sizes`` a JSONB list of ``{raw, width, height, diameter}``,
``rating`` a JSONB word object, ``place`` smallint, ``model_1c_id`` may be
NULL — such a place never joins a product).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from src.agent.tire_claim_guard import TireClaimState
from src.agent.tool_result_compressor import compress_tool_result
from src.agent.tyre_compare import TyreCompare
from src.store_client import tire_tests as tt
from src.store_client.client import StoreClient

# ── Rows in the shape of prod ─────────────────────────────────────────────


def _sizes(*raws: str) -> list[dict[str, Any]]:
    out = []
    for raw in raws:
        w, h, d = tt.parse_size(raw) or (None, None, None)
        out.append({"raw": raw, "width": w, "height": h, "diameter": d})
    return out


def _test_row(
    sku: str,
    test_id: int,
    *,
    place: int | None,
    places: int,
    year: int | None,
    sizes: list[dict[str, Any]] | str,
    title: str | None = "ADAC",
    key: str = "adac",
) -> dict[str, Any]:
    """A row of the tests query: a placement joined to its test and the SKU."""
    return {
        "sku": sku,
        "test_id": test_id,
        "place": place,
        "source_title": title,
        "source_key": key,
        "year": year,
        # asyncpg hands JSONB back as text unless a codec is set
        "sizes": json.dumps(sizes) if isinstance(sizes, list) else sizes,
        "places": places,
    }


CATALOGUE = {
    "id": "0201",
    "brand": "Bridgestone",
    "model": "Turanza 6",
    "size": "225/50 R17 98W",
    "season": "summer",
    "price": 5900,
    "stock_quantity": 6,
    "runflat": False,
    "eu_fuel": "C",
    "eu_wet": "B",
    "eu_noise_db": 71,
    "attr_studded": None,
    "attr_xl": None,
    "load_index": "98",
    "speed_index": "W",
    "desc_studded": False,
    "desc_studdable": False,
    "desc_xl": False,
}
CATALOGUE_2 = {**CATALOGUE, "id": "0202", "brand": "Taurus", "model": "Summer 3", "price": 3400}


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _Conn:
    def __init__(self, engine: _Engine) -> None:
        self._engine = engine

    async def __aenter__(self) -> _Conn:
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False

    async def execute(self, query: Any, params: dict[str, Any]) -> _Result:
        sql = str(query)
        self._engine.calls.append((sql, dict(params)))
        if "tire_test_placements" in sql:
            if self._engine.tests_error:
                raise RuntimeError("relation tire_test_placements does not exist")
            return _Result(self._engine.test_rows)
        return _Result(self._engine.catalogue)


class _Engine:
    def __init__(
        self,
        catalogue: list[dict[str, Any]],
        test_rows: list[dict[str, Any]],
        *,
        tests_error: bool = False,
    ) -> None:
        self.catalogue = catalogue
        self.test_rows = test_rows
        self.tests_error = tests_error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def connect(self) -> _Conn:
        return _Conn(self)

    def test_queries(self) -> list[tuple[str, dict[str, Any]]]:
        return [c for c in self.calls if "tire_test_placements" in c[0]]


def _squash(sql: str) -> str:
    return " ".join(sql.split())


SIZE = (225, 50, 17)

# ── The choice of tests ───────────────────────────────────────────────────


def test_own_size_before_same_diameter_even_if_older() -> None:
    rows = [
        _test_row("s", 1, place=2, places=12, year=2026, sizes=_sizes("225/45 R17")),
        _test_row("s", 2, place=9, places=12, year=2024, sizes=_sizes("225/50 R17")),
    ]
    got = tt.pick_tests(SIZE, rows)
    assert [t["size"] for t in got] == ["225/50 R17", "225/45 R17"]
    assert got[0] == {"source": "ADAC", "year": 2024, "place": 9, "of": 12, "size": "225/50 R17"}


def test_more_than_two_is_cut_to_two_newest_first() -> None:
    rows = [
        _test_row("s", n, place=n, places=20, year=2020 + n, sizes=_sizes("225/45 R17"))
        for n in range(1, 5)
    ]
    got = tt.pick_tests(SIZE, rows)
    assert len(got) == tt.MAX_TESTS == 2
    assert [t["year"] for t in got] == [2024, 2023]


def test_another_diameter_is_not_taken() -> None:
    rows = [
        _test_row("s", 1, place=1, places=10, year=2026, sizes=_sizes("225/50 R18")),
        _test_row("s", 2, place=3, places=10, year=2025, sizes=_sizes("205/55 R16")),
    ]
    assert tt.pick_tests(SIZE, rows) == []


def test_a_test_in_several_sizes_takes_the_best_tier() -> None:
    rows = [
        _test_row("s", 1, place=4, places=15, year=2025, sizes=_sizes("235/45 R17", "225/50 R17")),
    ]
    assert tt.pick_tests(SIZE, rows)[0]["size"] == "225/50 R17"


def test_no_place_no_size_no_title() -> None:
    rows = [
        _test_row("s", 1, place=None, places=10, year=2025, sizes=_sizes("225/50 R17")),
        _test_row("s", 2, place=5, places=10, year=2025, sizes="[]"),
        _test_row("s", 3, place=5, places=10, year=None, sizes=_sizes("225/50 R17"), title=None),
    ]
    got = tt.pick_tests(SIZE, rows)
    # no place → skipped; no size → no tier; no title → the source key
    assert got == [{"source": "adac", "place": 5, "of": 10, "size": "225/50 R17"}]


def test_parse_size_reads_the_catalogue_spellings() -> None:
    assert tt.parse_size("225/50 R17 98W") == SIZE
    assert tt.parse_size("225/50R17") == SIZE
    assert tt.parse_size("225/50 ZR17 XL") == SIZE
    assert tt.parse_size("7.50 R16") is None


# ── The query ─────────────────────────────────────────────────────────────


def test_one_query_for_all_skus_by_model_1c_id() -> None:
    engine = _Engine([], [])
    result = {"items": [{"id": "a", "size": "225/50 R17"}, {"id": "b", "size": "225/50 R17"}]}
    asyncio.run(tt.attach_tire_tests(engine, result))
    queries = engine.test_queries()
    assert len(queries) == 1
    sql, params = queries[0]
    sql = _squash(sql)
    assert "JOIN tire_products p ON p.model_id = tp.model_1c_id" in sql
    assert "tp.place IS NOT NULL" in sql
    assert "p.sku IN (:sku_0, :sku_1)" in sql
    assert sorted(params.values()) == ["a", "b"]
    # the place count is of the whole test, not of the found models
    assert "WHERE c.test_id = tp.test_id AND c.place IS NOT NULL" in sql


def test_no_items_no_query() -> None:
    engine = _Engine([], [])
    asyncio.run(tt.attach_tire_tests(engine, {"total": 0, "items": []}))
    asyncio.run(tt.attach_tire_tests(None, {"items": [{"id": "a"}]}))
    assert engine.calls == []


def test_db_error_leaves_items_without_tests() -> None:
    engine = _Engine([], [], tests_error=True)
    result = {"items": [{"id": "a", "size": "225/50 R17"}]}
    asyncio.run(tt.attach_tire_tests(engine, result))
    assert result == {"items": [{"id": "a", "size": "225/50 R17"}]}


# ── search_tires (the call site in client.py) ───────────────────────────


def _search(engine: _Engine) -> dict[str, Any]:
    client = StoreClient(base_url="http://localhost", api_key="k", db_engine=engine)
    return asyncio.run(client.search_tires(network="ProKoleso", width=225, profile=50, diameter=17))


TURANZA_TEST = _test_row("0201", 7, place=9, places=12, year=2025, sizes=_sizes("225/50 R17"))


def test_search_puts_tests_on_the_item_that_has_them() -> None:
    engine = _Engine([CATALOGUE, CATALOGUE_2], [TURANZA_TEST])
    result = _search(engine)
    by_model = {i["model"]: i for i in result["items"]}
    assert by_model["Turanza 6"]["tests"] == [
        {"source": "ADAC", "year": 2025, "place": 9, "of": 12, "size": "225/50 R17"}
    ]
    assert "tests" not in by_model["Summer 3"]
    assert len(engine.test_queries()) == 1


def test_search_db_error_on_tests_still_answers() -> None:
    engine = _Engine([CATALOGUE], [TURANZA_TEST], tests_error=True)
    result = _search(engine)
    assert [i["model"] for i in result["items"]] == ["Turanza 6"]
    assert "tests" not in result["items"][0]


def test_empty_tables_change_nothing() -> None:
    engine = _Engine([CATALOGUE], [])
    result = _search(engine)
    assert "tests" not in result["items"][0]


# ── R08: the model does not see the tests; the code keeps them ───────────


def _result_with_tests() -> dict[str, Any]:
    return _search(_Engine([CATALOGUE, CATALOGUE_2], [TURANZA_TEST]))


def test_compressor_never_passes_tests() -> None:
    result = _result_with_tests()
    assert result["items"][0].get("tests") or result["items"][1].get("tests")
    for sales in (True, False):
        out = compress_tool_result("search_tires", result, sales_enabled=sales)
        assert "tests" not in out
        assert "місце" not in out and "ADAC" not in out


def test_compare_gate_keeps_tests_under_sales_only() -> None:
    result = _result_with_tests()
    on = TyreCompare(sales_enabled=True)
    on.note_search(result)
    turanza = next(i for i in on.last_items if i["model"] == "Turanza 6")
    assert turanza["tests"][0]["place"] == 9
    off = TyreCompare(sales_enabled=False)
    off.note_search(result)
    assert off.last_items == []


def test_claim_guard_state_sees_the_tests() -> None:
    # `tire_claim_guard` rule ``unbacked_rating`` reads ``tests`` off its items
    state = TireClaimState(sales_enabled=True)
    state.note("search_tires", _result_with_tests())
    assert any(i.get("tests") for i in state.items)
