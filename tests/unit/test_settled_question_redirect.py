"""A question aimed at a ✅ checklist row must not reach the caller.

Wave 20 (2026-09-11). Three of that day's eight booking calls went back for a
field the progress block showed as collected at that very moment — seven times
between them, every one on colour or brand, and every one on the turn right
after the time slot was accepted: «15:20 прийнято. Назвіть, будь ласка, колір
автомобіля.» The LLM walks the Krok numbers in order, so once Krok 4 lands it
resumes at Krok 5 whether or not Kroks 5 and 6 were filled earlier out of order.

Both existing defences are prompt text — the ✅/⏳ block (added for this exact
bug in July) and the explicit ban in `prompts.py` — and both were in the context
window for all seven. Hence a code gate.

The fixtures below are the real progress dicts and the real sentences from
`e4fa7fc1` and `09668c25`, so a change that stops fixing those calls fails here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from src.agent.prompts import (
    FITTING_STEPS,
    fitting_confirmation_sentence,
    fitting_steps_collected,
    next_fitting_question,
)
from src.agent.streaming_loop import (
    redirect_settled_question,
    settled_field_asked,
)
from src.core.sentence_buffer import SentenceReady, buffer_sentences
from src.llm.models import StreamDone, TextDelta, ToolCallEnd, ToolCallStart, Usage
from src.monitoring.metrics import (
    settled_question_redirect_skipped_total,
    settled_question_redirected_total,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from src.core.sentence_buffer import BufferEvent

STORAGE_QUESTION = "Шини привозите свої з собою чи ті, що у нас на зберіганні?"
COLOUR_QUESTION = "Назвіть, будь ласка, колір автомобіля."

#: Call e4fa7fc1 at turn 14. Colour and brand came in together on turn 7 while
#: the bot was still asking about storage; `storage_choice_parser` then returned
#: not_mentioned eight turns running, so storage is the row still waiting.
E4FA7FC1 = {
    "customer_name": "Інна",
    "city": "Запоріжжя",
    "station_address": "вул. Перемоги, 72б",
    "storage_choice": None,
    "date": "2026-09-15",
    "time": "15:20",
    "plate": "червоний",
    "brand": "Škoda",
    "caller_phone": "0951234567",
}

#: Call 09668c25 at turn 21 — every row answered, and it still asked for the
#: colour. With nothing left to ask there is no question to substitute, so this
#: is the fixture that forces the Krok 8 fallback.
ALL_COLLECTED = {
    "customer_name": "Валерій",
    "city": "Запоріжжя",
    "station_address": "вул. Перемоги, 72б",
    "storage_choice": "own",
    "date": "2026-09-14",
    "time": "09:20",
    "plate": "сірий",
    "brand": "Volkswagen",
    "caller_phone": "0951112233",
}

ALL_SETTLED = dict.fromkeys((field_key for field_key, _ in FITTING_STEPS), True)


async def _emit_text(text: str) -> AsyncIterator[Any]:
    """One character at a time, which is how the clause splitter really sees it."""
    for char in text:
        yield TextDelta(text=char)
    yield StreamDone(stop_reason="end_turn", usage=Usage(1, 1))


async def _heard(
    progress: dict[str, Any],
    text: str,
    history: list[dict[str, Any]] | None = None,
) -> str:
    """What the caller would hear, reassembled from the fragments TTS is handed."""
    out = redirect_settled_question(
        buffer_sentences(_emit_text(text)), progress, history or []
    )
    return " ".join([e.text async for e in out if isinstance(e, SentenceReady)])


def _said_by(text: str) -> list[dict[str, Any]]:
    return [{"role": "assistant", "content": [{"type": "text", "text": text}]}]


def _counter_value(counter: Any, **labels: str) -> float:
    child = counter.labels(**labels) if labels else counter
    return child._value.get()


class TestWhichRowsCountAsAnswered:
    def test_a_station_address_settles_the_city_row(self) -> None:
        """The caller names a landmark, `get_fitting_stations` pins the city.

        Without this the bot would ask «У якому місті?» about a booking whose
        address it has already read back.
        """
        collected = fitting_steps_collected({"station_address": "вул. Перемоги, 72б"})
        assert collected["city"] is True

    def test_storage_is_tested_against_none_not_truthiness(self) -> None:
        """Its two values are `own` and `storage`; "" means never set."""
        assert fitting_steps_collected({"storage_choice": "own"})["storage"] is True
        assert fitting_steps_collected({"storage_choice": ""})["storage"] is True
        assert fitting_steps_collected({"storage_choice": None})["storage"] is False

    def test_an_empty_progress_block_has_nothing_collected(self) -> None:
        assert not any(fitting_steps_collected({}).values())

    def test_every_checklist_row_has_a_verdict(self) -> None:
        """A row the map forgets would be a silent hole in the gate."""
        collected = fitting_steps_collected(ALL_COLLECTED)
        assert set(collected) == {field_key for field_key, _ in FITTING_STEPS}


class TestTheReplacementIsTheStepTheChecklistPointsAt:
    def test_the_first_unanswered_row_in_krok_order(self) -> None:
        assert next_fitting_question(E4FA7FC1) == STORAGE_QUESTION

    def test_nothing_unanswered_means_no_question(self) -> None:
        assert next_fitting_question(ALL_COLLECTED) == ""

    def test_a_full_checklist_is_read_back_for_a_yes_or_no(self) -> None:
        """Krok 8 is what the checklist points at when no row is left."""
        sentence = fitting_confirmation_sentence(ALL_COLLECTED)
        assert sentence.startswith("Валерій, перевіримо:")
        assert "чотирнадцяте вересня" in sentence
        assert "09:20" in sentence
        assert "сірий Volkswagen" in sentence
        assert sentence.endswith("Підтверджуєте?")

    def test_a_gap_is_never_read_back_for_confirmation(self) -> None:
        """Asking «Підтверджуєте?» about a missing value invents one."""
        assert fitting_confirmation_sentence(E4FA7FC1) == ""

    def test_phone_carries_no_question(self) -> None:
        """It comes from CallerID; Krok 7 is satisfied without asking."""
        assert dict(FITTING_STEPS)["phone"] == ""


class TestRecognisingAQuestionAboutASettledRow:
    @pytest.mark.parametrize(
        ("sentence", "field_key"),
        [
            # The four re-asks that reached callers on 2026-09-11.
            ("15:20 прийнято. Назвіть, будь ласка, колір автомобіля.", "color"),
            ("Перепрошую, не розчула колір — назвіть, будь ласка, ще раз.", "color"),
            ("Яка марка вашого авто?", "brand"),
            ("Яка марка вашого автомобіля?", "brand"),
            # Matched across the whole checklist, not just the two rows seen
            # failing, so a row that starts regressing tomorrow is covered.
            ("Як до вас звертатися?", "name"),
            ("У якому місті вам зручніше записатися на шиномонтаж?", "city"),
            (STORAGE_QUESTION, "storage"),
            ("На яку дату записуємо?", "date"),
            ("О котрій зручніше?", "time"),
        ],
    )
    def test_a_settled_row_is_claimed(self, sentence: str, field_key: str) -> None:
        assert settled_field_asked(sentence, ALL_SETTLED) == field_key

    def test_a_full_stop_imperative_still_counts_as_a_request(self) -> None:
        """«Назвіть, будь ласка, колір автомобіля.» has no question mark."""
        assert settled_field_asked(COLOUR_QUESTION, ALL_SETTLED) == "color"

    def test_reading_an_answer_back_is_not_a_request(self) -> None:
        """Otherwise the acknowledgement would be rewritten into a question.

        «Отже, ви привозите шини свої з собою.» matches the storage pattern on
        wording alone — it is the bot confirming, not asking.
        """
        ack = "Отже, ви привозите шини свої з собою."
        assert settled_field_asked(ack, ALL_SETTLED) is None

    @pytest.mark.parametrize(
        "sentence",
        [
            # Real turn from 09668c25: the caller picked a date out of range.
            "На якій іншій даті зручніше?",
            # Real turn from bc1633fe, a reschedule.
            "На яку дату переносимо запис?",
            "Оберіть, будь ласка, іншу годину.",
        ],
    )
    def test_asking_for_a_different_value_is_not_a_re_ask(self, sentence: str) -> None:
        """`get_fitting_slots` writes `selected_fitting_date` on lookup, before
        the caller has chosen anything, so the date row reads ✅ at exactly the
        moment the bot has to ask for a replacement date."""
        assert settled_field_asked(sentence, ALL_SETTLED) is None

    def test_a_row_still_waiting_is_left_to_the_llm(self) -> None:
        collected = fitting_steps_collected(E4FA7FC1)
        assert settled_field_asked(STORAGE_QUESTION, collected) is None

    def test_a_sentence_that_asks_for_nothing_is_claimed_by_no_row(self) -> None:
        assert settled_field_asked("Добре, 15:40 прийнято.", ALL_SETTLED) is None

    def test_either_apostrophe_is_recognised(self) -> None:
        """The LLM is not consistent about U+02BC versus U+0027."""
        assert settled_field_asked("Ваше імʼя?", ALL_SETTLED) == "name"
        assert settled_field_asked("Ваше ім'я?", ALL_SETTLED) == "name"


class TestTheCallerHearsTheWaitingStepInstead:
    """Every one of the seven re-asks recorded on 2026-09-11."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "said",
        [
            "15:20 прийнято. Назвіть, будь ласка, колір автомобіля.",
            "Перепрошую, не розчула колір — назвіть, будь ласка, ще раз.",
            "Яка марка вашого авто?",
        ],
    )
    async def test_e4fa7fc1_is_steered_to_the_storage_question(self, said: str) -> None:
        assert STORAGE_QUESTION in await _heard(E4FA7FC1, said)

    @pytest.mark.asyncio
    async def test_the_original_question_is_gone(self) -> None:
        heard = await _heard(E4FA7FC1, "Яка марка вашого авто?")
        assert "марка" not in heard

    @pytest.mark.asyncio
    async def test_the_tail_of_a_replaced_sentence_does_not_survive_it(self) -> None:
        """«…ще раз.» arrives as its own fragment after the verdict is in.

        Flushing it would have the caller hear «…на зберіганні? будь ласка, ще
        раз.» — the replacement with the original's trailing clause stuck on.
        """
        heard = await _heard(
            E4FA7FC1, "Перепрошую, не розчула колір — назвіть, будь ласка, ще раз."
        )
        assert heard == STORAGE_QUESTION

    @pytest.mark.asyncio
    async def test_what_came_before_the_question_is_still_spoken(self) -> None:
        """The acknowledgement is true and the caller is owed it."""
        heard = await _heard(E4FA7FC1, "15:20 прийнято. Назвіть, будь ласка, колір автомобіля.")
        assert heard.startswith("15:20 прийнято.")

    @pytest.mark.asyncio
    async def test_a_full_checklist_is_steered_to_krok_8(self) -> None:
        """Call 09668c25 turn 21: nothing left to ask, so confirm instead."""
        heard = await _heard(
            ALL_COLLECTED, "Добре, дев'ята двадцять прийнято. Назвіть, будь ласка, колір автомобіля."
        )
        assert heard.endswith(fitting_confirmation_sentence(ALL_COLLECTED))
        assert "колір" not in heard


