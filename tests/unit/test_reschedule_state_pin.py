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
    return onec


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
    assertions below would pass for the wrong reason.
    """
    session = CallSession(uuid.uuid4())
    session.fitting_customer_name = "Олена"
    session.selected_fitting_date = _tomorrow()
    session.fitting_slots_offered = [{"date": _tomorrow(), "time": TIME}]
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
        return await router.execute(tool, args)


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
