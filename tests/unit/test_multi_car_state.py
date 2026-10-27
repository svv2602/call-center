"""Multi-car booking state: flag, session fields, tool filter (wave 2-C).

No wiring is tested here — call sites, FSM and the booking callback are
wave 3. Ids below are deliberately fake.
"""

from __future__ import annotations

import uuid

import pytest

from src.agent.multi_car_detect import MAX_CARS_PER_CALL
from src.agent.tools import ALL_TOOLS, filter_tools_by_state
from src.config import FeatureFlagSettings
from src.core.call_session import CallSession

BOOKING_TOOLS = {"book_fitting", "get_fitting_slots", "reserve_fitting_slot"}
NEW_KEYS = {"fitting_bookings", "cars_expected", "fitting_car_number"}


@pytest.fixture
def flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FF_MULTI_CAR_BOOKING", "true")


@pytest.fixture
def flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FF_MULTI_CAR_BOOKING", raising=False)


def _names(tools: list[dict]) -> set[str]:  # type: ignore[type-arg]
    return {t["name"] for t in tools}


def _book(session: CallSession, n: int) -> None:
    session.record_fitting_booking(
        booking_id=f"fake-guid-{n}",
        station_id="fake-station-1",
        date="2026-11-03",
        time="10:00",
        vehicle=f"FakeCar {n}",
        color="синій",
    )
    session.fitting_booked = True


def _filled_session() -> CallSession:
    """A session mid-call after the first car: every field start_next_car
    decides on is set to a non-default value."""
    s = CallSession(uuid.uuid4())
    s.caller_phone = "+380000000000"
    s.customer_id = "fake-customer"
    s.fitting_customer_name = "Олег"
    s.name_from_profile = True
    s.last_fitting_station_id = "fake-station-1"
    s.fitting_station_ids = {"fake-station-1", "fake-station-2"}
    s.fitting_stations_seen = [{"id": "fake-station-1", "city": "Київ"}]
    s.fitting_known_bookings = {"fake-guid-0": {"booking_id": "fake-guid-0"}}
    s.storage_contracts_found = ["fake-contract"]
    s.fitting_dates_no_slots = {"2026-11-02"}
    s.fitting_date_guard_fired = True
    s.fitting_price_quoted = True
    s.fsm_filled_fields = {"city": "Київ"}
    # car
    s.fitting_plate = "синій"
    s.fitting_vehicle_brand = "FakeCar"
    s.fitting_color_corrected = True
    s.fitting_storage_choice = "contract"
    s.fitting_storage_contract = "fake-contract"
    s.fitting_diameter_client = 16
    s.storage_contract_guard_triggered = True
    # slot
    s.selected_fitting_date = "2026-11-03"
    s.selected_fitting_time = "10:00"
    s.fitting_slots_offered = [{"date": "2026-11-03", "time": "10:00"}]
    s.fsm_slots_fetched = {"fake-station-1|2026-11-03"}
    s.fitting_requested_weekday = 1
    s.fitting_weekday_bounced_since_lookup = 1
    s.book_time_unchosen_refusals = 2
    s.krok8_confabulation_pending = True
    return s


# --- config / metric ------------------------------------------------------


def test_flag_defaults_off(flag_off: None) -> None:
    assert FeatureFlagSettings().multi_car_booking is False


def test_flag_reads_env(flag_on: None) -> None:
    assert FeatureFlagSettings().multi_car_booking is True


def test_metric_registered_with_event_label() -> None:
    from src.monitoring.metrics import multi_car_booking_total

    for event in ("started", "booked", "over_cap"):
        multi_car_booking_total.labels(event=event)
    assert multi_car_booking_total._name == "callcenter_multi_car_booking"


# --- filter_tools_by_state ------------------------------------------------


def test_filter_without_new_arg_is_head_behaviour() -> None:
    booked = filter_tools_by_state(ALL_TOOLS, order_stage=None, fitting_booked=True)
    assert not (_names(booked) & BOOKING_TOOLS)
    assert (
        filter_tools_by_state(
            ALL_TOOLS, order_stage=None, fitting_booked=True, next_car_pending=False
        )
        == booked
    )


def test_filter_keeps_booking_tools_when_next_car_pending() -> None:
    result = filter_tools_by_state(
        ALL_TOOLS, order_stage=None, fitting_booked=True, next_car_pending=True
    )
    assert _names(result) >= BOOKING_TOOLS


