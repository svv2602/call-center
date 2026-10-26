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


class TestAnUnbackedPromiseTurnsOnWhatFollowsIt:
    """Prose promises split in two, and the two halves deserve opposite answers.

    Replaying the 14 such turns from the 21 days to 2026-09-16 through this
    filter puts 12 in the first shape and 2 in the second.
    """

    @pytest.mark.asyncio
    async def test_a_promise_used_as_an_opener_is_dropped(self) -> None:
        """Call 53761bd9, verbatim: «Одну секунду, з'єдную вас з оператором.
        Як до вас звертатися?» — and then the bot carried straight on with the
        booking. The caller should hear the question and not the promise."""
        question = "Як до вас звертатися?"
        events = [SentenceReady(text=PROMISE), SentenceReady(text=question), _done()]
        out = await _collect(events, ALLOWED_HISTORY)
        assert _spoken(out) == [question]

    @pytest.mark.asyncio
    async def test_a_promise_that_is_the_whole_turn_is_still_spoken(self) -> None:
        """Call 18e96042: the caller asked for a human and the LLM only said so.

        Here dropping really would trade an untrue turn for a silent one — no
        tool ran, so there is no wait-phrase to cover the gap.
        """
        events = [SentenceReady(text="Добре, переключаю на оператора."), _done()]
        out = await _collect(events, ALLOWED_HISTORY)
        assert _spoken(out) == ["Добре, переключаю на оператора."]

    @pytest.mark.asyncio
    async def test_a_second_promise_is_not_content_to_keep_the_first_for(self) -> None:
        """Two ways of saying the same untrue thing still leave nothing behind."""
        events = [
            SentenceReady(text=PROMISE),
            SentenceReady(text="Переключаю на оператора."),
            _done(),
        ]
        out = await _collect(events, ALLOWED_HISTORY)
        assert _spoken(out) == [PROMISE, "Переключаю на оператора."]

    @pytest.mark.asyncio
    async def test_a_dropped_promise_moves_only_the_dropped_counter(self) -> None:
        """Asserted one label at a time on purpose: incrementing both by one is
        the same arithmetic whether or not the labels are the right way round."""
        before_dropped = _counter_value(transfer_promise_unbacked_total, outcome="dropped")
        before_spoken = _counter_value(transfer_promise_unbacked_total, outcome="spoken")
        await _collect(
            [SentenceReady(text=PROMISE), SentenceReady(text="Дніпро?"), _done()],
            ALLOWED_HISTORY,
        )
        assert _counter_value(
            transfer_promise_unbacked_total, outcome="dropped"
        ) == before_dropped + 1
        assert _counter_value(
            transfer_promise_unbacked_total, outcome="spoken"
        ) == before_spoken

    @pytest.mark.asyncio
    async def test_a_spoken_promise_moves_only_the_spoken_counter(self) -> None:
        before_dropped = _counter_value(transfer_promise_unbacked_total, outcome="dropped")
        before_spoken = _counter_value(transfer_promise_unbacked_total, outcome="spoken")
        await _collect([SentenceReady(text=PROMISE), _done()], ALLOWED_HISTORY)
        assert _counter_value(
            transfer_promise_unbacked_total, outcome="spoken"
        ) == before_spoken + 1
        assert _counter_value(
            transfer_promise_unbacked_total, outcome="dropped"
        ) == before_dropped

    @pytest.mark.asyncio
    async def test_a_backed_promise_is_not_counted_as_unbacked(self) -> None:
        before = sum(
            _counter_value(transfer_promise_unbacked_total, outcome=o)
            for o in ("dropped", "spoken")
        )
        events = [SentenceReady(text=PROMISE), *_transfer_call("customer_request")]
        await _collect(events, ALLOWED_HISTORY)
        assert (
            sum(
                _counter_value(transfer_promise_unbacked_total, outcome=o)
                for o in ("dropped", "spoken")
            )
            == before
        )

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


# ── The production provider sends no ToolCallEnd (2026-10-26) ─────────
#
# Every test above builds its stream in the Anthropic shape, with a
# ToolCallEnd. The production agent is `openai-gpt41-mini`, streamed through
# `OpenAIStreamParser`, which never emits one — and the verdict used to be
# taken on ToolCallEnd alone. Over 30 days: suppressed — no series,
# unbacked{dropped} 0, unbacked{spoken} 25. The streams below are produced by
# the real parser from OpenAI SSE chunks and go through the real sentence
# buffer, so the event shape is the provider's, not the test's.

