"""A booking needs a station the caller was shown, not just a city.

Call b72ec368 (2026-09-25): price in Харків → «так» → the checklist read
✅ Місто/точка on the city alone, the bot never asked where, looked up slots at
000000008 (an id no tool had returned), collected colour and brand, and was
stopped only by book_fitting.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from src.agent.prompts import (
    _render_fitting_progress,
    fitting_steps_collected,
    next_fitting_question,
)
from src.core.call_session import CallSession
from src.store_client.client import StoreClient
from tests.unit.test_reschedule_state_pin import _onec_mock, _run

CITY_ONLY = {"customer_name": "Валерій", "city": "Харків", "caller_phone": "0504874375"}

ONEC = {
    "data": [
        {
            "StationID": "000000028",
            "StationCity": "Харків",
            "StationAddress": "вул. Холодногірська, 11",
            "StationName": "Холодна Гора",
        },
        {
            "StationID": "000000003",
            "StationCity": "Дніпро",
            "StationAddress": "пров. Добровольців, 1д",
            "StationName": "Перемога",
        },
        {
            "StationID": "000000005",
            "StationCity": "Дніпро",
            "StationAddress": "Запорізьке шосе, 55к",
            "StationName": "Тополя",
        },
    ]
}


class TestTheChecklist:
    def test_a_city_alone_does_not_settle_the_station_row(self) -> None:
        assert fitting_steps_collected(CITY_ONLY)["city"] is False

    def test_a_station_settles_it(self) -> None:
        p = {**CITY_ONLY, "station_address": "вул. Холодногірська, 11"}
        assert fitting_steps_collected(p)["city"] is True

    def test_there_is_no_canned_question_to_substitute(self) -> None:
        """One point needs no question; several need a district — no single sentence fits."""
        assert next_fitting_question(CITY_ONLY) == ""

    def test_the_block_asks_for_the_lookup_not_the_city(self) -> None:
        block = _render_fitting_progress(CITY_ONLY)
        assert 'get_fitting_stations(city="Харків")' in block
        assert "район НЕ питай" in block
        assert "У якому місті" not in block

    def test_an_unknown_city_still_asks_for_the_city(self) -> None:
        assert "місті" in next_fitting_question({"customer_name": "Валерій"})


class TestTheOnlyStationIsPinned:
    @pytest.mark.asyncio
    async def test_a_single_station_city_pins_without_a_query(self) -> None:
        session = CallSession(uuid.uuid4())
        await _run(
            session,
            "get_fitting_stations",
            {"city": "Харків"},
            _onec_mock(stations=ONEC),
            AsyncMock(spec=StoreClient),
        )
        assert session.last_fitting_station_id == "000000028"

    @pytest.mark.asyncio
    async def test_a_multi_station_city_pins_nothing(self) -> None:
        session = CallSession(uuid.uuid4())
        await _run(
            session,
            "get_fitting_stations",
            {"city": "Дніпро"},
            _onec_mock(stations=ONEC),
            AsyncMock(spec=StoreClient),
        )
        assert session.last_fitting_station_id is None


class TestSlotsNeedAShownStation:
    @pytest.mark.asyncio
    async def test_an_id_nobody_returned_is_refused(self) -> None:
        session = CallSession(uuid.uuid4())
        session.fitting_station_ids = {"000000003", "000000005"}
        onec = _onec_mock()
        result = await _run(
            session,
            "get_fitting_slots",
            {"station_id": "000000008", "date_from": "2026-09-26"},
            onec,
            AsyncMock(spec=StoreClient),
        )
        assert result.get("reason") == "slots_station_not_chosen"
        onec.get_station_schedule.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_nothing_shown_yet_is_refused(self) -> None:
        session = CallSession(uuid.uuid4())
        result = await _run(
            session,
            "get_fitting_slots",
            {"station_id": "000000008", "date_from": "2026-09-26"},
            _onec_mock(),
            AsyncMock(spec=StoreClient),
        )
        assert result.get("reason") == "slots_station_not_chosen"

    @pytest.mark.asyncio
    async def test_a_shown_station_passes_this_guard(self) -> None:
        session = CallSession(uuid.uuid4())
        session.fitting_station_ids = {"000000028"}
        result = await _run(
            session,
            "get_fitting_slots",
            {"station_id": "000000028", "date_from": "2026-09-26"},
            _onec_mock(),
            AsyncMock(spec=StoreClient),
        )
        assert result.get("reason") != "slots_station_not_chosen"

    @pytest.mark.asyncio
    async def test_the_one_known_station_is_substituted(self) -> None:
        session = CallSession(uuid.uuid4())
        session.fitting_station_ids = {"000000028"}
        onec = _onec_mock()
        result = await _run(
            session,
            "get_fitting_slots",
            {"station_id": "000000008", "date_from": "2026-09-26"},
            onec,
            AsyncMock(spec=StoreClient),
        )
        assert result.get("reason") != "slots_station_not_chosen"


KYIV_TWO = {
    "data": [
        {
            "StationID": "000000015",
            "StationCity": "Київ",
            "StationAddress": "Харьківске шосе, 165",
            "StationName": "Лівий берег",
        },
        {
            "StationID": "000000006",
            "StationCity": "Київ",
            "StationAddress": "вул. Маршала Тимошенка, 7",
            "StationName": "Оболонь",
        },
        {
            "StationID": "000000028",
            "StationCity": "Харків",
            "StationAddress": "вул. Холодногірська, 11",
            "StationName": "Холодна Гора",
        },
    ]
}


class TestAnUnmatchedLandmark:
    """26c5ebc3: «університет» matched nothing and every address was read out."""

    @pytest.mark.asyncio
    async def test_without_a_city_the_city_is_asked(self) -> None:
        result = await _run(
            CallSession(uuid.uuid4()),
            "get_fitting_stations",
            {"query": "університет"},
            _onec_mock(stations=KYIV_TWO),
            AsyncMock(spec=StoreClient),
        )
        assert result.get("action_required") == "ask_city"
        assert result["stations"] == []

    @pytest.mark.asyncio
    async def test_with_a_city_only_districts_are_given(self) -> None:
        result = await _run(
            CallSession(uuid.uuid4()),
            "get_fitting_stations",
            {"city": "Київ", "query": "університет"},
            _onec_mock(stations=KYIV_TWO),
            AsyncMock(spec=StoreClient),
        )
        assert result.get("action_required") == "ask_district"
        assert result.get("no_query_match") is True
        assert "stations" not in result
