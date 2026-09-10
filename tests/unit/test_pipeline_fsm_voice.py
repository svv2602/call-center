"""Wave 7-0 — the FSM asks main-flow questions in its own words.

Until this seam existed, `STATES[...].question_template` was assigned to
`_fsm_shadow_reply` and never read again; the pipeline comment called it «a
reply nobody speaks». Every main-flow question came from the prompt, which is
why Wave 7-A could not simply delete `_MOD_FITTING` — deleting it would have
left the booking flow with no source of questions at all.

The invariant these tests exist for is the *suppression*: on a turn the FSM
speaks, the LLM turn must not also run. Both sides asking is exactly how the
reverted build (`c8c6601`) said «В якому місті?» twice in a row.

The production opt-in set is now **empty** — suppression turned out to eat the
turn on which the LLM acts on the previous answer, and the three shipped states
produced six re-asks and zero bookings on 2026-09-10 (see the comment on
`FSM_VOICE_STATES`). The machinery is kept, and so are these tests, because the
refusal branches are the part a redesign has to preserve. Every test that needs
a voiced state patches `FSM_VOICE_STATES` itself via `voiced(...)`, so the tests
say what they exercise instead of inheriting it from a constant that is now
empty.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from src.agent.fitting_fsm import STATES, FsmState
from src.core.call_session import CallSession
from src.core.pipeline import FSM_VOICE_STATES

from .test_pipeline_fsm_wire import LLM_REPLY, Harness, fsm_flags, intent

pytestmark = pytest.mark.asyncio

STORAGE_Q = STATES[FsmState.STORAGE].question_template
COLOR_Q = STATES[FsmState.COLOR].question_template
BRAND_Q = STATES[FsmState.BRAND].question_template

# Nothing the storage/colour parsers can read, so the FSM stays where it is and
# the voice path is reached with the state's own field still empty.
UNPARSEABLE = "ммм не знаю навіть"


def session_in(state: FsmState, **filled: object) -> CallSession:
    """A session parked in `state` with `filled` already collected."""
    session = CallSession(uuid.uuid4())
    session.fsm_state = state.value
    session.fsm_filled_fields = {"intent": "fitting", **filled}
    return session


def live_classifier():
    """Live mode with an intent that is main-flow, so no interrupt handler runs."""
    return patch(
        "src.agent.intent_classifier.classify_intent",
        AsyncMock(return_value=intent("BOOK")),
    )


def voiced(*states: FsmState):
    """Opt `states` into the voice path for the duration of the test."""
    return patch(
        "src.core.pipeline.FSM_VOICE_STATES",
        frozenset(state.value for state in states),
    )


class TestTheFsmTakesTheTurn:
    @pytest.mark.parametrize(
        ("state", "question"),
        [
            (FsmState.STORAGE, STORAGE_Q),
            (FsmState.COLOR, COLOR_Q),
            (FsmState.BRAND, BRAND_Q),
        ],
    )
    async def test_opted_in_state_is_asked_by_the_fsm(
        self, state: FsmState, question: str
    ) -> None:
        h = Harness(session_in(state, city="Київ", station_id="000000019"))
        with fsm_flags(enabled=True, shadow_mode=False), live_classifier(), voiced(state):
            await h.run(UNPARSEABLE)

        assert question in h.spoken
        assert h.llm_turns == [], "the LLM turn must be suppressed, not duplicated"
        assert LLM_REPLY not in h.assistant_texts

    async def test_the_customer_turn_is_still_recorded(self) -> None:
        """The voice path skips the streaming branch that normally logs it.

        A missing user turn would corrupt both the transcript and the next
        prompt, and would do it silently.
        """
        h = Harness(session_in(FsmState.STORAGE, city="Київ"))
        with (
            fsm_flags(enabled=True, shadow_mode=False),
            live_classifier(),
            voiced(FsmState.STORAGE),
        ):
            await h.run(UNPARSEABLE)

        assert UNPARSEABLE in h.customer_texts

    async def test_no_state_is_voiced_in_production(self) -> None:
        """The rollback of 2026-09-10, pinned so a refill has to be deliberate.

        Suppressing the LLM turn also suppresses the tool calls that turn was
        going to make about the *previous* answer — on call 3639c0b4 that lost
        the second `get_fitting_stations(query=...)` and the station was
        blind-picked. Six spoken events over two calls, six re-asks, zero
        bookings. Anyone adding a state back has to change this test, and the
        reason to change it is a design that lets the FSM own the tool calls.
        """
        assert sorted(FSM_VOICE_STATES) == []


class TestTheFsmDeclinesTheTurn:
    async def test_a_state_outside_the_opt_in_set_stays_with_the_llm(self) -> None:
        h = Harness(session_in(FsmState.DATE, city="Київ", station_id="000000019"))
        with (
            fsm_flags(enabled=True, shadow_mode=False),
            live_classifier(),
            voiced(FsmState.STORAGE),
        ):
            await h.run(UNPARSEABLE)

        assert h.llm_turns == [UNPARSEABLE]
        assert STATES[FsmState.DATE].question_template not in h.spoken

    async def test_a_filled_field_is_not_asked_about_again(self) -> None:
        """The backstop for a deterministic step that did not advance.

        Normally `_run_fsm_deterministic_step` walks STORAGE→DATE the moment
        `storage_choice` is filled, so the voice path never sees a filled field
        — which means that with the step running, this test passes even with the
        guard deleted, and asserts nothing. The reachable path is the step
        failing: it catches every exception, logs, and returns, leaving the
        session parked in a voiced state with its field already answered. Stub
        it out to stand in for that.
        """
        session = session_in(FsmState.STORAGE, city="Київ", storage_choice="свої з собою")
        h = Harness(session)
        h.pipeline._run_fsm_deterministic_step = lambda transcript: None  # type: ignore[method-assign]
        with (
            fsm_flags(enabled=True, shadow_mode=False),
            live_classifier(),
            voiced(FsmState.STORAGE),
        ):
            await h.run(UNPARSEABLE)

        assert session.fsm_state == FsmState.STORAGE.value, "the step must stay stubbed"
        assert STORAGE_Q not in h.spoken
        assert h.llm_turns == [UNPARSEABLE]

    async def test_an_unresolved_placeholder_is_never_spoken(self) -> None:
        """STATION's template needs `stations_count`/`districts`.

        `render` leaves them literal, so speaking here would put «[districts]»
        in the caller's ear. Observed live on call cff36659, where CITY
        auto-skipped past `entry_tool=get_fitting_stations` and left
        `fitting_stations_seen` empty.
        """
        h = Harness(session_in(FsmState.STATION, city="Київ"))
        with (
            fsm_flags(enabled=True, shadow_mode=False),
            live_classifier(),
            voiced(FsmState.STATION),
        ):
            await h.run(UNPARSEABLE)

        assert not any("[" in text for text in h.spoken), h.spoken
        assert h.llm_turns == [UNPARSEABLE]

    async def test_the_same_question_is_never_asked_twice_in_a_row(self) -> None:
        """Re-asking verbatim is the symptom the first attempt was reverted for.

        On a parser_null the FSM's own answer is to re-ask; falling through lets
        the LLM rephrase instead of repeating the sentence word for word.
        """
        session = session_in(FsmState.STORAGE, city="Київ")
        session.add_assistant_turn(STORAGE_Q)
        h = Harness(session)
        with (
            fsm_flags(enabled=True, shadow_mode=False),
            live_classifier(),
            voiced(FsmState.STORAGE),
        ):
            await h.run(UNPARSEABLE)

        assert h.spoken.count(STORAGE_Q) == 0
        assert h.llm_turns == [UNPARSEABLE]

    async def test_shadow_mode_cannot_reach_the_customer(self) -> None:
        h = Harness(session_in(FsmState.STORAGE, city="Київ"))
        with (
            fsm_flags(enabled=True, shadow_mode=True),
            live_classifier(),
            voiced(FsmState.STORAGE),
        ):
            await h.run(UNPARSEABLE)

        assert STORAGE_Q not in h.spoken
        assert h.llm_turns == [UNPARSEABLE]


class TestInterruptsOutrankTheVoice:
    async def test_a_price_interrupt_wins_over_the_state_question(self) -> None:
        """`_maybe_handle_intent` runs first, and its reply is the whole turn."""
        from .test_pipeline_fsm_wire import interrupt

        reply = "Монтаж R17 коштує 500 гривень."
        h = Harness(session_in(FsmState.STORAGE, city="Київ"))
        with (
            fsm_flags(enabled=True, shadow_mode=False),
            voiced(FsmState.STORAGE),
            patch(
                "src.agent.intent_classifier.classify_intent",
                AsyncMock(return_value=intent("PRICE")),
            ),
            patch(
                "src.agent.interrupts.handle_price_interrupt",
                AsyncMock(return_value=interrupt(reply=reply)),
            ),
        ):
            await h.run("скільки коштує монтаж")

        assert reply in h.spoken
        assert STORAGE_Q not in h.spoken
        assert h.llm_turns == []
