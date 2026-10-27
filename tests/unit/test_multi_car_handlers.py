"""Multi-car booking (FF_MULTI_CAR_BOOKING, 2026-10-27) — the tool handlers.

Owner's request: several cars booked one after another in one call (R01, ≤3 —
R04), the next car offered the same station and «the same time if there is
still room, else the nearest neighbouring slots» (R03), all behind a flag (R06).

The session side (`fitting_bookings`, `start_next_car`) is pinned in
`test_multi_car_state.py`. These tests drive the **registered** handlers out of
`_build_tool_router` — the seam where the record, the slot order and the time
guard are actually decided — the way `test_book_fitting_contract_guards.py` and
`test_reschedule_state_pin.py` do. The first car is always booked through the
real `book_fitting` handler, never written into the session by the test: a stand
that pre-fills `fitting_bookings` would describe the state the fix is meant to
produce (`codetrap_fixture_hardcodes_the_value_under_test`).

The 1C client is an `AsyncMock(spec=OneCClient)` with each method bound to the
real unbound method, so a renamed 1C API turns red instead of answering.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.core.call_session import CallSession
from src.main import _build_tool_router
from src.onec_client.client import OneCClient
from src.store_client.client import StoreClient

STATION = "000000003"
FIRST_TIME = "10:00"
FLAG = "FF_MULTI_CAR_BOOKING"


def _tomorrow() -> str:
    return (datetime.now(tz=UTC).date() + timedelta(days=1)).isoformat()


def _schedule(*free_times: str) -> dict[str, Any]:
    """A StationSchedule answer. `_redis` is None under test, so posts == 1
    and Quantity 0 is the only way a slot reads as free."""
    return {"data": [{"Time": f"{_tomorrow()}T{t}:00", "Quantity": 0} for t in free_times]}


def _onec(
    *,
    guid: str = "guid-1",
    schedule: dict[str, Any] | None = None,
    book: dict[str, Any] | None = None,
) -> AsyncMock:
    onec = AsyncMock(spec=OneCClient)
    onec.book_fitting_rest = AsyncMock(
        spec=OneCClient.book_fitting_rest,
        return_value=book if book is not None else {"success": True, "data": [{"GUID": guid}]},
    )
    onec.get_station_schedule = AsyncMock(
        spec=OneCClient.get_station_schedule,
        return_value=schedule if schedule is not None else _schedule(FIRST_TIME),
    )
    onec.get_customer_bookings_rest = AsyncMock(
        spec=OneCClient.get_customer_bookings_rest,
        return_value={"success": True, "data": []},
    )
    onec.cancel_fitting_rest = AsyncMock(
        spec=OneCClient.cancel_fitting_rest,
        return_value={"success": True, "data": [{"Canceled": True}]},
    )
    return onec


def _store() -> AsyncMock:
    store = AsyncMock(spec=StoreClient)
    store.book_fitting = AsyncMock(spec=StoreClient.book_fitting, return_value={})
    store.get_fitting_slots = AsyncMock(spec=StoreClient.get_fitting_slots, return_value={})
    return store


async def _run(
    session: CallSession,
    tool: str,
    args: dict[str, Any],
    *,
    onec: AsyncMock | None = None,
    store: AsyncMock | None = None,
) -> Any:
    onec = onec if onec is not None else _onec()
    store = store if store is not None else _store()
    with (
        patch("src.main._onec_client", onec),
        patch("src.main._call_logger", None),
        patch("src.main._redis", None),
    ):
        router = _build_tool_router(session, store_client=store)
        try:
            return await router.execute(tool, args)
        finally:
            # Prod adds this in the execute hook, wired only with a call logger.
            session.tools_called.add(tool)


def _offer(session: CallSession, *times: str) -> None:
    """What a successful `get_fitting_slots` leaves behind (Krok 3/4)."""
    session.selected_fitting_date = _tomorrow()
    session.fitting_slots_offered = [{"date": _tomorrow(), "time": t} for t in times]


def _caller(cars_expected: int | None = 2) -> CallSession:
    """A caller at Krok 8 for the first car: every earlier guard satisfied."""
    session = CallSession(uuid.uuid4())
    session.cars_expected = cars_expected
    session.fitting_customer_name = "Олена"
    session.fitting_plate = "синій"
    session.fitting_vehicle_brand = "Toyota"
    session.fitting_station_ids = {STATION}
    session.fsm_filled_fields["date"] = _tomorrow()
    session.add_assistant_turn("Яка марка вашого авто?")
    session.add_user_turn("Тойота")
    _offer(session, "09:20", FIRST_TIME, "10:40")
    session.add_assistant_turn("Вільно о 9:20, 10:00 та 10:40. О котрій вам зручніше?")
    session.add_user_turn("давайте на 10:00")
    session.selected_fitting_time = FIRST_TIME
    return session


def _book_args(time: str = FIRST_TIME, **kwargs: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "station_id": STATION,
        "date": _tomorrow(),
        "time": time,
        "customer_name": "Олена",
        "auto_number": "синій",
        "vehicle_info": "Toyota",
    }
    args.update(kwargs)
    return args


async def _book_first_car(session: CallSession, guid: str = "guid-1") -> Any:
    return await _run(session, "book_fitting", _book_args(), onec=_onec(guid=guid))


def _second_car_collected(session: CallSession) -> None:
    """Move on to car 2 and collect its colour and brand (what Krok 5-6 do)."""
    assert session.start_next_car() is True
    session.fitting_plate = "білий"
    session.fitting_vehicle_brand = "Skoda"


def _second_car_args(time: str = FIRST_TIME) -> dict[str, Any]:
    return _book_args(time, auto_number="білий", vehicle_info="Skoda")


@pytest.fixture
def flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FLAG, "true")


@pytest.fixture
def flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(FLAG, raising=False)


def _canon(result: Any) -> str:
    return json.dumps(result, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Phase 02 — every confirmed booking is recorded, nothing else is
# ---------------------------------------------------------------------------


class TestEachBookingIsRecorded:
    @pytest.mark.asyncio
    async def test_two_confirmed_bookings_make_two_records(self, flag_on: None) -> None:
        session = _caller()
        first = await _book_first_car(session, guid="guid-1")
        assert first["status"] == "confirmed"
        _second_car_collected(session)
        _offer(session, FIRST_TIME, "10:40")
        session.add_user_turn("і другу теж на 10:40")
        second = await _run(
            session, "book_fitting", _second_car_args("10:40"), onec=_onec(guid="guid-2")
        )
        assert second["status"] == "confirmed", second
        assert [b["booking_id"] for b in session.fitting_bookings] == ["guid-1", "guid-2"]
        assert session.fitting_bookings[0]["time"] == FIRST_TIME
        assert session.fitting_bookings[0]["vehicle"] == "Toyota"
        assert session.fitting_bookings[1]["time"] == "10:40"
        assert session.fitting_bookings[1]["color"] == "білий"
        assert session.fitting_bookings[1]["station_id"] == STATION

    @pytest.mark.asyncio
    async def test_the_same_guid_twice_is_one_booking(self, flag_on: None) -> None:
        session = _caller()
        await _book_first_car(session, guid="guid-1")
        await _book_first_car(session, guid="guid-1")
        assert len(session.fitting_bookings) == 1

    @pytest.mark.asyncio
    async def test_recorded_with_the_flag_off_too(self, flag_off: None) -> None:
        """The record is a fact about the call; only *using* it is flagged."""
        session = _caller(cars_expected=None)
        await _book_first_car(session)
        assert [b["booking_id"] for b in session.fitting_bookings] == ["guid-1"]

    @pytest.mark.asyncio
    async def test_a_guard_refusal_is_not_a_booking(self, flag_on: None) -> None:
        """Wave 17's P0 shape: a refusal is a dict, `success=True` on the router."""
        session = _caller()
        result = await _run(session, "book_fitting", _book_args("12:00"))
        assert result.get("error") is True
        assert session.fitting_bookings == []

    @pytest.mark.asyncio
    async def test_1c_success_without_guid_is_not_a_booking(self, flag_on: None) -> None:
        session = _caller()
        store = _store()
        store.book_fitting.return_value = {"error": 'Store API 404: {"detail":"Not Found"}'}
        await _run(
            session,
            "book_fitting",
            _book_args(),
            onec=_onec(book={"success": True, "data": [{"GUID": ""}]}),
            store=store,
        )
        assert session.fitting_bookings == []

    @pytest.mark.asyncio
    async def test_store_api_fallback_booking_is_recorded(self, flag_on: None) -> None:
        session = _caller()
        store = _store()
        store.book_fitting.return_value = {"id": "store-7", "status": "booked"}
        await _run(
            session,
            "book_fitting",
            _book_args(),
            onec=_onec(book={"success": False, "errors": ["1С відмовив"]}),
            store=store,
        )
        assert [b["booking_id"] for b in session.fitting_bookings] == ["store-7"]

    @pytest.mark.asyncio
    async def test_store_api_error_is_not_recorded(self, flag_on: None) -> None:
        session = _caller()
        store = _store()
        store.book_fitting.return_value = {"error": "Store API 404", "id": "x"}
        await _run(
            session,
            "book_fitting",
            _book_args(),
            onec=_onec(book={"success": False, "errors": ["1С відмовив"]}),
            store=store,
        )
        assert session.fitting_bookings == []


