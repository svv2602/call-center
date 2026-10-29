"""1C refuses a taken slot with a text for the client (since 2026-10-27).

TireRecording answers ``{"success": false, "data": [{"error": "<ua>",
"error_ru": "<ru>"}], "errors": ["Помилка запису на шиномонтаж"]}``. Before
this the handler read only ``errors[0]``, logged it and fell through to the
Store API fallback — a transport-failure path — so the model never learned
the slot was taken. tshina.ua hit the same shape (`99cfae9b6` there).

Drives the registered handlers, as `test_reschedule_state_pin.py` does.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.onec_client.client import OneCAPIError, booking_rejection, find_booking_guid
from src.store_client.client import StoreClient
from tests.unit.test_reschedule_state_pin import (
    STATION,
    TIME,
    TestRescheduleIsAtomic,
    _book_args,
    _onec_mock,
    _ready_session,
    _refused_by,
    _run,
    _tomorrow,
)

UA = "Дата та час вже зайняті. Оберіть, будь ласка, інший час."
RU = "Дата и время уже заняты. Выберите, пожалуйста, другое время."
TAKEN = {
    "success": False,
    "data": [{"error": UA, "error_ru": RU}],
    "errors": ["Помилка запису на шиномонтаж"],
}


class TestBookingRejection:
    def test_the_prod_shape_reads_from_data(self) -> None:
        assert booking_rejection(TAKEN) == UA

    def test_top_level_text_is_read_too(self) -> None:
        assert booking_rejection({"success": False, "error": UA}) == UA

    def test_russian_only_is_still_a_rejection(self) -> None:
        assert booking_rejection({"data": [{"error": "", "error_ru": RU}]}) == RU

    @pytest.mark.parametrize(
        "response",
        [
            {"success": False, "errors": ["Ошибка записи на шиномонтаж", "empty JSON"]},
            {"error": True, "message": "boom"},
            {"success": True, "data": [{"GUID": "g"}]},
            None,
            "text",
        ],
    )
    def test_no_client_text_is_no_rejection(self, response: Any) -> None:
        assert booking_rejection(response) is None

    def test_an_http_error_body_is_read(self) -> None:
        import json

        body = json.dumps(TAKEN, ensure_ascii=False)
        assert booking_rejection(OneCAPIError(400, body[:200], body)) == UA
        assert booking_rejection(OneCAPIError(502, "<html>bad gateway</html>")) is None


ENTRY = {
    "GUID": "own-1",
    "StationID": STATION,
    "Data": "2026-11-02T00:00:00",
    "Time": "0001-01-01T09:00:00",
}


class TestFindBookingGuid:
    def test_same_station_date_time(self) -> None:
        assert find_booking_guid([ENTRY], STATION, "2026-11-02", "09:00") == "own-1"

    def test_a_bare_hour_matches(self) -> None:
        assert find_booking_guid([ENTRY], STATION, "2026-11-02", "9:00") == "own-1"

    @pytest.mark.parametrize(
        ("station", "date", "time"),
        [
            ("000000007", "2026-11-02", "09:00"),
            (STATION, "2026-11-03", "09:00"),
            (STATION, "2026-11-02", "10:00"),
        ],
    )
    def test_another_slot_is_not_ours(self, station: str, date: str, time: str) -> None:
        assert find_booking_guid([ENTRY], station, date, time) == ""


def _session() -> Any:
    session = _ready_session(fitting_station_ids={STATION})
    session.caller_phone = "0501234567"
    return session


@pytest.mark.asyncio
class TestBookFittingSlotTaken:
    async def test_taken_slot_is_told_to_the_model(self) -> None:
        onec = _onec_mock(book=TAKEN)
        store = AsyncMock(spec=StoreClient)
        session = _session()

        result = await _run(session, "book_fitting", _book_args(), onec=onec, store=store)

        assert _refused_by(result, "slot_taken")
        assert UA in result["message"]
        store.book_fitting.assert_not_awaited()
        assert not session.fitting_booked
        assert {"date": _tomorrow(), "time": TIME} not in session.fitting_slots_offered

    async def test_taken_by_the_callers_own_booking_is_the_booking(self) -> None:
        own = {
            "GUID": "own-guid",
            "StationID": STATION,
            "Data": f"{_tomorrow()}T00:00:00",
            "Time": f"0001-01-01T{TIME}:00",
        }
        onec = _onec_mock(book=TAKEN, bookings={"success": True, "data": [own]})
        session = _session()

        result = await _run(session, "book_fitting", _book_args(), onec=onec)

        assert result["status"] == "confirmed"
        assert session.fitting_booked

    async def test_a_refusal_with_an_http_error_status(self) -> None:
        import json

        body = json.dumps(TAKEN, ensure_ascii=False)
        onec = _onec_mock()
        onec.book_fitting_rest.side_effect = OneCAPIError(400, body[:200], body)
        store = AsyncMock(spec=StoreClient)

        result = await _run(_session(), "book_fitting", _book_args(), onec=onec, store=store)

        assert _refused_by(result, "slot_taken")
        store.book_fitting.assert_not_awaited()

    async def test_a_refusal_without_client_text_keeps_the_old_path(self) -> None:
        onec = _onec_mock(book={"success": False, "errors": ["Ошибка записи"]})
        store = AsyncMock(spec=StoreClient)
        store.book_fitting.return_value = {"error": "Store API 404"}

        result = await _run(_session(), "book_fitting", _book_args(), onec=onec, store=store)

        assert result.get("reason") != "slot_taken"
        store.book_fitting.assert_awaited_once()


@pytest.mark.asyncio
class TestRescheduleSlotTaken:
    async def test_the_original_stands_and_the_model_hears_why(self) -> None:
        onec = _onec_mock(book=TAKEN)
        session = _session()
        await TestRescheduleIsAtomic()._with_known_booking(onec, session)

        result = await _run(
            session,
            "cancel_fitting",
            {
                "booking_id": "guid-1",
                "action": "reschedule",
                "new_date": "2026-09-17",
                "new_time": TIME,
            },
            onec=onec,
        )

        assert _refused_by(result, "slot_taken")
        assert UA in result["message"]
        onec.cancel_fitting_rest.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_http_error_keeps_the_whole_body() -> None:
    """`message` is cut to 200 chars; the rejection is read from the body,
    which must arrive whole or the JSON does not parse."""
    import json
    from unittest.mock import MagicMock

    from src.onec_client.client import OneCClient

    long_taken = {**TAKEN, "data": [{"error": UA * 3, "error_ru": RU * 3}]}
    raw = json.dumps(long_taken, ensure_ascii=False).encode()
    assert len(raw) > 200

    resp = MagicMock(status=400)
    resp.read = AsyncMock(return_value=raw)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=False)
    client = OneCClient("http://host", "u", "p")
    client._session = MagicMock()
    client._session.request.return_value = ctx

    with pytest.raises(OneCAPIError) as err:
        await client._do_request("POST", "http://host/x", json_data={})
    assert booking_rejection(err.value) == UA * 3
