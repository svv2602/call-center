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
    "country": None,
    "year_issue": None,
    "runflat": False,
    "eu_fuel": None,
    "eu_wet": None,
    "eu_noise_db": None,
    "eu_noise_class": None,
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
        "COALESCE(a.load_index, NULLIF(TRIM(p.load_rating), '')) AS load_index",
        "COALESCE(a.speed_index, NULLIF(TRIM(p.speed_rating), '')) AS speed_index",
        "AS desc_studded",
        "AS desc_studdable",
        "AS desc_xl",
    ):
        assert column in sql
    # a plain JOIN would drop every tyre the sync has not reached yet
    assert "JOIN tire_attributes" not in sql.replace("LEFT JOIN tire_attributes", "")


def test_query_selects_the_eu_label_with_the_noise_class() -> None:
    client, engine = _client([])
    asyncio.run(client._query_tire_rows("ProKoleso", {"width": 205}))
    sql = _squash(engine.queries[0])
    assert "LEFT JOIN tire_eu_labels l ON l.sku = p.sku" in sql
    for column in (
        "l.energy_class AS eu_fuel",
        "l.wet_grip_class AS eu_wet",
        "l.noise_db AS eu_noise_db",
        "l.noise_class AS eu_noise_class",
    ):
        assert column in sql


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


# ── EU noise class ───────────────────────────────────────────────────────


@pytest.mark.parametrize(("value", "expected"), [("B", "B"), (" a ", "A"), ("c", "C")])
def test_noise_class_goes_on_the_label(value: str, expected: str) -> None:
    row = {**SUMMER, "eu_fuel": "C", "eu_wet": "B", "eu_noise_db": 71, "eu_noise_class": value}
    label = StoreClient._tire_item(row)["eu_label"]
    assert label == {"fuel": "C", "wet": "B", "noise_db": 71, "noise_class": expected}


@pytest.mark.parametrize("value", [None, "", "  ", "D", "X", "AB", 1])
def test_missing_or_bad_noise_class_has_no_key(value: Any) -> None:
    row = {**SUMMER, "eu_fuel": "C", "eu_noise_db": 71, "eu_noise_class": value}
    label = StoreClient._tire_item(row)["eu_label"]
    assert label == {"fuel": "C", "noise_db": 71}


def test_noise_class_alone_makes_a_label() -> None:
    item = StoreClient._tire_item({**SUMMER, "eu_noise_class": "A"})
    assert item["eu_label"] == {"noise_class": "A"}


def test_no_label_columns_no_eu_label() -> None:
    assert "eu_label" not in StoreClient._tire_item(SUMMER)


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


# ── filter = label: the real query over a small catalogue ───────────────
#
# The search filters (``studded`` / ``xl``) and the item label read the same
# facts: the tshina attribute when the row has one, the description regex
# otherwise. In-memory SQLite runs the very SQL ``_query_tire_rows`` builds;
# only PostgreSQL ``~*`` / ``!~*`` are spelled as SQLite ``REGEXP`` (Python
# ``re``, case-insensitive — the patterns are plain enough for both, see
# ``test_fallback_regexes_read_the_1c_description``).

_SCHEMA = """
CREATE TABLE tire_models (
    id INTEGER PRIMARY KEY, manufacturer TEXT, name TEXT, seasonality TEXT, type_id TEXT
);
CREATE TABLE tire_products (
    sku TEXT PRIMARY KEY, model_id INTEGER, size TEXT, width INTEGER, profile INTEGER,
    diameter INTEGER, description TEXT, commercial BOOLEAN, load_rating TEXT,
    speed_rating TEXT
);
CREATE TABLE tire_stock (
    sku TEXT, trading_network TEXT, price REAL, stock_quantity INTEGER,
    country TEXT, year_issue TEXT
);
CREATE TABLE tire_eu_labels (
    sku TEXT, energy_class TEXT, wet_grip_class TEXT, noise_db INTEGER, noise_class TEXT
);
CREATE TABLE tire_attributes (
    sku TEXT PRIMARY KEY, studded TEXT, is_xl BOOLEAN, load_index TEXT, speed_index TEXT,
    season TEXT, is_runflat BOOLEAN, brand_title TEXT, model_name TEXT, width INTEGER,
    profile INTEGER, diameter REAL, size TEXT, commercial TEXT, suv BOOLEAN,
    electric_car BOOLEAN, tire_insulation TEXT, tire_rim_peeling TEXT, tire_ply TEXT,
    tire_technology TEXT
);
CREATE TABLE tshina_tire_models (
    model_1c_id INTEGER PRIMARY KEY, brand_title TEXT, name TEXT, season TEXT
);
CREATE TABLE product_ommology (sku TEXT PRIMARY KEY, codes TEXT);
"""