class TestRescheduleIsNotAnotherCar:
    @pytest.mark.asyncio
    async def test_a_moved_booking_takes_no_place_in_the_list(self, flag_on: None) -> None:
        session = _caller()
        await _book_first_car(session, guid="guid-1")
        before = [dict(b) for b in session.fitting_bookings]

        onec = _onec(guid="guid-moved", schedule=_schedule("11:20"))
        onec.get_customer_bookings_rest.return_value = {
            "success": True,
            "data": [
                {
                    "GUID": "guid-1",
                    "StationID": STATION,
                    "Data": f"{_tomorrow()}T00:00:00",
                    "Time": f"{_tomorrow()}T10:00:00",
                    "Period": "",
                    "Customer": "Олена",
                    "AutoNumber": "SINIY Toyota",
                }
            ],
        }
        await _run(session, "get_customer_bookings", {"phone": "0501234567"}, onec=onec)
        result = await _run(
            session,
            "cancel_fitting",
            {
                "booking_id": "guid-1",
                "action": "reschedule",
                "new_date": _tomorrow(),
                "new_time": "11:20",
            },
            onec=onec,
        )
        assert result["status"] == "rescheduled", result
        assert session.fitting_bookings == before


# ---------------------------------------------------------------------------
# Phase 03a — the next car is offered the previous car's time first (R03)
# ---------------------------------------------------------------------------


