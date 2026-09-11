"""The four `book_fitting` guards Wave 7-A leans on, pinned at the call site.

Wave 7-A deletes prompt rules only where a code successor already exists — the
lesson of the two reverts (`c8c6601`, `3b93213`). Four rules of «КРИТИЧНИЙ
КОНТРАКТ ІНСТРУМЕНТІВ» have one: `station_id` must be a 1C id, `time` must be
`HH:MM`, `date`/`time` must come from the offered slots, and a `find_storage`
hit must not be dropped. Before the prompt stops saying so, the guards have to
be worth leaning on — and `_book_fitting_with_metric` had no test at all: the
neighbouring files cover `effective_booking_date` and other pure functions, and
a pure-function test does not cross the seam where the guard actually decides.

So these drive the **registered** handler out of `_build_tool_router`, the same
way `test_fitting_slots_date_guard.py` does, and assert on the corrective
message too: the message is what carries the deleted rule back to the LLM at
the moment it is violated. A guard that rejects with the wrong instruction is
not a successor.

`_onec_client` is an `AsyncMock(spec=…)` bound to the real
`OneCClient.book_fitting_rest`; a bare mock would keep a renamed method green.
"""

from __future__ import annotations

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
OTHER_STATION = "000000007"
TIME = "14:00"


def _tomorrow() -> str:
    return (datetime.now(tz=UTC).date() + timedelta(days=1)).isoformat()


def _session(**overrides: Any) -> CallSession:
    """A caller who has passed every earlier guard.

    Name/colour/brand satisfy the required-fields and type-as-brand checks;
    `selected_fitting_date` + `fitting_slots_offered` satisfy Krok 3/4. Without
    all of those the handler returns long before the four guards under test,
    and the assertions would pass for the wrong reason.
    """
    session = CallSession(uuid.uuid4())
    session.fitting_customer_name = "Олена"
    session.fitting_plate = "синій"
    session.fitting_vehicle_brand = "Toyota"
    session.selected_fitting_date = _tomorrow()
    session.fitting_slots_offered = [
        {"date": _tomorrow(), "time": "10:00"},
        {"date": _tomorrow(), "time": TIME},
    ]
    for key, value in overrides.items():
        setattr(session, key, value)
    return session


async def _book(session: CallSession, **kwargs: Any) -> Any:
    onec = AsyncMock(spec=OneCClient)
    onec.book_fitting_rest = AsyncMock(
        spec=OneCClient.book_fitting_rest,
        return_value={"success": True, "data": [{"GUID": "booked-1"}]},
    )
    args: dict[str, Any] = {
        "station_id": STATION,
        "date": _tomorrow(),
        "time": TIME,
        "customer_name": "Олена",
        "auto_number": "синій",
        "vehicle_info": "Toyota",
    }
    args.update(kwargs)
    with (
        patch("src.main._onec_client", onec),
        patch("src.main._call_logger", None),
        patch("src.main._redis", None),
    ):
        router = _build_tool_router(session, store_client=AsyncMock(spec=StoreClient))
        result = await router.execute("book_fitting", args)
    return result, onec.book_fitting_rest


def _rejected(result: Any) -> bool:
    return isinstance(result, dict) and result.get("error") is True


def _refused_by(result: Any, marker: str) -> bool:
    """Rejected, and by the guard under test.

    Asserting only `error is True` lets a later guard cover for a broken
    earlier one: `time="14:70"` is refused by the offered-slots check whatever
    the `HH:MM` regex says, so loosening that regex to `\\d\\d` survived until
    these tests started naming the refusal.
    """
    return _rejected(result) and marker in result["message"]


class TestTheStandItselfHolds:
    """If the happy path does not book, every rejection below is meaningless."""

    @pytest.mark.asyncio
    async def test_a_clean_call_reaches_1c(self) -> None:
        result, booked = await _book(_session())
        assert not _rejected(result)
        booked.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_clean_call_is_sent_the_values_it_was_given(self) -> None:
        _, booked = await _book(_session())
        sent = booked.await_args.kwargs
        assert sent["station_id"] == STATION
        assert sent["date"] == _tomorrow()
        assert sent["time"] == TIME


