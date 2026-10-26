"""Studs, XL and load/speed index on a catalogue tyre item (``_tire_item``).

``_query_tire_rows`` LEFT JOINs ``tire_attributes`` (tshina Data API,
migration 066) by SKU and also selects the ``description`` regexes as
columns. ``_tire_item`` takes the attribute when the row has one and the
regex otherwise; a key the row has no fact for is absent (as ``eu_label``),
and ``studded`` exists only on a winter tyre. No real database: a fake
engine records the SQL and hands back rows shaped like the SELECT.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import pytest

from src.store_client import client as client_mod
from src.store_client.client import StoreClient

#: A row of ``_query_tire_rows`` for a SKU the attribute sync has not
#: reached: every ``tire_attributes`` column NULL, regex columns false.
WINTER = {
    "id": "0101",
    "brand": "Nokian",
    "model": "Hakkapeliitta R5",
    "size": "205/55 R16",
    "season": "winter",
    "price": 5120,
    "stock_quantity": 8,
    "runflat": False,
    "eu_fuel": None,
    "eu_wet": None,
    "eu_noise_db": None,
    "attr_studded": None,
    "attr_xl": None,
    "load_index": None,
    "speed_index": None,
    "desc_studded": False,
    "desc_studdable": False,
    "desc_xl": False,
}
SUMMER = {**WINTER, "id": "0102", "model": "Hakka Green 3", "season": "summer"}


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
        self._engine.queries.append(str(query))
        return _Result(self._engine.rows)


class _Engine:
    """Records every query; returns ``rows`` for each."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    def connect(self) -> _Conn:
        return _Conn(self)


def _client(rows: list[dict[str, Any]]) -> tuple[StoreClient, _Engine]:
    engine = _Engine(rows)
    client = StoreClient(base_url="http://localhost", api_key="k", db_engine=engine)
    return client, engine


def _squash(sql: str) -> str:
    return " ".join(sql.split())


# ── SQL ──────────────────────────────────────────────────────────────────


def test_query_left_joins_the_attributes_by_sku() -> None:
    client, engine = _client([])
    asyncio.run(client._query_tire_rows("ProKoleso", {"width": 205}))
    sql = _squash(engine.queries[0])
    assert "LEFT JOIN tire_attributes a ON a.sku = p.sku" in sql
    for column in (
        "a.studded AS attr_studded",
        "a.is_xl AS attr_xl",
        "a.load_index AS load_index",
        "a.speed_index AS speed_index",
        "AS desc_studded",
        "AS desc_studdable",
        "AS desc_xl",
    ):
        assert column in sql
    # a plain JOIN would drop every tyre the sync has not reached yet
    assert "JOIN tire_attributes" not in sql.replace("LEFT JOIN tire_attributes", "")


def test_fallback_columns_are_the_description_regexes() -> None:
    client, engine = _client([])
    asyncio.run(client._query_tire_rows("ProKoleso", {"width": 205}))
    sql = _squash(engine.queries[0])
    for expr, alias in (
        (client_mod._STUD_WORD_COLUMN_SQL, "desc_studded"),
        (client_mod._STUDDABLE_COLUMN_SQL, "desc_studdable"),
        (client_mod._XL_COLUMN_SQL, "desc_xl"),
    ):
        assert f"{_squash(expr)} AS {alias}" in sql


@pytest.mark.parametrize(
    ("description", "word", "studdable", "xl"),
    [
        ("Nokian Hakkapeliitta 10 [94T] Шип", True, False, False),
        ("Michelin X-Ice North 4 XL [94T] Під шип", True, True, True),
        ("Kumho WinterCraft WI51 [91T] Под шип", True, True, False),
        ("Matador MP30 Sibir Ice 2 WinSpike [91T]", False, False, False),
        ("Pirelli Ice Zero Friction Extra Load [94H]", False, False, True),
    ],
)
def test_fallback_regexes_read_the_1c_description(
    description: str, word: bool, studdable: bool, xl: bool
) -> None:
    """Python ``re`` stands in for PostgreSQL ``~*`` on these patterns."""
    assert bool(re.search(client_mod._STUD_WORD_RE, description, re.I)) is word
    assert bool(re.search(client_mod._STUDDABLE_RE, description, re.I)) is studdable
    assert bool(re.search(client_mod._XL_RE, description, re.I)) is xl


def test_query_without_filters_adds_no_stud_or_xl_condition() -> None:
    """The fallback regexes are columns, not filters: an unfiltered query stays unfiltered."""
    client, engine = _client([])
    asyncio.run(client._query_tire_rows("ProKoleso", {"width": 205}))
    where = _squash(engine.queries[0]).split(" WHERE ", 1)[1]
    assert "шип" not in where
    assert "xl" not in where.lower()


# ── studs ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("attr", "expected"),
    [("ship", "studded"), ("pod_ship", "studdable"), ("no_ship", "friction")],
)
def test_stud_attribute_maps_to_the_item(attr: str, expected: str) -> None:
    item = StoreClient._tire_item({**WINTER, "attr_studded": attr})
    assert item["studded"] == expected


