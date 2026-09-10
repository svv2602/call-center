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

from src.agent.fitting_fsm import COLOR_NOT_HEARD, STATES, FsmState
from src.agent.prompts import _render_fitting_progress
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


class TestTheLlmSeesWhatTheFsmCollected:
    """The precondition Wave 7-0 shipped without, and the reason it was reverted.

    The FSM writes `session.fsm_filled_fields`. The LLM's picture of the call
    comes from the `## 📋 Прогрес запису` block, which `_render_fitting_progress`
    builds from `_build_fitting_progress` — and *that* reads the flat
    `session.fitting_*` fields, which only tool handlers and the post-turn
    extraction write. Two checklists, and the one whose entire documented job is
    «so the LLM does not re-ask completed steps» cannot see the other.

    So every field the FSM takes silently is a hole in the LLM's picture, and the
    LLM fills a hole by asking again. That is the 1:1 behind «six spoken events,
    six re-asks» — not a coincidence, an identity.
    """

    async def test_a_colour_the_fsm_took_is_not_re_asked(self) -> None:
        """Call `3e8f3589` 2026-09-10, turns 10:47→11:30.

        «срібний» at 10:47 was consumed by the FSM, which spoke the brand
        question in the same second. At 11:30 the bot asked «Назвіть, будь
        ласка, колір автомобіля.» — the colour again. Same call, same shape at
        10:39→10:58 for the time, where the re-ask pushed the caller onto a slot
        that was never offered.

        The voiced state is BRAND, not COLOR: `_maybe_speak_fsm_question` runs
        *after* the deterministic step, so the state it checks is the one the
        answer moved us to. That is also why the suppressed turn is the one that
        owed work on the *previous* answer.

        Asserted on the kwargs rather than the session on purpose — the session
        does hold the colour. Being in the session is not the same as being
        visible, and the whole defect lives in that gap. `plate` is the progress
        key for colour: it kept the name when Krok 5 stopped collecting plates
        on 2026-08-18.
        """
        h = Harness(session_in(FsmState.COLOR, city="Київ", station_id="000000019"))
        with (
            fsm_flags(enabled=True, shadow_mode=False),
            live_classifier(),
            voiced(FsmState.BRAND),
        ):
            await h.run("срібний", "Mitsubishi")

        assert h.session.fsm_filled_fields.get("color") == "срібний"
        assert h.llm_turns == ["Mitsubishi"], (
            f"turn 1 belongs to the FSM and turn 2 to the LLM — got {h.llm_turns!r}"
        )
        assert h.llm_kwargs, "the second turn must reach the LLM"
        progress = h.llm_kwargs[-1].get("fitting_progress") or {}
        assert progress.get("plate"), (
            "the LLM cannot see the colour the FSM collected, so it re-asks it — "
            f"progress={progress!r}"
        )

    async def test_every_main_flow_field_is_mapped_or_deliberately_not(self) -> None:
        """A new state must not be able to go unmapped in silence.

        `intent` and `confirmed` are control, not checklist — there is no row
        for them in the block. `station_id` is resolved to a whole station dict
        by `_resolve_selected_station`, so it is not copied across. Anything
        else in `MAIN_FLOW` has to appear in the table, and adding a state
        without a decision here fails this test rather than a call.
        """
        from src.agent.fitting_fsm import MAIN_FLOW, STATES
        from src.core.pipeline import _FSM_FIELD_TO_PROGRESS

        exempt = {"intent", "confirmed", "station_id"}
        fields = {STATES[state].field_name for state in MAIN_FLOW if STATES[state].field_name}
        assert fields - exempt == set(_FSM_FIELD_TO_PROGRESS)

    async def test_a_tool_call_result_is_never_overwritten_by_a_parse(self) -> None:
        """Gap-fill only — the flat field is a tool call, and it outranks.

        A tool call is an action taken on the caller's behalf; a parse is a
        reading of what they said. When the two disagree the action is the one
        already reflected in the slots offered, so the block must keep showing
        it.
        """
        h = Harness(session_in(FsmState.BRAND, city="Львів"))
        h.session.fitting_plate = "чорний"
        h.session.fsm_filled_fields["color"] = "срібний"

        progress = h.pipeline._build_fitting_progress(None, krok8_confirmed=False)

        assert progress["plate"] == "чорний"
        assert progress["city"] == "Львів", "the empty slot still gets filled"

    async def test_with_the_fsm_off_the_block_is_unchanged(self) -> None:
        """The merge must not reach a call that never enters the machine."""
        h = Harness(CallSession(uuid.uuid4()))
        h.session.fitting_plate = "синій"

        progress = h.pipeline._build_fitting_progress(None, krok8_confirmed=False)

        assert progress["plate"] == "синій"
        assert [k for k, v in progress.items() if v not in (None, "", False)] == ["plate"]

    async def test_the_give_up_sentinel_is_surfaced_rather_than_swallowed(self) -> None:
        """«колір не розчула» is a value, not a hole — and the same one the prompt names.

        `_color_null_exhausted` writes that sentinel and moves to BRAND after two
        failed re-asks. It is not a phrase the FSM invented: `prompts.py:613`
        tells the LLM to pass exactly `"колір не розчула"` to `book_fitting` in
        the same situation, so surfacing it makes the two sides agree instead of
        leaving the LLM to re-ask a colour the machine has already given up on —
        which is the loop this merge exists to close, in its worst shape.
        """
        h = Harness(session_in(FsmState.BRAND, city="Київ"))
        h.session.fsm_filled_fields["color"] = COLOR_NOT_HEARD

        progress = h.pipeline._build_fitting_progress(None, krok8_confirmed=False)

        assert progress["plate"] == COLOR_NOT_HEARD
        colour_row = next(
            line for line in _render_fitting_progress(progress).splitlines() if "Колір" in line
        )
        assert colour_row.startswith("✅"), colour_row

    async def test_an_empty_value_is_not_a_collected_field(self) -> None:
        """`""` must stay a hole, because for one row it renders as a filled one.

        `_render_fitting_progress` tests most rows with `bool(...)`, where `""`
        and `None` come out the same. `storage_choice` is the exception — it
        tests `is not None`, so an empty string renders «✅ Зберігання: не
        з'ясовано»: a step marked done whose own text says it is not. The LLM
        then skips Krok 2 and books without knowing whose tyres it is fitting.

        No parser emits an empty applied value today — the whole registry was
        fuzzed over a corpus of fillers, bare quotes and one-letter turns and
        came back with none, and so did `compound_parse`. So this pins a
        convention rather than a live bug, and it is the reason the emptiness
        half of the gap-fill's check is written at all: four sibling guards in
        the seam already read `in (None, "")`, and a future parser returning a
        stripped `""` would land on the one row that cannot tell it from a real
        answer.
        """
        h = Harness(session_in(FsmState.STORAGE, city="Київ"))
        h.session.fsm_filled_fields["storage_choice"] = ""

        progress = h.pipeline._build_fitting_progress(None, krok8_confirmed=False)

        assert progress["storage_choice"] is None, (
            "an empty string must not be copied across as a collected field"
        )
        storage_row = next(
            line for line in _render_fitting_progress(progress).splitlines() if "Зберігання" in line
        )
        assert storage_row.startswith("⏳"), storage_row

    async def test_the_station_the_fsm_resolved_reaches_the_block(self) -> None:
        """Call `3639c0b4` — «Харківське шосе» was heard and then lost.

        The FSM filled `station_id`; `_resolve_selected_station` keyed only off
        `last_fitting_station_id`, which a tool call writes. So the summary read
        back the *other* Kyiv station and the Krok 1 guard then refused the
        caller's correction.
        """
        h = Harness(session_in(FsmState.STORAGE, city="Київ", station_id="000000019"))
        h.session.fitting_stations_seen = [
            {"id": "000000019", "city": "Київ", "address": "Харківське шосе, 165"},
            {"id": "000000021", "city": "Київ", "address": "вул. Маршала Тимошенка, 7"},
        ]

        progress = h.pipeline._build_fitting_progress(
            h.pipeline._resolve_selected_station(), krok8_confirmed=False
        )

        assert progress["station_address"] == "Харківське шосе, 165"

    async def test_a_station_the_llm_acted_on_outranks_the_one_parsed(self) -> None:
        """Slots were offered against `last_fitting_station_id`, so it wins.

        Demoting it would silently re-point a call whose slots, and possibly
        whose booking, were already taken elsewhere.
        """
        h = Harness(session_in(FsmState.STORAGE, city="Київ", station_id="000000019"))
        h.session.fitting_stations_seen = [
            {"id": "000000019", "city": "Київ", "address": "Харківське шосе, 165"},
            {"id": "000000021", "city": "Київ", "address": "вул. Маршала Тимошенка, 7"},
        ]
        h.session.last_fitting_station_id = "000000021"

        assert (h.pipeline._resolve_selected_station() or {}).get("address") == (
            "вул. Маршала Тимошенка, 7"
        )


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
