"""A time the caller named exactly must not be put back up for discussion.

2026-09-14. Call `23a189af` asked «На дванадцяту рівно чи дванадцяту тридцять?»
about a 40-minute grid holding neither 12:00 nor 12:30, four seconds after the
backend had already pinned 12:20 — and then booked 12:20 while telling the
caller 12:30 twice. Chasing that led to a replay of the 196 calls that reached
`get_fitting_slots` in 45 days, and to the narrower defect this gate closes.

The gate does NOT cover `23a189af` itself. There the caller said a bare «на 12»
and the pin came from the bare-hour widening — the same widening that turns «на
15.10» into 10:20, five hours from the time asked for. Silencing the bot on that
evidence would confirm the wrong slot, so the gate arms only when the caller
named the hour *and* the minutes. That boundary is the point of
`TestTheBareHourIsLeftAlone`. The 12-hour reading still applies inside it —
«5:00» arms against an offered 17:00, call `74c61ff8` — because there the
membership test against the offered list is what bounds the widening.

Every sentence below is verbatim production text, so a change that stops fixing
these calls fails here. Fire/silence membership is the measured contract: twelve
firings across 4994 bot sentences, nothing else touched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from src.agent.streaming_loop import _last_user_text, confirm_settled_time
from src.agent.time_detect import (
    asks_which_time,
    detect_time_choice,
    lists_alternative_times,
)
from src.core.sentence_buffer import SentenceReady, buffer_sentences
from src.llm.models import StreamDone, TextDelta, Usage
from src.monitoring.metrics import reopened_time_choice_total

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

#: The 40-minute grid `get_fitting_slots` really returned for `23a189af`.
GRID_40 = [
    "09:40",
    "10:20",
    "11:00",
    "11:40",
    "12:20",
    "13:00",
    "13:40",
    "14:20",
    "15:00",
    "15:40",
    "16:20",
    "17:00",
]
#: The half-hour grid behind `40fa9b38` and `891d84f0`.
GRID_30 = [
    "09:00",
    "09:30",
    "10:00",
    "10:30",
    "11:00",
    "11:30",
    "12:00",
    "12:30",
    "13:00",
    "13:30",
    "14:00",
    "14:30",
    "15:00",
    "15:30",
    "16:00",
    "16:30",
]
#: The 40-minute grid behind `406e75e6`, `f7557bd1` and `4e09dfab`.
GRID_40_FROM_NINE = [
    "09:00",
    "09:40",
    "10:20",
    "11:00",
    "11:40",
    "12:20",
    "13:00",
    "13:40",
    "14:20",
    "15:00",
    "15:40",
    "16:20",
    "17:00",
]
#: `add8354b` — the hourly-plus-twenty grid the bot then denied.
GRID_20 = ["09:20", "10:20", "11:20", "12:20", "13:20", "14:20", "15:20"]


def _slots(times: list[str]) -> list[dict[str, str]]:
    return [{"date": "2026-09-15", "time": t} for t in times]


def _said(text: str) -> dict[str, Any]:
    return {"role": "user", "content": [{"type": "text", "text": text}]}


async def _emit_text(text: str) -> AsyncIterator[Any]:
    """One character at a time, which is how the clause splitter really sees it."""
    for char in text:
        yield TextDelta(text=char)
    yield StreamDone(stop_reason="end_turn", usage=Usage(1, 1))


async def _heard(
    text: str,
    offered: list[str] | None,
    customer_text: str,
) -> str:
    """What the caller would hear, reassembled from the fragments TTS is handed."""
    out = confirm_settled_time(
        buffer_sentences(_emit_text(text)),
        _slots(offered) if offered else None,
        [_said(customer_text)],
    )
    return " ".join([e.text async for e in out if isinstance(e, SentenceReady)])


class TestTheTwelveFiringsFromProduction:
    """Each one: the caller named a slot off the list, the bot asked again."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("customer", "bot", "offered", "expected"),
        [
            # 406e75e6 — «900» is 09:00 compressed by STT, and it is on the list.
            ("900", "На 9:00 чи 9:40?", GRID_40_FROM_NINE, "09:00"),
            # 40fa9b38 — the anti-pattern `prompts.py:593` names by date.
            ("1100", "На 11:00 чи 11:30?", GRID_30, "11:00"),
            # 66c8f00d — re-offers an hour the caller had already declined.
            ("16:00", "На 15:00 чи 16:00 вам зручніше?", GRID_30, "16:00"),
            # 891d84f0 — asks the open question again with no alternatives at all.
            ("16:00", "О котрій зручніше?", GRID_30, "16:00"),
            # f7557bd1 — the anti-pattern `prompts.py:588` names by call id:
            # the two alternatives offered are the same value.
            ("11:40", "На 11:40 чи 11:40 точно?", GRID_40_FROM_NINE, "11:40"),
            # add8354b — the worst of the six. The caller picked 14:20 off the
            # list and the bot denied a slot it had offered one turn earlier.
            (
                "14 это 20",
                "З переліку 9:20, 10:20, 11:20, 12:20, 13:20, 14:20, 15:20 який зручніший?",
                GRID_20,
                "14:20",
            ),
        ],
    )
    async def test_the_pick_is_accepted_instead(
        self, customer: str, bot: str, offered: list[str], expected: str
    ) -> None:
        heard = await _heard(bot, offered, customer)
        assert heard == f"Добре, {expected} прийнято."

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("customer", "bot", "offered", "expected"),
        [
            # add8354b — the denial, then the list re-read as a question.
            (
                "14 это 20",
                "Слота на чотирнадцяту двадцять немає. "
                "З переліку 9:20, 10:20, 11:20, 12:20, 13:20, 14:20, 15:20 який зручніший?",
                GRID_20,
                "14:20",
            ),
            # 9c82ce3d — 15:00 was on the list the bot itself had just read out.
            # The recital that follows is a statement, so it takes the third
            # trigger; a question-only gate would speak it after the acceptance.
            (
                "давайте на 15:00",
                "Слоту на п'ятнадцяту годину немає. "
                "З переліку: 9:00, 9:40, 10:20, 11:00, 11:40, 12:20, 13:00,",
                GRID_40_FROM_NINE,
                "15:00",
            ),
            # 74c61ff8 — «5:00» is the afternoon on a grid that ends at 17:00,
            # and the caller barged in over the recital, which is why it has no
            # terminator.
            (
                "на 2 на 5:00",
                "Слоту на п'яту годину немає. З переліку 9:00, 9:40,",
                GRID_40_FROM_NINE,
                "17:00",
            ),
        ],
    )
    async def test_a_denial_and_its_recital_both_go(
        self, customer: str, bot: str, offered: list[str], expected: str
    ) -> None:
        """Three calls denied the slot and then re-read the list. Both halves
        contradict the acceptance, so neither may reach the caller."""
        heard = await _heard(bot, offered, customer)
        assert "нема" not in heard
        assert heard == f"Добре, {expected} прийнято."

    @pytest.mark.asyncio
    async def test_a_recital_with_no_denial_at_all_is_caught(self) -> None:
        """`159e7b49`: no question, no denial — the bot simply read the whole
        list back over a pick it had already been given."""
        heard = await _heard(
            "На четверте вересня вільний час: 9:00, 10:20, 11:40, 13:00, 14:20.",
            GRID_40_FROM_NINE,
            "на 13:00",
        )
        assert heard == "Добре, 13:00 прийнято."

    @pytest.mark.asyncio
    async def test_the_metric_counts_the_firing(self) -> None:
        before = reopened_time_choice_total._value.get()
        await _heard("На 11:00 чи 11:30?", GRID_30, "1100")
        assert reopened_time_choice_total._value.get() == before + 1