class TestSentencesTheGateMustNotTouch:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("progress", "said"),
        [
            # A genuine first ask: the colour row is empty.
            (
                {**ALL_COLLECTED, "plate": None, "brand": None},
                "Добре, 15:40 прийнято. Назвіть, будь ласка, колір автомобіля.",
            ),
            # The replacement itself, asked for the first time.
            (E4FA7FC1, STORAGE_QUESTION),
            # An acknowledgement plus the next genuine step.
            (
                {**ALL_COLLECTED, "date": None, "time": None},
                "Отже, ви привозите шини свої з собою. На яку дату записуємо?",
            ),
            # The date row is ✅ from the lookup, but this asks for another one.
            (
                ALL_COLLECTED,
                "Записати на 9 жовтня не можу, бронювання доступне лише на "
                "найближчі три тижні. На якій іншій даті зручніше?",
            ),
        ],
    )
    async def test_it_is_spoken_verbatim(self, progress: dict[str, Any], said: str) -> None:
        heard = await _heard(progress, said)
        assert heard.replace(" ", "") == said.replace(" ", "")

    @pytest.mark.asyncio
    async def test_a_booked_call_is_left_alone(self) -> None:
        """After the booking the checklist stops describing the conversation.

        `fitting_booked` turns the rows into history, and steering a caller who
        is now asking about something else back to Krok 8 would talk over them.
        """
        booked = {**ALL_COLLECTED, "booked": True}
        assert await _heard(booked, COLOUR_QUESTION) == COLOUR_QUESTION

    @pytest.mark.asyncio
    async def test_a_call_with_no_fitting_progress_at_all_is_left_alone(self) -> None:
        """Tyre search and price calls never build a checklist."""
        assert await _heard({}, COLOUR_QUESTION) == COLOUR_QUESTION

    @pytest.mark.asyncio
    async def test_non_sentence_events_keep_their_place_in_the_stream(self) -> None:
        """A tool call between sentences must not be swallowed or reordered."""

        async def _stream() -> AsyncIterator[BufferEvent]:
            yield SentenceReady(text="Одну секунду.")
            yield ToolCallStart(id="t1", name="get_fitting_slots")
            yield ToolCallEnd(id="t1")
            yield StreamDone(stop_reason="tool_use", usage=Usage(1, 1))

        out = [e async for e in redirect_settled_question(_stream(), E4FA7FC1, [])]
        assert [type(e) for e in out] == [
            SentenceReady,
            ToolCallStart,
            ToolCallEnd,
            StreamDone,
        ]

    @pytest.mark.asyncio
    async def test_a_fragment_held_when_a_tool_call_arrives_is_still_spoken(self) -> None:
        """The verdict needs a whole sentence, and a turn can end mid-sentence.

        «Одну секунду, зараз перевірю» is under the 25-character clause floor, so
        it sits in the queue waiting for a full stop that never comes — the LLM
        emits a tool call instead. Dropping the queue here loses the wait phrase
        and the caller hears silence over the 1C lookup.
        """

        async def _stream() -> AsyncIterator[BufferEvent]:
            yield SentenceReady(text="Одну секунду,")
            yield ToolCallStart(id="t1", name="get_fitting_slots")
            yield ToolCallEnd(id="t1")
            yield StreamDone(stop_reason="tool_use", usage=Usage(1, 1))

        out = [e async for e in redirect_settled_question(_stream(), E4FA7FC1, [])]
        assert [e.text for e in out if isinstance(e, SentenceReady)] == ["Одну секунду,"]


