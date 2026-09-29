"""The EU label in the catalogue tyre search (``tire_eu_labels``, migration 064).

``_query_tire_rows`` LEFT JOINs the label by SKU, ``_tire_item`` puts
``eu_label`` on the item only when the row has one — a tyre without a label
row carries no key, so nothing downstream reads an empty label as data.
No real database: a fake engine records the SQL and hands back rows.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.store_client.client import StoreClient

BASE = {
    "id": "0001",
    "brand": "Bridgestone",
    "model": "Turanza 6",
    "size": "215/55 R17",
    "season": "літня",
    "price": 6687,
    "stock_quantity": 4,
    "runflat": False,
}


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


def test_query_left_joins_the_label_by_sku() -> None:
    client, engine = _client([])
    asyncio.run(client._query_tire_rows("ProKoleso", {"width": 215}))
    sql = _squash(engine.queries[0])
    assert "LEFT JOIN tire_eu_labels l ON l.sku = p.sku" in sql
    for column in (
        "l.energy_class AS eu_fuel",
        "l.wet_grip_class AS eu_wet",
        "l.noise_db AS eu_noise_db",
    ):
        assert column in sql
    # a plain JOIN would drop every tyre without a label from the result
    assert "JOIN tire_eu_labels" not in sql.replace("LEFT JOIN tire_eu_labels", "")


def test_item_with_a_label() -> None:
    row = {**BASE, "eu_fuel": "C", "eu_wet": "B", "eu_noise_db": 71}
    item = StoreClient._tire_item(row)
    assert item["eu_label"] == {
        "fuel": row["eu_fuel"],
        "wet": row["eu_wet"],
        "noise_db": row["eu_noise_db"],
    }


@pytest.mark.parametrize(
    "extra",
    [
        {},  # a row without the columns (another query, an old engine)
        {"eu_fuel": None, "eu_wet": None, "eu_noise_db": None},  # no label row
        {"eu_fuel": " ", "eu_wet": "", "eu_noise_db": None},
    ],
)
def test_item_without_a_label_has_no_key(extra: dict[str, Any]) -> None:
    item = StoreClient._tire_item({**BASE, **extra})
    assert "eu_label" not in item


def test_partial_label_keeps_only_what_is_there() -> None:
    item = StoreClient._tire_item({**BASE, "eu_fuel": None, "eu_wet": "a", "eu_noise_db": 70})
    assert item["eu_label"] == {"wet": "A", "noise_db": 70}
    item = StoreClient._tire_item({**BASE, "eu_fuel": "B", "eu_wet": None, "eu_noise_db": None})
    assert item["eu_label"] == {"fuel": "B"}


def test_search_puts_the_label_on_items_that_have_one() -> None:
    labelled = {**BASE, "eu_fuel": "A", "eu_wet": "A", "eu_noise_db": 71}
    bare = {
        **BASE,
        "id": "0002",
        "model": "Turanza T005",
        "eu_fuel": None,
        "eu_wet": None,
        "eu_noise_db": None,
    }
    client, engine = _client([labelled, bare])
    res = asyncio.run(
        client._search_tires_db(network="ProKoleso", width=215, profile=55, diameter=17)
    )
    assert "tire_eu_labels" in engine.queries[0]
    by_id = {i["id"]: i for i in res["items"]}
    assert by_id["0001"]["eu_label"] == {"fuel": "A", "wet": "A", "noise_db": 71}
    assert "eu_label" not in by_id["0002"]
