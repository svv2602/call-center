"""A price quote is followed by an offer to book, not by the booking itself.

Call 0d7c099e (2026-09-24): the quote ended «У вас легковий чи позашляховик?»,
the caller said «позашляховик», and the bot went on to «Шини привозите свої з
собою чи ті, що у нас на зберіганні?» — a booking the caller never asked for.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from src.agent.booking_consent import (
    BOOKING_DECLINED_FAREWELL,
    BOOKING_OFFER,
    GATE_FAREWELL,
    GATE_OFFER,
    caller_agreed_to_book,
    caller_declined_booking,
    gate_mode,
    offers_spoken,
)
from src.agent.streaming_loop import BookingOfferGate, offer_booking_before_checklist
from src.core.call_session import CallSession
from src.core.pipeline import CallPipeline
from src.core.sentence_buffer import SentenceReady, buffer_sentences
from src.llm.models import StreamDone, TextDelta, ToolCallEnd, ToolCallStart, Usage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

GREETING = (
    "Добрий день! Це Марина, Твоя Шина, дзвінок автоматичний. Ви бажаєте записатися "
    "на шиномонтаж, дізнатися вартість, скасувати чи перенести запис?"
)
QUOTE = (
    "Для R18 у Дніпрі комплексний шиномонтаж легкових авто коштує 396 гривень за "
    "колесо, для позашляховиків — 438 гривень за колесо. У вас легковий автомобіль "
    "чи позашляховик?"
)
#: 0d7c099e, verbatim up to the storage question.
PRICE_CALL = [
    ("assistant", GREETING),
    ("user", "хочу дізнатися вартість у Дніпрі"),
    ("assistant", "Який діаметр коліс вас цікавить?"),
    ("user", "18"),
    ("assistant", QUOTE),
    ("user", "позашляховик"),
]


async def _emit(text: str) -> AsyncIterator[Any]:
    for char in text:
        yield TextDelta(text=char)
    yield StreamDone(stop_reason="end_turn", usage=Usage(1, 1))


async def _heard(text: str, gate: BookingOfferGate) -> str:
    out = offer_booking_before_checklist(buffer_sentences(_emit(text)), gate)
    return " ".join([e.text async for e in out if isinstance(e, SentenceReady)])


class TestConsent:
    def test_the_price_call_has_not_agreed(self) -> None:
        assert caller_agreed_to_book(PRICE_CALL) is False

    def test_yes_to_the_offer_is_consent(self) -> None:
        turns = [*PRICE_CALL, ("assistant", BOOKING_OFFER), ("user", "так")]
        assert caller_agreed_to_book(turns) is True

    def test_yes_to_the_prompts_own_offer_is_consent(self) -> None:
        turns = [*PRICE_CALL, ("assistant", "Записуємо на монтаж?"), ("user", "так давайте")]
        assert caller_agreed_to_book(turns) is True

    def test_asking_to_book_is_consent(self) -> None:
        turns = [
            *PRICE_CALL,
            ("assistant", "Для позашляховика 438 гривень."),
            ("user", "запишіть мене на завтра"),
        ]
        assert caller_agreed_to_book(turns) is True

    def test_no_to_the_offer_is_not(self) -> None:
        turns = [*PRICE_CALL, ("assistant", BOOKING_OFFER), ("user", "ні дякую")]
        assert caller_agreed_to_book(turns) is False

    def test_offers_are_counted(self) -> None:
        turns = [
            *PRICE_CALL,
            ("assistant", BOOKING_OFFER),
            ("user", "позашляховик"),
            ("assistant", BOOKING_OFFER),
        ]
        assert offers_spoken(turns) == 2


class TestFilter:
    async def test_the_storage_question_becomes_the_offer(self) -> None:
        heard = await _heard(
            "Для позашляховика 438 гривень за колесо. Шини привозите свої з собою "
            "чи ті, що у нас на зберіганні?",
            BookingOfferGate(GATE_OFFER),
        )
        assert heard.endswith(BOOKING_OFFER)
        assert "зберіганні" not in heard
        assert "438 гривень" in heard

    async def test_the_rest_of_the_turn_is_dropped(self) -> None:
        heard = await _heard(
            "Шини привозите свої з собою? На яку дату вас записати?",
            BookingOfferGate(GATE_OFFER),
        )
        assert heard == BOOKING_OFFER

    async def test_inactive_gate_changes_nothing(self) -> None:
        text = "Шини привозите свої з собою чи ті, що у нас на зберіганні?"
        heard = await _heard(text, BookingOfferGate(None))
        assert "зберіганні" in heard
        assert BOOKING_OFFER not in heard

    async def test_city_and_name_are_not_booking_questions(self) -> None:
        text = "У якому місті вас цікавить вартість? Як до вас звертатися?"
        heard = await _heard(text, BookingOfferGate(GATE_OFFER))
        assert BOOKING_OFFER not in heard
        assert "місті" in heard

    async def test_a_second_round_neither_offers_again_nor_resumes(self) -> None:
        gate = BookingOfferGate(GATE_OFFER)
        first = await _heard("Шини свої з собою?", gate)

        async def _round_two() -> AsyncIterator[Any]:
            yield ToolCallStart(id="t1", name="get_fitting_slots")
            yield ToolCallEnd(id="t1")
            async for e in _emit("На яку дату вас записати?"):
                yield e

        out = offer_booking_before_checklist(buffer_sentences(_round_two()), gate)
        second = [e.text async for e in out if isinstance(e, SentenceReady)]

        assert first == BOOKING_OFFER
        assert second == []


def _pipeline(session: CallSession) -> CallPipeline:
    from unittest.mock import MagicMock

    return CallPipeline(
        conn=MagicMock(spec=["is_closed"]),
        stt=MagicMock(spec=[]),
        tts=MagicMock(spec=[]),
        agent=MagicMock(spec=[]),
        session=session,
    )


def _session(turns: list[tuple[str, str]], *, quoted: bool) -> CallSession:
    session = CallSession(uuid.uuid4())
    session.fitting_price_quoted = quoted
    for speaker, content in turns:
        if speaker == "user":
            session.add_user_turn(content=content)
        else:
            session.add_assistant_turn(content)
    return session


class TestPipelineDecision:
    def test_on_after_a_quote_without_consent(self) -> None:
        assert _pipeline(_session(PRICE_CALL, quoted=True))._booking_gate_mode() == GATE_OFFER

    def test_off_without_a_quote(self) -> None:
        assert _pipeline(_session(PRICE_CALL, quoted=False))._booking_gate_mode() is None

    def test_off_after_two_offers(self) -> None:
        turns = [
            *PRICE_CALL,
            ("assistant", BOOKING_OFFER),
            ("user", "позашляховик"),
            ("assistant", BOOKING_OFFER),
            ("user", "позашляховик"),
        ]
        assert _pipeline(_session(turns, quoted=True))._booking_gate_mode() is None

    def test_the_flag_survives_the_redis_snapshot(self) -> None:
        session = _session(PRICE_CALL, quoted=True)
        restored = CallSession.from_dict(session.to_dict())
        assert restored.fitting_price_quoted is True


STORAGE_QUESTION = "Шини привозите свої з собою чи ті, що у нас на зберіганні?"


def _loop_saying(text: str) -> tuple[Any, Any]:
    import asyncio

    from src.agent.agent import ToolRouter
    from src.agent.streaming_loop import StreamingAgentLoop
    from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection
    from tests.unit.mocks.mock_llm_router import MockLLMRouter
    from tests.unit.test_settled_question_redirect import _RecordingTTS

    tts = _RecordingTTS()
    loop = StreamingAgentLoop(
        llm_router=MockLLMRouter(
            [[TextDelta(text=text), StreamDone(stop_reason="end_turn", usage=Usage(1, 1))]]
        ),
        tool_router=ToolRouter(),
        tts=tts,
        conn=MockAudioSocketConnection(),
        barge_in_event=asyncio.Event(),
        system_prompt="Test system prompt",
    )
    return loop, tts


class TestWiredIntoTheTurn:
    """The predicate tests above do not cover `run_turn`'s speech path."""

    async def test_the_offer_reaches_tts_instead_of_the_checklist(self) -> None:
        loop, tts = _loop_saying(STORAGE_QUESTION)
        await loop.run_turn("позашляховик", [], booking_gate_mode=GATE_OFFER)
        assert any(BOOKING_OFFER in t for t in tts.texts)
        assert not any("зберіганні" in t for t in tts.texts)

    async def test_the_flag_is_what_turns_it_on(self) -> None:
        loop, tts = _loop_saying(STORAGE_QUESTION)
        await loop.run_turn("позашляховик", [])
        assert any("зберіганні" in t for t in tts.texts)

    async def test_the_pipeline_hands_the_decision_to_the_loop(self) -> None:
        from tests.unit.test_pipeline_fsm_wire import Harness, fsm_flags

        session = _session(PRICE_CALL[:-1], quoted=True)
        h = Harness(session)
        with fsm_flags(enabled=False):
            await h.run("позашляховик")
        assert h.llm_kwargs[-1]["booking_gate_mode"] == GATE_OFFER

    async def test_a_price_quote_marks_the_session(self) -> None:
        """`get_fitting_price` as registered — the flag lives in the wiring."""
        session, _ = await _quote({"data": [{"point_id": "000000003", "price": 438}]})
        assert session.fitting_price_quoted is True

    async def test_no_price_found_marks_nothing(self) -> None:
        session, _ = await _quote({"data": []})
        assert session.fitting_price_quoted is False