class TestWhatMustStillBeSpoken:
    """The eleven post-pick questions the replay says are legitimate."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("customer", "bot"),
        [
            # Krok 8 read-back: a question that names the booked time.
            (
                "на 13:00",
                "Вікторія, перевіримо: чотирнадцяте вересня о 13:00, Київ, "
                "вул. Маршала Тимошенка, 7, сріблястий Volkswagen. Підтверджуєте?",
            ),
            # An acceptance that moves on to the next row.
            ("десята 20", "Добре, час 10:20 прийнято. На яке ім'я записати?"),
            ("11:00", "Отже, записую на 5 серпня о 11:00 — назвіть, будь ласка, ваше ім'я?"),
            # Questions about other rows entirely.
            ("10:20", "Де колір автомобіля?"),
            ("820", "Зрозуміла. Підкажіть, будь ласка, держномер автомобіля?"),
            ("Добре давайте 8:20", "Назвіть, будь ласка, як до вас звертатися?"),
            ("номер авто 0.1.00", "Прийняла номер 0100, вірно?"),
            # «чи» about something that is not a time.
            (
                "1500 потрібно",
                "Підкажіть, будь ласка, вас цікавить вартість шиномонтажу чи ціна шин?",
            ),
            # «зручніше» about something that is not a time — the near-miss that
            # makes «О котрій зручніше?» need the interrogative, not the adverb.
            (
                "на цьому на 7 вересня 10:30",
                "У Києві є 2 точки: Харківське шосе та Оболонь. У якому районі вам зручніше?",
            ),
        ],
    )
    async def test_the_sentence_survives_untouched(self, customer: str, bot: str) -> None:
        assert await _heard(bot, GRID_40_FROM_NINE + GRID_30, customer) == bot


class TestTheBareHourIsLeftAlone:
    """The boundary that keeps the gate off a pin it cannot trust."""

    @pytest.mark.asyncio
    async def test_a_bare_hour_with_two_slots_in_it_keeps_the_question(self) -> None:
        """`4e09dfab`: «на 9» against 09:00 and 09:40 leaves a real choice."""
        bot = "На 9:00 чи 9:40?"
        assert await _heard(bot, GRID_40_FROM_NINE, "Алло на 9") == bot

    @pytest.mark.asyncio
    async def test_the_mis_pinned_caller_still_hears_the_correction(self) -> None:
        """`135cf711`: «на 15.10» widens to 10:20, five hours off the request.

        The bot's answer — that 15:10 is unavailable and 15:00/15:40 are — is
        correct, and a gate trusting that pin would replace it with «Добре,
        10:20 прийнято.»
        """
        bot = (
            "Слоту на п'ятнадцяту десять немає. "
            "З переліку вільних є п'ятнадцята і п'ятнадцята сорок. Який час зручніший?"
        )
        assert await _heard(bot, GRID_40_FROM_NINE, "на 15.10") == bot

    @pytest.mark.asyncio
    async def test_the_taken_slot_is_not_confirmed_out_from_under_the_bot(self) -> None:
        """`29ed5791`: the pin says 17:00, the bot says 17:00 is already gone."""
        bot = (
            "На четвер, сімнадцяте вересня вільні слоти до 17:00, "
            "17:00 вже зайнято. Який час вам зручний?"
        )
        heard = await _heard(bot, GRID_40_FROM_NINE, "на вечер на 5 часов где-то примерно")
        assert heard == bot

    def test_the_widening_really_is_what_separates_them(self) -> None:
        """The precondition, asserted directly against the shipped parser.

        If `detect_time_choice` ever starts resolving these without the
        widening, the gate silently grows to cover them and the three tests
        above stop meaning what they say.
        """
        assert detect_time_choice("на 15.10", GRID_40_FROM_NINE, allow_hour_only=False) is None
        assert detect_time_choice("Алло на 9", GRID_40_FROM_NINE, allow_hour_only=False) is None
        assert detect_time_choice("давайте на 12", GRID_40, allow_hour_only=False) is None
        assert detect_time_choice("900", GRID_40_FROM_NINE, allow_hour_only=False) == "09:00"
        assert detect_time_choice("11:40", GRID_40_FROM_NINE, allow_hour_only=False) == "11:40"


class TestTheGateNeedsBothHalves:
    @pytest.mark.asyncio
    async def test_no_offered_list_means_no_gate(self) -> None:
        """Before `get_fitting_slots` runs there is nothing to have picked from."""
        bot = "На 11:00 чи 11:30?"
        assert await _heard(bot, None, "1100") == bot

    @pytest.mark.asyncio
    async def test_a_time_outside_the_offered_list_does_not_arm_it(self) -> None:
        """«17:15» is exact but not on offer, so the bot's answer must stand."""
        bot = "Зрозуміла. 17:15 немає. Є 15:00 чи 15:40, який зручніший?"
        assert await _heard(bot, GRID_40_FROM_NINE, "17:15 є час") == bot

    @pytest.mark.asyncio
    async def test_it_fires_at_most_once_per_turn(self) -> None:
        """A second re-opening in the same turn is dropped, not re-announced."""
        heard = await _heard("На 11:00 чи 11:30? О котрій зручніше?", GRID_30, "1100")
        assert heard == "Добре, 11:00 прийнято."

    def test_the_last_user_turn_is_the_one_read(self) -> None:
        """An earlier pick must not arm the gate on a later, unrelated turn."""
        history = [
            _said("11:40"),
            {"role": "assistant", "content": [{"type": "text", "text": "Добре."}]},
            _said("ні, давайте інший день"),
        ]
        assert _last_user_text(history) == "ні, давайте інший день"

    def test_plain_string_content_is_read_too(self) -> None:
        assert _last_user_text([{"role": "user", "content": "11:40"}]) == "11:40"