async def _slots(
    session: CallSession, *, onec: AsyncMock | None = None, store: AsyncMock | None = None
) -> Any:
    return await _run(
        session,
        "get_fitting_slots",
        {"station_id": STATION, "date_from": _tomorrow(), "date_to": _tomorrow()},
        onec=onec,
        store=store,
    )


async def _at_second_car(session: CallSession) -> None:
    await _book_first_car(session)
    _second_car_collected(session)


class TestSameTimeFirst:
    @pytest.mark.asyncio
    async def test_the_previous_time_leads_when_still_free(self, flag_on: None) -> None:
        session = _caller()
        await _at_second_car(session)
        result = await _slots(
            session, onec=_onec(schedule=_schedule("09:20", "10:00", "10:40", "11:20"))
        )
        assert result["slots"] == ["10:00", "09:20", "10:40", "11:20"]
        assert "На 10:00 теж є місце" in result["next_car_hint"]

    @pytest.mark.asyncio
    async def test_nearest_neighbours_lead_when_it_is_taken(self, flag_on: None) -> None:
        session = _caller()
        await _at_second_car(session)
        result = await _slots(
            session, onec=_onec(schedule=_schedule("08:00", "09:20", "10:40", "11:20"))
        )
        assert result["slots"] == ["09:20", "10:40", "11:20", "08:00"]
        assert "уже зайнятий" in result["next_car_hint"]
        assert "09:20, 10:40" in result["next_car_hint"]

    @pytest.mark.asyncio
    async def test_the_spoken_range_stays_chronological(self, flag_on: None) -> None:
        """A long list is read as «з X до Y» — off the ends of the day, not of
        the reordered list."""
        session = _caller()
        await _at_second_car(session)
        result = await _slots(
            session,
            onec=_onec(schedule=_schedule("08:00", "09:20", "10:00", "10:40", "11:20")),
        )
        assert result["slots"][0] == "10:00"
        assert "з 08:00 до 11:20" in result["speak"]

    @pytest.mark.asyncio
    async def test_the_store_fallback_dict_shape_is_ordered_too(self, flag_on: None) -> None:
        session = _caller()
        await _at_second_car(session)
        onec = _onec()
        onec.get_station_schedule.side_effect = RuntimeError("1C down")
        store = _store()
        store.get_fitting_slots.return_value = {
            "station_id": STATION,
            "slots": [
                {"date": _tomorrow(), "time": "09:00", "available": True},
                {"date": _tomorrow(), "time": "10:00", "available": True},
                {"date": _tomorrow(), "time": "12:00", "available": True},
            ],
        }
        result = await _slots(session, onec=onec, store=store)
        assert [s["time"] for s in result["slots"]] == ["10:00", "09:00", "12:00"]
        assert "next_car_hint" in result


