"""Wheel catalog sync: ``size`` parsing, ``disk_products`` wiring, C-tyre diameter.

The parser corpus is real 1C ``size`` values of wheel SKU (``type_id='566'``)
from prod 2026-09-28: every value that deviates from the plain
``16 4/100x6.5 ET50 DIA54.1`` shape, plus 200 regular ones stratified by
diameter and bolt count.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest

from scripts.import_vehicle_db import _insert_disk_sizes, import_data, iter_disk_sizes
from src.onec_client.sync import (
    CatalogSyncService,
    _parse_tire_diameter,
    parse_disk_color,
    parse_disk_size,
)
from src.store_client.catalog_types import PASSENGER_TIRE, WHEEL

if TYPE_CHECKING:
    from pathlib import Path

# Corpus (prod 2026-09-28, wheel SKU ``size``): every non-plain value + 200 plain ones

_ODD_SIZES = (
    "13 8/100-114.3x4.5 ET43 DIA69.1",
    "15 4/100-108x6.5 ET35 DIA72.6",
    "15 4/100-108x6.5 ET38 DIA72.3",
    "15 5/100-112x6.5 ET35 DIA57.1",
    "15C 0/x195 ET DIA",
    "16 5/112x6.5 ET37 DIA66,6",
    "16 5/114,3x6.5 ET38 DIA67,1",
    "16 5/114,3x6.5 ET46 DIA67,1",
    "17 4/100-108x7.0 ET35 DIA72.3",
    "17 4/100-108x7.5 ET35 DIA72.6",
    "17 5/100-112x7.5 ET42 DIA57.1",
    "17 5/100-112x7.5 ET42 DIA67.1",
    "17 5/100-112x7.5 ET45 DIA57.1",
    "17 5/112-120x7.5 ET42 DIA72.6",
    "17 5/114,3x7.0 ET48 DIA67.1",
    "18 4/100-108x8.0 ET40 DIA72.6",
    "18 5/100-112x8.0 ET42 DIA72.6",
    "18 5/100-112x8.0 ET45 DIA72.6",
    "18 5/108-114.3x8.0 ET42 DIA72.6",
    "18 5/112-120x8.0 ET40 DIA72.6",
    "18 5/112-120x8.0 ET42 DIA72.6",
    "18 5/114,3x8.0 ET45 DIA67.1",
    "19 5/100-112x8.0 ET35 DIA57.1",
    "20 5/100-112x8.0 ET32 DIA66.6",
    "20 5/100-112x8.0 ET45 DIA57.1",
    "20 6/139.7x8.5 ET15 DIA110.5-108",
    "20 6/139.7x8.5 ET15 DIA110.5-108.",
    "21 5/130x<Пус ET58 DIA71.6",
)
_REGULAR_SIZES = (
    "13 3/256x4.5 ET30 DIA228",
    "13 4/098x4.5 ET32 DIA56.6",
    "13 4/098x5 ET40 DIA59",
    "13 4/098x5.0 ET29 DIA58.6",
    "13 4/100x5.0 ET49 DIA56.6",
    "13 4/98x5.0 ET35 DIA58.6",
    "13 5/112x5.5 ET30 DIA67.1",
    "13 8/100x4.5 ET43 DIA69.1",
    "14 4/098x5 ET35 DIA58.6",
    "14 4/100x5.5 ET35 DIA57.1",
    "14 4/100x5.5 ET43 DIA60.1",
    "14 4/100x5.5 ET45 DIA56.56",
    "14 4/114.3x6.0 ET37 DIA67.1",
    "14 4/114.3x6.0 ET38 DIA67.1",
    "14 5/100x5.0 ET35 DIA57",
    "15 3/112x5.0 ET25 DIA57.1",
    "15 3/112x5.0 ET30 DIA57.1",
    "15 4/098x6.5 ET35 DIA58.1",
    "15 4/100x5.5 ET45 DIA60",
    "15 4/108x6.0 ET40 DIA63.4",
    "15 4/108x6.0 ET45 DIA63.3",
    "15 4/108x6.0 ET52.5 DIA63.3",
    "15 4/108x6.5 ET25 DIA65.1",
    "15 4/108x6.5 ET27 DIA65",
    "15 4/108x6.5 ET36 DIA67.1",
    "15 4/108x6.5 ET38 DIA67.1",
    "15 4/114.3x6.0 ET45 DIA56.6",
    "15 5/098x6.5 ET35 DIA67.1",
    "15 5/100x6.5 ET40 DIA67.1",
    "15 5/108x6.5 ET38 DIA63.4",
    "15 5/114.3x6 ET45 DIA76",
    "15 5/114.3x6.0 ET39 DIA60",
    "15 5/114.3x6.0 ET39 DIA60.1",
    "15 5/114.3x6.5 ET40 DIA72.3",
    "15 5/114.3x6.5 ET45 DIA67.1",
    "15 5/130x6.0 ET75 DIA84.1",
    "15 5/98x6.0 ET38 DIA58.1",
    "15 6/139.7x7.0 ET0 DIA110",
    "16 10/110x7.0 ET40 DIA73.1",
    "16 4/098x7 ET38 DIA67.1",
    "16 4/100x6.0 ET37 DIA60.1",
    "16 4/100x6.0 ET45 DIA60.1",
    "16 4/100x7 ET38 DIA67.1",
    "16 4/114.3x7.0 ET38 DIA67.1",
    "16 4/114.3x7.0 ET40 DIA57.1",
    "16 5/098x7 ET38 DIA58.1",
    "16 5/100x6.5 ET35 DIA57.1",
    "16 5/100x7 ET38 DIA67.1",
    "16 5/108x6.5 ET45 DIA72",
    "16 5/108x6.5 ET52.5 DIA63.4",
    "16 5/108x7.0 ET38 DIA67.1",
    "16 5/108x7.0 ET40 DIA67.1",
    "16 5/110x6.5 ET45 DIA67.1",
    "16 5/112x6.5 ET38 DIA57.1",
    "16 5/112x6.5 ET45 DIA57.1",
    "16 5/112x7 ET40 DIA57.1",
    "16 5/112x7.0 ET40 DIA73.1",
    "16 5/112x7.0 ET45 DIA57",
    "16 5/114.3x6.5 ET35 DIA66.1",
    "16 5/120x6.5 ET51 DIA65.1",
    "16 5/139.7x7.5 ET0 DIA108",
    "16 5/98x6.5 ET25 DIA58.1",
    "16 6/114.3x7 ET40 DIA66.1",
    "16 6/139.7x7 ET40 DIA67.1",
    "16 6/139.7x7.0 ET40 DIA100.1",
    "16 6/139.7x7.0 ET42 DIA92.5",
    "16 6/170x5.5 ET113 DIA130",
    "16 8/100x7.0 ET35 DIA72.3",
    "17 4/098x7.5 ET25 DIA58.1",
    "17 4/100x6.5 ET43 DIA60.1",
    "17 4/108x7.0 ET25 DIA65.1",
    "17 4/108x7.5 ET40 DIA67.1",
    "17 5/100x7 ET38 DIA57.1",
    "17 5/108x7.5 ET45 DIA67.1",
    "17 5/112x7.0 ET40 DIA57.1",
    "17 5/112x7.0 ET42 DIA66.6",
    "17 5/112x7.0 ET48.5 DIA57.1",
    "17 5/112x7.5 ET45 DIA72.3",
    "17 5/112x8.0 ET45 DIA79.6",
    "17 5/114.3x6.5 ET38 DIA67.1",
    "17 5/114.3x7.0 ET40 DIA66.1",
    "17 5/114.3x7.0 ET42 DIA67.1",
    "17 5/114.3x7.5 ET50 DIA72.3",
    "17 5/114.3x8.0 ET45 DIA72.3",
    "17 5/120x7.5 ET20 DIA72.6",
    "17 5/120x7.5 ET35 DIA72.6",
    "17 5/98x7.0 ET35 DIA58.1",
    "17 6/114.3x7.5 ET30 DIA66.1",
    "17 6/139.7x7.5 ET24 DIA106.1",
    "17 6/139.7x7.5 ET30 DIA106.1",
    "17 6/139.7x7.5 ET40 DIA67.1",
    "18 4/100x7 ET52 DIA56.1",
    "18 5/100x7.0 ET48 DIA56.1",
    "18 5/108x7.0 ET49 DIA67.1",
    "18 5/108x7.5 ET50.5 DIA63.4",
    "18 5/108x7.5 ET52.5 DIA63.4",
    "18 5/108x8.0 ET42 DIA63.4",
    "18 5/108x8.0 ET45 DIA67.1",
    "18 5/110x7.5 ET37 DIA65.1",
    "18 5/110x7.5 ET41 DIA65.1",
    "18 5/112x7.5 ET40 DIA57.1",
    "18 5/112x7.5 ET43 DIA66.6",
    "18 5/112x7.5 ET47 DIA66.6",
    "18 5/112x8 ET33 DIA66.6",
    "18 5/112x8 ET39 DIA57.1",
    "18 5/112x8 ET39 DIA76",
    "18 5/112x8.0 ET35 DIA57.1",
    "18 5/112x8.0 ET48 DIA72.3",
    "18 5/112x8.0 ET60 DIA66.6",
    "18 5/112x8.5 ET29 DIA66.6",
    "18 5/112x9.0 ET42 DIA79.6",
    "18 5/114.3x7.0 ET45 DIA60.1",
    "18 5/114.3x7.5 ET48 DIA76",
    "18 5/114.3x8 ET40 DIA67.1",
    "18 5/114.3x8 ET45 DIA72.6",
    "18 5/114.3x8.0 ET50 DIA76",
    "18 5/120x7.5 ET43 DIA74.1",
    "18 5/120x7.5 ET52 DIA72.6",
    "18 5/120x8 ET45 DIA72.6",
    "18 5/120x8.0 ET30 DIA72.6",
    "18 5/120x8.0 ET35 DIA72.6",
    "18 5/120x8.0 ET42 DIA72.6",
    "18 5/120x8.5 ET35 DIA74.1",
    "18 5/120x8.5 ET37 DIA72.6",
    "18 5/120x8.5 ET46 DIA72.6",
    "18 6/114.3x8.0 ET25 DIA67.1",
    "18 6/139.7x7.5 ET25 DIA106",
    "18 6/139.7x7.5 ET30 DIA106.1",
    "19 5/105x8.0 ET40 DIA56.6",
    "19 5/108x8.5 ET42 DIA63.4",
    "19 5/110x9.0 ET34 DIA65.1",
    "19 5/112x7.5 ET40 DIA57.1",
    "19 5/112x8 ET21 DIA66.5",
    "19 5/112x8.5 ET30 DIA66.6",
    "19 5/112x8.5 ET38 DIA57.1",
    "19 5/112x8.5 ET40 DIA66.5",
    "19 5/112x9.5 ET31 DIA66.6",
    "19 5/112x9.5 ET32 DIA66.6",
    "19 5/114.3x7.5 ET40 DIA60.1",
    "19 5/114.3x7.5 ET49.5 DIA67.1",
    "19 5/114.3x7.5 ET50 DIA64.1",
    "19 5/114.3x8 ET40 DIA76",
    "19 5/114.3x8.0 ET34 DIA67.1",
    "19 5/114.3x8.0 ET35 DIA60.1",
    "19 5/114.3x8.0 ET45 DIA67.1",
    "19 5/120x10.0 ET21 DIA72.6",
    "19 5/120x8.5 ET12 DIA72.6",
    "19 5/120x8.5 ET25 DIA72.6",
    "19 5/120x9 ET37 DIA74.1",
    "19 5/120x9.0 ET18 DIA74.1",
    "19 5/120x9.0 ET25 DIA72.6",
    "19 5/120x9.0 ET37 DIA72.6",
    "19 5/120x9.0 ET41 DIA74.1",
    "19 5/120x9.0 ET44 DIA74.1",
    "19 5/120x9.0 ET48 DIA74.1",
    "19 5/120x9.5 ET19 DIA74.1",
    "19 5/120x9.5 ET23 DIA72.6",
    "19 5/130x12.0 ET51 DIA71.6",
    "19 5/130x8 ET57 DIA71.6",
    "20 10/335x8.5 ET163 DIA281",
    "20 5/108x8 ET48.5 DIA63.4",
    "20 5/112x8.5 ET30 DIA66.6",
    "20 5/112x8.5 ET35 DIA76",
    "20 5/112x8.5 ET35 DIA79.6",
    "20 5/112x8.5 ET40 DIA66.6",
    "20 5/112x9.0 ET29 DIA66.6",
    "20 5/112x9.0 ET52 DIA57.1",
    "20 5/112x9.5 ET41 DIA76",
    "20 5/120x10.0 ET30 DIA64.1",
    "20 5/120x10.0 ET41 DIA72.6",
    "20 5/120x11.0 ET35 DIA72.6",
    "20 5/120x8.5 ET43 DIA72.6",
    "20 5/120x8.5 ET45 DIA72.6",
    "20 5/120x9 ET40 DIA64.1",
    "20 5/120x9.0 ET30 DIA74.1",
    "20 5/120x9.0 ET42 DIA79.6",
    "20 5/120x9.0 ET44 DIA72.6",
    "20 5/120x9.5 ET45 DIA72.56",
    "20 5/120x9.5 ET50 DIA72.56",
    "20 5/127x8.0 ET40 DIA71.6",
    "20 5/130x11.5 ET50 DIA71.5",
    "20 5/130x9.0 ET57 DIA71.6",
    "20 5/150x9.0 ET45 DIA110.1",
    "20 6/114.3x9.0 ET30 DIA66.1",
    "20 6/139.7x9.0 ET40 DIA67.1",
    "21 5/112x10 ET19 DIA66.5",
    "21 5/112x10.0 ET44 DIA66.6",
    "21 5/112x11 ET49 DIA66.6",
    "21 5/112x11.0 ET38 DIA66.6",
    "21 5/112x9 ET26 DIA66.5",
    "21 5/112x9.5 ET20 DIA66.6",
    "21 5/112x9.5 ET25 DIA66.6",
    "21 5/112x9.5 ET43 DIA66.5",
    "21 5/114.3x9.5 ET51 DIA66.1",
    "21 5/130x10.0 ET45 DIA71.6",
    "21 5/130x9.5 ET46 DIA71.6",
    "22 5/112x10 ET12 DIA66.5",
    "22 5/120x9.5 ET48 DIA72.6",
    "22 6/139.7x10.0 ET20 DIA106.1",
    "23 5/130x10.5 ET47 DIA71.6",
)


# ---------------------------------------------------------------------------
# Recording fake for AsyncEngine: begin() -> async CM -> conn.execute(sql, rows)
# ---------------------------------------------------------------------------


def _inserts_into(sql: str, table: str) -> bool:
    return re.search(rf"INSERT INTO {table}\b", sql) is not None


class _EmptyResult:
    """Result of a SELECT on empty tables: no rows, counts of 1 (RETURNING id)."""

    def __iter__(self) -> Any:
        return iter(())

    def scalar_one(self) -> int:
        return 1


class _RecordingConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def execute(self, sql: Any, params: Any = None) -> _EmptyResult:
        self.calls.append((str(sql), params))
        return _EmptyResult()

    def rows_for(self, table: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for sql, params in self.calls:
            if _inserts_into(sql, table):
                rows.extend(params)
        return rows


class _Ctx:
    def __init__(self, conn: _RecordingConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _RecordingConn:
        return self._conn

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _RecordingEngine:
    def __init__(self) -> None:
        self.conn = _RecordingConn()

    def begin(self) -> _Ctx:
        return _Ctx(self.conn)


def _service(engine: _RecordingEngine) -> CatalogSyncService:
    return CatalogSyncService(onec_client=None, db_engine=engine)  # type: ignore[arg-type]


_TYRE_WARE = {
    "type": PASSENGER_TIRE,
    "model_id": "000167134",
    "model": "Winter1",
    "manufacturer_id": "000022523",
    "manufacturer": "Tigar",
    "seasonality": "Зимняя",
    "tread_pattern_type": "",
    "product": [
        {
            "sku": "00000019835",
            "text": "155/70 R13 Tigar Winter1 [75T]",
            "diametr": "13",
            "size": "155/70R13",
            "profile_height": "70",
            "profile_width": "155",
            "speed_rating": "T",
            "load_rating": "75",
            "studded": "",
        },
        {
            "sku": "00000050001",
            "text": "195/75 R16C Tigar Cargo Speed [107/105R]",
            "diametr": "16C",
            "size": "195/75R16C",
            "profile_height": "75",
            "profile_width": "195",
            "speed_rating": "R",
            "load_rating": "107/105",
            "studded": "",
        },
    ],
}

_WHEEL_WARE = {
    "type": WHEEL,
    "model_id": "000900001",
    "model": "W1903",
    "manufacturer_id": "000900000",
    "manufacturer": "WSP Italy",
    "seasonality": "",
    "tread_pattern_type": "",
    "product": [
        {
            "sku": "00000036319",
            "text": "16 4/100х6.5 ЕТ50 DIA 54.1 W1903 ANTHRACITE WSP Italy",
            "diametr": "16",
            "size": "16 4/100x6.5 ET50 DIA54.1",
            "profile_height": "",
            "profile_width": "",
            "speed_rating": "",
            "load_rating": "",
            "studded": "",
        },
        {
            "sku": "00000036320",
            "text": "21 5/130х ЕТ58 DIA 71.6 W1903 SILVER WSP Italy",
            "diametr": "21",
            "size": "21 5/130x<Пус ET58 DIA71.6",
            "profile_height": "",
            "profile_width": "",
            "speed_rating": "",
            "load_rating": "",
            "studded": "",
        },
    ],
}


# ---------------------------------------------------------------------------
# parse_disk_size — corpus invariants
# ---------------------------------------------------------------------------

_UNREADABLE = {"15C 0/x195 ET DIA", "21 5/130x<Пус ET58 DIA71.6"}
_LEADING = re.compile(r"^(\d+) (\d+)/")


@pytest.mark.parametrize("size", [*_ODD_SIZES, *_REGULAR_SIZES])
def test_corpus_parses_everything_but_the_two_known_unreadable(size: str) -> None:
    parsed = parse_disk_size(size)
    assert parsed["parse_ok"] is (size not in _UNREADABLE)
    if not parsed["parse_ok"]:
        assert all(v is None for k, v in parsed.items() if k != "parse_ok")
        return
    for key in ("diameter", "width_j", "bolt_count", "pcd", "et", "dia"):
        assert parsed[key] is not None, key
    lead = _LEADING.match(size)
    assert lead is not None
    assert parsed["diameter"] == int(lead.group(1))
    assert parsed["bolt_count"] == int(lead.group(2))
    # a second PCD is exactly the ``-NNN`` between the slash and the ``x``
    assert (parsed["pcd_alt"] is not None) is bool(re.search(r"/[\d.,]+-[\d.,]+x", size))
    for key in ("width_j", "pcd", "pcd_alt", "et", "dia"):
        value = parsed[key]
        assert value is None or isinstance(value, Decimal), key


def test_corpus_counts_match_prod_measure() -> None:
    """FINDINGS I §2: 23 multi-PCD values in the whole catalog, all in _ODD_SIZES."""
    odd = [parse_disk_size(s) for s in _ODD_SIZES]
    assert sum(p["pcd_alt"] is not None for p in odd) == len(
        [s for s in _ODD_SIZES if re.search(r"/[\d.,]+-", s)]
    )
    assert [s for s in _ODD_SIZES if not parse_disk_size(s)["parse_ok"]] == sorted(_UNREADABLE)


def test_plain_size() -> None:
    assert parse_disk_size("16 4/100x6.5 ET50 DIA54.1") == {
        "diameter": 16,
        "width_j": Decimal("6.5"),
        "bolt_count": 4,
        "pcd": Decimal("100"),
        "pcd_alt": None,
        "et": Decimal("50"),
        "dia": Decimal("54.1"),
        "parse_ok": True,
    }


def test_multi_pcd_keeps_both() -> None:
    parsed = parse_disk_size("18 5/108-114.3x8.0 ET42 DIA72.6")
    assert parsed["pcd"] == Decimal("108")
    assert parsed["pcd_alt"] == Decimal("114.3")


def test_comma_decimal_mark() -> None:
    parsed = parse_disk_size("16 5/114,3x6.5 ET38 DIA67,1")
    assert parsed["pcd"] == Decimal("114.3")
    assert parsed["dia"] == Decimal("67.1")


def test_dia_range_takes_first_number() -> None:
    assert parse_disk_size("20 6/139.7x8.5 ET15 DIA110.5-108.")["dia"] == Decimal("110.5")


def test_negative_and_fractional_et() -> None:
    assert parse_disk_size("17 5/120x8.0 ET-12 DIA72.6")["et"] == Decimal("-12")
    assert parse_disk_size("18 5/100x7.5 ET39.5 DIA57.1")["et"] == Decimal("39.5")


@pytest.mark.parametrize(
    "size",
    [
        "",
        None,
        "   ",
        "205/55R16",
        "16 4/100x6.5 ET50",
        # wider than the column: would abort the whole wares transaction
        "16 4/100x1000 ET50 DIA54.1",
        "16 4/100x6.5 ET50 DIA10000",
        "99999 4/100x6.5 ET50 DIA54.1",
    ],
)
def test_garbage_is_not_parsed(size: str | None) -> None:
    assert parse_disk_size(size)["parse_ok"] is False


# ---------------------------------------------------------------------------
# parse_disk_color
# ---------------------------------------------------------------------------


def test_color_between_model_and_manufacturer() -> None:
    assert (
        parse_disk_color(
            "16 4/100х6.5 ЕТ50 DIA 54.1 W1903 ANTHRACITE WSP Italy", "W1903", "WSP Italy"
        )
        == "ANTHRACITE"
    )
    assert (
        parse_disk_color("16 5/98х7.0 ЕТ38 DIA 58.1 602 S Disla (Повреждение ЛКП)", "602", "Disla")
        == "S"
    )


def test_color_none_when_model_is_not_in_description() -> None:
    assert (
        parse_disk_color("18 5/120х8.0 ЕТ30 DIA 72.6 W671 HYPER WSP Italy", "W671.", "WSP Italy")
        is None
    )
    assert parse_disk_color("", "W1", "X") is None


# ---------------------------------------------------------------------------
# D2 — C-tyre diameter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("16C", (16, True)),
        ("15c", (15, True)),
        ("16С", (16, True)),  # Cyrillic С
        ("16", (16, False)),
        (17, (17, False)),
        ("", (0, False)),
        (None, (0, False)),
        ("22.5", (0, False)),  # half-inch truck sizes: not fixed here, see PROGRESS
    ],
)
def test_parse_tire_diameter(raw: str | int | None, expected: tuple[int, bool]) -> None:
    assert _parse_tire_diameter(raw) == expected


# ---------------------------------------------------------------------------
# _upsert_wares wiring — mixed batch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mixed_batch_wheels_go_to_disk_products_tyres_do_not() -> None:
    engine = _RecordingEngine()
    await _service(engine)._upsert_wares([_TYRE_WARE, _WHEEL_WARE])

    disk_skus = {row["sku"] for row in engine.conn.rows_for("disk_products")}
    assert disk_skus == {"00000036319", "00000036320"}

    # catalog, price and stock stay shared: every SKU still lands in tire_products
    product_skus = {row["sku"] for row in engine.conn.rows_for("tire_products")}
    assert product_skus == {"00000019835", "00000050001", "00000036319", "00000036320"}


@pytest.mark.asyncio
async def test_disk_rows_are_written_after_products() -> None:
    """disk_products.sku references tire_products — order matters in one transaction."""
    engine = _RecordingEngine()
    await _service(engine)._upsert_wares([_WHEEL_WARE])
    tables = [
        t
        for sql, _ in engine.conn.calls
        for t in ("tire_models", "tire_products", "disk_products")
        if _inserts_into(sql, t)
    ]
    assert tables == ["tire_models", "tire_products", "disk_products"]


@pytest.mark.asyncio
async def test_unparsed_wheel_is_written_with_parse_ok_false_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = _RecordingEngine()
    with caplog.at_level("WARNING", logger="src.onec_client.sync"):
        await _service(engine)._upsert_wares([_WHEEL_WARE])
    rows = {row["sku"]: row for row in engine.conn.rows_for("disk_products")}
    assert rows["00000036319"]["parse_ok"] is True
    assert rows["00000036319"]["pcd"] == Decimal("100")
    assert rows["00000036319"]["color"] == "ANTHRACITE"
    assert rows["00000036320"]["parse_ok"] is False
    assert rows["00000036320"]["pcd"] is None
    assert "1 of 2" in caplog.text
    assert "00000036320" in caplog.text


@pytest.mark.asyncio
async def test_tyre_only_batch_writes_no_disk_rows() -> None:
    engine = _RecordingEngine()
    await _service(engine)._upsert_wares([_TYRE_WARE])
    assert engine.conn.rows_for("disk_products") == []
    assert not any("disk_products" in sql for sql, _ in engine.conn.calls)


@pytest.mark.asyncio
async def test_c_tyre_stored_with_diameter_and_commercial_flag() -> None:
    engine = _RecordingEngine()
    await _service(engine)._upsert_wares([_TYRE_WARE])
    rows = {row["sku"]: row for row in engine.conn.rows_for("tire_products")}
    assert rows["00000050001"]["diameter"] == 16
    assert rows["00000050001"]["commercial"] is True
    assert rows["00000019835"]["diameter"] == 13
    assert rows["00000019835"]["commercial"] is False
    product_sql = next(sql for sql, _ in engine.conn.calls if _inserts_into(sql, "tire_products"))
    assert "commercial = EXCLUDED.commercial" in product_sql


# ---------------------------------------------------------------------------
# vehicle_disk_sizes CSV import
# ---------------------------------------------------------------------------

_CSV = (
    '"id","kit","width","diameter","et","type","axle","axle_group"\n'
    '"1","1","7.50","18.00","45.0","1","0",NULL\n'
    '"2","1","8.00","18.00",NULL,"2","1","0"\n'
    '"3","2","","17.00","40.0","1","0",NULL\n'
    '4,3,6.25,16.00,-12.5,"2","2","3"\n'
)


def _write_csv(tmp_path: Path) -> Path:
    (tmp_path / "test_table_car2_kit_disk_size.csv").write_text(_CSV, encoding="utf-8")
    return tmp_path


def test_disk_size_csv_uneven_quoting_and_null_et(tmp_path: Path) -> None:
    rows = [r for batch in iter_disk_sizes(_write_csv(tmp_path)) for r in batch]
    assert [r["id"] for r in rows] == [1, 2, 4]  # row 3 has no width
    assert rows[0] == {
        "id": 1,
        "kit_id": 1,
        "width": Decimal("7.50"),
        "diameter": Decimal("18.00"),
        "et": Decimal("45.0"),
        "type": 1,
        "axle": 0,
        "axle_group": None,
    }
    assert rows[1]["et"] is None
    assert rows[1]["type"] == 2
    assert rows[1]["axle_group"] == 0
    assert rows[2]["et"] == Decimal("-12.5")
    assert rows[2]["width"] == Decimal("6.25")


def test_disk_size_csv_batches(tmp_path: Path) -> None:
    batches = list(iter_disk_sizes(_write_csv(tmp_path), batch_size=2))
    assert [len(b) for b in batches] == [2, 1]


def test_disk_size_csv_missing_is_empty(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        assert list(iter_disk_sizes(tmp_path)) == []
    assert "vehicle_disk_sizes left empty" in caplog.text


@pytest.mark.asyncio
async def test_insert_disk_sizes_writes_every_row(tmp_path: Path) -> None:
    conn = _RecordingConn()
    inserted = await _insert_disk_sizes(conn, _write_csv(tmp_path))
    assert inserted == 3
    assert [r["id"] for r in conn.rows_for("vehicle_disk_sizes")] == [1, 2, 4]


def _write_vehicle_db(tmp_path: Path) -> Path:
    (tmp_path / "test_table_car2_brand.csv").write_text(
        '"id","name"\n"1","Acura"\n', encoding="utf-8"
    )
    (tmp_path / "test_table_car2_model.csv").write_text(
        '"id","brand","name"\n"1","1","CDX"\n"2","1","CL"\n"3","1","MDX"\n', encoding="utf-8"
    )
    (tmp_path / "test_table_car2_kit.csv").write_text(
        '"id","model","year","name","pcd","bolt_count","dia","bolt_size"\n'
        '"1","1","2016","1.5 Turbo","114.30","5","64.10","M12 x 1.5"\n'
        '"2","2","2017","2.0","114.30","5","64.10","M12 x 1.5"\n'
        '"3","3","2018","3.5","120.00","5","64.10","M14 x 1.5"\n',
        encoding="latin-1",
    )
    (tmp_path / "test_table_car2_kit_tyre_size.csv").write_text(
        '"id","kit","width","height","diameter","type","axle","axle_group"\n'
        '"1","1","235.00","50.00","18.00","1","0",NULL\n',
        encoding="utf-8",
    )
    return _write_csv(tmp_path)


@pytest.mark.asyncio
async def test_import_apply_refreshes_disk_sizes_after_kits(tmp_path: Path) -> None:
    engine = _RecordingEngine()
    await import_data(engine, _write_vehicle_db(tmp_path), mode="apply")  # type: ignore[arg-type]
    markers = (
        r"DELETE FROM vehicle_kits\b",
        r"INSERT INTO vehicle_kits\b",
        r"INSERT INTO vehicle_disk_sizes\b",
    )
    seen = [m for sql, _ in engine.conn.calls for m in markers if re.search(m, sql)]
    assert seen == list(markers)
    assert [r["id"] for r in engine.conn.rows_for("vehicle_disk_sizes")] == [1, 2, 4]


@pytest.mark.asyncio
async def test_import_dryrun_reports_disk_count_and_writes_nothing(tmp_path: Path) -> None:
    engine = _RecordingEngine()
    result = await import_data(engine, _write_vehicle_db(tmp_path), mode="dryrun")  # type: ignore[arg-type]
    assert result["diff_report"]["disk_sizes"] == {"csv_count": 3}
    assert engine.conn.rows_for("vehicle_disk_sizes") == []