class TestTheLoopBreaker:
    """A short-circuiting guard needs one — Wave 13's repeated five times live.

    The replacement is only worth speaking while the caller can still answer it.
    `e4fa7fc1` is the case that proves it: `storage_choice_parser` returned
    not_mentioned on eight turns running, so forcing the storage question
    forever would talk past the caller instead of past the LLM.
    """

    @pytest.mark.asyncio
    async def test_the_second_redirect_still_fires(self) -> None:
        history = _said_by(STORAGE_QUESTION)
        assert STORAGE_QUESTION in await _heard(E4FA7FC1, COLOUR_QUESTION, history)

    @pytest.mark.asyncio
    async def test_after_two_the_llm_gets_to_speak(self) -> None:
        history = _said_by(STORAGE_QUESTION) + _said_by(STORAGE_QUESTION)
        assert await _heard(E4FA7FC1, COLOUR_QUESTION, history) == COLOUR_QUESTION

    @pytest.mark.asyncio
    async def test_the_released_sentence_is_whole(self) -> None:
        """It arrives in three fragments; yielding only the last one would have
        the caller hear «колір автомобіля.» on its own."""
        history = _said_by(STORAGE_QUESTION) + _said_by(STORAGE_QUESTION)
        said = "Перепрошую, не розчула колір — назвіть, будь ласка, ще раз."
        assert await _heard(E4FA7FC1, said, history) == said

    @pytest.mark.asyncio
    async def test_one_turn_never_redirects_twice(self) -> None:
        """Two settled questions in a turn would otherwise both be replaced."""
        said = "Назвіть, будь ласка, колір автомобіля. Яка марка вашого авто?"
        assert await _heard(E4FA7FC1, said) == STORAGE_QUESTION

    @pytest.mark.asyncio
    async def test_a_different_question_already_spoken_does_not_count(self) -> None:
        """The cap is per replacement, not per turn count."""
        history = _said_by("Як до вас звертатися?") + _said_by("О котрій зручніше?")
        assert STORAGE_QUESTION in await _heard(E4FA7FC1, COLOUR_QUESTION, history)