class TestFirstCarAndFlagOffUnchanged:
    """Without the flag, or for the first car, the answer is byte-for-byte the
    one the handler gave before this wave."""

    SCHEDULE = ("09:20", "10:00", "10:40", "11:20")

    async def _baseline(self) -> Any:
        """A call that has never booked anything."""
        return await _slots(_caller(), onec=_onec(schedule=_schedule(*self.SCHEDULE)))

    @pytest.mark.asyncio
    async def test_first_car_with_the_flag_on(self, flag_on: None) -> None:
        result = await self._baseline()
        assert result["slots"] == list(self.SCHEDULE)
        assert "next_car_hint" not in result

    @pytest.mark.asyncio
    async def test_first_car_after_its_booking_is_unchanged(self, flag_on: None) -> None:
        """Booked, but the next car was never started (car number still 1)."""
        baseline = await self._baseline()
        session = _caller()
        await _book_first_car(session)
        result = await _slots(session, onec=_onec(schedule=_schedule(*self.SCHEDULE)))
        assert _canon(result) == _canon(baseline)

    @pytest.mark.asyncio
    async def test_second_car_state_with_the_flag_removed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Rollback mid-call: the restored session still says car 2."""
        monkeypatch.setenv(FLAG, "true")
        session = _caller()
        await _at_second_car(session)
        monkeypatch.delenv(FLAG)
        baseline = await self._baseline()
        result = await _slots(session, onec=_onec(schedule=_schedule(*self.SCHEDULE)))
        assert session.fitting_car_number == 2
        assert _canon(result) == _canon(baseline)

    @pytest.mark.asyncio
    async def test_store_fallback_is_returned_as_is(self, flag_on: None) -> None:
        onec = _onec()
        onec.get_station_schedule.side_effect = RuntimeError("1C down")
        store = _store()
        payload = {"station_id": STATION, "slots": [{"date": _tomorrow(), "time": "12:00"}]}
        store.get_fitting_slots.return_value = payload
        result = await _slots(_caller(), onec=onec, store=store)
        assert result is payload


# ---------------------------------------------------------------------------
# Phase 03b — the time guard reads only what was said after the last booking
# ---------------------------------------------------------------------------


def _refused_time_not_chosen(result: Any) -> bool:
    return isinstance(result, dict) and result.get("reason") == "time_not_chosen"


class TestTimeGuardForTheNextCar:
    @pytest.mark.asyncio
    async def test_the_first_cars_hour_does_not_count(self, flag_on: None) -> None:
        """«давайте на 10:00» was said for car 1. Car 2 at 10:00 with nothing
        said since is a time nobody picked for it."""
        session = _caller()
        await _at_second_car(session)
        _offer(session, "09:20", FIRST_TIME, "10:40")
        result = await _run(session, "book_fitting", _second_car_args())
        assert _refused_time_not_chosen(result), result
        assert len(session.fitting_bookings) == 1

    @pytest.mark.asyncio
    async def test_a_time_named_after_the_booking_counts(self, flag_on: None) -> None:
        session = _caller()
        await _at_second_car(session)
        _offer(session, "09:20", FIRST_TIME, "10:40")
        session.add_user_turn("другу теж на 10:00")
        result = await _run(session, "book_fitting", _second_car_args(), onec=_onec(guid="g2"))
        assert result.get("status") == "confirmed", result

    @pytest.mark.asyncio
    async def test_yes_to_the_same_time_offer_counts(self, flag_on: None) -> None:
        """R03's happy path: the bot offers the hour, the caller only agrees."""
        session = _caller()
        await _at_second_car(session)
        _offer(session, "09:20", FIRST_TIME, "10:40")
        session.add_assistant_turn("На 10:00 теж є місце, записати?")
        session.add_user_turn("так")
        result = await _run(session, "book_fitting", _second_car_args(), onec=_onec(guid="g2"))
        assert result.get("status") == "confirmed", result
        assert [b["booking_id"] for b in session.fitting_bookings] == ["guid-1", "g2"]

    @pytest.mark.asyncio
    async def test_yes_does_not_stretch_to_another_hour(self, flag_on: None) -> None:
        session = _caller()
        await _at_second_car(session)
        _offer(session, "09:20", FIRST_TIME, "10:40")
        session.add_assistant_turn("На 10:00 теж є місце, записати?")
        session.add_user_turn("так")
        result = await _run(session, "book_fitting", _second_car_args("10:40"))
        assert _refused_time_not_chosen(result), result

    @pytest.mark.asyncio
    async def test_an_answer_other_than_yes_does_not_count(self, flag_on: None) -> None:
        session = _caller()
        await _at_second_car(session)
        _offer(session, "09:20", FIRST_TIME, "10:40")
        session.add_assistant_turn("На 10:00 теж є місце, записати?")
        session.add_user_turn("а можна пізніше?")
        result = await _run(session, "book_fitting", _second_car_args())
        assert _refused_time_not_chosen(result), result

    @pytest.mark.asyncio
    async def test_first_car_still_reads_the_whole_dialog(self, flag_on: None) -> None:
        """N=1: the pin is gone but the caller named it — booked, as before."""
        session = _caller()
        session.selected_fitting_time = None
        result = await _run(session, "book_fitting", _book_args())
        assert result.get("status") == "confirmed", result

    @pytest.mark.asyncio
    async def test_flag_off_reads_the_whole_dialog(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(FLAG, "true")
        session = _caller()
        await _at_second_car(session)
        _offer(session, "09:20", FIRST_TIME, "10:40")
        monkeypatch.delenv(FLAG)
        result = await _run(session, "book_fitting", _second_car_args(), onec=_onec(guid="g2"))
        assert result.get("status") == "confirmed", result
