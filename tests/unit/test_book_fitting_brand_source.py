"""book_fitting needs a brand from somewhere other than the LLM's argument.

Call 26c5ebc3 (2026-09-25): «Назвіть колір» → «всі ігри» (misheard) → re-ask →
«сірий» → book_fitting(vehicle_info="всі ігри"). The brand was never asked.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.core.call_session import CallSession
from src.store_client.client import StoreClient
from tests.unit.test_reschedule_state_pin import (
    STATION,
    TIME,
    _book_args,
    _onec_mock,
    _run,
    _tomorrow,
)

COLOUR_THEN_BOOK = [
    ("assistant", "Назвіть, будь ласка, колір автомобіля."),
    ("user", "всі ігри"),
    ("assistant", "Перепрошую, не розчула колір — назвіть, будь ласка, ще раз."),
    ("user", "сірий"),
]


def _session(turns: list[tuple[str, str]] = COLOUR_THEN_BOOK, **fields: Any) -> CallSession:
    session = CallSession(uuid.uuid4())
    session.fitting_customer_name = "Константин"
    session.selected_fitting_date = _tomorrow()
    session.selected_fitting_time = TIME
    session.fitting_slots_offered = [{"date": _tomorrow(), "time": TIME}]
    session.fitting_station_ids = {STATION}
    for speaker, content in turns:
        if speaker == "user":
            session.add_user_turn(content=content)
        else:
            session.add_assistant_turn(content)
    for name, value in fields.items():
        setattr(session, name, value)
    return session


async def _book(session: CallSession) -> tuple[Any, AsyncMock]:
    onec = _onec_mock()
    result = await _run(
        session,
        "book_fitting",
        _book_args(vehicle_info="всі ігри", auto_number="сірий"),
        onec,
        AsyncMock(spec=StoreClient),
    )
    return result, onec.book_fitting_rest


@pytest.mark.asyncio
async def test_a_brand_nobody_asked_for_is_refused() -> None:
    session = _session()
    result, booked = await _book(session)

    assert result.get("reason") == "brand_not_asked"
    assert "Яка марка" in result["message"]
    booked.assert_not_awaited()
    assert session.fitting_vehicle_brand is None, "the refused argument must not stick"


@pytest.mark.asyncio
async def test_asking_the_brand_is_enough() -> None:
    session = _session(
        [*COLOUR_THEN_BOOK, ("assistant", "Яка марка вашого авто?"), ("user", "Ауді")]
    )
    result, _ = await _book(session)
    assert result.get("reason") != "brand_not_asked"


@pytest.mark.asyncio
async def test_a_brand_already_known_is_enough() -> None:
    result, _ = await _book(_session(fitting_vehicle_brand="Volkswagen"))
    assert result.get("reason") != "brand_not_asked"


@pytest.mark.asyncio
async def test_a_brand_the_fsm_parsed_is_enough() -> None:
    session = _session()
    session.fsm_filled_fields["brand"] = "Audi"
    result, _ = await _book(session)
    assert result.get("reason") != "brand_not_asked"


@pytest.mark.asyncio
async def test_a_car_in_the_profile_is_enough() -> None:
    result, _ = await _book(_session(profile_has_vehicle=True))
    assert result.get("reason") != "brand_not_asked"


def test_the_profile_flag_survives_the_redis_snapshot() -> None:
    session = CallSession(uuid.uuid4())
    session.profile_has_vehicle = True
    assert CallSession.from_dict(session.to_dict()).profile_has_vehicle is True
