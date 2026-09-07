"""Unit tests for `src.agent.interrupts` (Wave 1-C: T4 + T5).

The system pytest on this host lacks pytest-asyncio, so each test drives
the async handler via `asyncio.run(...)`. Tests remain synchronous
functions; that keeps them portable regardless of asyncio plugin
configuration.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.agent.interrupts import (
    InterruptResult,
    handle_cancel_interrupt,
    handle_price_interrupt,
)

# Real UUIDs so the guard passes.
_ID_A = "11111111-1111-1111-1111-111111111111"
_ID_B = "22222222-2222-2222-2222-222222222222"
_ID_C = "33333333-3333-3333-3333-333333333333"


def _run(coro: Any) -> Any:
    """Run an async coroutine synchronously — avoids pytest-asyncio dep."""
    return asyncio.run(coro)


# --- Fixtures ---------------------------------------------------------------


@pytest.fixture
def mock_session() -> SimpleNamespace:
    """Bare-minimum session with the fields the handlers read/write.

    Uses SimpleNamespace so tests can override individual fields without
    constructing a full CallSession (Redis-serializable overhead is
    irrelevant for interrupt logic).
    """
    return SimpleNamespace(
        caller_phone="+380671112233",
        last_fitting_station_id=None,
        fitting_stations_seen=[],
        fitting_diameter_client=None,
        fsm_state=None,
        pending_price_interrupt_needs_diameter=False,
        pending_cancel_action=None,
        pending_cancel_target_id=None,
        pending_cancel_bookings=None,
    )


@pytest.fixture
def mock_router() -> AsyncMock:
    """AsyncMock with an `execute(name, args)` method.

    Tests set `router.execute.return_value = ...` or `.side_effect = [...]`
    to control per-call responses.
    """
    return AsyncMock()


def _station(sid: str, city: str, address: str = "вул. Тестова, 1") -> dict[str, Any]:
    return {"id": sid, "city": city, "address": address, "name": "Точка"}


def _price_result(car: int | None = 800, suv: int | None = 1000) -> dict[str, Any]:
    prices: list[dict[str, Any]] = []
    if car is not None:
        prices.append({"category": "car", "price": car, "service": "Комплекс легкові R16"})
    if suv is not None:
        prices.append({"category": "suv", "price": suv, "service": "Комплекс SUV R16"})
    return {"prices": prices}


def _booking(
    bid: str,
    date: str = "2026-09-10",
    time_: str = "14:00",
    city: str = "Київ",
    address: str = "вул. Тестова, 1",
) -> dict[str, Any]:
    return {
        "booking_id": bid,
        "date": date,
        "time": time_,
        "city": city,
        "address": address,
        "station_name": "Тестова точка",
    }


# --- Price: happy path ------------------------------------------------------


class TestPriceInterruptHappyPath:
    def test_city_and_diameter_from_session(self, mock_session: Any, mock_router: AsyncMock) -> None:
        mock_session.last_fitting_station_id = "st-1"
        mock_session.fitting_stations_seen = [_station("st-1", "Київ")]
        mock_session.fitting_diameter_client = 16

        mock_router.execute.return_value = _price_result(car=800, suv=1000)
        result = _run(handle_price_interrupt("а скільки коштує?", mock_session, mock_router))

        assert result.handled is True
        assert "R16" in result.reply_to_customer
        assert "Київ" in result.reply_to_customer
        assert "800" in result.reply_to_customer
        assert "1000" in result.reply_to_customer

        mock_router.execute.assert_awaited_once_with(
            "get_fitting_price", {"tire_diameter": 16, "station_id": "st-1"}
        )

    def test_diameter_from_customer_text(self, mock_session: Any, mock_router: AsyncMock) -> None:
        mock_session.last_fitting_station_id = "st-1"
        mock_session.fitting_stations_seen = [_station("st-1", "Львів")]

        mock_router.execute.return_value = _price_result(car=900, suv=1200)
        result = _run(handle_price_interrupt("а скільки за R17?", mock_session, mock_router))

        assert result.handled is True
        assert "R17" in result.reply_to_customer
        assert result.session_updates["fitting_diameter_client"] == 17
        mock_router.execute.assert_awaited_once()

    def test_word_form_diameter(self, mock_session: Any, mock_router: AsyncMock) -> None:
        mock_session.last_fitting_station_id = "st-1"
        mock_session.fitting_stations_seen = [_station("st-1", "Одеса")]
        mock_router.execute.return_value = _price_result(car=700, suv=850)

        result = _run(handle_price_interrupt("на шістнадцять", mock_session, mock_router))
        assert result.handled is True
        assert result.session_updates["fitting_diameter_client"] == 16

    def test_different_tenant_different_city(self, mock_session: Any, mock_router: AsyncMock) -> None:
        mock_session.last_fitting_station_id = "st-42"
        mock_session.fitting_stations_seen = [_station("st-42", "Дніпро")]
        mock_session.fitting_diameter_client = 18
        mock_router.execute.return_value = _price_result(car=1100, suv=1400)

        result = _run(handle_price_interrupt("а ціна?", mock_session, mock_router))
        assert "Дніпро" in result.reply_to_customer
        assert "1100" in result.reply_to_customer

    def test_pins_diameter_into_session(self, mock_session: Any, mock_router: AsyncMock) -> None:
        mock_session.last_fitting_station_id = "st-1"
        mock_session.fitting_stations_seen = [_station("st-1", "Київ")]
        mock_session.fitting_diameter_client = 15
        mock_router.execute.return_value = _price_result(car=750, suv=900)

        result = _run(handle_price_interrupt("а скільки?", mock_session, mock_router))
        assert result.session_updates.get("fitting_diameter_client") == 15
        assert result.session_updates.get("pending_price_interrupt_needs_diameter") is False


# --- Price: needs-diameter multi-turn ---------------------------------------


class TestPriceInterruptNeedsDiameter:
    def test_no_diameter_asks_and_sets_flag(self, mock_session: Any, mock_router: AsyncMock) -> None:
        mock_session.last_fitting_station_id = "st-1"
        mock_session.fitting_stations_seen = [_station("st-1", "Київ")]
        # no diameter, text without a number

        result = _run(handle_price_interrupt("а ціна?", mock_session, mock_router))
        assert result.handled is True
        assert "діаметр" in result.reply_to_customer.lower()
        assert result.session_updates["pending_price_interrupt_needs_diameter"] is True
        # No tool call yet.
        mock_router.execute.assert_not_awaited()

    def test_followup_turn_with_number_returns_price(
        self, mock_session: Any, mock_router: AsyncMock
    ) -> None:
        # Simulate the followup turn: flag is set, diameter still None.
        mock_session.last_fitting_station_id = "st-1"
        mock_session.fitting_stations_seen = [_station("st-1", "Київ")]
        mock_session.pending_price_interrupt_needs_diameter = True
        mock_router.execute.return_value = _price_result(car=800, suv=1000)

        result = _run(handle_price_interrupt("16", mock_session, mock_router))
        assert result.handled is True
        assert "R16" in result.reply_to_customer
        assert result.session_updates["pending_price_interrupt_needs_diameter"] is False
        mock_router.execute.assert_awaited_once()

    def test_followup_turn_without_number_asks_again(
        self, mock_session: Any, mock_router: AsyncMock
    ) -> None:
        mock_session.last_fitting_station_id = "st-1"
        mock_session.fitting_stations_seen = [_station("st-1", "Київ")]
        mock_session.pending_price_interrupt_needs_diameter = True

        result = _run(handle_price_interrupt("не пам'ятаю", mock_session, mock_router))
        assert result.handled is True
        assert "діаметр" in result.reply_to_customer.lower()
        assert result.session_updates["pending_price_interrupt_needs_diameter"] is True
        mock_router.execute.assert_not_awaited()


# --- Price: resume message --------------------------------------------------


class TestPriceInterruptResumeMessage:
    def test_reply_ends_with_resume_phrase_fallback(
        self, mock_session: Any, mock_router: AsyncMock
    ) -> None:
        mock_session.last_fitting_station_id = "st-1"
        mock_session.fitting_stations_seen = [_station("st-1", "Київ")]
        mock_session.fitting_diameter_client = 16
        mock_router.execute.return_value = _price_result(car=800, suv=1000)

        result = _run(handle_price_interrupt("а ціна?", mock_session, mock_router))
        assert "Продовжуємо запис" in result.reply_to_customer

    def test_resume_phrase_uses_fsm_state_when_set(
        self, mock_session: Any, mock_router: AsyncMock
    ) -> None:
        mock_session.last_fitting_station_id = "st-1"
        mock_session.fitting_stations_seen = [_station("st-1", "Київ")]
        mock_session.fitting_diameter_client = 16
        mock_session.fsm_state = "krok_4_storage"
        mock_router.execute.return_value = _price_result(car=800, suv=1000)

        result = _run(handle_price_interrupt("а ціна?", mock_session, mock_router))
        assert "Продовжуємо запис" in result.reply_to_customer
        assert "krok_4_storage" in result.reply_to_customer

    def test_resume_phrase_even_when_price_empty(
        self, mock_session: Any, mock_router: AsyncMock
    ) -> None:
        mock_session.last_fitting_station_id = "st-1"
        mock_session.fitting_stations_seen = [_station("st-1", "Київ")]
        mock_session.fitting_diameter_client = 16
        mock_router.execute.return_value = {"prices": []}

        result = _run(handle_price_interrupt("а ціна?", mock_session, mock_router))
        assert result.handled is True
        assert "Продовжуємо запис" in result.reply_to_customer


# --- Cancel: zero bookings --------------------------------------------------


class TestCancelInterruptZeroBookings:
    def test_reports_no_bookings(self, mock_session: Any, mock_router: AsyncMock) -> None:
        mock_router.execute.return_value = {"total": 0, "bookings": []}

        result = _run(handle_cancel_interrupt("скасуйте запис", mock_session, mock_router))
        assert result.handled is True
        assert "немає" in result.reply_to_customer.lower()
        assert result.session_updates["pending_cancel_action"] is None

    def test_resume_phrase_included(self, mock_session: Any, mock_router: AsyncMock) -> None:
        mock_router.execute.return_value = {"total": 0, "bookings": []}

        result = _run(handle_cancel_interrupt("скасуйте запис", mock_session, mock_router))
        assert "Продовжуємо запис" in result.reply_to_customer


# --- Cancel: single booking -------------------------------------------------


class TestCancelInterruptSingleBooking:
    def test_confirm_yes_cancels(self, mock_session: Any, mock_router: AsyncMock) -> None:
        # 1) initial turn: 1 booking → propose confirm.
        mock_router.execute.side_effect = [
            {"total": 1, "bookings": [_booking(_ID_A)]},
            {"booking_id": _ID_A, "status": "cancelled", "message": "Ok"},
        ]
        first = _run(handle_cancel_interrupt("скасуйте", mock_session, mock_router))
        assert first.handled is True
        assert first.session_updates["pending_cancel_action"] == "awaiting_confirmation"
        assert first.session_updates["pending_cancel_target_id"] == _ID_A

        # 2) followup turn: user says «так».
        for k, v in first.session_updates.items():
            setattr(mock_session, k, v)

        second = _run(handle_cancel_interrupt("так", mock_session, mock_router))
        assert second.handled is True
        assert "скасовано" in second.reply_to_customer.lower()
        assert second.session_updates["pending_cancel_action"] is None
        assert second.session_updates.get("fitting_booked") is False
        # cancel_fitting was called with the correct id.
        assert mock_router.execute.await_args_list[-1].args == (
            "cancel_fitting",
            {"booking_id": _ID_A},
        )

    def test_confirm_no_keeps_booking(self, mock_session: Any, mock_router: AsyncMock) -> None:
        mock_session.pending_cancel_action = "awaiting_confirmation"
        mock_session.pending_cancel_target_id = _ID_A

        result = _run(handle_cancel_interrupt("ні, залиште", mock_session, mock_router))
        assert result.handled is True
        assert "залишаємо" in result.reply_to_customer.lower()
        assert result.session_updates["pending_cancel_action"] is None
        mock_router.execute.assert_not_awaited()

    def test_confirm_unclear_reasks(self, mock_session: Any, mock_router: AsyncMock) -> None:
        mock_session.pending_cancel_action = "awaiting_confirmation"
        mock_session.pending_cancel_target_id = _ID_A

        result = _run(handle_cancel_interrupt("хмм не знаю", mock_session, mock_router))
        assert result.handled is True
        assert result.resume_state == "cancel_awaiting_confirmation"
        assert "«так»" in result.reply_to_customer
        mock_router.execute.assert_not_awaited()


# --- Cancel: multiple bookings ---------------------------------------------


class TestCancelInterruptMultipleBookings:
    def test_pick_by_ordinal(self, mock_session: Any, mock_router: AsyncMock) -> None:
        bookings = [
            _booking(_ID_A, date="2026-09-10", time_="10:00"),
            _booking(_ID_B, date="2026-09-12", time_="14:00"),
        ]
        mock_router.execute.return_value = {"total": 2, "bookings": bookings}

        first = _run(handle_cancel_interrupt("скасуйте", mock_session, mock_router))
        assert first.session_updates["pending_cancel_action"] == "awaiting_selection"
        # Both bookings mentioned in the listing.
        assert "1)" in first.reply_to_customer and "2)" in first.reply_to_customer

        for k, v in first.session_updates.items():
            setattr(mock_session, k, v)

        pick = _run(handle_cancel_interrupt("другий", mock_session, mock_router))
        assert pick.handled is True
        assert pick.session_updates["pending_cancel_target_id"] == _ID_B
        assert pick.resume_state == "cancel_awaiting_confirmation"

    def test_pick_by_date_fragment(self, mock_session: Any, mock_router: AsyncMock) -> None:
        bookings = [
            _booking(_ID_A, date="2026-09-10", time_="10:00"),
            _booking(_ID_B, date="2026-09-22", time_="14:00"),
        ]
        mock_session.pending_cancel_action = "awaiting_selection"
        mock_session.pending_cancel_bookings = bookings

        pick = _run(handle_cancel_interrupt("на 22", mock_session, mock_router))
        assert pick.handled is True
        assert pick.session_updates["pending_cancel_target_id"] == _ID_B

    def test_ambiguous_selection_reasks(self, mock_session: Any, mock_router: AsyncMock) -> None:
        bookings = [
            _booking(_ID_A, date="2026-09-10", time_="10:00"),
            _booking(_ID_B, date="2026-09-12", time_="14:00"),
            _booking(_ID_C, date="2026-09-15", time_="16:00"),
        ]
        mock_session.pending_cancel_action = "awaiting_selection"
        mock_session.pending_cancel_bookings = bookings

        pick = _run(handle_cancel_interrupt("не знаю", mock_session, mock_router))
        assert pick.handled is True
        assert pick.resume_state == "cancel_awaiting_selection"
        assert (
            "номер зі списку" in pick.reply_to_customer
            or "дату" in pick.reply_to_customer
        )


# --- Cancel: guard against invalid booking ids -----------------------------


class TestCancelInterruptGuardInvalidBookingId:
    def test_placeholder_uuid_in_confirmation_falls_back_gracefully(
        self, mock_session: Any, mock_router: AsyncMock
    ) -> None:
        # Pending confirmation but target_id is the sentinel placeholder.
        mock_session.pending_cancel_action = "awaiting_confirmation"
        mock_session.pending_cancel_target_id = "00000000-0000-0000-0000-000000000000"

        result = _run(handle_cancel_interrupt("так", mock_session, mock_router))
        assert result.handled is True
        # No cancel_fitting call because guard rejected the id.
        mock_router.execute.assert_not_awaited()
        assert result.session_updates["pending_cancel_action"] is None
        assert result.session_updates["pending_cancel_target_id"] is None

    def test_single_booking_with_invalid_id_returns_fallback(
        self, mock_session: Any, mock_router: AsyncMock
    ) -> None:
        # get_customer_bookings returns a booking with placeholder id (1C bug).
        mock_router.execute.return_value = {
            "total": 1,
            "bookings": [_booking("000000000")],
        }

        result = _run(handle_cancel_interrupt("скасуйте", mock_session, mock_router))
        # Handler bails to LLM path (handled=False) rather than propose
        # a fake cancellation.
        assert result.handled is False


# --- Return format guards --------------------------------------------------


class TestReturnFormat:
    def test_cancel_no_caller_phone_returns_handled_false(
        self, mock_session: Any, mock_router: AsyncMock
    ) -> None:
        mock_session.caller_phone = None
        result = _run(handle_cancel_interrupt("скасуйте", mock_session, mock_router))
        assert isinstance(result, InterruptResult)
        assert result.handled is False
        assert result.reply_to_customer == ""
        mock_router.execute.assert_not_awaited()

    def test_price_tool_error_returns_handled_false(
        self, mock_session: Any, mock_router: AsyncMock
    ) -> None:
        mock_session.last_fitting_station_id = "st-1"
        mock_session.fitting_stations_seen = [_station("st-1", "Київ")]
        mock_session.fitting_diameter_client = 16
        mock_router.execute.return_value = {"error": True, "reason": "diameter_not_asked"}

        result = _run(handle_price_interrupt("а ціна?", mock_session, mock_router))
        assert isinstance(result, InterruptResult)
        assert result.handled is False

    def test_cancel_bookings_result_not_dict_returns_handled_false(
        self, mock_session: Any, mock_router: AsyncMock
    ) -> None:
        mock_router.execute.return_value = "unexpected string"
        result = _run(handle_cancel_interrupt("скасуйте", mock_session, mock_router))
        assert result.handled is False

    def test_cancel_1c_failed_status_falls_through(
        self, mock_session: Any, mock_router: AsyncMock
    ) -> None:
        # Confirmation turn — cancel_fitting responds with error status.
        mock_session.pending_cancel_action = "awaiting_confirmation"
        mock_session.pending_cancel_target_id = _ID_A
        mock_router.execute.return_value = {
            "booking_id": _ID_A,
            "status": "error",
            "message": "fail",
        }

        result = _run(handle_cancel_interrupt("так", mock_session, mock_router))
        assert result.handled is False


# --- Sanity: uuid helper works on real UUIDs -------------------------------


def test_real_uuid_is_accepted() -> None:
    # Cheap sanity check that our test id constants are actually valid.
    for id_ in (_ID_A, _ID_B, _ID_C):
        uuid.UUID(id_)