class TestStationIdMustBeAnId:
    """R1 — «station_id тільки з поля `id` результату get_fitting_stations»."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad", ["Оболонь", "Тимошенко", "", "12345", "0000000031234", "00000-003"]
    )
    async def test_a_landmark_is_refused(self, bad: str) -> None:
        result, booked = await _book(_session(), station_id=bad)
        assert _refused_by(result, "get_fitting_stations")
        booked.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_refusal_names_the_discovery_tool(self) -> None:
        """The deleted prompt rule has to survive inside this message."""
        result, _ = await _book(_session(), station_id="Оболонь")
        assert "get_fitting_stations" in result["message"]
        assert "id" in result["message"]

    @pytest.mark.asyncio
    async def test_an_id_from_the_catalog_is_accepted(self) -> None:
        result, booked = await _book(_session(fitting_station_ids={STATION}))
        assert not _rejected(result)
        booked.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_an_id_outside_the_catalog_is_refused(self) -> None:
        session = _session(fitting_station_ids={STATION, OTHER_STATION})
        result, booked = await _book(session, station_id="000000099")
        assert _refused_by(result, "Невірний station_id")
        assert STATION in result["message"]
        booked.assert_not_awaited()


class TestTimeMustBeHhmm:
    """R2 — «time у форматі HH:MM, отриманий з get_fitting_slots»."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad", ["", "друга дня", "14", "14.00", "14:70", "о 14:00", "2 PM"]
    )
    async def test_an_invented_time_is_refused(self, bad: str) -> None:
        result, booked = await _book(_session(), time=bad)
        assert _refused_by(result, "HH:MM")
        booked.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_refusal_names_the_slots_tool(self) -> None:
        result, _ = await _book(_session(), time="друга дня")
        assert "get_fitting_slots" in result["message"]
        assert "HH:MM" in result["message"]


class TestDateAndTimeComeFromTheOfferedSlots:
    """R3 — «date/time тільки зі списку, який ти озвучив клієнту»."""

    @pytest.mark.asyncio
    async def test_a_date_never_offered_is_refused(self) -> None:
        other_day = (datetime.now(tz=UTC).date() + timedelta(days=5)).isoformat()
        result, booked = await _book(_session(), date=other_day)
        assert _rejected(result)
        assert _tomorrow() in result["message"]
        booked.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_time_never_offered_on_that_date_is_refused(self) -> None:
        result, booked = await _book(_session(), time="19:30")
        assert _rejected(result)
        assert TIME in result["message"]
        booked.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_empty_date_adopts_the_only_offered_one(self) -> None:
        """`effective_booking_date`: one offered date is not a choice to make."""
        session = _session(fitting_slots_offered=[{"date": _tomorrow(), "time": TIME}])
        result, booked = await _book(session, date="")
        assert not _rejected(result)
        assert booked.await_args.kwargs["date"] == _tomorrow()

    @pytest.mark.asyncio
    async def test_an_accepted_pair_is_locked_into_the_session(self) -> None:
        session = _session()
        await _book(session)
        assert session.selected_fitting_date == _tomorrow()
        assert session.selected_fitting_time == TIME


class TestAStorageHitIsNotDropped:
    """R4.5 — «знайшов договір у find_storage → передай його в book_fitting»."""

    @pytest.mark.asyncio
    async def test_an_empty_contract_after_a_hit_is_refused(self) -> None:
        session = _session(storage_contracts_found=["ДГ-00123"])
        result, booked = await _book(session)
        assert _rejected(result)
        assert "ДГ-00123" in result["message"]
        booked.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_refusal_teaches_both_branches(self) -> None:
        """The rule being deleted is two-sided: pass it, or pass the sentinel."""
        session = _session(storage_contracts_found=["ДГ-00123"])
        result, _ = await _book(session)
        assert "NONE" in result["message"]

    @pytest.mark.asyncio
    async def test_the_contract_is_forwarded_when_supplied(self) -> None:
        session = _session(storage_contracts_found=["ДГ-00123"])
        result, booked = await _book(session, storage_contract="ДГ-00123")
        assert not _rejected(result)
        assert booked.await_args.kwargs["storage_contract"] == "ДГ-00123"
        assert session.fitting_storage_contract == "ДГ-00123"

    @pytest.mark.asyncio
    async def test_the_sentinel_books_without_a_contract(self) -> None:
        session = _session(storage_contracts_found=["ДГ-00123"])
        result, booked = await _book(session, storage_contract="NONE")
        assert not _rejected(result)
        assert booked.await_args.kwargs["storage_contract"] == ""
        assert session.fitting_storage_choice == "own"

    @pytest.mark.asyncio
    async def test_the_guard_fires_once_per_call(self) -> None:
        """One retry, never a loop — the caller hears nothing about any of this."""
        session = _session(storage_contracts_found=["ДГ-00123"])
        assert _rejected((await _book(session))[0])
        result, booked = await _book(session)
        assert not _rejected(result)
        booked.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_storage_hit_means_no_guard(self) -> None:
        result, booked = await _book(_session())
        assert not _rejected(result)
        booked.assert_awaited_once()
