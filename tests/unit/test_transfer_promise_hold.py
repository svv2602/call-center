"""The «I'm connecting you to an operator» sentence must not outrun the guard.

Wave 19 (2026-09-11). `_should_block_false_transfer` already refuses the
hallucinated transfers — that part works. What it could not do was un-say the
sentence, because it runs where the tool executes and `send_audio_stream` has
finished long before that. Over 30 days of production, 12 calls promised an
operator and none arrived; replaying their real customer turns through the real
guard showed it blocking all five reasons on 11 of them. The caller heard the
promise anyway on every one.

The sentence cannot simply move after the tool instead: a successful AMI
redirect tears the channel down before `_close_turn` reaches `TRANSFER_TEXT`, so
in those same 30 days the template was spoken zero times and this sentence was
the *only* thing a genuinely transferred caller heard.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import pytest

from src.agent.streaming_loop import (
    hold_unconfirmed_transfer_promise,
    is_transfer_promise,
)
from src.core.sentence_buffer import SentenceReady
from src.llm.models import (
    StreamDone,
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
    Usage,
)
from src.monitoring.metrics import (
    transfer_promise_suppressed_total,
    transfer_promise_unbacked_total,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from src.core.sentence_buffer import BufferEvent

PROMISE = "Одну секунду, з'єдную вас з оператором."

#: A history the guard blocks for every reason: one customer turn, a bare name,
#: no operator keyword and no escalation. This is call 4065c49d's real shape.
BLOCKED_HISTORY: list[dict[str, Any]] = [
    {"role": "assistant", "content": "Добрий день!"},
    {"role": "user", "content": "Напишите на монтаж Когда у вас свободно"},
]

#: The caller asked for a human in plain words, so `customer_request` passes.
ALLOWED_HISTORY: list[dict[str, Any]] = [
    {"role": "assistant", "content": "Добрий день!"},
    {"role": "user", "content": "переключите меня на оператора"},
]


def _done() -> StreamDone:
    return StreamDone(stop_reason="tool_use", usage=Usage(1, 1))


async def _emit(events: list[BufferEvent]) -> AsyncIterator[BufferEvent]:
    for event in events:
        yield event


async def _collect(events: list[BufferEvent], history: list[dict[str, Any]]) -> list[BufferEvent]:
    return [e async for e in hold_unconfirmed_transfer_promise(_emit(events), history)]


def _spoken(events: list[BufferEvent]) -> list[str]:
    return [e.text for e in events if isinstance(e, SentenceReady)]


def _counter_value(counter: Any, **labels: str) -> float:
    """Read a prometheus_client counter without resetting the global registry."""
    child = counter.labels(**labels) if labels else counter
    return child._value.get()


def _transfer_call(reason: str, tool_id: str = "t1") -> list[BufferEvent]:
    return [
        ToolCallStart(id=tool_id, name="transfer_to_operator"),
        ToolCallDelta(id=tool_id, arguments_chunk=json.dumps({"reason": reason})),
        ToolCallEnd(id=tool_id),
        _done(),
    ]


class TestThePredicateSeparatesPromiseFromMention:
    """Drawn from every distinct operator-mentioning bot sentence in 30 days.

    The tense is the discriminator, not the noun: each positive below has a
    first-person verb that says the connection is happening now, and not one
    negative does — they offer («можу переключити»), recommend, instruct, or
    report that nobody is available.
    """

    @pytest.mark.parametrize(
        "sentence",
        [
            "Одну секунду, з'єдную вас з оператором.",
            "Добре, переключаю на оператора.",
            "Будь ласка, зачекайте — переключаю на оператора.",
            "Зачекайте, будь ласка, з'єдную вас з оператором.",
            "Перекладаю вас на оператора.",
            "Переключую вас на оператора, одну секунду.",
            # The buffer flushes clauses past 25 chars, so a promise can reach
            # the filter already cut in half. Verb and noun survive together.
            "Переключую вас на оператора,",
            "Зараз переключу на спеціаліста.",
            # U+02BC rather than U+0027 — the LLM is not consistent about which
            # apostrophe it emits and the sentence must match either way.
            "Зараз зʼєдную вас з оператором. Залишайтесь на лінії.",
        ],
    )
    def test_promise(self, sentence: str) -> None:
        assert is_transfer_promise(sentence) is True

    @pytest.mark.parametrize(
        "sentence",
        [
            "Для вирішення вашого питання краще поговорити з оператором.",
            "На жаль, зараз оператори недоступні.",
            "Наразі оператори недоступні.",
            "Перепрошую, зараз оператори недоступні.",
            "Ваш номер зафіксовано, ми передзвонимо, як тільки оператори будуть доступні.",
            "Оператори тимчасово недоступні.",
            'Оператор СТО ідентифікує авто за маркою на місці — передам як "колір не назвали".',
            "Рекомендую звернутися до оператора для допомоги.",
            "Якщо потрібно, можу переключити вас на оператора.",
            "Якщо хочете змінити місто, будь ласка, зверніться до оператора.",
            "У якому місті вам зручніше записатися на шиномонтаж?",
        ],
    )
    def test_not_a_promise(self, sentence: str) -> None:
        assert is_transfer_promise(sentence) is False

    def test_a_connecting_verb_alone_is_not_enough(self) -> None:
        """The noun is required because this filter deletes audio.

        «переключаю» could plausibly come to mean switching a station or a city,
        and the cost of a false positive here is a silent turn on a live call.
        """
        assert is_transfer_promise("Переключаю на іншу точку.") is False


class TestTheGuardVerdictDecidesWhetherItIsSpoken:
    @pytest.mark.asyncio
    async def test_blocked_transfer_swallows_the_promise(self) -> None:
        events = [SentenceReady(text=PROMISE), *_transfer_call("customer_request")]
        out = await _collect(events, BLOCKED_HISTORY)
        assert _spoken(out) == []

    @pytest.mark.asyncio
    async def test_allowed_transfer_still_speaks_it(self) -> None:
        """The load-bearing half. Silencing this is worse than the bug."""
        events = [SentenceReady(text=PROMISE), *_transfer_call("customer_request")]
        out = await _collect(events, ALLOWED_HISTORY)
        assert _spoken(out) == [PROMISE]

    @pytest.mark.asyncio
    async def test_a_suppression_is_counted_under_its_reason(self) -> None:
        before = _counter_value(transfer_promise_suppressed_total, reason="cannot_help")
        events = [SentenceReady(text=PROMISE), *_transfer_call("cannot_help")]
        await _collect(events, BLOCKED_HISTORY)
        after = _counter_value(transfer_promise_suppressed_total, reason="cannot_help")
        assert after == before + 1

    @pytest.mark.asyncio
    async def test_the_tool_call_itself_is_never_swallowed(self) -> None:
        """Only the sentence is withheld — the transfer must still execute.

        The guard blocks it again downstream where the tool runs; that is its
        job. If this filter ate the ToolCallEnd, an *allowed* transfer would
        stop happening too.
        """
        events = [SentenceReady(text=PROMISE), *_transfer_call("customer_request")]
        out = await _collect(events, BLOCKED_HISTORY)
        assert [type(e) for e in out] == [ToolCallStart, ToolCallDelta, ToolCallEnd, StreamDone]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "reason",
        [
            "customer_request",
            "cannot_help",
            "negative_emotion",
            "complex_question",
            "non_fitting_scope",
        ],
    )
    async def test_every_reason_the_guard_blocks(self, reason: str) -> None:
        """Wave 16 made the guard default-deny; this must not reintroduce a subset."""
        events = [SentenceReady(text=PROMISE), *_transfer_call(reason)]
        out = await _collect(events, BLOCKED_HISTORY)
        assert _spoken(out) == []


class TestReleasingIsTheDefault:
    @pytest.mark.asyncio
    async def test_a_promise_with_no_tool_call_is_still_spoken(self) -> None:
        """Call 18e96042: the caller asked for a human and the LLM only said so.

        Dropping this would trade an untrue turn for a silent one — no tool ran,
        so there is no wait-phrase to cover the gap. It is counted instead
        (`transfer_promise_unbacked_total`) so the shape stays visible.
        """
        events = [SentenceReady(text="Добре, переключаю на оператора."), _done()]
        out = await _collect(events, ALLOWED_HISTORY)
        assert _spoken(out) == ["Добре, переключаю на оператора."]

    @pytest.mark.asyncio
    async def test_an_unbacked_promise_is_counted(self) -> None:
        """Releasing it is a deliberate choice, so it may not also be silent.

        This counter is the only trace the prose-only shape leaves; without it
        the decision to let it through would be unreviewable.
        """
        before = _counter_value(transfer_promise_unbacked_total)
        await _collect([SentenceReady(text=PROMISE), _done()], ALLOWED_HISTORY)
        assert _counter_value(transfer_promise_unbacked_total) == before + 1

    @pytest.mark.asyncio
    async def test_a_backed_promise_is_not_counted_as_unbacked(self) -> None:
        before = _counter_value(transfer_promise_unbacked_total)
        events = [SentenceReady(text=PROMISE), *_transfer_call("customer_request")]
        await _collect(events, ALLOWED_HISTORY)
        assert _counter_value(transfer_promise_unbacked_total) == before

    @pytest.mark.asyncio
    async def test_a_different_tool_behind_the_promise_releases_it(self) -> None:
        events = [
            SentenceReady(text=PROMISE),
            ToolCallStart(id="t9", name="get_fitting_stations"),
            ToolCallDelta(id="t9", arguments_chunk='{"city": "Дніпро"}'),
            ToolCallEnd(id="t9"),
            _done(),
        ]
        out = await _collect(events, BLOCKED_HISTORY)
        assert _spoken(out) == [PROMISE]

    @pytest.mark.asyncio
    async def test_unparseable_arguments_are_judged_not_waved_through(self) -> None:
        """Truncated JSON must not become an escape hatch.

        An empty reason is still a reason the default-deny guard rules on, so
        a promise behind mangled arguments is withheld rather than released.
        """
        events = [
            SentenceReady(text=PROMISE),
            ToolCallStart(id="t1", name="transfer_to_operator"),
            ToolCallDelta(id="t1", arguments_chunk='{"reason": "customer_'),
            ToolCallEnd(id="t1"),
            _done(),
        ]
        out = await _collect(events, BLOCKED_HISTORY)
        assert _spoken(out) == []


class TestNothingElseIsDisturbed:
    @pytest.mark.asyncio
    async def test_a_turn_without_a_promise_passes_through_unchanged(self) -> None:
        events: list[BufferEvent] = [
            SentenceReady(text="У якому місті вам зручніше записатися на шиномонтаж?"),
            ToolCallStart(id="t9", name="get_fitting_stations"),
            ToolCallDelta(id="t9", arguments_chunk="{}"),
            ToolCallEnd(id="t9"),
            _done(),
        ]
        out = await _collect(events, BLOCKED_HISTORY)
        assert out == events

    @pytest.mark.asyncio
    async def test_sentences_queued_behind_the_promise_keep_their_order(self) -> None:
        """Holding must not reorder the turn.

        Everything after the promise waits with it, so an allowed transfer
        replays the turn exactly as the LLM composed it.
        """
        events = [
            SentenceReady(text="Розумію."),
            SentenceReady(text=PROMISE),
            SentenceReady(text="Зачекайте, будь ласка."),
            *_transfer_call("customer_request"),
        ]
        out = await _collect(events, ALLOWED_HISTORY)
        assert _spoken(out) == ["Розумію.", PROMISE, "Зачекайте, будь ласка."]

    @pytest.mark.asyncio
    async def test_a_blocked_promise_does_not_take_its_neighbours_with_it(self) -> None:
        events = [
            SentenceReady(text="Розумію."),
            SentenceReady(text=PROMISE),
            *_transfer_call("customer_request"),
        ]
        out = await _collect(events, BLOCKED_HISTORY)
        assert _spoken(out) == ["Розумію."]


class TestTheFilterIsActuallyWiredIntoTheTurn:
    """A corpus test on the predicate does not cover the call site.

    `run_turn` builds the speech path itself, and an `elif False:` at the seam
    would leave every test above green while production spoke the promise
    unchanged. These drive the real loop and assert on what TTS was handed.
    """

    @pytest.mark.asyncio
    async def test_blocked_promise_never_reaches_tts(self) -> None:
        loop, _router, _tools, _conn, tts = _build_promising_loop()
        await loop.run_turn("Напишите на монтаж Когда у вас свободно", [])
        assert not any(is_transfer_promise(t) for t in tts.texts)

    @pytest.mark.asyncio
    async def test_allowed_promise_does_reach_tts(self) -> None:
        loop, _router, _tools, _conn, tts = _build_promising_loop()
        await loop.run_turn("переключите меня на оператора", [])
        assert any(is_transfer_promise(t) for t in tts.texts)


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


def _build_promising_loop() -> tuple[Any, Any, Any, Any, _RecordingTTS]:
    from src.agent.agent import ToolRouter
    from src.agent.streaming_loop import StreamingAgentLoop
    from src.llm.models import TextDelta
    from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection
    from tests.unit.mocks.mock_llm_router import MockLLMRouter

    args = json.dumps({"reason": "customer_request", "summary": "хоче оператора"})
    responses = [
        [
            TextDelta(text=PROMISE + " "),
            ToolCallStart(id="t1", name="transfer_to_operator"),
            ToolCallDelta(id="t1", arguments_chunk=args),
            ToolCallEnd(id="t1"),
            StreamDone(stop_reason="tool_use", usage=Usage(1, 1)),
        ],
        [
            TextDelta(text="Добре."),
            StreamDone(stop_reason="end_turn", usage=Usage(1, 1)),
        ],
    ]
    router = MockLLMRouter(responses)
    tool_router = ToolRouter()

    async def _transfer(**_kwargs: Any) -> dict[str, str]:
        return {"status": "transferring", "message": "З'єдную з оператором"}

    tool_router.register("transfer_to_operator", _transfer)
    tts = _RecordingTTS()
    conn = MockAudioSocketConnection()
    loop = StreamingAgentLoop(
        llm_router=router,
        tool_router=tool_router,
        tts=tts,
        conn=conn,
        barge_in_event=asyncio.Event(),
        system_prompt="Test system prompt",
    )
    return loop, router, tool_router, conn, tts