#: ``(sku, season, description, (studded, is_xl) of tire_attributes or None)``
#: — None = a SKU the attribute sync has not reached (no row at all).
_CATALOGUE: list[tuple[str, str, str, tuple[str | None, bool | None] | None]] = [
    # attribute vs description disagree → the attribute decides
    ("w-noship-desc-ship", "winter", "Nokian Hakka R5 [94T] Шип", ("no_ship", None)),
    ("w-ship-desc-none", "winter", "Nokian Hakka 10 [94T]", ("ship", None)),
    ("w-podship-desc-ship", "winter", "Michelin X-Ice North 4 [94T] Шип", ("pod_ship", None)),
    # no attribute value / no attribute row → the description regex, as before
    ("w-null-desc-ship", "winter", "Nokian Hakka 9 [91T] Шип", (None, None)),
    ("w-null-desc-podship", "winter", "Kumho WI51 [91T] Під шип", (None, None)),
    ("w-norow-desc-ship", "winter", "Nordman 8 [91T] Шип", None),
    ("w-norow-desc-none", "winter", "Matador Sibir Ice 2 WinSpike [91T]", None),
    # XL: attribute true with no «XL» text (most R18 winter tyres on prod)
    ("x-attr-true", "winter", "Pirelli Ice Zero 2 [103H]", ("no_ship", True)),
    ("x-attr-false-desc-xl", "summer", "Michelin Pilot Sport 4 XL [99Y]", (None, False)),
    ("x-null-desc-xl", "summer", "Hankook K135 XL [99Y]", (None, None)),
    ("x-norow-desc-xl", "summer", "Kumho PS71 Extra Load [99Y]", None),
    ("s-norow-plain", "summer", "Continental PC7 [95Y]", None),
]


#: ``tire_stock.country`` / ``year_issue`` / ``stock_quantity`` of some SKUs —
#: the rest NULL / NULL / 4. 1C spells the country in any case, and 7 % of
#: the rows in stock have none (prod 2026-10-27).
_ORIGINS: dict[str, tuple[str | None, str | None, int]] = {
    "w-noship-desc-ship": ("Китай", "2023", 1),
    "w-ship-desc-none": ("КИТАЙ", "25-24", 4),
    "x-null-desc-xl": ("Польща", "2024", 12),
    "x-attr-true": ("", "", 4),
    "s-norow-plain": (None, "2025", 2),
}


#: ``tire_eu_labels`` rows: ``(sku, energy_class, wet_grip_class, noise_db, noise_class)``.
_LABELS: list[tuple[str, str, str, int, str | None]] = [
    ("s-norow-plain", "C", "B", 71, "B"),
    ("x-null-desc-xl", "B", "A", 69, None),
]


def _regexp(pattern: str, value: str | None) -> bool | None:
    if value is None:
        return None
    return re.search(pattern, value, re.I) is not None


class _SqliteResult:
    #: Columns PostgreSQL hands back as booleans (SQLite gives 0/1).
    _BOOLS = ("runflat", "attr_xl", "desc_studded", "desc_studdable", "desc_xl")

    def __init__(self, cursor: Any) -> None:
        names = [c[0] for c in cursor.description]
        self._rows = []
        for values in cursor.fetchall():
            row = dict(zip(names, values, strict=True))
            for key in self._BOOLS:
                if row.get(key) is not None:
                    row[key] = bool(row[key])
            self._rows.append(row)

    def mappings(self) -> _SqliteResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _SqliteConn:
    def __init__(self, engine: _SqliteEngine) -> None:
        self._engine = engine

    async def __aenter__(self) -> _SqliteConn:
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False

    async def execute(self, query: Any, params: dict[str, Any]) -> _SqliteResult:
        sql = str(query)
        self._engine.queries.append(sql)
        sql = sql.replace("!~*", " NOT REGEXP ").replace("~*", " REGEXP ")
        return _SqliteResult(self._engine.db.execute(sql, params))