class TestItIsCounted:
    @pytest.mark.asyncio
    async def test_a_redirect_is_counted_under_the_row_that_was_asked(self) -> None:
        before = _counter_value(settled_question_redirected_total, field="brand")
        await _heard(E4FA7FC1, "Яка марка вашого авто?")
        assert _counter_value(settled_question_redirected_total, field="brand") == before + 1

    @pytest.mark.asyncio
    async def test_giving_up_is_counted_too(self) -> None:
        """Letting the LLM through is a decision, so it may not also be silent."""
        before = _counter_value(settled_question_redirect_skipped_total, reason="repeat")
        history = _said_by(STORAGE_QUESTION) + _said_by(STORAGE_QUESTION)
        await _heard(E4FA7FC1, COLOUR_QUESTION, history)
        assert (
            _counter_value(settled_question_redirect_skipped_total, reason="repeat")
            == before + 1
        )


class TestTheGateIsActuallyWiredIntoTheTurn:
    """A corpus test on the predicate does not cover the call site.

    `run_turn` builds the speech path itself, and an `elif False:` at the seam
    would leave every test above green while production re-asked as before.
    These drive the real loop and assert on what TTS was handed.
    """

    @pytest.mark.asyncio
    async def test_a_settled_question_never_reaches_tts(self) -> None:
        loop, tts = _build_reasking_loop()
        await loop.run_turn("червоний", [], fitting_progress=E4FA7FC1)
        assert not any("колір" in text for text in tts.texts)
        assert any(STORAGE_QUESTION in text for text in tts.texts)

    @pytest.mark.asyncio
    async def test_without_progress_the_same_turn_is_spoken_as_written(self) -> None:
        """The other half: the gate must be reading the argument it is passed."""
        loop, tts = _build_reasking_loop()
        await loop.run_turn("червоний", [], fitting_progress=None)
        assert any("колір" in text for text in tts.texts)

    @pytest.mark.asyncio
    async def test_the_loop_breaker_sees_the_turns_already_spoken(self) -> None:
        """The cap counts the live history, so the call site must hand it over.

        Passing a fresh list here would leave every loop-breaker test above
        green while production steered to the same dead question forever.
        """
        loop, tts = _build_reasking_loop()
        history = _said_by(STORAGE_QUESTION) + _said_by(STORAGE_QUESTION)
        await loop.run_turn("червоний", history, fitting_progress=E4FA7FC1)
        assert any("колір" in text for text in tts.texts)


class _RecordingTTS:
    """Records what it was asked to say. `spec=` is pointless here — the whole
    point is to observe the text, which a bare mock would silently drop."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    async def initialize(self) -> None:
        return None

    async def synthesize(self, text: str) -> bytes:
        self.texts.append(text)
        return b"\x00" * 640

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        self.texts.append(text)
        yield b"\x00" * 640


def _build_reasking_loop() -> tuple[Any, _RecordingTTS]:
    import asyncio

    from src.agent.agent import ToolRouter
    from src.agent.streaming_loop import StreamingAgentLoop
    from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection
    from tests.unit.mocks.mock_llm_router import MockLLMRouter

    responses = [
        [
            TextDelta(text="15:20 прийнято. "),
            TextDelta(text=COLOUR_QUESTION),
            StreamDone(stop_reason="end_turn", usage=Usage(1, 1)),
        ]
    ]
    tts = _RecordingTTS()
    loop = StreamingAgentLoop(
        llm_router=MockLLMRouter(responses),
        tool_router=ToolRouter(),
        tts=tts,
        conn=MockAudioSocketConnection(),
        barge_in_event=asyncio.Event(),
        system_prompt="Test system prompt",
    )
    return loop, tts