def test_filter_pending_without_booking_is_unchanged() -> None:
    assert filter_tools_by_state(
        ALL_TOOLS, order_stage=None, fitting_booked=False, next_car_pending=True
    ) == filter_tools_by_state(ALL_TOOLS, order_stage=None, fitting_booked=False)


# --- next_car_pending -----------------------------------------------------


def test_flag_off_session_gives_head_tools(flag_off: None) -> None:
    s = CallSession(uuid.uuid4())
    s.cars_expected = 2
    _book(s, 1)
    assert s.next_car_pending is False
    result = filter_tools_by_state(
        ALL_TOOLS, fitting_booked=s.fitting_booked, next_car_pending=s.next_car_pending
    )
    assert result == filter_tools_by_state(ALL_TOOLS, fitting_booked=True)
    assert not (_names(result) & BOOKING_TOOLS)


def test_flag_on_second_car_pending_keeps_tools(flag_on: None) -> None:
    s = CallSession(uuid.uuid4())
    s.cars_expected = 2
    _book(s, 1)
    assert s.next_car_pending is True
    result = filter_tools_by_state(
        ALL_TOOLS, fitting_booked=s.fitting_booked, next_car_pending=s.next_car_pending
    )
    assert _names(result) >= BOOKING_TOOLS


def test_flag_on_all_expected_cars_booked_closes(flag_on: None) -> None:
    s = CallSession(uuid.uuid4())
    s.cars_expected = 2
    _book(s, 1)
    _book(s, 2)
    assert s.next_car_pending is False


def test_flag_on_no_count_named_is_not_pending(flag_on: None) -> None:
    s = CallSession(uuid.uuid4())
    _book(s, 1)
    assert s.cars_expected is None
    assert s.next_car_pending is False


def test_cap_three_bookings_closes_tools(flag_on: None) -> None:
    s = CallSession(uuid.uuid4())
    s.cars_expected = 5
    for n in range(1, MAX_CARS_PER_CALL):
        _book(s, n)
    assert s.next_car_pending is True  # third car still allowed
    _book(s, MAX_CARS_PER_CALL)
    assert len(s.fitting_bookings) == 3
    assert s.next_car_pending is False
    result = filter_tools_by_state(
        ALL_TOOLS, fitting_booked=True, next_car_pending=s.next_car_pending
    )
    assert not (_names(result) & BOOKING_TOOLS)


def test_cars_over_cap() -> None:
    s = CallSession(uuid.uuid4())
    assert s.cars_over_cap is False
    s.cars_expected = MAX_CARS_PER_CALL
    assert s.cars_over_cap is False
    s.cars_expected = MAX_CARS_PER_CALL + 1
    assert s.cars_over_cap is True


def test_record_booking_dedupes_same_guid() -> None:
    s = CallSession(uuid.uuid4())
    _book(s, 1)
    assert (
        s.record_fitting_booking(
            booking_id="fake-guid-1",
            station_id=None,
            date=None,
            time=None,
            vehicle=None,
            color=None,
        )
        is False
    )
    assert len(s.fitting_bookings) == 1


def test_previous_booking_slot() -> None:
    s = CallSession(uuid.uuid4())
    assert s.previous_booking_slot() is None
    _book(s, 1)
    assert s.previous_booking_slot() == ("2026-11-03", "10:00")


# --- start_next_car -------------------------------------------------------


def test_start_next_car_resets_car_and_slot_keeps_call_fields(flag_on: None) -> None:
    s = _filled_session()
    s.cars_expected = 2
    _book(s, 1)
    assert s.start_next_car() is True
    assert s.fitting_car_number == 2
    # car + slot cleared
    assert s.fitting_plate is None
    assert s.fitting_vehicle_brand is None
    assert s.fitting_color_corrected is False
    assert s.fitting_storage_choice is None
    assert s.fitting_storage_contract is None
    assert s.fitting_diameter_client is None
    assert s.storage_contract_guard_triggered is False
    assert s.selected_fitting_date is None
    assert s.selected_fitting_time is None
    assert s.fitting_slots_offered == []
    assert s.fsm_slots_fetched == set()
    assert s.fitting_requested_weekday is None
    assert s.fitting_weekday_bounced_since_lookup is None
    assert s.book_time_unchosen_refusals == 0
    assert s.krok8_confabulation_pending is False
    # R03: city / station / name / phone stay; call-level facts stay
    assert s.last_fitting_station_id == "fake-station-1"
    assert s.fitting_station_ids == {"fake-station-1", "fake-station-2"}
    assert s.fitting_stations_seen == [{"id": "fake-station-1", "city": "Київ"}]
    assert s.fitting_customer_name == "Олег"
    assert s.name_from_profile is True
    assert s.caller_phone == "+380000000000"
    assert s.customer_id == "fake-customer"
    assert s.fitting_booked is True
    assert s.fitting_known_bookings == {"fake-guid-0": {"booking_id": "fake-guid-0"}}
    assert s.storage_contracts_found == ["fake-contract"]
    assert s.fitting_dates_no_slots == {"2026-11-02"}
    assert s.fitting_date_guard_fired is True
    assert s.fitting_price_quoted is True
    assert s.fsm_filled_fields == {"city": "Київ"}
    # the «same time» hint survives the reset (R03)
    assert s.previous_booking_slot() == ("2026-11-03", "10:00")


