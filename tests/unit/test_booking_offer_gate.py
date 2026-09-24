"""A price quote is followed by an offer to book, not by the booking itself.

Call 0d7c099e (2026-09-24): the quote ended «У вас легковий чи позашляховик?»,
the caller said «позашляховик», and the bot went on to «Шини привозите свої з
собою чи ті, що у нас на зберіганні?» — a booking the caller never asked for.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from src.agent.booking_consent import BOOKING_OFFER, caller_agreed_to_book, offers_spoken
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
            BookingOfferGate(active=True),
        )
        assert heard.endswith(BOOKING_OFFER)
        assert "зберіганні" not in heard
        assert "438 гривень" in heard

    async def test_the_rest_of_the_turn_is_dropped(self) -> None:
        heard = await _heard(
            "Шини привозите свої з собою? На яку дату вас записати?",
            BookingOfferGate(active=True),
        )
        assert heard == BOOKING_OFFER

    async def test_inactive_gate_changes_nothing(self) -> None:
        text = "Шини привозите свої з собою чи ті, що у нас на зберіганні?"
        heard = await _heard(text, BookingOfferGate(active=False))
        assert "зберіганні" in heard
        assert BOOKING_OFFER not in heard

    async def test_city_and_name_are_not_booking_questions(self) -> None:
        text = "У якому місті вас цікавить вартість? Як до вас звертатися?"
        heard = await _heard(text, BookingOfferGate(active=True))
        assert BOOKING_OFFER not in heard
        assert "місті" in heard

    async def test_a_second_round_neither_offers_again_nor_resumes(self) -> None:
        gate = BookingOfferGate(active=True)
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
        assert _pipeline(_session(PRICE_CALL, quoted=True))._offer_booking_first() is True

    def test_off_without_a_quote(self) -> None:
        assert _pipeline(_session(PRICE_CALL, quoted=False))._offer_booking_first() is False

    def test_off_after_two_offers(self) -> None:
        turns = [
            *PRICE_CALL,
            ("assistant", BOOKING_OFFER),
            ("user", "позашляховик"),
            ("assistant", BOOKING_OFFER),
            ("user", "позашляховик"),
        ]
        assert _pipeline(_session(turns, quoted=True))._offer_booking_first() is False

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
        await loop.run_turn("позашляховик", [], offer_booking_first=True)
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
        assert h.llm_kwargs[-1]["offer_booking_first"] is True

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