class _SqliteEngine:
    def __init__(self) -> None:
        import sqlite3

        from src.store_client.catalog_types import TIRE_SEARCH_TYPES

        self.queries: list[str] = []
        self.db = sqlite3.connect(":memory:")
        self.db.create_function("regexp", 2, _regexp, deterministic=True)
        self.db.executescript(_SCHEMA)
        type_id = sorted(TIRE_SEARCH_TYPES)[0]
        for i, (sku, season, description, attr) in enumerate(_CATALOGUE):
            self.db.execute(
                "INSERT INTO tire_models VALUES (?, ?, ?, ?, ?)",
                (i, description.split()[0], sku, season, type_id),
            )
            self.db.execute(
                "INSERT INTO tire_products VALUES (?, ?, '225/45 R18', 225, 45, 18, ?, 0, NULL, NULL)",
                (sku, i, description),
            )
            country, year, qty = _ORIGINS.get(sku, (None, None, 4))
            self.db.execute(
                "INSERT INTO tire_stock VALUES (?, 'ProKoleso', ?, ?, ?, ?)",
                (sku, 3000 + i, qty, country, year),
            )
            if attr is not None:
                self.db.execute(
                    "INSERT INTO tire_attributes (sku, studded, is_xl) VALUES (?, ?, ?)",
                    (sku, *attr),
                )
        for label in _LABELS:
            self.db.execute("INSERT INTO tire_eu_labels VALUES (?, ?, ?, ?, ?)", label)

    def connect(self) -> _SqliteConn:
        return _SqliteConn(self)


def _items(params: dict[str, Any]) -> dict[str, dict[str, Any]]:
    engine = _SqliteEngine()
    client = StoreClient(base_url="http://localhost", api_key="k", db_engine=engine)
    rows = asyncio.run(client._query_tire_rows("ProKoleso", {"width": 225, **params}))
    return {row["id"]: StoreClient._tire_item(row) for row in rows}


def test_sqlite_stand_reads_the_whole_catalogue() -> None:
    """Sanity of the stand: no filter → every SKU, labelled as before."""
    items = _items({})
    assert set(items) == {sku for sku, *_ in _CATALOGUE}
    assert items["w-noship-desc-ship"]["studded"] == "friction"
    assert items["w-null-desc-ship"]["studded"] == "studded"
    assert items["w-null-desc-podship"]["studded"] == "studdable"
    assert items["x-attr-true"]["xl"] is True
    assert "xl" not in items["x-attr-false-desc-xl"]


def test_sqlite_stand_carries_the_noise_class() -> None:
    """The real SELECT over ``tire_eu_labels``: the class when set, no key when NULL."""
    items = _items({})
    assert items["s-norow-plain"]["eu_label"] == {
        "fuel": "C",
        "wet": "B",
        "noise_db": 71,
        "noise_class": "B",
    }
    assert items["x-null-desc-xl"]["eu_label"] == {"fuel": "B", "wet": "A", "noise_db": 69}
    assert "eu_label" not in items["w-noship-desc-ship"]


@pytest.mark.parametrize(
    ("studded", "labels"),
    [(True, {"studded"}), (False, {"studdable", "friction"})],
)
def test_studs_filter_agrees_with_the_label(studded: bool, labels: set[str]) -> None:
    every = _items({})
    winter = {sku for sku, item in every.items() if "studded" in item}
    expected = {sku for sku in winter if every[sku]["studded"] in labels}
    found = _items({"studded": studded})
    assert set(found) & winter == expected
    for sku in found.keys() & winter:
        assert found[sku]["studded"] in labels


def test_studded_filter_follows_the_attribute_then_the_regex() -> None:
    found = set(_items({"studded": True}))
    # 1C «Шип» in the description, tshina says non-studded / stud-ready → out
    assert "w-noship-desc-ship" not in found
    assert "w-podship-desc-ship" not in found
    # tshina says studded with no «Шип» in the text → in
    assert "w-ship-desc-none" in found
    # no attribute (NULL value or no row) → the regex, exactly as before
    assert {"w-null-desc-ship", "w-norow-desc-ship"} <= found
    assert not {"w-null-desc-podship", "w-norow-desc-none"} & found