def test_attribute_wins_over_the_description() -> None:
    """1C description says «Шип», tshina says stud-ready → stud-ready."""
    row = {**WINTER, "attr_studded": "pod_ship", "desc_studded": True}
    assert StoreClient._tire_item(row)["studded"] == "studdable"
    row = {**WINTER, "attr_studded": "no_ship", "desc_studdable": True}
    assert StoreClient._tire_item(row)["studded"] == "friction"


@pytest.mark.parametrize(
    ("desc", "expected"),
    [
        ({"desc_studded": True}, "studded"),
        # «Під шип» matches the word «шип» too: both columns are true
        ({"desc_studdable": True, "desc_studded": True}, "studdable"),
        ({}, "friction"),
    ],
)
def test_no_attribute_row_falls_back_to_the_description(
    desc: dict[str, bool], expected: str
) -> None:
    assert StoreClient._tire_item({**WINTER, **desc})["studded"] == expected


def test_winter_in_the_1c_spelling_counts_as_winter() -> None:
    item = StoreClient._tire_item({**WINTER, "season": "зимова", "desc_studded": True})
    assert item["studded"] == "studded"


@pytest.mark.parametrize("season", ["summer", "all_season", "літня", None])
def test_non_winter_tyre_has_no_studs_key(season: str | None) -> None:
    row = {**SUMMER, "season": season, "attr_studded": "no_ship", "desc_studded": True}
    assert "studded" not in StoreClient._tire_item(row)


def test_row_without_the_columns_has_no_studs_key() -> None:
    """Another query (or an old engine) without the columns → no claim at all."""
    row = {k: v for k, v in WINTER.items() if not k.startswith(("attr_", "desc_"))}
    assert "studded" not in StoreClient._tire_item(row)


# ── XL ───────────────────────────────────────────────────────────────────


def test_xl_from_the_attribute() -> None:
    assert StoreClient._tire_item({**WINTER, "attr_xl": True})["xl"] is True


def test_xl_attribute_false_wins_over_the_description() -> None:
    assert "xl" not in StoreClient._tire_item({**WINTER, "attr_xl": False, "desc_xl": True})


def test_xl_falls_back_to_the_description() -> None:
    assert StoreClient._tire_item({**SUMMER, "desc_xl": True})["xl"] is True


def test_not_xl_has_no_key() -> None:
    assert "xl" not in StoreClient._tire_item(WINTER)
    assert "xl" not in StoreClient._tire_item(SUMMER)


# ── load / speed index ───────────────────────────────────────────────────


def test_indices_from_the_attributes() -> None:
    item = StoreClient._tire_item({**SUMMER, "load_index": "109/107", "speed_index": " T "})
    assert item["load_index"] == "109/107"
    assert item["speed_index"] == "T"


@pytest.mark.parametrize("value", [None, "", "  "])
def test_empty_index_has_no_key(value: str | None) -> None:
    item = StoreClient._tire_item({**SUMMER, "load_index": value, "speed_index": value})
    assert "load_index" not in item
    assert "speed_index" not in item


# ── through the search ───────────────────────────────────────────────────


def test_empty_attribute_table_gives_the_regex_answer() -> None:
    """``tire_attributes`` empty (sync not on yet): items = the regexes alone."""
    rows = [
        {**WINTER, "desc_studded": True, "desc_xl": True},
        {
            **WINTER,
            "id": "0103",
            "model": "Hakkapeliitta R3",
            "desc_studded": True,
            "desc_studdable": True,
        },
        {**SUMMER},
    ]
    client, engine = _client(rows)
    res = asyncio.run(
        client._search_tires_db(network="ProKoleso", width=205, profile=55, diameter=16)
    )
    assert "tire_attributes" in engine.queries[0]
    by_id = {i["id"]: i for i in res["items"]}
    assert by_id["0101"]["studded"] == "studded"
    assert by_id["0101"]["xl"] is True
    assert by_id["0103"]["studded"] == "studdable"
    assert "xl" not in by_id["0103"]
    assert "studded" not in by_id["0102"]
    for item in res["items"]:
        assert "load_index" not in item
        assert "speed_index" not in item


def test_search_puts_the_attributes_on_the_items() -> None:
    rows = [
        {
            **WINTER,
            "attr_studded": "pod_ship",
            "attr_xl": True,
            "load_index": "94",
            "speed_index": "T",
            "desc_studded": True,
        },
    ]
    client, _ = _client(rows)
    res = asyncio.run(
        client._search_tires_db(network="ProKoleso", width=205, profile=55, diameter=16)
    )
    item = res["items"][0]
    assert item["studded"] == "studdable"
    assert item["xl"] is True
    assert (item["load_index"], item["speed_index"]) == ("94", "T")
    assert item["season"] == "winter"


def test_http_fallback_items_carry_no_new_keys() -> None:
    """The Store API path formats its own items; nothing of the above leaks in."""
    data = {"items": [{"id": "1", "brand": "B", "size": "205/55 R16", "studded": "x", "xl": True}]}
    item = StoreClient._format_tire_results(data)["items"][0]
    for key in ("studded", "xl", "load_index", "speed_index"):
        assert key not in item