class TestThePredicateItself:
    def test_one_time_literal_is_not_a_choice(self) -> None:
        """«записую на 5 серпня о 11:00 — ваше ім'я?» names a time and asks
        about something else entirely."""
        assert asks_which_time("Є вільний слот о 11:00, підходить?") is False

    def test_two_literals_make_a_choice(self) -> None:
        assert asks_which_time("На 11:00 чи 11:30?") is True

    def test_a_statement_is_never_a_question(self) -> None:
        assert asks_which_time("Вільні часи: 11:00, 11:30, 12:00.") is False

    def test_an_acceptance_vetoes_the_recital(self) -> None:
        """Krok 8 asks «Підтверджуєте?» while listing two times — booked date
        and booked hour — and must not read as re-opening the choice."""
        assert asks_which_time("Перевіримо: о 13:00, виїзд о 13:40. Підтверджуєте?") is False

    def test_a_booking_verb_does_not_veto_the_interrogative(self) -> None:
        """The veto belongs to the recital branch only.

        These three are verbatim corpus questions that carry a booking verb and
        still ask which hour. Vetoing them would disarm the gate for the
        `891d84f0` defect whenever the LLM phrased it with «записуємо».
        """
        assert asks_which_time("На яку годину записувати?") is True
        assert asks_which_time("Далі, на який час записуємо 29 серпня?") is True
        assert (
            asks_which_time("Отже, записуємо на середу, 5 серпня — який час вам підходить?") is True
        )

    def test_empty_input_is_not_a_question(self) -> None:
        assert asks_which_time("") is False

    def test_a_recital_needs_no_question_mark(self) -> None:
        assert lists_alternative_times("Вільний час: 9:00, 10:20, 11:40.") is True

    def test_one_literal_is_not_a_recital(self) -> None:
        assert lists_alternative_times("Вільний час: 9:00.") is False

    def test_the_read_back_is_vetoed_despite_two_literals(self) -> None:
        """Krok 8 names the booked hour and the arrival time. Without the veto
        the third trigger would silence the confirmation the booking depends on."""
        assert (
            lists_alternative_times("Перевіримо: о 13:00, виїзд о 13:40. Підтверджуєте?") is False
        )