def test_unstudded_filter_takes_pod_ship_and_no_ship() -> None:
    found = set(_items({"studded": False}))
    assert {"w-noship-desc-ship", "w-podship-desc-ship", "x-attr-true"} <= found
    assert {"w-null-desc-podship", "w-norow-desc-none"} <= found
    assert not {"w-ship-desc-none", "w-null-desc-ship", "w-norow-desc-ship"} & found
    # summer: ``studded`` NULL = not applicable → the regex (no «шип») keeps them
    assert "s-norow-plain" in found


@pytest.mark.parametrize("xl", [True, False])
def test_xl_filter_agrees_with_the_label(xl: bool) -> None:
    every = _items({})
    expected = {sku for sku, item in every.items() if ("xl" in item) is xl}
    found = _items({"xl": xl})
    assert set(found) == expected
    assert all(("xl" in item) is xl for item in found.values())


def test_xl_filter_follows_the_attribute_then_the_regex() -> None:
    found = set(_items({"xl": True}))
    assert "x-attr-true" in found  # tshina XL, no «XL» in the text
    assert "x-attr-false-desc-xl" not in found  # tshina says not XL
    assert {"x-null-desc-xl", "x-norow-desc-xl"} <= found  # regex fallback
    assert "s-norow-plain" not in found


def test_no_filter_value_adds_no_attribute_condition() -> None:
    engine = _SqliteEngine()
    client = StoreClient(base_url="http://localhost", api_key="k", db_engine=engine)
    asyncio.run(client._query_tire_rows("ProKoleso", {"width": 225, "studded": None, "xl": None}))
    where = _squash(engine.queries[0]).split(" WHERE ", 1)[1]
    assert "a.studded" not in where
    assert "a.is_xl" not in where


# ── country, production year, stock (sales operator gaps 2026-10-27) ─────


def test_query_selects_country_and_year_of_the_stock_row() -> None:
    client, engine = _client([])
    asyncio.run(client._query_tire_rows("ProKoleso", {"width": 205}))
    sql = _squash(engine.queries[0])
    assert "s.country AS country" in sql
    assert "s.year_issue AS year_issue" in sql


def test_sqlite_stand_items_carry_country_year_and_quantity() -> None:
    """The real SELECT: what 1C has reaches the item; an empty value is no key."""
    items = _items({})
    assert items["w-noship-desc-ship"]["country"] == "Китай"
    assert items["w-noship-desc-ship"]["year"] == "2023"
    assert items["w-noship-desc-ship"]["quantity"] == 1
    assert items["x-null-desc-xl"]["quantity"] == 12
    assert (items["w-ship-desc-none"]["country"], items["w-ship-desc-none"]["year"]) == (
        "КИТАЙ",
        "25-24",
    )
    assert "country" not in items["x-attr-true"] and "year" not in items["x-attr-true"]
    assert "country" not in items["s-norow-plain"]
    assert items["s-norow-plain"]["year"] == "2025"
    assert "country" not in items["w-norow-desc-none"] and "year" not in items["w-norow-desc-none"]


def test_sqlite_stand_drops_a_refused_country_in_any_case() -> None:
    every = _items({})
    found = _items({"exclude_countries": ["Китай"]})
    assert "w-noship-desc-ship" not in found
    assert "w-ship-desc-none" not in found  # «КИТАЙ» in 1C
    assert "x-null-desc-xl" in found  # Польща
    # no country known (NULL / empty) — kept, not known to be from there
    assert {"x-attr-true", "s-norow-plain", "w-norow-desc-none"} <= set(found)
    assert set(found) == set(every) - {"w-noship-desc-ship", "w-ship-desc-none"}


def test_no_refused_country_adds_no_country_condition() -> None:
    client, engine = _client([])
    asyncio.run(client._query_tire_rows("ProKoleso", {"width": 205, "exclude_countries": []}))
    where = _squash(engine.queries[0]).split(" WHERE ", 1)[1]
    assert "country" not in where