async def _quote(onec_prices: dict[str, Any]) -> tuple[CallSession, Any]:
    from unittest.mock import AsyncMock

    from src.onec_client.client import OneCClient
    from src.store_client.client import StoreClient
    from tests.unit.test_reschedule_state_pin import _onec_mock, _run

    onec = _onec_mock()
    onec.get_fitting_prices = AsyncMock(
        spec=OneCClient.get_fitting_prices, return_value=onec_prices
    )
    store = AsyncMock(spec=StoreClient)
    store.get_fitting_price = AsyncMock(
        spec=StoreClient.get_fitting_price, return_value={"prices": []}
    )
    session = CallSession(uuid.uuid4())
    result = await _run(session, "get_fitting_price", {"station_id": "000000003"}, onec, store)
    return session, result


#: 354ff3b5 (2026-09-25), verbatim: the FSM quoted and offered, the caller said no.
FSM_OFFER_CALL = [
    ("assistant", GREETING),
    ("user", "вартість у Дніпрі"),
    ("assistant", "Який діаметр коліс?"),
    ("user", "18"),
    (
        "assistant",
        "Шиномонтаж R18 у місті Дніпро: легкові — 396 грн, позашляховики — 438 грн. "
        "Бажаєте записатися на шиномонтаж?",
    ),
    ("user", "Ні дякую"),
]