class TestTheGateIsActuallyWiredIntoTheTurn:
    """A corpus test on the predicate does not cover the call site.

    `run_turn` assembles the speech path itself, and an `elif False:` at the
    seam would leave every test above green while production re-opened the
    choice exactly as before. These drive the real loop and assert on what TTS
    was handed.
    """

    @pytest.mark.asyncio
    async def test_the_reopening_never_reaches_tts(self) -> None:
        loop, tts = _build_reasking_loop()
        await loop.run_turn("1100", [], offered_slots=_slots(GRID_30))
        assert not any("11:30" in text for text in tts.texts)
        assert any("11:00 прийнято" in text for text in tts.texts)

    @pytest.mark.asyncio
    async def test_without_the_offered_list_the_turn_is_spoken_as_written(self) -> None:
        """The other half: the gate must be reading the argument it is passed.

        Hardcoding the slots, or passing `None` through, would leave the test
        above green for the wrong reason.
        """
        loop, tts = _build_reasking_loop()
        await loop.run_turn("1100", [], offered_slots=None)
        assert any("11:30" in text for text in tts.texts)

    @pytest.mark.asyncio
    async def test_the_caller_turn_reaches_the_gate_through_the_history(self) -> None:
        """`run_turn` appends `user_text` before building the chain, and the
        gate reads the pick out of that history rather than off a parameter.
        Handing it a fresh list would disarm the gate in production only."""
        loop, tts = _build_reasking_loop()
        await loop.run_turn("яка у вас адреса", [], offered_slots=_slots(GRID_30))
        assert any("11:30" in text for text in tts.texts)


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
    """A loop whose LLM re-opens a choice the caller has already made."""
    import asyncio

    from src.agent.agent import ToolRouter
    from src.agent.streaming_loop import StreamingAgentLoop
    from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection
    from tests.unit.mocks.mock_llm_router import MockLLMRouter

    responses = [
        [
            TextDelta(text="На 11:00 чи 11:30? "),
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
