"""Size guard of `search_tires` — call 78139ffb (2026-10-27): STT made «235»
into «255» and the bot offered 255/55 R18 tyres for a Tiguan."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from src.agent.tire_size_check import near_factory_size, tire_size_refusal, vehicle_sizes
from src.core.call_session import CallSession
from tests.unit.test_tire_consult_dialogue import SALES_OFF, SALES_ON, _router

# The Tiguan 2019 result of call 78139ffb, as the catalogue returned it.
TIGUAN = {
    "brand": "Volkswagen",
    "found": True,
    "model": "Tiguan",
    "stock_sizes": ["215/65 R17", "235/55 R17", "235/55 R18", "255/45 R19"],
    "acceptable_sizes": [
        "215/60 R17",
        "215/65 R16",
        "215/65 R17",
        "235/45 R20",
        "235/50 R18",
        "235/50 R19",
        "255/40 R19",
        "255/40 R20",
        "255/45 R19",
    ],
    "acceptable_sizes_policy": "specialist_only",
}
STOCK, ACCEPTABLE = vehicle_sizes(TIGUAN)
MISHEARD = {"width": 255, "profile": 55, "diameter": 18, "season": "winter"}


def _refusal(params: dict, refused: dict | None = None, turns: int = 3) -> dict | None:
    return tire_size_refusal(
        params, stock=STOCK, acceptable=ACCEPTABLE, refused=refused or {}, caller_turns=turns
    )


class TestNearFactorySize:
    def test_the_call_78139ffb_size(self) -> None:
        r = _refusal(MISHEARD)
        assert r is not None and r["factory_size"] == "235/55 R18"
        assert r["asked_size"] == "255/55 R18"
        assert "235/55 R18" in r["message"]

    @pytest.mark.parametrize("size", ["235/55 R18", "215/65 R17", "235/50 R18", "255/40 R20"])
    def test_factory_or_acceptable_size_passes(self, size: str) -> None:
        w, rest = size.split("/")
        p, d = rest.split(" R")
        assert _refusal({"width": int(w), "profile": int(p), "diameter": int(d)}) is None

    def test_two_numbers_off_is_not_a_misheard_number(self) -> None:
        assert _refusal({"width": 205, "profile": 55, "diameter": 16}) is None

    def test_two_factory_sizes_one_number_away_names_none(self) -> None:
        # 235/55 R19 is one number from 235/55 R17 AND 235/55 R18.
        assert near_factory_size((235, 55, 19), STOCK, ACCEPTABLE) is None

    def test_unknown_car_or_no_size_args_pass(self) -> None:
        assert (
            tire_size_refusal(MISHEARD, stock=[], acceptable=[], refused={}, caller_turns=1) is None
        )
        assert _refusal({"season": "winter", "price_mode": "cheaper"}) is None

    def test_not_found_result_has_no_sizes(self) -> None:
        assert vehicle_sizes({"found": False}) == ([], [])


class TestLoopBreaker:
    def test_same_turn_retry_is_refused_again(self) -> None:
        assert _refusal(MISHEARD, refused={"255/55 R18": 3}, turns=3) is not None

    def test_passes_after_the_caller_spoke(self) -> None:
        assert _refusal(MISHEARD, refused={"255/55 R18": 3}, turns=4) is None


class TestWiring:
    @pytest.mark.asyncio
    async def test_lookup_then_misheard_search_is_refused(self) -> None:
        session = CallSession(uuid.uuid4())
        session.tire_query = {"season": "winter"}
        router, store = _router(session, SALES_ON)
        store.get_vehicle_tire_sizes.return_value = dict(TIGUAN)
        with patch("src.main._call_logger", None):
            await router.execute(
                "get_vehicle_tire_sizes", {"brand": "Volkswagen", "model": "Tiguan"}
            )
            result = await router.execute("search_tires", dict(MISHEARD))
        assert session.vehicle_stock_sizes == STOCK
        assert result["reason"] == "size_not_for_vehicle"
        assert session.tire_size_refused == {"255/55 R18": 0}
        store.search_tires.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_guard_while_sales_are_off(self) -> None:
        session = CallSession(uuid.uuid4())
        router, store = _router(session, SALES_OFF)
        store.get_vehicle_tire_sizes.return_value = dict(TIGUAN)
        with patch("src.main._call_logger", None):
            await router.execute(
                "get_vehicle_tire_sizes", {"brand": "Volkswagen", "model": "Tiguan"}
            )
            await router.execute("search_tires", dict(MISHEARD))
        assert session.vehicle_stock_sizes == []
        store.search_tires.assert_awaited_once()


def test_session_round_trip_keeps_the_guard_state() -> None:
    s = CallSession(uuid.uuid4())
    s.vehicle_stock_sizes = list(STOCK)
    s.vehicle_acceptable_sizes = list(ACCEPTABLE)
    s.tire_size_refused = {"255/55 R18": 2}
    back = CallSession.from_dict(s.to_dict())
    assert back.vehicle_stock_sizes == STOCK
    assert back.vehicle_acceptable_sizes == ACCEPTABLE
    assert back.tire_size_refused == {"255/55 R18": 2}