class TestDeclined:
    def test_no_to_the_fsm_offer_is_a_decline(self) -> None:
        assert caller_declined_booking(FSM_OFFER_CALL) is True
        assert gate_mode(FSM_OFFER_CALL) == GATE_FAREWELL

    def test_a_later_yes_overrides_the_no(self) -> None:
        turns = [*FSM_OFFER_CALL, ("assistant", BOOKING_OFFER), ("user", "так")]
        assert gate_mode(turns) is None

    def test_asking_to_book_after_a_no_overrides_it(self) -> None:
        turns = [*FSM_OFFER_CALL, ("assistant", "Добре."), ("user", "хоча запишіть на завтра")]
        assert gate_mode(turns) is None

    def test_an_off_topic_answer_is_not_a_decline(self) -> None:
        turns = [*FSM_OFFER_CALL[:-1], ("user", "позашляховик")]
        assert caller_declined_booking(turns) is False
        assert gate_mode(turns) == GATE_OFFER

    def test_the_fsm_offer_counts_toward_the_cap(self) -> None:
        turns = [
            *FSM_OFFER_CALL[:-1],
            ("user", "позашляховик"),
            ("assistant", BOOKING_OFFER),
            ("user", "позашляховик"),
        ]
        assert offers_spoken(turns) == 2
        assert gate_mode(turns) is None

    async def test_the_booking_question_becomes_the_farewell(self) -> None:
        heard = await _heard(
            "Шини привозите свої з собою чи ті, що у нас на зберіганні?",
            BookingOfferGate(GATE_FAREWELL),
        )
        assert heard == BOOKING_DECLINED_FAREWELL

    def test_the_farewell_ends_the_call(self) -> None:
        """It must carry a `_FAREWELL_MARKERS` phrase, or the caller waits out the silence ladder."""
        from src.core.pipeline import _is_farewell

        assert _is_farewell(BOOKING_DECLINED_FAREWELL)

    async def test_the_pipeline_passes_farewell_after_a_no(self) -> None:
        from tests.unit.test_pipeline_fsm_wire import Harness, fsm_flags

        h = Harness(_session(FSM_OFFER_CALL[:-1], quoted=True))
        with fsm_flags(enabled=False):
            await h.run("Ні дякую")
        assert h.llm_kwargs[-1]["booking_gate_mode"] == GATE_FAREWELL