#: Verbatim bot turn, call 2b0e76de (2026-10-05 06:42): the guard blocked
#: `transfer_to_operator(non_fitting_scope)` and the caller heard this anyway.
PROD_TURN = "Одну секунду, з'єдную вас з оператором. Як до вас звертатися?"


def _openai_events(text: str, transfer_args: dict[str, Any] | None) -> list[Any]:
    """Run OpenAI SSE chunks through the real parser, the way the provider does."""
    from src.llm.format_converter import OpenAIStreamParser

    chunks: list[dict[str, Any]] = [
        {"choices": [{"delta": {"content": text[i : i + 6]}}]} for i in range(0, len(text), 6)
    ]
    if transfer_args is not None:
        args = json.dumps(transfer_args, ensure_ascii=False)
        head = {
            "index": 0,
            "id": "call_1",
            "function": {"name": "transfer_to_operator", "arguments": ""},
        }
        chunks.append({"choices": [{"delta": {"tool_calls": [head]}}]})
        for i in range(0, len(args), 8):
            piece = {"index": 0, "function": {"arguments": args[i : i + 8]}}
            chunks.append({"choices": [{"delta": {"tool_calls": [piece]}}]})
        chunks.append({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]})
    else:
        chunks.append({"choices": [{"delta": {}, "finish_reason": "stop"}]})
    chunks.append({"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
    parser = OpenAIStreamParser("openai-gpt41-mini", "gpt-4.1-mini")
    events: list[Any] = []
    for chunk in chunks:
        events.extend(parser.feed(chunk))
    events.extend(parser.flush())
    return events


async def _through_buffer(events: list[Any], history: list[dict[str, Any]]) -> list[BufferEvent]:
    from src.core.sentence_buffer import buffer_sentences

    async def _llm() -> AsyncIterator[Any]:
        for event in events:
            yield event

    return [e async for e in hold_unconfirmed_transfer_promise(buffer_sentences(_llm()), history)]


def _promises(events: list[BufferEvent]) -> list[str]:
    return [t for t in _spoken(events) if is_transfer_promise(t)]


def _unbacked_total() -> float:
    return sum(
        _counter_value(transfer_promise_unbacked_total, outcome=o) for o in ("dropped", "spoken")
    )


class TestTheVerdictDoesNotWaitForAToolCallEndThatNeverComes:
    def test_the_production_parser_really_sends_no_tool_call_end(self) -> None:
        """The premise. If OpenAIStreamParser ever starts emitting ToolCallEnd,
        the end-of-stream verdict becomes a fallback and this test says so."""
        events = _openai_events(PROMISE, {"reason": "non_fitting_scope"})
        assert ToolCallStart in {type(e) for e in events}
        assert ToolCallEnd not in {type(e) for e in events}

    @pytest.mark.asyncio
    async def test_blocked_transfer_in_the_promise_round_is_not_heard(self) -> None:
        """The production shape: round 1 is the promise plus the transfer the
        guard refuses; the question comes in round 2 after the refusal."""
        events = _openai_events(PROMISE, {"reason": "non_fitting_scope"})
        out = await _through_buffer(events, BLOCKED_HISTORY)
        assert _promises(out) == []

    @pytest.mark.asyncio
    async def test_blocked_transfer_is_counted_as_suppressed(self) -> None:
        before = _counter_value(transfer_promise_suppressed_total, reason="non_fitting_scope")
        events = _openai_events(PROMISE, {"reason": "non_fitting_scope"})
        await _through_buffer(events, BLOCKED_HISTORY)
        after = _counter_value(transfer_promise_suppressed_total, reason="non_fitting_scope")
        assert after == before + 1

    @pytest.mark.asyncio
    async def test_blocked_transfer_is_not_counted_as_unbacked(self) -> None:
        """Before the fix this exact turn landed in unbacked{spoken}."""
        before = _unbacked_total()
        events = _openai_events(PROMISE, {"reason": "non_fitting_scope"})
        await _through_buffer(events, BLOCKED_HISTORY)
        assert _unbacked_total() == before

    @pytest.mark.asyncio
    async def test_blocked_transfer_keeps_the_question_of_the_same_round(self) -> None:
        events = _openai_events(PROD_TURN, {"reason": "non_fitting_scope"})
        out = await _through_buffer(events, BLOCKED_HISTORY)
        assert _promises(out) == []
        assert "Як до вас звертатися?" in _spoken(out)

    @pytest.mark.asyncio
    async def test_the_tool_call_still_reaches_the_executor(self) -> None:
        events = _openai_events(PROMISE, {"reason": "non_fitting_scope"})
        out = await _through_buffer(events, BLOCKED_HISTORY)
        assert [e.name for e in out if isinstance(e, ToolCallStart)] == ["transfer_to_operator"]
        assert isinstance(out[-1], StreamDone)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("sales_enabled", "promised"), [(False, True), (True, False)])
    async def test_the_verdict_follows_the_networks_scope(
        self, sales_enabled: bool, promised: bool
    ) -> None:
        """Buying is off-topic for a fitting-only network and the bot's own topic
        under sales, so the same turn is a transfer in one and a refusal in the other."""
        from src.core.sentence_buffer import buffer_sentences

        history = [
            {"role": "assistant", "content": "Добрий день!"},
            {"role": "user", "content": "хочу купити шини"},
        ]
        events = _openai_events(PROD_TURN, {"reason": "non_fitting_scope"})

        async def _llm() -> AsyncIterator[Any]:
            for event in events:
                yield event

        out = [
            e
            async for e in hold_unconfirmed_transfer_promise(
                buffer_sentences(_llm()), history, sales_enabled=sales_enabled
            )
        ]
        assert bool(_promises(out)) is promised

    @pytest.mark.asyncio
    async def test_allowed_transfer_still_speaks_the_promise(self) -> None:
        """AMI redirect drops the channel before TRANSFER_TEXT: this sentence is
        all a transferred caller hears."""
        events = _openai_events(PROD_TURN, {"reason": "customer_request"})
        out = await _through_buffer(events, ALLOWED_HISTORY)
        assert _promises(out) == ["з'єдную вас з оператором."]
        assert "Як до вас звертатися?" in _spoken(out)

    @pytest.mark.asyncio
    async def test_allowed_transfer_is_not_counted_as_unbacked(self) -> None:
        """Before the fix a successful transfer counted as unbacked — the metric
        could not tell a real handoff from prose."""
        before = _unbacked_total()
        events = _openai_events(PROD_TURN, {"reason": "customer_request"})
        await _through_buffer(events, ALLOWED_HISTORY)
        assert _unbacked_total() == before

    @pytest.mark.asyncio
    async def test_a_stream_cut_before_stream_done_is_still_judged(self) -> None:
        """No StreamDone either (a provider that stops mid-way): judge what came."""
        events = [
            e
            for e in _openai_events(PROMISE, {"reason": "non_fitting_scope"})
            if not isinstance(e, StreamDone)
        ]
        out = await _through_buffer(events, BLOCKED_HISTORY)
        assert _promises(out) == []
        assert [e.name for e in out if isinstance(e, ToolCallStart)] == ["transfer_to_operator"]

    @pytest.mark.asyncio
    async def test_a_transfer_already_judged_is_not_judged_again(self) -> None:
        """A second promise after a judged transfer must not re-count the refusal.

        The end-of-stream verdict looks at every transfer the round named; one
        already ruled on at its ToolCallEnd is not asked about twice.
        """
        before = _counter_value(transfer_promise_suppressed_total, reason="cannot_help")
        events = [
            SentenceReady(text=PROMISE),
            ToolCallStart(id="t1", name="transfer_to_operator"),
            ToolCallDelta(id="t1", arguments_chunk=json.dumps({"reason": "cannot_help"})),
            ToolCallEnd(id="t1"),
            SentenceReady(text="Переключаю на оператора."),
            _done(),
        ]
        await _collect(events, BLOCKED_HISTORY)
        after = _counter_value(transfer_promise_suppressed_total, reason="cannot_help")
        assert after == before + 1

    @pytest.mark.asyncio
    async def test_a_promise_with_no_transfer_is_still_judged_as_prose(self) -> None:
        """Round 2 after a refusal, no tool call of its own: unchanged path."""
        out = await _through_buffer(_openai_events(PROD_TURN, None), BLOCKED_HISTORY)
        assert _promises(out) == []
        assert "Як до вас звертатися?" in _spoken(out)


def _build_openai_loop(rounds: list[list[Any]]) -> tuple[Any, _RecordingTTS, list[str]]:
    from src.agent.agent import ToolRouter
    from src.agent.streaming_loop import StreamingAgentLoop
    from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection
    from tests.unit.mocks.mock_llm_router import MockLLMRouter

    executed: list[str] = []
    tool_router = ToolRouter()

    async def _transfer(**_kwargs: Any) -> dict[str, str]:
        executed.append("transfer_to_operator")
        return {"status": "transferring", "message": "З'єдную з оператором"}

    tool_router.register("transfer_to_operator", _transfer)
    tts = _RecordingTTS()
    loop = StreamingAgentLoop(
        llm_router=MockLLMRouter(rounds),
        tool_router=tool_router,
        tts=tts,
        conn=MockAudioSocketConnection(),
        barge_in_event=asyncio.Event(),
        system_prompt="Test system prompt",
    )
    return loop, tts, executed


class TestTheTurnWithTheProductionProvider:
    """`run_turn` driven with OpenAI-shaped rounds: the wiring at the call site."""

    @pytest.mark.asyncio
    async def test_blocked_promise_never_reaches_tts(self) -> None:
        loop, tts, executed = _build_openai_loop(
            [
                _openai_events(PROMISE, {"reason": "customer_request"}),
                _openai_events("Як до вас звертатися?", None),
            ]
        )
        await loop.run_turn("Напишите на монтаж Когда у вас свободно", [])
        assert not any(is_transfer_promise(t) for t in tts.texts)
        assert any("Як до вас звертатися?" in t for t in tts.texts)
        assert executed == []

    @pytest.mark.asyncio
    async def test_allowed_promise_reaches_tts_and_the_transfer_runs(self) -> None:
        loop, tts, executed = _build_openai_loop(
            [
                _openai_events(PROMISE, {"reason": "customer_request"}),
                _openai_events("Добре.", None),
            ]
        )
        await loop.run_turn("переключите меня на оператора", [])
        assert any(is_transfer_promise(t) for t in tts.texts)
        assert executed == ["transfer_to_operator"]


class TestTheLoggedTurnWithTheProductionProvider:
    """Through the shared `_build_loop` of the streaming-loop tests, asserting on
    `TurnResult.spoken_text` — the text `call_turns` stores for the bot, built
    from what was actually sent as audio."""

    @staticmethod
    def _loop(rounds: list[list[Any]]) -> tuple[Any, list[str]]:
        from tests.unit.test_streaming_loop import _build_loop

        loop, _router, tool_router, _conn = _build_loop(rounds)
        executed: list[str] = []

        async def _transfer(**_kwargs: Any) -> dict[str, str]:
            executed.append("transfer_to_operator")
            return {"status": "transferring", "message": "З'єдную з оператором"}

        tool_router.register("transfer_to_operator", _transfer)
        return loop, executed

    @pytest.mark.asyncio
    async def test_the_logged_turn_of_a_blocked_transfer_has_no_promise(self) -> None:
        """Call 2b0e76de's turn, rebuilt round by round as production ran it."""
        loop, executed = self._loop(
            [
                _openai_events(PROMISE, {"reason": "non_fitting_scope"}),
                _openai_events("Як до вас звертатися?", None),
            ]
        )
        result = await loop.run_turn("от", [])
        assert "з'єдную" not in result.spoken_text
        assert "Як до вас звертатися?" in result.spoken_text
        assert executed == []

    @pytest.mark.asyncio
    async def test_the_logged_turn_of_an_allowed_transfer_keeps_it(self) -> None:
        loop, executed = self._loop(
            [
                _openai_events(PROMISE, {"reason": "customer_request"}),
                _openai_events("Добре.", None),
            ]
        )
        result = await loop.run_turn("переключите меня на оператора", [])
        assert "з'єдную вас з оператором" in result.spoken_text
        assert executed == ["transfer_to_operator"]
