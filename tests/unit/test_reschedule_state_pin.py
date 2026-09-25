"""A reschedule must not lose the station it was handed, nor re-ask the car.

Call 29ed5791 (2026-09-14) left a real caller with no booking at all. The chain
was three links long and each link is pinned here:

1. `get_customer_bookings` returned station `000000003` and «SIRIY Tiguan» and
   wrote **nothing** to the session, so `book_fitting`'s auto-inject — which
   reads `session.fitting_plate` / `session.fitting_vehicle_brand` — was a
   silent no-op and the bot re-asked colour and brand it had just been told.
2. `book_fitting` pinned `session.last_fitting_station_id` on the `\\d{6,12}`
   shape alone, before 1C was called. The invented `000000010` stuck through
   the 404 and then armed the Krok 1 regression guard, which refused the
   `get_fitting_stations` the LLM reached for to recover.
3. The cross-check `if session.fitting_station_ids and station_id not in …`
   was vacuous on an empty set — and only `get_fitting_stations` ever filled
   that set, which a reschedule never calls.

Everything here drives the **registered** handlers out of `_build_tool_router`,
the way `test_book_fitting_contract_guards.py` does. A test that re-implements
the parse would have stayed green through all three defects: the wiring is the
thing that was broken, not the string handling
(`codetrap_corpus_tests_dont_cover_the_wiring`).

`_onec_client` is an `AsyncMock(spec=OneCClient)` with each method bound to the
real unbound method, so a renamed or dropped 1C API turns red here instead of
answering happily to anything (`codetrap_asyncmock_hides_missing_api`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.agent.color_translit import (
    _KNOWN_COLOR_FORMS,
    _LATIN_COLOR_COLLISIONS,
    latin_prefix_to_color,
    translit_color_to_latin,
)
from src.core.call_session import CallSession
from src.main import _build_tool_router
from src.onec_client.client import OneCClient
from src.store_client.client import StoreClient

STATION = "000000003"
OTHER_STATION = "000000007"
INVENTED_STATION = "000000010"
TIME = "14:00"

# The exact 404 body call 29ed5791 got back from the Store API fallback.
STORE_API_404 = {"error": 'Store API 404: {"detail":"Not Found"}'}


def _tomorrow() -> str:
    return (datetime.now(tz=UTC).date() + timedelta(days=1)).isoformat()


def _onec_mock(**returns: Any) -> AsyncMock:
    """A 1C client that only answers methods `OneCClient` really has."""
    onec = AsyncMock(spec=OneCClient)
    onec.get_customer_bookings_rest = AsyncMock(
        spec=OneCClient.get_customer_bookings_rest,
        return_value=returns.get("bookings", {"success": True, "data": []}),
    )
    onec.book_fitting_rest = AsyncMock(
        spec=OneCClient.book_fitting_rest,
        return_value=returns.get("book", {"success": True, "data": [{"GUID": "booked-1"}]}),
    )
    onec.get_fitting_stations_rest = AsyncMock(
        spec=OneCClient.get_fitting_stations_rest,
        return_value=returns.get("stations", {"data": []}),
    )
    onec.get_station_schedule = AsyncMock(
        spec=OneCClient.get_station_schedule,
        return_value=returns.get("schedule", _schedule(TIME)),
    )
    onec.cancel_fitting_rest = AsyncMock(
        spec=OneCClient.cancel_fitting_rest,
        return_value=returns.get("cancel", {"success": True, "data": [{"Canceled": True}]}),
    )
    return onec


def _schedule(*free_times: str) -> dict[str, Any]:
    """A StationSchedule answer. `_redis` is None under test, so posts == 1
    and Quantity 0 is the only way a slot reads as free."""
    return {
        "data": [
            {"Time": f"2026-09-17T{t}:00", "Quantity": 0} for t in free_times
        ]
    }


def _booking(station_id: str, auto_number: str, guid: str = "guid-1") -> dict[str, Any]:
    """One row in the shape `get_customer_bookings_rest` returns."""
    return {
        "GUID": guid,
        "StationID": station_id,
        "Data": "2026-09-17T00:00:00",
        "Time": "2026-09-17T09:00:00",
        "Period": "",
        "Customer": "Олена",
        "AutoNumber": auto_number,
    }


def _ready_session(**overrides: Any) -> CallSession:
    """A caller who has cleared every guard that precedes the ones under test.

    Krok 3/4 needs `selected_fitting_date` + `fitting_slots_offered`; without
    them `book_fitting` returns long before the station cross-check and the
    assertions below would pass for the wrong reason. `selected_fitting_time`
    is the caller's pick — offering a slot is not choosing it, and a time
    nobody named is refused.
    """
    session = CallSession(uuid.uuid4())
    session.fitting_customer_name = "Олена"
    session.selected_fitting_date = _tomorrow()
    session.selected_fitting_time = TIME
    session.fitting_slots_offered = [{"date": _tomorrow(), "time": TIME}]
    # By Krok 8 the bot has asked for the car; without that the brand guard
    # (2026-09-25) refuses first and the station checks under test never run.
    # Asked rather than stored, so the vehicle-pin tests still see no brand.
    session.add_assistant_turn("Яка марка вашого авто?")
    for key, value in overrides.items():
        setattr(session, key, value)
    return session


async def _run(
    session: CallSession,
    tool: str,
    args: dict[str, Any],
    onec: AsyncMock | None = None,
    store: AsyncMock | None = None,
) -> Any:
    onec = onec if onec is not None else _onec_mock()
    store = store if store is not None else AsyncMock(spec=StoreClient)
    with (
        patch("src.main._onec_client", onec),
        patch("src.main._call_logger", None),
        patch("src.main._redis", None),
    ):
        router = _build_tool_router(session, store_client=store)
        try:
            return await router.execute(tool, args)
        finally:
            # Prod records this in the router's on-execute callback, which is
            # only wired when `_call_logger` is set — and it is None here. A
            # second `_run` on the same session would otherwise see a tool it
            # just ran as never having run (`cancel_fitting` requires
            # `get_customer_bookings` in this set).
            session.tools_called.add(tool)


def _book_args(**kwargs: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "station_id": STATION,
        "date": _tomorrow(),
        "time": TIME,
        "customer_name": "Олена",
        "auto_number": "сірий",
        "vehicle_info": "Toyota",
    }
    args.update(kwargs)
    return args


def _rejected(result: Any) -> bool:
    return isinstance(result, dict) and result.get("error") is True


def _refused_by(result: Any, reason: str) -> bool:
    """Rejected, and by the guard under test.

    Prod names only the first guard that fired, so a test asserting bare
    `error is True` lets a neighbouring guard cover for a broken or absent one
    (`feedback_earlier_guard_hides_later`).
    """
    return _rejected(result) and result.get("reason") == reason


# --------------------------------------------------------------------------
# Phase 1 — the reverse colour table
# --------------------------------------------------------------------------


class TestLatinPrefixToColor:
    def test_the_prod_call_decodes(self) -> None:
        assert latin_prefix_to_color("SIRIY") == "сірий"

    def test_case_does_not_matter(self) -> None:
        assert latin_prefix_to_color("siriy") == "сірий"
        assert latin_prefix_to_color(" Siriy ") == "сірий"

    def test_a_russian_spelling_comes_back_ukrainian(self) -> None:
        """The value re-enters 1C and is read out at Krok 8."""
        assert latin_prefix_to_color("CHERVONNIY") == "червоний"
        assert latin_prefix_to_color("POMARANCHIVIY") == "помаранчевий"

    @pytest.mark.parametrize(
        "junk",
        ["1873", "3220", "0400", "4448KA", "4502", "NEVIDOMIY", "NE", "KOLIR", "", "   "],
    )
    def test_junk_is_not_a_colour(self, junk: str) -> None:
        """Plate debris and the «колір не назвали» hatch must not decode."""
        assert latin_prefix_to_color(junk) is None

    def test_an_unlisted_colour_is_not_guessed(self) -> None:
        """No fuzzy matching: «золотий» was never measured, so it is asked for."""
        assert latin_prefix_to_color("ZOLOTIY") is None

    def test_no_two_colours_claim_one_latin_key(self) -> None:
        """A dict comprehension would drop one of them in silence.

        `срібний`/`сріблястий`, `червоний`/`червонный` and `синій`/`сірий` are
        the near-misses in the measured list; the assertion covers all pairs.
        """
        assert _LATIN_COLOR_COLLISIONS == {}

    def test_keys_are_generated_by_the_function_they_invert(self) -> None:
        """Hand-typed Latin keys would drift when the char map is edited."""
        for form, canonical in _KNOWN_COLOR_FORMS:
            key = translit_color_to_latin(form).upper()
            assert latin_prefix_to_color(key) == canonical


# --------------------------------------------------------------------------
# Phase 1 — get_customer_bookings writes the state a reschedule needs
# --------------------------------------------------------------------------


class TestBookingsPinTheVehicle:
    @pytest.mark.asyncio
    async def test_the_prod_booking_pins_brand_colour_and_station(self) -> None:
        session = _ready_session()
        onec = _onec_mock(bookings={"success": True, "data": [_booking(STATION, "SIRIY Tiguan")]})
        result = await _run(session, "get_customer_bookings", {"phone": "+380501112233"}, onec)

        assert result["total"] == 1
        assert session.fitting_vehicle_brand == "Tiguan"
        assert session.fitting_plate == "сірий"
        assert STATION in session.fitting_station_ids

    @pytest.mark.asyncio
    async def test_having_a_booking_is_not_having_chosen_a_station(self) -> None:
        """`last_fitting_station_id` arms the Krok 1 regression guard.

        Setting it here would refuse the caller the station list before they
        have picked anything — ammunition for the very guard that ended 29ed5791.
        """
        session = _ready_session()
        onec = _onec_mock(bookings={"success": True, "data": [_booking(STATION, "SIRIY Tiguan")]})
        await _run(session, "get_customer_bookings", {"phone": "+380501112233"}, onec)

        assert session.last_fitting_station_id is None

    @pytest.mark.asyncio
    async def test_a_cyrillic_two_word_brand_survives_whole(self) -> None:
        """1C keeps the brand verbatim — «CHORNIY Тойота Прадо» is one car."""
        session = _ready_session()
        onec = _onec_mock(
            bookings={"success": True, "data": [_booking(STATION, "CHORNIY Тойота Прадо")]}
        )
        await _run(session, "get_customer_bookings", {"phone": "+380501112233"}, onec)

        assert session.fitting_vehicle_brand == "Тойота Прадо"
        assert session.fitting_plate == "чорний"

    @pytest.mark.asyncio
    async def test_an_unreadable_colour_leaves_the_colour_unclaimed(self) -> None:
        """Fallback to variant (a): pin the brand, let the bot ask the colour."""
        session = _ready_session()
        onec = _onec_mock(bookings={"success": True, "data": [_booking(STATION, "ZOLOTIY Mazda")]})
        await _run(session, "get_customer_bookings", {"phone": "+380501112233"}, onec)

        assert session.fitting_vehicle_brand == "Mazda"
        assert session.fitting_plate is None

    @pytest.mark.asyncio
    async def test_a_single_token_pins_no_half_of_a_car(self) -> None:
        """«4448ка» is plate debris, not a colour and not a brand."""
        session = _ready_session()
        onec = _onec_mock(bookings={"success": True, "data": [_booking(STATION, "4448KA")]})
        await _run(session, "get_customer_bookings", {"phone": "+380501112233"}, onec)

        assert session.fitting_vehicle_brand is None
        assert session.fitting_plate is None
        assert STATION in session.fitting_station_ids

    @pytest.mark.asyncio
    async def test_two_bookings_pin_both_stations_and_no_car(self) -> None:
        """Which car is being moved is an open question with two bookings."""
        session = _ready_session()
        onec = _onec_mock(
            bookings={
                "success": True,
                "data": [
                    _booking(STATION, "SIRIY Tiguan", guid="g1"),
                    _booking(OTHER_STATION, "CHORNIY Mazda", guid="g2"),
                ],
            }
        )
        await _run(session, "get_customer_bookings", {"phone": "+380501112233"}, onec)

        assert session.fitting_vehicle_brand is None
        assert session.fitting_plate is None
        assert session.fitting_station_ids == {STATION, OTHER_STATION}

    @pytest.mark.asyncio
    async def test_pinned_state_survives_a_redis_round_trip(self) -> None:
        """The session lives in Redis; a field outside to_dict/from_dict is gone
        by the next turn."""
        session = _ready_session()
        onec = _onec_mock(bookings={"success": True, "data": [_booking(STATION, "SIRIY Tiguan")]})
        await _run(session, "get_customer_bookings", {"phone": "+380501112233"}, onec)

        restored = CallSession.from_dict(session.to_dict())
        assert restored.fitting_vehicle_brand == "Tiguan"
        assert restored.fitting_plate == "сірий"
        assert STATION in restored.fitting_station_ids
        assert restored.last_fitting_station_id is None


class TestTheAutoInjectNowHasASource:
    """Task 1.7 — the call site, not the parse.

    The parse tests above all stay green if `book_fitting` stops reading the
    session, so the wiring gets its own stand.
    """

    @pytest.mark.asyncio
    async def test_book_fitting_sends_1c_the_car_from_the_existing_booking(self) -> None:
        session = _ready_session()
        onec = _onec_mock(bookings={"success": True, "data": [_booking(STATION, "SIRIY Tiguan")]})
        await _run(session, "get_customer_bookings", {"phone": "+380501112233"}, onec)

        result = await _run(
            session,
            "book_fitting",
            _book_args(auto_number="", vehicle_info=""),
            onec,
        )

        assert result.get("status") == "confirmed"
        sent = onec.book_fitting_rest.await_args.kwargs
        assert sent["vehicle_info"] == "Tiguan"
        assert sent["auto_number"] == "сірий"


# --------------------------------------------------------------------------
# Phase 2 — the station is pinned only once a booking exists
# --------------------------------------------------------------------------


class TestStationPinFollowsTheBooking:
    @pytest.mark.asyncio
    async def test_a_confirmed_booking_pins_the_station(self) -> None:
        session = _ready_session(fitting_station_ids={STATION})
        result = await _run(session, "book_fitting", _book_args())

        assert result.get("status") == "confirmed"
        assert session.last_fitting_station_id == STATION

    @pytest.mark.asyncio
    async def test_a_store_api_404_pins_nothing(self) -> None:
        """The exact failure of 29ed5791: numeric, plausible, non-existent."""
        session = _ready_session(fitting_station_ids={STATION, OTHER_STATION})
        onec = _onec_mock(book={"success": False, "errors": ["Not Found"]})
        store = AsyncMock(spec=StoreClient)
        store.book_fitting = AsyncMock(spec=StoreClient.book_fitting, return_value=STORE_API_404)

        result = await _run(session, "book_fitting", _book_args(), onec, store)

        assert result == STORE_API_404
        assert session.last_fitting_station_id is None

    @pytest.mark.asyncio
    async def test_a_failure_does_not_overwrite_the_station_already_chosen(self) -> None:
        """`get_fitting_slots` pinned 000000003; a failed retry must not move it."""
        session = _ready_session(
            fitting_station_ids={STATION, OTHER_STATION},
            last_fitting_station_id=STATION,
        )
        onec = _onec_mock(book={"success": False, "errors": ["Not Found"]})
        store = AsyncMock(spec=StoreClient)
        store.book_fitting = AsyncMock(spec=StoreClient.book_fitting, return_value=STORE_API_404)

        await _run(session, "book_fitting", _book_args(station_id=OTHER_STATION), onec, store)

        assert session.last_fitting_station_id == STATION

    @pytest.mark.asyncio
    async def test_a_1c_exception_pins_nothing(self) -> None:
        session = _ready_session(fitting_station_ids={STATION})
        onec = _onec_mock()
        onec.book_fitting_rest = AsyncMock(
            spec=OneCClient.book_fitting_rest, side_effect=RuntimeError("connection reset")
        )
        store = AsyncMock(spec=StoreClient)
        store.book_fitting = AsyncMock(spec=StoreClient.book_fitting, return_value=STORE_API_404)

        await _run(session, "book_fitting", _book_args(), onec, store)

        assert session.last_fitting_station_id is None

    @pytest.mark.asyncio
    async def test_a_guard_rejection_pins_nothing(self) -> None:
        """`success=t` in the audit only means «did not raise»
        (`codetrap_tool_success_means_no_exception`)."""
        session = _ready_session(fitting_station_ids={STATION})
        result = await _run(session, "book_fitting", _book_args(time="25:99"))

        assert _rejected(result)
        assert session.last_fitting_station_id is None


class TestRecoveryAfterAFailedBooking:
    """Task 2.5 — the chain, not the field.

    What ended 29ed5791 was not a stale field but what the stale field did: the
    LLM's attempt to recover via `get_fitting_stations` was refused by the Krok 1
    regression guard, quoting the id it had just invented.
    """

    @pytest.mark.asyncio
    async def test_a_failed_booking_does_not_lock_the_station_list(self) -> None:
        session = _ready_session(fitting_station_ids={STATION, OTHER_STATION})
        onec = _onec_mock(
            book={"success": False, "errors": ["Not Found"]},
            stations={"data": []},
        )
        store = AsyncMock(spec=StoreClient)
        store.book_fitting = AsyncMock(spec=StoreClient.book_fitting, return_value=STORE_API_404)
        store.get_fitting_stations = AsyncMock(
            spec=StoreClient.get_fitting_stations, return_value={"total": 0, "stations": []}
        )

        await _run(session, "book_fitting", _book_args(station_id=INVENTED_STATION), onec, store)
        recovery = await _run(session, "get_fitting_stations", {"city": "Дніпро"}, onec, store)

        assert not _refused_by(recovery, "past_krok_2")
        assert not _refused_by(recovery, "cross_city")


# --------------------------------------------------------------------------
# Phase 3 — an empty set is «nothing to check against», not «anything goes»
# --------------------------------------------------------------------------


class TestStationCrossCheckIsDefaultDeny:
    @pytest.mark.asyncio
    async def test_an_unverifiable_station_never_reaches_1c(self) -> None:
        session = _ready_session()
        onec = _onec_mock()
        store = AsyncMock(spec=StoreClient)
        store.book_fitting = AsyncMock(spec=StoreClient.book_fitting)

        result = await _run(session, "book_fitting", _book_args(), onec, store)

        assert _refused_by(result, "no_known_stations")
        onec.book_fitting_rest.assert_not_awaited()
        store.book_fitting.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_empty_set_refusal_is_its_own_refusal(self) -> None:
        """Two guards phrased alike are two guards nobody can tell apart in prod."""
        session = _ready_session()
        result = await _run(session, "book_fitting", _book_args())

        assert "Регресія Кроку 1" not in result["message"]
        assert "Невірний station_id" not in result["message"]
        assert "get_fitting_stations" in result["message"]

    @pytest.mark.asyncio
    async def test_one_known_station_corrects_an_invented_one(self) -> None:
        """Task 3.4 — the branch that rescues the reschedule."""
        session = _ready_session(fitting_station_ids={STATION})
        onec = _onec_mock()

        result = await _run(session, "book_fitting", _book_args(station_id=INVENTED_STATION), onec)

        assert result.get("status") == "confirmed"
        assert onec.book_fitting_rest.await_args.kwargs["station_id"] == STATION
        assert session.last_fitting_station_id == STATION

    @pytest.mark.asyncio
    async def test_two_known_stations_refuse_an_invented_one(self) -> None:
        session = _ready_session(fitting_station_ids={STATION, OTHER_STATION})
        onec = _onec_mock()

        result = await _run(session, "book_fitting", _book_args(station_id=INVENTED_STATION), onec)

        assert _rejected(result)
        assert "Невірний station_id" in result["message"]
        assert result.get("reason") != "no_known_stations"
        onec.book_fitting_rest.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_known_station_passes_straight_through(self) -> None:
        session = _ready_session(fitting_station_ids={STATION, OTHER_STATION})
        onec = _onec_mock()

        result = await _run(session, "book_fitting", _book_args(station_id=OTHER_STATION), onec)

        assert result.get("status") == "confirmed"
        assert onec.book_fitting_rest.await_args.kwargs["station_id"] == OTHER_STATION


class TestEveryPathThatHandsOutAnIdFillsTheSet:
    """Default-deny turns every unfilled source into a refusal loop.

    `book_fitting` now answers an empty set with «call get_fitting_stations» —
    so a `get_fitting_stations` that returns ids without recording them sends
    the LLM around the same circle forever, with no breaker
    (`feedback_guard_needs_loop_breaker`). The 1C branch fills the set at
    :2623; the Store API fallback below it did not.
    """

    @pytest.mark.asyncio
    async def test_the_store_api_fallback_records_what_it_hands_out(self) -> None:
        session = _ready_session()
        onec = _onec_mock()
        onec.get_fitting_stations_rest = AsyncMock(
            spec=OneCClient.get_fitting_stations_rest,
            side_effect=RuntimeError("1C unreachable"),
        )
        store = AsyncMock(spec=StoreClient)
        store.get_fitting_stations = AsyncMock(
            spec=StoreClient.get_fitting_stations,
            return_value={
                "total": 1,
                "stations": [{"id": STATION, "name": "Дніпро, Титова"}],
            },
        )

        result = await _run(session, "get_fitting_stations", {"city": "Дніпро"}, onec, store)

        assert result["stations"][0]["id"] == STATION
        assert STATION in session.fitting_station_ids

    @pytest.mark.asyncio
    async def test_a_booking_on_a_fallback_station_is_not_refused(self) -> None:
        """The loop itself, not the field: discovery then booking, in prod order."""
        session = _ready_session()
        onec = _onec_mock()
        onec.get_fitting_stations_rest = AsyncMock(
            spec=OneCClient.get_fitting_stations_rest,
            side_effect=RuntimeError("1C unreachable"),
        )
        store = AsyncMock(spec=StoreClient)
        store.get_fitting_stations = AsyncMock(
            spec=StoreClient.get_fitting_stations,
            return_value={"total": 1, "stations": [{"id": STATION, "name": "Дніпро, Титова"}]},
        )

        await _run(session, "get_fitting_stations", {"city": "Дніпро"}, onec, store)
        result = await _run(session, "book_fitting", _book_args(), onec, store)

        assert not _refused_by(result, "no_known_stations")
        assert result.get("status") == "confirmed"


class TestReserveFittingSlotIsDefaultDenyToo:
    """`reserve_fitting_slot` writes to 1C through the same kind of guard.

    It kept the vacuous form one wave longer, and it was weaker in a second
    way: the condition opened with `station_id and`, so an **empty** id skipped
    the check altogether and was forwarded to 1C as "". Default-deny closes
    both, because "" is not a member of the set either.

    Measured before changing (prod, 2026-09-14): 5 calls ever, none since
    2026-08-03, and `get_fitting_stations` preceded every single one — so the
    set is filled on the real path and none of the 5 would have been refused.
    """

    def _session_with_car(self, **overrides: Any) -> CallSession:
        """`reserve_fitting_slot` refuses outright without plate or brand,
        and that guard sits *after* the station check — so the stand has to
        clear it or the assertions pass for the wrong reason."""
        return _ready_session(fitting_plate="сірий", fitting_vehicle_brand="Tiguan", **overrides)

    @pytest.mark.asyncio
    async def test_an_empty_set_refuses_and_1c_is_untouched(self) -> None:
        session = self._session_with_car()
        onec = _onec_mock()
        onec.reserve_fitting_slot = AsyncMock(spec=OneCClient.reserve_fitting_slot)

        result = await _run(
            session,
            "reserve_fitting_slot",
            {"station_id": STATION, "date": _tomorrow(), "time": TIME},
            onec,
        )

        assert _refused_by(result, "no_known_stations_reserve")
        onec.reserve_fitting_slot.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_empty_station_id_no_longer_slips_past(self) -> None:
        """`station_id and …` used to skip the guard and send "" to 1C."""
        session = self._session_with_car(fitting_station_ids={STATION, OTHER_STATION})
        onec = _onec_mock()
        onec.reserve_fitting_slot = AsyncMock(spec=OneCClient.reserve_fitting_slot)

        result = await _run(
            session,
            "reserve_fitting_slot",
            {"station_id": "", "date": _tomorrow(), "time": TIME},
            onec,
        )

        assert _refused_by(result, "station_not_in_catalog_reserve")
        onec.reserve_fitting_slot.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_invented_station_is_refused(self) -> None:
        session = self._session_with_car(fitting_station_ids={STATION, OTHER_STATION})
        onec = _onec_mock()
        onec.reserve_fitting_slot = AsyncMock(spec=OneCClient.reserve_fitting_slot)

        result = await _run(
            session,
            "reserve_fitting_slot",
            {"station_id": INVENTED_STATION, "date": _tomorrow(), "time": TIME},
            onec,
        )

        assert _refused_by(result, "station_not_in_catalog_reserve")
        onec.reserve_fitting_slot.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_two_refusals_are_told_apart(self) -> None:
        """Prod names only the first guard that fired; so must the reason."""
        empty = await _run(
            self._session_with_car(),
            "reserve_fitting_slot",
            {"station_id": STATION, "date": _tomorrow(), "time": TIME},
        )
        wrong = await _run(
            self._session_with_car(fitting_station_ids={STATION, OTHER_STATION}),
            "reserve_fitting_slot",
            {"station_id": INVENTED_STATION, "date": _tomorrow(), "time": TIME},
        )

        assert empty["reason"] != wrong["reason"]
        # And apart from book_fitting's, which the LLM may hit in the same call.
        assert empty["reason"] != "no_known_stations"

    @pytest.mark.asyncio
    async def test_a_known_station_still_reserves(self) -> None:
        """The 5 real prod calls all looked like this — none may start failing."""
        session = self._session_with_car(fitting_station_ids={STATION, OTHER_STATION})
        onec = _onec_mock()
        onec.reserve_fitting_slot = AsyncMock(
            spec=OneCClient.reserve_fitting_slot,
            return_value={"success": True, "data": [{"GUID": "res-1"}]},
        )

        result = await _run(
            session,
            "reserve_fitting_slot",
            {"station_id": STATION, "date": _tomorrow(), "time": TIME},
            onec,
        )

        assert result.get("status") == "reserved"
        assert onec.reserve_fitting_slot.await_args.kwargs["station_id"] == STATION


class TestTheRescheduleThatFailedInProd:
    """Task 3.6 — phases 1 and 3 in the order prod ran them.

    Neither phase alone saves this call: without phase 1 the set is empty and
    phase 3 refuses a legitimate reschedule; without phase 3 the invented id
    reaches 1C and comes back 404.
    """

    @pytest.mark.asyncio
    async def test_the_invented_station_is_replaced_by_the_booked_one(self) -> None:
        session = _ready_session()
        onec = _onec_mock(bookings={"success": True, "data": [_booking(STATION, "SIRIY Tiguan")]})

        await _run(session, "get_customer_bookings", {"phone": "+380501112233"}, onec)
        result = await _run(
            session,
            "book_fitting",
            _book_args(station_id=INVENTED_STATION, auto_number="", vehicle_info=""),
            onec,
        )

        assert result.get("status") == "confirmed"
        sent = onec.book_fitting_rest.await_args.kwargs
        assert sent["station_id"] == STATION
        assert sent["vehicle_info"] == "Tiguan"
        assert sent["auto_number"] == "сірий"


# --------------------------------------------------------------------------
# Phase 4 — the reschedule is one operation, and it books before it cancels
# --------------------------------------------------------------------------


class TestRescheduleIsAtomic:
    """`action="reschedule"` used to be schema-only: the handler cancelled and
    left re-booking to the LLM.

    Both live callers it was tried on lost their slot. fb854e33 (2026-09-16)
    got no second `book_fitting` at all and was told «Ви записані на 9:00»
    about a booking that had just been cancelled; 29ed5791 (2026-09-14) got one
    aimed at an invented station and a 404. Cancel-first is what made either
    survivable, so the ordering is the invariant under test — not the wording.
    """

    async def _with_known_booking(
        self, onec: AsyncMock, session: CallSession, guid: str = "guid-1"
    ) -> None:
        """Drive the real `get_customer_bookings` so the reschedule reads state
        that handler actually wrote, not state the test invented."""
        onec.get_customer_bookings_rest.return_value = {
            "success": True,
            "data": [_booking(STATION, "SIRIY Tiguan", guid=guid)],
        }
        await _run(session, "get_customer_bookings", {"phone": "0501234567"}, onec=onec)

    async def test_the_new_booking_is_created_before_the_old_is_released(self) -> None:
        calls: list[str] = []
        onec = _onec_mock()
        session = _ready_session()
        await self._with_known_booking(onec, session)

        onec.book_fitting_rest.side_effect = lambda **_: (
            calls.append("book"), {"success": True, "data": [{"GUID": "new-guid"}]}
        )[1]
        onec.cancel_fitting_rest.side_effect = lambda *_, **__: (
            calls.append("cancel"), {"success": True, "data": [{"Canceled": True}]}
        )[1]

        result = await _run(
            session,
            "cancel_fitting",
            {"booking_id": "guid-1", "action": "reschedule",
             "new_date": "2026-09-17", "new_time": TIME},
            onec=onec,
        )

        assert result["status"] == "rescheduled"
        assert calls == ["book", "cancel"]

    async def test_a_failed_rebook_leaves_the_original_standing(self) -> None:
        """fb854e33's outcome, made impossible: no GUID, so nothing is cancelled
        and the bot is told in so many words not to claim a move."""
        onec = _onec_mock(book={"success": False, "errors": ["Ошибка записи"]})
        session = _ready_session()
        await self._with_known_booking(onec, session)

        result = await _run(
            session,
            "cancel_fitting",
            {"booking_id": "guid-1", "action": "reschedule",
             "new_date": "2026-09-17", "new_time": TIME},
            onec=onec,
        )

        assert _refused_by(result, "rebook_failed")
        onec.cancel_fitting_rest.assert_not_awaited()

    async def test_a_time_the_grid_never_held_is_refused(self) -> None:
        """The reschedule path must not become the way back into booking an
        hour 1C never offered — the defect `0c7248b` closed on first booking."""
        onec = _onec_mock(schedule=_schedule("09:00", "09:40", "10:20"))
        session = _ready_session()
        await self._with_known_booking(onec, session)

        result = await _run(
            session,
            "cancel_fitting",
            {"booking_id": "guid-1", "action": "reschedule",
             "new_date": "2026-09-17", "new_time": "14:00"},
            onec=onec,
        )

        assert _refused_by(result, "slot_not_free")
        assert result["slots"] == ["09:00", "09:40", "10:20"]
        onec.book_fitting_rest.assert_not_awaited()
        onec.cancel_fitting_rest.assert_not_awaited()

    async def test_a_bare_hour_still_matches_a_padded_grid(self) -> None:
        """1C says «09:00», a caller says «дев'ята» and the LLM writes «9:00».
        Refusing that would send the caller round a loop over punctuation."""
        onec = _onec_mock(schedule=_schedule("09:00"))
        session = _ready_session()
        await self._with_known_booking(onec, session)

        result = await _run(
            session,
            "cancel_fitting",
            {"booking_id": "guid-1", "action": "reschedule",
             "new_date": "2026-09-17", "new_time": "9:00"},
            onec=onec,
        )

        assert result["status"] == "rescheduled"

    async def test_no_new_time_cancels_nothing(self) -> None:
        """fb854e33 reached `cancel_fitting` on «перенеси» alone. Asking is the
        only correct move, and the old booking must survive the asking."""
        onec = _onec_mock()
        session = _ready_session()
        await self._with_known_booking(onec, session)

        result = await _run(
            session,
            "cancel_fitting",
            {"booking_id": "guid-1", "action": "reschedule", "new_date": "2026-09-17"},
            onec=onec,
        )

        assert _rejected(result)
        assert result["action_required"] == "ask_new_time"
        onec.cancel_fitting_rest.assert_not_awaited()
        onec.book_fitting_rest.assert_not_awaited()

    async def test_the_station_comes_from_the_booking_not_from_the_model(self) -> None:
        """29ed5791 sent 000000010, which no tool in that call had ever
        returned. The LLM is no longer asked, so it cannot answer wrongly."""
        onec = _onec_mock()
        session = _ready_session()
        await self._with_known_booking(onec, session)

        await _run(
            session,
            "cancel_fitting",
            {"booking_id": "guid-1", "action": "reschedule",
             "new_date": "2026-09-17", "new_time": TIME,
             "station_id": INVENTED_STATION},
            onec=onec,
        )

        assert onec.book_fitting_rest.await_args.kwargs["station_id"] == STATION

    async def test_the_car_is_carried_over_not_re_asked(self) -> None:
        """«SIRIY Tiguan» came back from 1C; a reschedule that dropped it would
        put an empty car into the new booking and the СТО could not identify it."""
        onec = _onec_mock()
        session = _ready_session()
        await self._with_known_booking(onec, session)

        await _run(
            session,
            "cancel_fitting",
            {"booking_id": "guid-1", "action": "reschedule",
             "new_date": "2026-09-17", "new_time": TIME},
            onec=onec,
        )

        sent = onec.book_fitting_rest.await_args.kwargs
        assert sent["vehicle_info"] == "Tiguan"
        assert sent["auto_number"] == "сірий"

    async def test_an_unknown_booking_id_is_not_rescheduled(self) -> None:
        """Only an id `get_customer_bookings` actually returned may be moved."""
        onec = _onec_mock()
        session = _ready_session()
        await self._with_known_booking(onec, session)

        result = await _run(
            session,
            "cancel_fitting",
            {"booking_id": "guid-does-not-exist", "action": "reschedule",
             "new_date": "2026-09-17", "new_time": TIME},
            onec=onec,
        )

        assert _rejected(result)
        onec.cancel_fitting_rest.assert_not_awaited()
        onec.book_fitting_rest.assert_not_awaited()

    async def test_a_plain_cancel_is_still_a_plain_cancel(self) -> None:
        """The reschedule branch must not swallow `action="cancel"`."""
        onec = _onec_mock()
        session = _ready_session()
        await self._with_known_booking(onec, session)

        result = await _run(
            session,
            "cancel_fitting",
            {"booking_id": "guid-1", "action": "cancel"},
            onec=onec,
        )

        assert result["status"] == "cancelled"
        onec.book_fitting_rest.assert_not_awaited()

    async def test_get_customer_bookings_records_what_a_reschedule_will_need(self) -> None:
        """The wiring, pinned on its own: a corpus test on the reschedule alone
        would stay green if this write disappeared
        (`codetrap_corpus_tests_dont_cover_the_wiring`)."""
        onec = _onec_mock()
        session = _ready_session()
        await self._with_known_booking(onec, session)

        assert session.fitting_known_bookings["guid-1"]["station_id"] == STATION
