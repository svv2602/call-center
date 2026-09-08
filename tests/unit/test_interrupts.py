"""Unit tests for the side-door interrupt handlers (`src/agent/interrupts.py`).

Wave 3-B of the FSM refactor. The first group, `TestRevertRegressions`, pins the
three defects that got the first version of this module reverted (`c8c6601`):

1. the fire counter did not survive a Redis round-trip, so the cap never
   tripped and the PRICE handler answered five turns in a row;
2. an unconditional «Продовжуємо запис» was appended even with no booking in
   progress;
3. `handled=True` was returned with an empty `session_updates` and
   `advanced=False`, so the dialog did not move.

Every handled path is additionally checked against the contract invariant
`handled is False or advanced or session_updates`.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.agent.fitting_fsm import STATES, FsmState
from src.agent.interrupts import (
    CANCEL_AWAITING_CONFIRMATION,
    CANCEL_AWAITING_SELECTION,
    CANCEL_HANDLER,
    MAX_INTERRUPT_FIRES,
    PRICE_HANDLER,
    InterruptResult,
    handle_cancel_interrupt,
    handle_price_interrupt,
)
from src.core.call_session import CallSession

# The literal the reverted version hardcoded. It must never come back.
REVERTED_RESUME_LITERAL = "Продовжуємо запис"

BOOKING_A = "11111111-1111-4111-8111-111111111111"
BOOKING_B = "22222222-2222-4222-8222-222222222222"
BOOKING_C = "33333333-3333-4333-8333-333333333333"

PRICE_QUESTION = "А скільки коштує шиномонтаж?"
CANCEL_REQUEST = "Скасуйте мій запис, будь ласка"


# --- Fixtures ---------------------------------------------------------------


def make_session(**overrides: Any) -> CallSession:
    """A `CallSession` mid-booking: station pinned, caller identified."""
    session = CallSession(uuid.uuid4())
    session.caller_phone = "+380671234567"
    session.last_fitting_station_id = "ST-1"
    session.fitting_stations_seen = [
        {"id": "ST-1", "name": "Шиномонтаж №1", "city": "Київ", "address": "вул. Тестова, 1"}
    ]
    session.fsm_state = FsmState.CITY.value
    for name, value in overrides.items():
        setattr(session, name, value)
    return session


def make_booking(booking_id: str, date: str = "10.09.2026", time: str = "10:00") -> dict[str, Any]:
    """A booking dict shaped like `_get_customer_bookings` output."""
    return {
        "booking_id": booking_id,
        "station_id": "ST-1",
        "date": date,
        "time": time,
        "period": "",
        "person": "Тест",
        "city": "Київ",
        "address": "вул. Тестова, 1",
        "station_name": "Шиномонтаж №1",
    }


@pytest.fixture
def session() -> CallSession:
    return make_session()


@pytest.fixture
def router() -> AsyncMock:
    """AsyncMock router exposing the three tools the handlers use."""
    mock = AsyncMock()
    mock.get_fitting_price.return_value = {
        "prices": [
            {"service": "Комплекс R17 легкового", "price": "450", "category": "car"},
            {"service": "Комплекс R17 SUV", "price": "550", "category": "suv"},
        ]
    }
    mock.get_customer_bookings.return_value = {"total": 0, "bookings": []}
    mock.cancel_fitting.return_value = {"booking_id": BOOKING_A, "status": "cancelled"}
    return mock


def assert_contract(result: InterruptResult) -> None:
    """The Wave 3-B invariant: a handled turn must move something."""
    assert result.handled is False or result.advanced or result.session_updates


# --- 1. Regressions on the reverted version ---------------------------------


class TestRevertRegressions:
    """Direct regressions for the three defects behind `c8c6601`."""

    @pytest.mark.parametrize("text", ["так", "17", "мені", "не про це"])
    async def test_price_handler_ignores_bare_booking_replies(
        self, text: str, session: CallSession, router: AsyncMock
    ) -> None:
        """«так» / «17» / «мені» / «не про» must not open the price flow.

        These are the exact transcripts on which the reverted handler fired and
        repeated the same sentence. Without a price question in the caller's own
        words (default-deny) the handler declines the turn.
        """
        session.fitting_diameter_client = 17
        result = await handle_price_interrupt(text, session, router)
        assert result.handled is False
        assert result.reply_to_customer == ""
        router.get_fitting_price.assert_not_awaited()
        assert_contract(result)

    async def test_price_handler_stops_after_cap_on_repeated_questions(
        self, session: CallSession, router: AsyncMock
    ) -> None:
        """Three genuine price questions: the third falls through to the LLM."""
        session.fitting_diameter_client = 17
        first = await handle_price_interrupt(PRICE_QUESTION, session, router)
        second = await handle_price_interrupt(PRICE_QUESTION, session, router)
        third = await handle_price_interrupt(PRICE_QUESTION, session, router)

        assert first.handled is True
        assert second.handled is True
        assert third.handled is False
        assert third.reply_to_customer == ""
        assert session.interrupt_counts[PRICE_HANDLER] == MAX_INTERRUPT_FIRES
        assert router.get_fitting_price.await_count == MAX_INTERRUPT_FIRES
        for result in (first, second, third):
            assert_contract(result)

    async def test_no_resume_phrase_when_no_booking_in_progress(self, router: AsyncMock) -> None:
        """`fsm_state is None` → no resume phrase and no resume state."""
        session = make_session(fsm_state=None, fitting_diameter_client=17)
        result = await handle_price_interrupt(PRICE_QUESTION, session, router)

        assert result.handled is True
        assert result.resume_state is None
        assert REVERTED_RESUME_LITERAL not in result.reply_to_customer
        for config in STATES.values():
            assert config.resume_phrase not in result.reply_to_customer
        assert_contract(result)

    async def test_cancel_reply_has_no_hardcoded_resume_phrase(self, router: AsyncMock) -> None:
        """The cancel flow must not invent a resume phrase either."""
        session = make_session(fsm_state=None)
        router.get_customer_bookings.return_value = {
            "total": 1,
            "bookings": [make_booking(BOOKING_A)],
        }
        result = await handle_cancel_interrupt(CANCEL_REQUEST, session, router)

        assert result.handled is True
        assert result.resume_state is None
        assert REVERTED_RESUME_LITERAL not in result.reply_to_customer
        assert_contract(result)

    async def test_handled_true_always_moves_the_dialog(self, router: AsyncMock) -> None:
        """Sweep the handled paths: none returns "nothing changed"."""
        results: list[InterruptResult] = []

        priced = make_session(fitting_diameter_client=17)
        results.append(await handle_price_interrupt(PRICE_QUESTION, priced, router))

        asked = make_session()
        results.append(await handle_price_interrupt(PRICE_QUESTION, asked, router))

        router.get_customer_bookings.return_value = {"total": 0, "bookings": []}
        empty = make_session()
        results.append(await handle_cancel_interrupt(CANCEL_REQUEST, empty, router))

        router.get_customer_bookings.return_value = {
            "total": 1,
            "bookings": [make_booking(BOOKING_A)],
        }
        single = make_session()
        results.append(await handle_cancel_interrupt(CANCEL_REQUEST, single, router))
        results.append(await handle_cancel_interrupt("так", single, router))

        router.get_customer_bookings.return_value = {
            "total": 2,
            "bookings": [make_booking(BOOKING_A), make_booking(BOOKING_B, date="15.09.2026")],
        }
        multi = make_session()
        results.append(await handle_cancel_interrupt(CANCEL_REQUEST, multi, router))
        results.append(await handle_cancel_interrupt("не знаю", multi, router))

        assert any(r.handled for r in results)
        for result in results:
            assert_contract(result)
            if result.handled:
                assert result.reply_to_customer.strip()

    async def test_contract_violation_is_downgraded_not_spoken(
        self, session: CallSession, router: AsyncMock
    ) -> None:
        """A tool failure must not leave a handled turn with nothing to say."""
        session.fitting_diameter_client = 17
        router.get_fitting_price.side_effect = RuntimeError("1C down")
        result = await handle_price_interrupt(PRICE_QUESTION, session, router)

        assert result.handled is False
        assert result.reply_to_customer == ""
        assert result.advanced is False
        assert_contract(result)


# --- 2. PRICE happy path ----------------------------------------------------


class TestPriceInterruptHappyPath:
    async def test_quotes_price_with_city_and_diameter(
        self, session: CallSession, router: AsyncMock
    ) -> None:
        session.fitting_diameter_client = 17
        result = await handle_price_interrupt(PRICE_QUESTION, session, router)

        assert result.handled is True
        assert result.advanced is True
        assert "R17" in result.reply_to_customer
        assert "Київ" in result.reply_to_customer
        assert "450" in result.reply_to_customer
        assert_contract(result)

    async def test_groups_car_and_suv_prices(self, session: CallSession, router: AsyncMock) -> None:
        session.fitting_diameter_client = 17
        result = await handle_price_interrupt(PRICE_QUESTION, session, router)

        assert "легкові — 450 грн" in result.reply_to_customer
        assert "позашляховики — 550 грн" in result.reply_to_customer
        assert_contract(result)

    async def test_pins_diameter_and_station_before_calling_the_tool(
        self, router: AsyncMock
    ) -> None:
        """Wave 12 guard: `_get_fitting_price` rejects an unpinned diameter."""
        session = make_session()
        result = await handle_price_interrupt("Скільки коштує монтаж R21?", session, router)

        router.get_fitting_price.assert_awaited_once_with(tire_diameter=21, station_id="ST-1")
        assert session.fitting_diameter_client == 21
        assert result.session_updates["fitting_diameter_client"] == 21
        assert_contract(result)

    async def test_falls_back_to_router_execute_for_the_real_tool_router(
        self, session: CallSession
    ) -> None:
        """The production `ToolRouter` only exposes `execute(name, args)`."""

        class ExecuteOnlyRouter:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, Any]]] = []

            async def execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
                self.calls.append((name, args))
                return {"prices": [{"service": "Комплекс R16", "price": "400", "category": "car"}]}

        session.fitting_diameter_client = 16
        execute_router = ExecuteOnlyRouter()
        result = await handle_price_interrupt(PRICE_QUESTION, session, execute_router)

        assert execute_router.calls == [
            ("get_fitting_price", {"tire_diameter": 16, "station_id": "ST-1"})
        ]
        assert "400" in result.reply_to_customer
        assert_contract(result)

    async def test_empty_price_list_is_answered_honestly(
        self, session: CallSession, router: AsyncMock
    ) -> None:
        session.fitting_diameter_client = 19
        router.get_fitting_price.return_value = {"prices": []}
        result = await handle_price_interrupt(PRICE_QUESTION, session, router)

        assert result.handled is True
        assert result.advanced is True
        assert "R19" in result.reply_to_customer
        assert "грн" not in result.reply_to_customer
        assert session.pending_price_interrupt_needs_diameter is False
        assert_contract(result)


# --- 3. PRICE multi-turn diameter -------------------------------------------


class TestPriceInterruptNeedsDiameter:
    async def test_asks_for_diameter_and_arms_the_session_flag(
        self, session: CallSession, router: AsyncMock
    ) -> None:
        result = await handle_price_interrupt(PRICE_QUESTION, session, router)

        assert result.handled is True
        assert result.advanced is True
        assert result.reply_to_customer == STATES[FsmState.PRICE_INTERRUPT].question_template
        assert session.pending_price_interrupt_needs_diameter is True
        assert result.session_updates["pending_price_interrupt_needs_diameter"] is True
        router.get_fitting_price.assert_not_awaited()
        assert_contract(result)

    async def test_next_turn_with_a_number_produces_the_quote(
        self, session: CallSession, router: AsyncMock
    ) -> None:
        await handle_price_interrupt(PRICE_QUESTION, session, router)
        result = await handle_price_interrupt("сімнадцять", session, router)

        router.get_fitting_price.assert_awaited_once_with(tire_diameter=17, station_id="ST-1")
        assert result.handled is True
        assert "450" in result.reply_to_customer
        assert session.pending_price_interrupt_needs_diameter is False
        assert_contract(result)

    async def test_reask_uses_a_different_prompt_than_the_first_ask(
        self, session: CallSession, router: AsyncMock
    ) -> None:
        """A repeated question must not replay the identical sentence."""
        first = await handle_price_interrupt(PRICE_QUESTION, session, router)
        second = await handle_price_interrupt("а яка ціна взагалі?", session, router)

        config = STATES[FsmState.PRICE_INTERRUPT]
        assert first.reply_to_customer == config.question_template
        assert second.reply_to_customer == config.silence_reprompt
        assert first.reply_to_customer != second.reply_to_customer
        assert_contract(second)


# --- 4. PRICE resume phrasing -----------------------------------------------


class TestPriceInterruptResumeMessage:
    @pytest.mark.parametrize("state", [FsmState.CITY, FsmState.DATE, FsmState.CONFIRM])
    async def test_resume_phrase_comes_from_per_state_config(
        self, state: FsmState, router: AsyncMock
    ) -> None:
        session = make_session(fsm_state=state.value, fitting_diameter_client=17)
        result = await handle_price_interrupt(PRICE_QUESTION, session, router)

        assert result.resume_state == state.value
        assert result.reply_to_customer.endswith(STATES[state].resume_phrase)
        assert REVERTED_RESUME_LITERAL not in result.reply_to_customer
        assert_contract(result)

    async def test_non_resumable_state_gets_no_phrase(self, router: AsyncMock) -> None:
        """DONE is terminal — there is no booking step to come back to."""
        session = make_session(fsm_state=FsmState.DONE.value, fitting_diameter_client=17)
        result = await handle_price_interrupt(PRICE_QUESTION, session, router)

        assert result.resume_state is None
        assert STATES[FsmState.DONE].resume_phrase not in result.reply_to_customer
        assert_contract(result)

    async def test_unknown_persisted_state_is_treated_as_no_booking(
        self, router: AsyncMock
    ) -> None:
        session = make_session(fsm_state="SOME_RENAMED_STATE", fitting_diameter_client=17)
        result = await handle_price_interrupt(PRICE_QUESTION, session, router)

        assert result.resume_state is None
        assert result.reply_to_customer.endswith(".")
        assert_contract(result)


# --- 5. Loop-breaker --------------------------------------------------------


class TestInterruptCap:
    async def test_price_cap_blocks_the_third_entry(
        self, session: CallSession, router: AsyncMock
    ) -> None:
        session.fitting_diameter_client = 16
        for _ in range(MAX_INTERRUPT_FIRES):
            assert (await handle_price_interrupt(PRICE_QUESTION, session, router)).handled

        capped = await handle_price_interrupt(PRICE_QUESTION, session, router)
        assert capped.handled is False
        assert router.get_fitting_price.await_count == MAX_INTERRUPT_FIRES
        assert_contract(capped)

    async def test_cancel_cap_blocks_the_third_entry(
        self, session: CallSession, router: AsyncMock
    ) -> None:
        router.get_customer_bookings.return_value = {"total": 0, "bookings": []}
        for _ in range(MAX_INTERRUPT_FIRES):
            assert (await handle_cancel_interrupt(CANCEL_REQUEST, session, router)).handled

        capped = await handle_cancel_interrupt(CANCEL_REQUEST, session, router)
        assert capped.handled is False
        assert session.interrupt_counts[CANCEL_HANDLER] == MAX_INTERRUPT_FIRES
        assert router.get_customer_bookings.await_count == MAX_INTERRUPT_FIRES
        assert router.cancel_fitting.await_count == 0
        assert_contract(capped)

    async def test_counter_survives_the_redis_round_trip(
        self, session: CallSession, router: AsyncMock
    ) -> None:
        """The whole point: the cap is useless if the session reload drops it.

        The Call Processor is stateless — every turn reloads the session from
        Redis. The reverted version kept the counter only on the live object,
        so the cap reset each turn and the handler looped.
        """
        session.fitting_diameter_client = 16
        for _ in range(MAX_INTERRUPT_FIRES):
            await handle_price_interrupt(PRICE_QUESTION, session, router)
        session.pending_cancel_action = f"{CANCEL_AWAITING_CONFIRMATION}:{BOOKING_A}"

        restored = CallSession.from_dict(session.to_dict())
        assert restored.interrupt_counts == session.interrupt_counts
        assert restored.interrupt_counts[PRICE_HANDLER] == MAX_INTERRUPT_FIRES
        assert restored.pending_cancel_action == session.pending_cancel_action
        assert restored.pending_price_interrupt_needs_diameter is False

        # The armed boolean survives too.
        session.pending_price_interrupt_needs_diameter = True
        assert CallSession.from_dict(session.to_dict()).pending_price_interrupt_needs_diameter

        # And the cap still holds on the restored session.
        after_reload = await handle_price_interrupt(PRICE_QUESTION, restored, router)
        assert after_reload.handled is False
        assert_contract(after_reload)


# --- 6-9. CANCEL ------------------------------------------------------------


class TestCancelInterruptZeroBookings:
    async def test_says_there_are_no_bookings(
        self, session: CallSession, router: AsyncMock
    ) -> None:
        router.get_customer_bookings.return_value = {"total": 0, "bookings": []}
        result = await handle_cancel_interrupt(CANCEL_REQUEST, session, router)

        assert result.handled is True
        assert result.advanced is True
        assert "немає активних записів" in result.reply_to_customer
        assert result.resume_state is None
        assert_contract(result)

    async def test_never_calls_cancel_fitting(
        self, session: CallSession, router: AsyncMock
    ) -> None:
        router.get_customer_bookings.return_value = {"total": 0, "bookings": []}
        await handle_cancel_interrupt(CANCEL_REQUEST, session, router)

        router.cancel_fitting.assert_not_awaited()
        assert session.pending_cancel_action is None


class TestCancelInterruptSingleBooking:
    @pytest.fixture(autouse=True)
    def _one_booking(self, router: AsyncMock) -> None:
        router.get_customer_bookings.return_value = {
            "total": 1,
            "bookings": [make_booking(BOOKING_A)],
        }

    async def test_confirm_yes_cancels(self, session: CallSession, router: AsyncMock) -> None:
        asked = await handle_cancel_interrupt(CANCEL_REQUEST, session, router)
        assert asked.session_updates["pending_cancel_action"] == (
            f"{CANCEL_AWAITING_CONFIRMATION}:{BOOKING_A}"
        )

        done = await handle_cancel_interrupt("так, скасовуйте", session, router)
        router.cancel_fitting.assert_awaited_once_with(booking_id=BOOKING_A)
        assert done.handled is True
        assert done.advanced is True
        assert "скасовано" in done.reply_to_customer
        assert session.pending_cancel_action is None
        assert session.fitting_booked is False
        assert_contract(done)

    async def test_confirm_no_keeps_the_booking_and_resumes(
        self, session: CallSession, router: AsyncMock
    ) -> None:
        await handle_cancel_interrupt(CANCEL_REQUEST, session, router)
        kept = await handle_cancel_interrupt("ні, залиште", session, router)

        router.cancel_fitting.assert_not_awaited()
        assert kept.handled is True
        assert kept.resume_state == FsmState.CITY.value
        assert kept.reply_to_customer.endswith(STATES[FsmState.CITY].resume_phrase)
        assert session.pending_cancel_action is None
        assert_contract(kept)

    async def test_unclear_answer_reasks_without_cancelling(
        self, session: CallSession, router: AsyncMock
    ) -> None:
        await handle_cancel_interrupt(CANCEL_REQUEST, session, router)
        unclear = await handle_cancel_interrupt("що?", session, router)

        router.cancel_fitting.assert_not_awaited()
        assert unclear.handled is True
        assert "«так»" in unclear.reply_to_customer
        assert session.pending_cancel_action == f"{CANCEL_AWAITING_CONFIRMATION}:{BOOKING_A}"
        assert_contract(unclear)


class TestCancelInterruptMultipleBookings:
    @pytest.fixture(autouse=True)
    def _two_bookings(self, router: AsyncMock) -> None:
        router.get_customer_bookings.return_value = {
            "total": 2,
            "bookings": [
                make_booking(BOOKING_A, date="10.09.2026", time="10:00"),
                make_booking(BOOKING_B, date="15.09.2026", time="12:00"),
            ],
        }

    async def test_selection_by_ordinal(self, session: CallSession, router: AsyncMock) -> None:
        listed = await handle_cancel_interrupt(CANCEL_REQUEST, session, router)
        assert listed.session_updates["pending_cancel_action"] == CANCEL_AWAITING_SELECTION
        assert "1)" in listed.reply_to_customer and "2)" in listed.reply_to_customer

        picked = await handle_cancel_interrupt("другий", session, router)
        assert picked.handled is True
        assert session.pending_cancel_action == f"{CANCEL_AWAITING_CONFIRMATION}:{BOOKING_B}"
        assert_contract(picked)

    async def test_selection_by_date(self, session: CallSession, router: AsyncMock) -> None:
        await handle_cancel_interrupt(CANCEL_REQUEST, session, router)
        picked = await handle_cancel_interrupt("той, що на 15", session, router)

        assert picked.handled is True
        assert session.pending_cancel_action == f"{CANCEL_AWAITING_CONFIRMATION}:{BOOKING_B}"
        assert_contract(picked)

    async def test_ambiguous_selection_reasks_and_cancels_nothing(
        self, session: CallSession, router: AsyncMock
    ) -> None:
        await handle_cancel_interrupt(CANCEL_REQUEST, session, router)
        unclear = await handle_cancel_interrupt("не пам'ятаю", session, router)

        router.cancel_fitting.assert_not_awaited()
        assert unclear.handled is True
        assert session.pending_cancel_action == CANCEL_AWAITING_SELECTION
        assert_contract(unclear)


class TestCancelInterruptGuardInvalidBookingId:
    async def test_placeholder_booking_id_is_refused(
        self, session: CallSession, router: AsyncMock
    ) -> None:
        router.get_customer_bookings.return_value = {
            "total": 1,
            "bookings": [make_booking("000000000")],
        }
        result = await handle_cancel_interrupt(CANCEL_REQUEST, session, router)

        assert result.handled is False
        router.cancel_fitting.assert_not_awaited()
        assert session.pending_cancel_action is None
        assert_contract(result)

    async def test_pending_id_not_returned_by_get_customer_bookings_is_refused(
        self, session: CallSession, router: AsyncMock
    ) -> None:
        """The id must come from a live `get_customer_bookings` result."""
        session.pending_cancel_action = f"{CANCEL_AWAITING_CONFIRMATION}:{BOOKING_C}"
        router.get_customer_bookings.return_value = {
            "total": 1,
            "bookings": [make_booking(BOOKING_A)],
        }
        result = await handle_cancel_interrupt("так", session, router)

        assert result.handled is False
        router.cancel_fitting.assert_not_awaited()
        assert session.pending_cancel_action is None
        assert_contract(result)


# --- 10. Return format ------------------------------------------------------


class TestReturnFormat:
    async def test_cancel_without_caller_phone_is_not_handled(self, router: AsyncMock) -> None:
        session = make_session(caller_phone=None)
        result = await handle_cancel_interrupt(CANCEL_REQUEST, session, router)

        assert result.handled is False
        assert result.reply_to_customer == ""
        assert result.resume_state is None
        assert result.session_updates == {}
        router.get_customer_bookings.assert_not_awaited()
        assert_contract(result)

    async def test_cancel_without_positive_evidence_is_not_handled(
        self, session: CallSession, router: AsyncMock
    ) -> None:
        """Default-deny: «не треба скасовувати» is not a cancel request."""
        for text in ("добре", "не треба скасовувати", "запишіть мене на завтра"):
            result = await handle_cancel_interrupt(text, session, router)
            assert result.handled is False, text
            assert_contract(result)
        router.get_customer_bookings.assert_not_awaited()

    async def test_price_without_positive_evidence_is_not_handled(
        self, session: CallSession, router: AsyncMock
    ) -> None:
        for text in ("", "мене не цікавить ціна", "хочу записатись на четвер"):
            result = await handle_price_interrupt(text, session, router)
            assert result.handled is False, text
            assert result.session_updates == {}
            assert_contract(result)
        router.get_fitting_price.assert_not_awaited()

    async def test_tool_failure_falls_through_instead_of_speaking(
        self, session: CallSession, router: AsyncMock
    ) -> None:
        router.get_customer_bookings.side_effect = RuntimeError("1C timeout")
        result = await handle_cancel_interrupt(CANCEL_REQUEST, session, router)

        assert result.handled is False
        assert result.reply_to_customer == ""
        assert result.advanced is False
        assert session.pending_cancel_action is None
        assert_contract(result)
