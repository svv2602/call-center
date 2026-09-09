"""Wave 17 (2026-09-09) — a confirmation over an incomplete checklist must correct the bot.

Calls `c1988daf` and `57494646` both logged «Krok 8 «так» arrived but fields
still ⏳ … Suppressing emergency banner» and then went on to tell the customer
«ви записані». Refusing to force a doomed `book_fitting` was right; saying
nothing at all was not — the LLM had no signal that its summary was invented.

`c1988daf` is the shape pinned below: the bot announced «19 вересня о 9:00»
while the customer had never named an hour, the customer said «да», and the
bot closed the call with a booking that does not exist.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from src.agent.prompts import _render_fitting_progress
from src.agent.streaming_loop import TurnResult
from src.core.call_session import CallSession
from src.llm.models import Usage
from tests.unit.test_pipeline_fsm_wire import LLM_REPLY, Harness

STATION = {"id": "st-1", "city": "Дніпро", "address": "Донецьке шосе, 69"}
CONFIRM_QUESTION = "Превіримо: 19 вересня о 9:00, м. Дніпро, білий Toyota. Підтверджуєте?"


def _session(*, with_time: bool) -> CallSession:
    """A booking one field away from complete — the hour is what `c1988daf` lacked."""
    s = CallSession(uuid.uuid4())
    s.fitting_customer_name = "Вікторія"
    s.fitting_stations_seen = [STATION]
    s.fitting_storage_choice = "own"
    s.selected_fitting_date = "2026-09-19"
    s.selected_fitting_time = "09:00" if with_time else None
    s.fitting_plate = "білий"
    s.fitting_vehicle_brand = "Toyota"
    s.caller_phone = "+380671112233"
    s.add_assistant_turn(CONFIRM_QUESTION)
    return s


def _record(h: Harness) -> list[dict[str, Any]]:
    """Capture every kwarg handed to the LLM, not just the user text."""
    calls: list[dict[str, Any]] = []

    async def _run_turn(**kw: Any) -> TurnResult:
        calls.append(kw)
        return TurnResult(
            spoken_text=LLM_REPLY,
            tool_calls_made=0,
            stop_reason="end_turn",
            total_usage=Usage(10, 5),
        )

    h.streaming_loop.run_turn = _run_turn
    return calls


class TestIncompleteChecklistRaisesTheBanner:
    @pytest.mark.asyncio
    async def test_confirmation_over_missing_time_flags_confabulation(self) -> None:
        h = Harness(session=_session(with_time=False))
        calls = _record(h)

        await h.run("так")

        progress = calls[0]["fitting_progress"]
        assert progress["krok8_confabulation_pending"] is True
        # The doomed book_fitting is still not forced.
        assert progress["krok8_confirmed"] is False

    @pytest.mark.asyncio
    async def test_the_flag_is_consumed_after_the_turn(self) -> None:
        """One-shot: the banner must not repeat on a later, unrelated turn."""
        h = Harness(session=_session(with_time=False))
        _record(h)

        await h.run("так")

        assert h.session.krok8_confabulation_pending is False


class TestCompleteChecklistIsUntouched:
    @pytest.mark.asyncio
    async def test_all_fields_ready_still_forces_the_booking(self) -> None:
        h = Harness(session=_session(with_time=True))
        calls = _record(h)

        await h.run("так")

        progress = calls[0]["fitting_progress"]
        assert progress["krok8_confirmed"] is True
        assert progress["krok8_confabulation_pending"] is False


class TestWhatTheLLMActuallyReads:
    """The dict is only useful if it renders into an instruction."""

    def test_banner_tells_the_bot_its_summary_was_invented(self) -> None:
        block = _render_fitting_progress(
            {
                "booked": False,
                "krok8_confabulation_pending": True,
                "krok8_confirmed": False,
                "customer_name": "Вікторія",
                "city": STATION["city"],
                "station_address": STATION["address"],
                "storage_choice": "own",
                "date": "2026-09-19",
                "time": None,
                "plate": "білий",
                "brand": "Toyota",
                "caller_phone": "+380671112233",
            }
        )
        assert "ГАЛЮЦИНАЦІЄЮ" in block
        assert "НЕ ВИКОРИСТОВУЙ" in block
        # …and it must not simultaneously order the booking it just refused.
        assert "EMERGENCY BOOK-FITTING NOW" not in block

    def test_a_booked_session_still_short_circuits_to_the_done_block(self) -> None:
        """`booked` outranks the banner — a real booking is not a confabulation."""
        block = _render_fitting_progress({"booked": True, "krok8_confabulation_pending": True})
        assert "ЗАПИС СТВОРЕНО" in block
        assert "ГАЛЮЦИНАЦІЄЮ" not in block
