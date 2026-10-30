"""A model one character off is asked about (call c096e663, 2026-10-29).

«Suzuki S4» — STT lost the «X» of SX4; the lookup said «не знайдено» and the
caller hung up. The not-found answer now names the brand's models one
character away, and the bot asks «можливо, SX4?».
"""

from __future__ import annotations

from typing import Any

import pytest

from src.agent.tool_result_compressor import compress_tool_result
from src.store_client.client import StoreClient, near_model_names

SUZUKI = ["SX4", "SX4 S-Cross", "SX4 Sedan", "Swift", "Vitara", "Grand Vitara", "Jimny"]


class TestNearNames:
    @pytest.mark.parametrize(
        ("heard", "near"),
        [("S4", ["SX4"]), ("Vitra", ["Vitara"]), ("Jimni", ["Jimny"]), ("sx-4", [])],
    )
    def test_one_character_away(self, heard: str, near: list[str]) -> None:
        assert near_model_names(heard, SUZUKI) == near

    @pytest.mark.parametrize("heard", ["X", "", "Corolla"])
    def test_nothing_near(self, heard: str) -> None:
        assert near_model_names(heard, SUZUKI) == []

    def test_at_most_three(self) -> None:
        assert len(near_model_names("A4", ["A1", "A3", "A5", "A6", "A7"])) == 3


class _Rows:
    def __init__(self, first: Any = None, rows: list[tuple[str]] | None = None) -> None:
        self._first, self._rows = first, rows or []

    def mappings(self) -> _Rows:
        return self

    def first(self) -> Any:
        return self._first

    def all(self) -> list[tuple[str]]:
        return list(self._rows)

    def __iter__(self) -> Any:
        return iter(self._rows)


class _Conn:
    async def __aenter__(self) -> _Conn:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def execute(self, query: Any, params: Any = None) -> _Rows:
        sql = str(query)
        if "FROM vehicle_brands" in sql:
            return _Rows({"id": 7, "name": "Suzuki"})
        if "SELECT DISTINCT name FROM vehicle_models" in sql:
            return _Rows(rows=[(n,) for n in SUZUKI])
        return _Rows()


class _Engine:
    def connect(self) -> _Conn:
        return _Conn()


class TestLookup:
    @pytest.mark.asyncio
    async def test_not_found_names_the_near_model(self) -> None:
        client = StoreClient(base_url="http://x", api_key="k", db_engine=_Engine())
        result = await client.get_vehicle_tire_sizes(brand="Suzuki", model="S4", year=2020)
        assert result["found"] is False
        assert result["did_you_mean"] == ["SX4"]
        assert "SX4" in result["message"]
        assert "перепитай" in result["message"]

    @pytest.mark.asyncio
    async def test_nothing_near_keeps_the_plain_answer(self) -> None:
        client = StoreClient(base_url="http://x", api_key="k", db_engine=_Engine())
        result = await client.get_vehicle_tire_sizes(brand="Suzuki", model="Corolla")
        assert result["message"] == "Модель 'Corolla' не знайдено для Suzuki"
        assert "did_you_mean" not in result


@pytest.mark.parametrize("sales", [True, False])
def test_the_model_sees_the_question(sales: bool) -> None:
    result = {
        "found": False,
        "brand": "Suzuki",
        "message": "Модель 'S4' не знайдено для Suzuki. Схоже, клієнт мав на увазі SX4 — перепитай",
        "did_you_mean": ["SX4"],
    }
    out = compress_tool_result("get_vehicle_tire_sizes", result, sales_enabled=sales)
    assert '"did_you_mean":["SX4"]' in out
    assert "перепитай" in out


def test_a_plain_not_found_is_unchanged() -> None:
    result = {"found": False, "brand": "Suzuki", "message": "Модель 'X' не знайдено для Suzuki"}
    assert (
        compress_tool_result("get_vehicle_tire_sizes", result) == '{"found":false,"brand":"Suzuki"}'
    )
