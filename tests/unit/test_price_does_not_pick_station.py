"""A price question does not choose the caller's station.

Call af7fb2f1 (2026-09-18): «вартість у Дніпрі» → the price lookup pinned the
first station in the city as `last_fitting_station_id`, the checklist read
✅ Місто/точка, the bot never asked where, and booked пров. Добровольців for a
caller who wanted Запорізьке шосе. The booking was cancelled on the same call.

Driven through the registered handlers, as `test_reschedule_state_pin.py` does:
the pin lived in the wiring, not in any parser.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.core.call_session import CallSession
from src.store_client.client import StoreClient
from tests.unit.test_reschedule_state_pin import _onec_mock, _run

DNIPRO = [
    {
        "id": "000000003",
        "city": "Дніпро",
        "address": "пров. Добровольців, 1д",
        "district": "Перемога",
    },
    {"id": "000000005", "city": "Дніпро", "address": "Запорізьке шосе, 1", "district": "Тополя"},
]


#: The 1C shape — the `for_price` branch lives on the 1C path, not the Store API
#: fallback.
ONEC_STATIONS = {
    "data": [
        {
            "StationID": s["id"],
            "StationCity": s["city"],
            "StationAddress": s["address"],
            "StationName": s["district"],
        }
        for s in DNIPRO
    ]
}


def _store() -> AsyncMock:
    store = AsyncMock(spec=StoreClient)
    store.get_fitting_stations = AsyncMock(
        spec=StoreClient.get_fitting_stations,
        return_value={"total": len(DNIPRO), "stations": DNIPRO},
    )
    return store


class TestPriceLookupLeavesTheStationOpen:
    @pytest.mark.asyncio
    async def test_stations_for_price_pin_nothing(self) -> None:
        session = CallSession(uuid.uuid4())
        onec = _onec_mock(stations=ONEC_STATIONS)

        result = await _run(
            session,
            "get_fitting_stations",
            {"city": "Дніпро", "for_price": True},
            onec,
            _store(),
        )

        assert result.get("for_price") is True, result
        assert session.last_fitting_station_id is None

    @pytest.mark.asyncio
    async def test_price_lookup_pins_nothing(self) -> None:
        session = CallSession(uuid.uuid4())
        session.fitting_station_ids = {s["id"] for s in DNIPRO}
        session.fitting_diameter_client = 18
        onec = _onec_mock(stations=ONEC_STATIONS)

        await _run(
            session,
            "get_fitting_price",
            {"tire_diameter": 18, "station_id": "000000003"},
            onec,
            _store(),
        )

        assert session.last_fitting_station_id is None

    @pytest.mark.asyncio
    async def test_the_price_hint_keeps_the_address_out_of_the_answer(self) -> None:
        session = CallSession(uuid.uuid4())
        result: Any = await _run(
            session,
            "get_fitting_stations",
            {"city": "Дніпро", "for_price": True},
            _onec_mock(stations=ONEC_STATIONS),
            _store(),
        )
        assert "НЕ адресу" in result["hint"]