class TestGreetingIsNotAnOffer:
    def test_yes_to_the_menu_is_not_consent(self) -> None:
        """«так» to «записатися, дізнатися вартість, скасувати…?» picks nothing."""
        assert caller_agreed_to_book([("assistant", GREETING), ("user", "так")]) is False

    def test_the_menu_does_not_use_up_an_offer(self) -> None:
        assert offers_spoken([("assistant", GREETING)]) == 0


class TestExcludedStationPrices:
    """«Камион Aeolus» (000000022) is hidden from the tenant but was in every quote."""

    async def test_an_excluded_station_is_not_quoted(self) -> None:
        from unittest.mock import AsyncMock

        from src.onec_client.client import OneCClient
        from src.store_client.client import StoreClient
        from tests.unit.test_reschedule_state_pin import _onec_mock, _run

        onec = _onec_mock()
        onec.get_fitting_prices = AsyncMock(
            spec=OneCClient.get_fitting_prices,
            return_value={
                "data": [
                    {"city": "Дніпро", "point_id": "000000022", "price": 219},
                    {"city": "Дніпро", "point_id": "000000003", "price": 396},
                ]
            },
        )
        session = CallSession(uuid.uuid4())
        session.excluded_station_ids = {"000000022"}

        result = await _run(session, "get_fitting_price", {}, onec, AsyncMock(spec=StoreClient))

        assert [p["point_id"] for p in result["prices"]] == ["000000003"]


class TestNoDoubleOffer:
    async def test_the_llms_own_offer_is_not_repeated(self) -> None:
        """da525a9a: «Записуємо на шиномонтаж? Записати вас на шиномонтаж?»."""
        heard = await _heard(
            "Записуємо на шиномонтаж? Шини привозите свої з собою чи ті, що у нас на зберіганні?",
            BookingOfferGate(GATE_OFFER),
        )
        assert heard == "Записуємо на шиномонтаж?"

    async def test_after_a_no_the_farewell_still_replaces(self) -> None:
        heard = await _heard(
            "Записуємо на шиномонтаж? Шини привозите свої з собою?",
            BookingOfferGate(GATE_FAREWELL),
        )
        assert heard.endswith(BOOKING_DECLINED_FAREWELL)


class TestAfterANoEveryQuestionEnds:
    async def test_a_city_question_becomes_the_farewell(self) -> None:
        """dd835342: «Ні дякую» → «Підкажіть, у якому місті вас цікавить шиномонтаж?»."""
        heard = await _heard(
            "Підкажіть, у якому місті вас цікавить шиномонтаж?",
            BookingOfferGate(GATE_FAREWELL),
        )
        assert heard == BOOKING_DECLINED_FAREWELL

    async def test_before_a_no_a_city_question_is_left_alone(self) -> None:
        heard = await _heard("У якому місті вас цікавить вартість?", BookingOfferGate(GATE_OFFER))
        assert "місті" in heard