def test_start_next_car_noop_when_flag_off(flag_off: None) -> None:
    s = _filled_session()
    s.cars_expected = 2
    _book(s, 1)
    before = s.to_dict()
    assert s.start_next_car() is False
    assert s.to_dict() == before


def test_start_next_car_noop_before_current_car_booked(flag_on: None) -> None:
    s = _filled_session()
    s.cars_expected = 2
    assert s.start_next_car() is False
    assert s.fitting_plate == "синій"
    assert s.fitting_car_number == 1


def test_start_next_car_twice_in_a_row_resets_once(flag_on: None) -> None:
    s = _filled_session()
    s.cars_expected = 3
    _book(s, 1)
    assert s.start_next_car() is True
    s.fitting_vehicle_brand = "SecondFakeCar"
    assert s.start_next_car() is False
    assert s.fitting_vehicle_brand == "SecondFakeCar"
    assert s.fitting_car_number == 2


def test_start_next_car_refused_at_cap(flag_on: None) -> None:
    s = _filled_session()
    s.cars_expected = 5
    for n in range(1, MAX_CARS_PER_CALL + 1):
        _book(s, n)
        s.fitting_car_number = n
    assert s.start_next_car() is False
    assert s.fitting_plate == "синій"


# --- serialization --------------------------------------------------------


def test_roundtrip_real_session(flag_on: None) -> None:
    s = _filled_session()
    s.cars_expected = 3
    _book(s, 1)
    s.start_next_car()
    restored = CallSession.from_dict(s.to_dict())
    assert restored.fitting_bookings == s.fitting_bookings
    assert restored.cars_expected == 3
    assert restored.fitting_car_number == 2
    assert restored.next_car_pending is True
    assert restored.to_dict() == s.to_dict()


def test_old_session_without_new_keys_loads_with_defaults() -> None:
    data = _filled_session().to_dict()
    for key in NEW_KEYS:
        data.pop(key)
    restored = CallSession.from_dict(data)
    assert restored.fitting_bookings == []
    assert restored.cars_expected is None
    assert restored.fitting_car_number == 1
    assert restored.next_car_pending is False


def test_to_dict_only_adds_new_keys() -> None:
    """Flag off and nothing set: every pre-existing key keeps its value."""
    s = CallSession(uuid.uuid4())
    data = s.to_dict()
    assert {k: data[k] for k in NEW_KEYS} == {
        "fitting_bookings": [],
        "cars_expected": None,
        "fitting_car_number": 1,
    }


@pytest.mark.parametrize(
    ("key", "bad"),
    [
        ("fitting_bookings", "fake-guid-1"),
        ("cars_expected", True),
        ("cars_expected", 0),
        ("cars_expected", "2"),
        ("fitting_car_number", 0),
        ("fitting_car_number", None),
    ],
)
def test_malformed_values_fall_back_to_defaults(
    key: str, bad: object, caplog: pytest.LogCaptureFixture
) -> None:
    data = CallSession(uuid.uuid4()).to_dict()
    data[key] = bad
    with caplog.at_level("WARNING", logger="src.core.call_session"):
        restored = CallSession.from_dict(data)
    defaults = CallSession(uuid.uuid4())
    assert getattr(restored, key) == getattr(defaults, key)
    # logged, never silently defaulted
    assert any(key in r.getMessage() for r in caplog.records)


def test_malformed_booking_entries_are_dropped() -> None:
    data = CallSession(uuid.uuid4()).to_dict()
    data["fitting_bookings"] = [{"booking_id": "fake-guid-1"}, "junk", None]
    restored = CallSession.from_dict(data)
    assert restored.fitting_bookings == [{"booking_id": "fake-guid-1"}]
