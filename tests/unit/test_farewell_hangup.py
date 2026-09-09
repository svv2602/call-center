"""Wave 18 (2026-09-09) — the bot must put the phone down after it says goodbye.

Testers on 2026-09-09 had to hang up by hand on every call: the bot said
«Дякую за звернення! Всього найкращого!» and then listened for the full
3×SILENCE_TIMEOUT_SEC ladder. Calls `280dae63` and `e0556668` both ended
customer-side.

The closing lines and the counter-example («А ще питання» on `239d9e33`) are
taken from 14 days of prod `call_turns`.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import patch

import pytest

from src.agent.prompts import WAIT_BOOKING_LOOKUP_POOL
from src.agent.streaming_loop import _TOOL_WAIT_POOLS, TurnResult
from src.core.call_session import SILENCE_TIMEOUT_SEC, CallSession
from src.core.pipeline import _FAREWELL_HANGUP_GRACE_SEC, _is_farewell
from src.llm.models import Usage
from src.stt.base import Transcript
from tests.unit.test_pipeline_fsm_wire import Harness

# Real closing lines — every one of these was the LAST bot turn in prod.
CLOSINGS = [
    "Дякую за звернення! Всього найкращого!",
    "Була рада допомогти! До побачення, гарного вам дня!",
    "До побачення! Гарного дня!",
    "Дякую, що звернулися до нас! Гарного дня, до побачення!",
    "Ви вже записані. Дякую за звернення! Всього найкращого!",
    "Нет спасибо На все добре",
]

# Mid-call lines that must NOT be read as a goodbye.
NOT_CLOSINGS = [
    "Шини привозите свої з собою чи ті, що у нас на зберіганні?",
    "Я на зв'язку. Якщо маєте запитання — я слухаю.",
    # «дякую за звернення» alone is deliberately not a marker — the bot says
    # it mid-call too.
    "Дякую за звернення! Якщо знадобиться допомога з записом — звертайтесь.",
    "Готово, записала на десяте вересня о девʼятій ранку.",
    "",
]


class TestFarewellDetection:
    @pytest.mark.parametrize("text", CLOSINGS)
    def test_real_closing_lines_are_detected(self, text: str) -> None:
        assert _is_farewell(text) is True

    @pytest.mark.parametrize("text", NOT_CLOSINGS)
    def test_mid_call_lines_are_not_farewells(self, text: str) -> None:
        assert _is_farewell(text) is False


def _reply_with(h: Harness, *replies: str) -> list[str]:
    """Make the LLM answer with `replies` in order, repeating the last one."""
    seen: list[str] = []

    async def _run_turn(**kw: Any) -> TurnResult:
        seen.append(kw.get("user_text", ""))
        text = replies[min(len(seen) - 1, len(replies) - 1)]
        return TurnResult(
            spoken_text=text,
            tool_calls_made=0,
            stop_reason="end_turn",
            total_usage=Usage(10, 5),
        )

    h.streaming_loop.run_turn = _run_turn
    return seen


async def _drive(h: Harness, script: list[str | None], max_silences: int = 4) -> int:
    """Feed `script` to the loop; None means silence.

    The connection is deliberately left OPEN so the only way the loop can end
    is the farewell break. Returns how many silences were consumed; a runaway
    loop is capped so a broken guard fails loudly instead of hanging.
    """
    queue = list(script)
    silences = 0

    async def _wait() -> Transcript | None:
        nonlocal silences
        if queue:
            item = queue.pop(0)
            if item is not None:
                return Transcript(text=item, is_final=True, confidence=0.95, language="uk-UA")
        silences += 1
        if silences > max_silences:
            h.conn.is_closed = True
        return None

    h.pipeline._wait_for_final_transcript = _wait  # type: ignore[method-assign]
    await h.pipeline._transcript_processor_loop()
    return silences


class TestHangupAfterGoodbye:
    @pytest.mark.asyncio
    async def test_silence_after_farewell_ends_the_call(self) -> None:
        h = Harness(session=CallSession(uuid.uuid4()))
        _reply_with(h, "Дякую за звернення! Всього найкращого!")

        silences = await _drive(h, ["все, дякую", None])

        # Exactly one silence — the grace window — and then the loop stopped.
        assert silences == 1
        assert h.pipeline._farewell_spoken is True

    @pytest.mark.asyncio
    async def test_the_bot_does_not_say_goodbye_twice(self) -> None:
        """The silence ladder would append a second, generated farewell."""
        h = Harness(session=CallSession(uuid.uuid4()))
        _reply_with(h, "До побачення! Гарного дня!")

        await _drive(h, ["все", None])

        assert h.assistant_texts == ["До побачення! Гарного дня!"]
        assert h.spoken == []

    @pytest.mark.asyncio
    async def test_a_caller_who_speaks_up_is_not_cut_off(self) -> None:
        """`239d9e33` said «А ще питання» right after the closing line."""
        h = Harness(session=CallSession(uuid.uuid4()))
        seen = _reply_with(
            h,
            "Готово, записала. Дякую за звернення! Всього найкращого!",
            "Слухаю вас.",
        )

        await _drive(h, ["записуйте", "А ще питання", None])

        assert seen == ["записуйте", "А ще питання"]
        assert h.pipeline._farewell_spoken is False

    @pytest.mark.asyncio
    async def test_a_caller_who_barges_in_over_the_goodbye_is_not_cut_off(self) -> None:
        """Barge-in adds no bot turn, so the goodbye stays last in the history."""
        h = Harness(session=CallSession(uuid.uuid4()))
        replies = iter(["До побачення! Гарного дня!"])

        async def _run_turn(**kw: Any) -> TurnResult:
            text = next(replies, "")
            return TurnResult(
                spoken_text=text,
                tool_calls_made=0,
                stop_reason="end_turn",
                total_usage=Usage(10, 5),
                interrupted=not text,
            )

        h.streaming_loop.run_turn = _run_turn

        silences = await _drive(h, ["все, дякую", "А ще питання", None])

        # The second turn was interrupted — the bot said nothing — so the stale
        # goodbye must not re-arm the hangup.
        assert h.pipeline._farewell_spoken is False
        assert silences > 1

    @pytest.mark.asyncio
    async def test_an_ordinary_turn_keeps_the_full_silence_ladder(self) -> None:
        h = Harness(session=CallSession(uuid.uuid4()))
        _reply_with(h, "На яку дату записуємо?")

        silences = await _drive(h, ["хочу на монтаж", None])

        # The guard must not fire — silence goes through record_timeout().
        assert silences > 1
        assert h.pipeline._farewell_spoken is False


class TestGraceWindowIsActuallyUsed:
    """The flag is only useful if it shortens the wait."""

    @staticmethod
    async def _captured_timeout(*, farewell_spoken: bool) -> float:
        h = Harness(session=CallSession(uuid.uuid4()))
        h.pipeline._farewell_spoken = farewell_spoken
        seen: list[float] = []

        async def _fake_wait_for(coro: Any, timeout: float) -> None:
            seen.append(timeout)
            coro.close()
            raise TimeoutError

        with patch.object(asyncio, "wait_for", _fake_wait_for):
            assert await h.pipeline._wait_for_final_transcript() is None
        return seen[0]

    @pytest.mark.asyncio
    async def test_after_goodbye_the_wait_is_the_grace_window(self) -> None:
        assert await self._captured_timeout(farewell_spoken=True) == pytest.approx(
            _FAREWELL_HANGUP_GRACE_SEC
        )

    @pytest.mark.asyncio
    async def test_otherwise_the_wait_is_unchanged(self) -> None:
        assert await self._captured_timeout(farewell_spoken=False) == pytest.approx(
            SILENCE_TIMEOUT_SEC
        )

    def test_the_grace_window_is_shorter_than_the_ladder(self) -> None:
        assert _FAREWELL_HANGUP_GRACE_SEC < SILENCE_TIMEOUT_SEC


class TestBookingLookupWaitPhrase:
    """`e0556668` asked to cancel and heard «Зараз перевірю розклад»."""

    def test_lookup_has_its_own_pool(self) -> None:
        assert _TOOL_WAIT_POOLS["get_customer_bookings"] is WAIT_BOOKING_LOOKUP_POOL

    @pytest.mark.parametrize("phrase", WAIT_BOOKING_LOOKUP_POOL)
    def test_no_phrase_talks_about_free_slots(self, phrase: str) -> None:
        lowered = phrase.lower()
        assert "розклад" not in lowered
        assert "вільн" not in lowered
        assert "запис" in lowered
