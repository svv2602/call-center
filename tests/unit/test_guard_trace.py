"""The guards that fired on a bot reply are written on that reply's row.

`call_turns.guards_fired` (migration 069): a JSON list on the row of a reply the
model produced — `[]` when no guard fired — and SQL `NULL` on every other row
(the caller's turn, a phrase spoken by code).

Three layers, each with its own tests, because a test of one does not see a
break in another:

* the collector (`src/agent/guard_trace.py`) — turn boundary, isolation;
* the write (`CallLogger.log_turn`) — `NULL` against `[]`, and a trace that
  cannot be written never costs the turn itself;
* the wiring — a real guard fires inside a turn driven through
  `CallPipeline._transcript_processor_loop` and a real `CallLogger`, and the
  statement that would reach PostgreSQL is read back. Every such test checks
  *whose* code landed, not that «something» did: a neighbouring guard hides
  both a missing one and a broken one.

No network, no database, no Redis: the logger's session factory is replaced by
a recorder of statements.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import copy
import json
import logging
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import ProgrammingError

from src.agent import guard_trace
from src.agent import tire_claim_guard as tire_guard
from src.agent.streaming_loop import TurnResult, control_plane_syntax, drop_control_plane_prose
from src.agent.tire_claim_guard import (
    REPLACEMENTS,
    RULE_CHEAPEST,
    TireClaimState,
    guard_tire_claims,
    guard_tire_claims_text,
)
from src.core.call_session import MAX_TIMEOUTS_BEFORE_HANGUP, CallSession
from src.core.pipeline import _FAREWELL_MIN_TURNS, FAREWELL_TEXT, _strip_filler
from src.core.sentence_buffer import SentenceReady
from src.llm.models import Usage
from src.logging.call_logger import CallLogger
from src.stt.base import Transcript
from tests.unit.test_pipeline_fsm_wire import Harness

ROOT = Path(__file__).resolve().parents[2]

# ── The stand ────────────────────────────────────────────────────────────

#: The shown tyres, in the live shape of `StoreClient._tire_item` — the prices
#: of `tests/unit/test_tire_claim_guard.py`, where the lie below was refused.
ITEMS = [
    {
        "id": id_,
        "brand": brand,
        "model": model,
        "size": "235/55 R18",
        "season": "winter",
        "price": price,
        "in_stock": True,
    }
    for id_, brand, model, price in [
        ("1", "Bridgestone", "Blizzak 6", 8625),
        ("3", "CrossWind", "Grip Peak Winter", 4314),
        ("5", "Doublestar", "WINTERKING DW02", 3606),
    ]
]
ASKED = "зимові шини 235/55 R18 на Тігуан"
CHEAP_LIE = "CrossWind Grip Peak Winter — найдешевша серед показаних."
OFFER = "Є Bridgestone Blizzak 6 за 8625 гривень."
MACHINERY = 'transfer_to_operator(reason="customer_request")'
PLAIN = "Добре, підкажіть, будь ласка, місто."

TIRE_ENTRY = {"guard": "tire_claim_refused", "rule": RULE_CHEAPEST}


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(tire_guard.ENV_FLAG, raising=False)
    guard_trace.discard()
    yield
    guard_trace.discard()


def _tire_state() -> TireClaimState:
    state = TireClaimState(sales_enabled=True)
    state.note("search_tires", {"total": len(ITEMS), "items": copy.deepcopy(ITEMS)})
    return state


class _Tx:
    async def __aenter__(self) -> _Tx:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _Session:
    def __init__(self, recorder: StatementRecorder) -> None:
        self._recorder = recorder

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def begin(self) -> _Tx:
        return _Tx()

    async def execute(self, clause: Any, params: dict[str, Any]) -> None:
        sql = str(clause)
        self._recorder.attempts.append((sql, params))
        error = self._recorder.fail(sql) if self._recorder.fail is not None else None
        if error is not None:
            raise error
        self._recorder.written.append((sql, params))


class StatementRecorder:
    """Stands in for `async_sessionmaker`: keeps each statement instead of sending it.

    Hand-written rather than a mock on purpose — an attribute the logger does
    not really use raises `AttributeError` here instead of answering.
    """

    def __init__(self, fail: Any = None) -> None:
        self.attempts: list[tuple[str, dict[str, Any]]] = []
        self.written: list[tuple[str, dict[str, Any]]] = []
        self.fail = fail

    def __call__(self) -> _Session:
        return _Session(self)

    def rows(self) -> list[tuple[str, str, Any]]:
        """(speaker, content, guards_fired as PostgreSQL would store it) per written turn."""
        return [(p["speaker"], p["content"], _stored_trace(sql, p)) for sql, p in self.written]


def _stored_trace(sql: str, params: dict[str, Any]) -> Any:
    """What the statement puts in `guards_fired`: None for SQL NULL."""
    if "guards_fired" not in sql:
        assert "guards_fired" not in params
        return None
    assert "CAST(:guards_fired AS jsonb)" in sql
    raw = params["guards_fired"]
    if raw is None:
        return None
    assert isinstance(raw, str), "JSONB goes in as JSON text, cast in SQL"
    return json.loads(raw)


def _logger(recorder: StatementRecorder, redis: Any = None) -> CallLogger:
    # The engine is lazy: nothing connects until a statement is sent, and none is.
    call_logger = CallLogger("postgresql+asyncpg://u:p@127.0.0.1:1/none", redis=redis)
    call_logger._session_factory = recorder  # type: ignore[assignment]
    return call_logger


def _missing_column(sql: str) -> Exception | None:
    """PostgreSQL before migration 069, as SQLAlchemy reports it."""
    if "guards_fired" not in sql:
        return None
    return ProgrammingError(
        sql,
        {"content": "<the reply>"},
        Exception('column "guards_fired" of relation "call_turns" does not exist'),
    )


# ── The collector ────────────────────────────────────────────────────────


class TestCollector:
    def test_no_turn_no_trace(self) -> None:
        assert guard_trace.take() is None

    def test_a_turn_starts_empty(self) -> None:
        guard_trace.begin_turn("c1")
        assert guard_trace.take() == []

    def test_record_outside_a_turn_is_dropped_and_keeps_nothing(self) -> None:
        for _ in range(1000):
            guard_trace.record("tire_claim_refused", rule="cheapest")
        assert guard_trace._current.get() is None
        # …and does not surface on the turn that starts afterwards.
        guard_trace.begin_turn("c1")
        assert guard_trace.take() == []

    def test_entry_is_the_code_and_the_counter_labels(self) -> None:
        guard_trace.begin_turn("c1")
        guard_trace.record("control_plane_prose_dropped", form="json_args", site="stream")
        assert guard_trace.take() == [
            {"guard": "control_plane_prose_dropped", "form": "json_args", "site": "stream"}
        ]

    def test_a_guard_that_fired_twice_is_two_entries(self) -> None:
        guard_trace.begin_turn("c1")
        guard_trace.record("tire_claim_refused", rule="cheapest")
        guard_trace.record("tire_claim_refused", rule="cheapest")
        assert guard_trace.take() == [TIRE_ENTRY, TIRE_ENTRY]

    def test_take_closes_the_turn(self) -> None:
        guard_trace.begin_turn("c1")
        guard_trace.record("tire_claim_refused", rule="cheapest")
        taken = guard_trace.take()
        guard_trace.record("network_claim_blocked", rule="late")
        assert taken == [TIRE_ENTRY]
        assert guard_trace.take() is None

    def test_next_turn_starts_empty_after_a_written_one(self) -> None:
        guard_trace.begin_turn("c1")
        guard_trace.record("tire_claim_refused", rule="cheapest")
        guard_trace.take()
        guard_trace.begin_turn("c1")
        assert guard_trace.take() == []

    def test_a_turn_nobody_took_does_not_leak_into_the_next(self) -> None:
        """Barge-in before the bot spoke: no bot row, and the trace stays open."""
        guard_trace.begin_turn("c1")
        guard_trace.record("tire_claim_refused", rule="cheapest")
        guard_trace.begin_turn("c1")
        assert guard_trace.take() == []

    def test_values_are_short_strings_whatever_came_in(self) -> None:
        """`false_transfer_blocked.reason` is an argument the model wrote."""
        guard_trace.begin_turn("c1")
        guard_trace.record("false_transfer_blocked", reason="x" * 500)
        guard_trace.record("bot_filler_stripped", pattern=7)
        first, second = guard_trace.take() or []
        assert first == {"guard": "false_transfer_blocked", "reason": "x" * 80}
        assert second == {"guard": "bot_filler_stripped", "pattern": "7"}

    def test_a_label_cannot_overwrite_the_guard_code(self) -> None:
        guard_trace.begin_turn("c1")
        guard_trace.record("tire_claim_refused", **{"guard": "something_else"})
        assert guard_trace.take() == [{"guard": "tire_claim_refused"}]

    def test_record_never_raises_into_the_guard(self, caplog: pytest.LogCaptureFixture) -> None:
        class Unprintable:
            def __str__(self) -> str:
                raise RuntimeError("boom")

        guard_trace.begin_turn("call-77")
        with caplog.at_level(logging.WARNING, logger="src.agent.guard_trace"):
            guard_trace.record("tire_claim_refused", rule=Unprintable())
        assert guard_trace.take() == []
        lost = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(lost) == 1
        assert "call-77" in lost[0].getMessage()
        assert "tire_claim_refused" in lost[0].getMessage()

    async def test_a_child_task_writes_into_its_parents_turn(self) -> None:
        """Tools run under `gather`, TTS in its own task: both are children of the turn."""
        guard_trace.begin_turn("c1")

        async def child() -> None:
            await asyncio.sleep(0)
            guard_trace.record("tire_claim_refused", rule="cheapest")

        await asyncio.create_task(child())
        await asyncio.gather(child(), child())
        assert guard_trace.take() == [TIRE_ENTRY] * 3

    async def test_a_task_older_than_the_turn_is_outside_it(self) -> None:
        """The limit of the design, pinned so that it is not discovered in prod.

        A task copies the context when it is created. One created before
        `begin_turn()` never sees the turn: its `record()` is dropped. No guard
        runs in such a task today (`_audio_reader_loop`, `_keepalive_loop`,
        `_transcript_reader_loop` carry none); a guard moved into one would
        need the trace handed to it explicitly.
        """
        go = asyncio.Event()

        async def older() -> None:
            await go.wait()
            guard_trace.record("tire_claim_refused", rule="cheapest")

        task = asyncio.create_task(older())
        await asyncio.sleep(0)
        guard_trace.begin_turn("c1")
        go.set()
        await task
        assert guard_trace.take() == []

    async def test_two_calls_at_once_do_not_see_each_other(self) -> None:
        async def call(call_id: str, fires: int) -> list[dict[str, str]] | None:
            guard_trace.begin_turn(call_id)
            for _ in range(fires):
                await asyncio.sleep(0)
                guard_trace.record("tire_claim_refused", rule=call_id)
            await asyncio.sleep(0)
            return guard_trace.take()

        first, second, quiet = await asyncio.gather(call("a", 2), call("b", 1), call("c", 0))
        assert first == [{"guard": "tire_claim_refused", "rule": "a"}] * 2
        assert second == [{"guard": "tire_claim_refused", "rule": "b"}]
        assert quiet == []


# ── The write ────────────────────────────────────────────────────────────


class TestLogTurn:
    CALL = uuid.UUID("11111111-2222-3333-4444-555555555555")

    async def test_no_trace_is_the_statement_as_before(self) -> None:
        """NULL, and on a database without the column this row still goes in."""
        rec = StatementRecorder()
        await _logger(rec).log_turn(self.CALL, 0, "customer", "Алло")
        ((sql, params),) = rec.written
        assert "guards_fired" not in sql
        assert "guards_fired" not in params
        assert rec.rows() == [("customer", "Алло", None)]

    async def test_empty_trace_is_an_empty_list_not_null(self) -> None:
        rec = StatementRecorder()
        await _logger(rec).log_turn(self.CALL, 1, "bot", PLAIN, guards_fired=[])
        ((sql, params),) = rec.written
        assert "CAST(:guards_fired AS jsonb)" in sql
        assert params["guards_fired"] == "[]"
        assert rec.rows() == [("bot", PLAIN, [])]

    async def test_trace_goes_in_as_json(self) -> None:
        trace = [TIRE_ENTRY, {"guard": "bot_filler_stripped", "pattern": "opener:зараз перевірю"}]
        rec = StatementRecorder()
        await _logger(rec).log_turn(self.CALL, 1, "bot", PLAIN, guards_fired=trace)
        assert rec.rows() == [("bot", PLAIN, trace)]
        assert "зараз перевірю" in rec.written[0][1]["guards_fired"]  # not \\u-escaped

    async def test_missing_column_keeps_the_turn_and_says_why(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Code out before migration 069: the reply is written, the trace is not."""
        rec = StatementRecorder(fail=_missing_column)
        call_logger = _logger(rec)
        with caplog.at_level(logging.WARNING, logger="src.logging.call_logger"):
            await call_logger.log_turn(self.CALL, 1, "bot", "Секретна репліка", guards_fired=[])
        assert len(rec.attempts) == 2
        assert rec.rows() == [("bot", "Секретна репліка", None)]
        assert call_logger._db_available is True
        (said,) = [r for r in caplog.records if r.levelno >= logging.WARNING]
        message = said.getMessage()
        assert said.levelno == logging.ERROR
        assert str(self.CALL) in message
        assert "069" in message
        assert 'column "guards_fired"' in message
        assert "Секретна репліка" not in message
        assert "<the reply>" not in message, "the wrapper's parameters must not be logged"

    async def test_unserializable_trace_keeps_the_turn_and_says_so(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        rec = StatementRecorder()
        with caplog.at_level(logging.WARNING, logger="src.logging.call_logger"):
            await _logger(rec).log_turn(
                self.CALL,
                1,
                "bot",
                PLAIN,
                guards_fired=[{"guard": object()}],  # type: ignore[dict-item]
            )
        assert rec.rows() == [("bot", PLAIN, None)]
        (said,) = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert str(self.CALL) in said.getMessage()

    async def test_an_outage_buffers_the_turn_with_its_trace(self) -> None:
        """Not a schema problem: same road as every other write, trace included."""
        pushed: list[tuple[str, str]] = []
        redis = MagicMock(spec=["rpush", "expire"])

        async def rpush(key: str, entry: str) -> int:
            pushed.append((key, entry))
            return 1

        async def expire(key: str, ttl: int) -> bool:
            return True

        redis.rpush = rpush
        redis.expire = expire
        rec = StatementRecorder(fail=lambda sql: ConnectionRefusedError("down"))
        call_logger = _logger(rec, redis=redis)
        await call_logger.log_turn(self.CALL, 1, "bot", PLAIN, guards_fired=[TIRE_ENTRY])
        assert rec.written == []
        assert len(rec.attempts) == 1, "an outage is not retried without the trace"
        assert call_logger._db_available is False
        ((_key, entry),) = pushed
        buffered = json.loads(entry)
        assert "guards_fired" in buffered["query"]
        assert json.loads(buffered["params"]["guards_fired"]) == [TIRE_ENTRY]


# ── The wiring: a turn driven through the pipeline ───────────────────────


async def _through_tire_guard(*sentences: str) -> str:
    """Say `sentences` through the real stream filter, consumed in a child task."""

    async def _stream():
        for sentence in sentences:
            await asyncio.sleep(0)
            yield SentenceReady(text=sentence)

    async def _consume() -> list[str]:
        filtered = guard_tire_claims(_stream(), _tire_state(), ASKED, "test")
        return [event.text async for event in filtered]

    return " ".join(await asyncio.create_task(_consume()))


async def _through_control_plane_guard(*sentences: str) -> str:
    async def _stream():
        for sentence in sentences:
            yield SentenceReady(text=sentence)

    filtered = drop_control_plane_prose(_stream(), "test")
    return " ".join([event.text async for event in filtered if isinstance(event, SentenceReady)])


def _turn(text: str, **kw: Any) -> TurnResult:
    return TurnResult(
        spoken_text=text, tool_calls_made=0, stop_reason="end_turn", total_usage=Usage(10, 5), **kw
    )


class _ScriptOverError(Exception):
    """The test has no more caller turns to feed."""


class Stand:
    """`Harness` + a real `CallLogger` whose statements are kept."""

    def __init__(self, *replies: Any, fail: Any = None) -> None:
        self.h = Harness(session=CallSession(uuid.uuid4()))
        self.rec = StatementRecorder(fail=fail)
        self.h.pipeline._call_logger = _logger(self.rec)
        self._replies = list(replies)

        async def _run_turn(**kw: Any) -> TurnResult:
            reply = self._replies.pop(0)
            return await reply() if callable(reply) else _turn(reply)

        self.h.streaming_loop.run_turn = _run_turn

    async def drive(self, *script: str | None) -> None:
        """Feed the loop; None is a silence timeout. Ends when the script does.

        The end is an exception and not a closed connection: the loop reads a
        `None` as a silence first and would add a re-prompt row of its own.
        """
        queue = list(script)

        async def _wait() -> Transcript | None:
            if not queue:
                raise _ScriptOverError
            item = queue.pop(0)
            if item is None:
                return None
            return Transcript(text=item, is_final=True, confidence=0.95, language="uk-UA")

        self.h.pipeline._wait_for_final_transcript = _wait  # type: ignore[method-assign]
        with contextlib.suppress(_ScriptOverError):
            await self.h.pipeline._transcript_processor_loop()

    def bot_rows(self) -> list[tuple[str, Any]]:
        return [(content, trace) for speaker, content, trace in self.rec.rows() if speaker == "bot"]

    def customer_rows(self) -> list[tuple[str, Any]]:
        return [(c, trace) for speaker, c, trace in self.rec.rows() if speaker == "customer"]


class TestWiringStreamingPath:
    async def test_the_guard_that_fired_is_on_that_turns_bot_row(self) -> None:
        async def lie() -> TurnResult:
            return _turn(await _through_tire_guard(CHEAP_LIE, OFFER))

        stand = Stand(lie, PLAIN)
        await stand.drive("яка найдешевша?", "добре")

        assert stand.rec.rows() == [
            ("customer", "яка найдешевша?", None),
            ("bot", f"{REPLACEMENTS[RULE_CHEAPEST]} {OFFER}", [TIRE_ENTRY]),
            ("customer", "добре", None),
            # The next turn, on which nothing fired: a trace, and an empty one.
            ("bot", PLAIN, []),
        ]

    async def test_customer_rows_never_carry_a_trace(self) -> None:
        """Even though the trace is already open when the caller's row is written."""
        stand = Stand(PLAIN)
        await stand.drive("алло")
        assert stand.customer_rows() == [("алло", None)]
        (sql, _params) = stand.rec.written[0]
        assert "guards_fired" not in sql

    async def test_filler_stripped_from_the_logged_reply_is_traced_by_pattern(self) -> None:
        said = "Зрозуміла. Зараз перевірю. Чекайте, будь ласка. Є вільний час о десятій."
        cleaned, stripped = _strip_filler(said)
        assert len(stripped) == 2, "the stand needs a reply the strip really cuts twice"

        stand = Stand(said)
        await stand.drive("запишіть мене")

        assert stand.bot_rows() == [
            (cleaned, [{"guard": "bot_filler_stripped", "pattern": p} for p, _ in stripped])
        ]

    async def test_reply_cut_whole_by_a_guard_explains_the_fallback_row(self) -> None:
        """Machinery dropped by the stream filter → nothing spoken → the soft fallback."""
        form = control_plane_syntax(MACHINERY)
        assert form is not None

        async def machinery() -> TurnResult:
            return _turn(await _through_control_plane_guard(MACHINERY))

        stand = Stand(machinery)
        await stand.drive("з'єднайте з оператором")

        ((content, trace),) = stand.bot_rows()
        assert content and MACHINERY not in content
        assert trace == [{"guard": "control_plane_prose_dropped", "form": form, "site": "stream"}]

    async def test_code_phrases_are_null_not_empty(self) -> None:
        """Silence re-prompts and the template farewell are not the model's turn."""
        stand = Stand(PLAIN)
        await stand.drive("алло", *[None] * MAX_TIMEOUTS_BEFORE_HANGUP)

        bot = stand.bot_rows()
        assert bot[0] == (PLAIN, [])
        assert len(bot) == 1 + MAX_TIMEOUTS_BEFORE_HANGUP
        assert bot[-1][0] == FAREWELL_TEXT  # dialog too short: the model is not asked
        assert [trace for _content, trace in bot[1:]] == [None] * MAX_TIMEOUTS_BEFORE_HANGUP

    async def test_an_interrupted_turn_does_not_hand_its_trace_to_the_farewell(self) -> None:
        """Barge-in before speech writes no bot row; the trace it left must not resurface."""

        async def interrupted() -> TurnResult:
            await _through_tire_guard(CHEAP_LIE)
            return _turn("", interrupted=True)

        stand = Stand(interrupted)
        await stand.drive("яка найдешевша?", *[None] * MAX_TIMEOUTS_BEFORE_HANGUP)

        bot = stand.bot_rows()
        assert bot[-1][0] == FAREWELL_TEXT
        assert [trace for _content, trace in bot] == [None] * len(bot)

    async def test_trace_write_failure_keeps_the_reply(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        async def lie() -> TurnResult:
            return _turn(await _through_tire_guard(CHEAP_LIE))

        stand = Stand(lie, fail=_missing_column)
        with caplog.at_level(logging.WARNING, logger="src.logging.call_logger"):
            await stand.drive("яка найдешевша?")

        assert stand.rec.rows() == [
            ("customer", "яка найдешевша?", None),
            ("bot", REPLACEMENTS[RULE_CHEAPEST], None),
        ]
        loud = [r for r in caplog.records if r.name == "src.logging.call_logger"]
        assert [r.levelno for r in loud] == [logging.ERROR]
        assert str(stand.h.session.channel_uuid) in loud[0].getMessage()

    async def test_two_calls_at_once_keep_their_own_traces(self) -> None:
        async def lie() -> TurnResult:
            return _turn(await _through_tire_guard(CHEAP_LIE, OFFER))

        async def honest() -> TurnResult:
            return _turn(await _through_tire_guard(OFFER, OFFER))

        loud, quiet = Stand(lie, lie), Stand(honest, honest)
        await asyncio.gather(loud.drive("перше", "друге"), quiet.drive("перше", "друге"))

        assert [trace for _c, trace in loud.bot_rows()] == [[TIRE_ENTRY], [TIRE_ENTRY]]
        assert [trace for _c, trace in quiet.bot_rows()] == [[], []]


class TestWiringFarewell:
    """The farewell the model writes on a silence timeout is a model turn too."""

    @staticmethod
    def _stand(farewell: str) -> Stand:
        stand = Stand(PLAIN, PLAIN)
        agent = MagicMock(spec=["process_message"])

        async def process_message(**kw: Any) -> tuple[str, None]:
            return farewell, None

        agent.process_message = process_message
        stand.h.pipeline._agent = agent
        return stand

    async def test_machinery_in_the_farewell_is_on_the_farewell_row(self) -> None:
        form = control_plane_syntax(MACHINERY)
        stand = self._stand(MACHINERY)
        await stand.drive("алло", "добре", *[None] * MAX_TIMEOUTS_BEFORE_HANGUP)

        assert len(stand.h.session.dialog_history) >= _FAREWELL_MIN_TURNS
        assert stand.bot_rows()[-1] == (
            FAREWELL_TEXT,
            [{"guard": "control_plane_prose_dropped", "form": form, "site": "farewell"}],
        )

    async def test_a_clean_generated_farewell_has_an_empty_trace(self) -> None:
        stand = self._stand("Була рада допомогти! До побачення!")
        await stand.drive("алло", "добре", *[None] * MAX_TIMEOUTS_BEFORE_HANGUP)
        assert stand.bot_rows()[-1] == ("Була рада допомогти! До побачення!", [])


class TestWiringBlockingPath:
    """No streaming loop: `LLMAgent.process_message`, with the text-path guards."""

    @staticmethod
    def _stand(*replies: str) -> Stand:
        stand = Stand()
        queue = list(replies)
        agent = MagicMock(spec=["process_message"])

        async def process_message(**kw: Any) -> tuple[str, None]:
            return guard_tire_claims_text(queue.pop(0), _tire_state(), ASKED, "test"), None

        agent.process_message = process_message
        stand.h.pipeline._agent = agent
        stand.h.pipeline._streaming_loop = None
        return stand

    async def test_text_path_guard_lands_on_the_reply_not_on_the_wait_phrase(self) -> None:
        stand = self._stand(f"{CHEAP_LIE} {OFFER}", PLAIN)
        await stand.drive("яка найдешевша?", "добре")

        rows = stand.rec.rows()
        # Per turn: the wait phrase (code), the caller, the reply.
        assert [speaker for speaker, _c, _t in rows] == ["bot", "customer", "bot"] * 2
        assert [trace for _s, _c, trace in rows] == [None, None, [TIRE_ENTRY], None, None, []]
        assert rows[2][1] == f"{REPLACEMENTS[RULE_CHEAPEST]} {OFFER}"

    async def test_blocking_filler_strip_is_traced(self) -> None:
        said = "Зараз перевірю наявність. Є Bridgestone Blizzak 6 за 8625 гривень."
        cleaned, stripped = _strip_filler(said)
        assert stripped
        stand = self._stand(said)
        await stand.drive("є блізак?")
        assert stand.bot_rows()[-1] == (
            cleaned,
            [{"guard": "bot_filler_stripped", "pattern": p} for p, _ in stripped],
        )

    async def test_empty_reply_fallback_closes_the_turn(self) -> None:
        stand = self._stand("")
        await stand.drive("алло")
        wait_row, fallback_row = stand.bot_rows()
        assert wait_row[1] is None
        assert fallback_row[0] and fallback_row[1] == []


# ── The registry: every guard counter increment has a trace beside it ────

#: counter variable → {file: how many increments}. Each one must be followed,
#: as the very next statement, by `guard_trace.record("<code>", <same labels>)`.
TRACED: dict[str, dict[str, int]] = {
    "false_transfer_blocked_total": {"src/agent/streaming_loop.py": 1},
    "booking_offer_redirected_total": {"src/agent/streaming_loop.py": 2},
    "settled_question_redirected_total": {"src/agent/streaming_loop.py": 1},
    "control_plane_prose_dropped_total": {
        "src/agent/streaming_loop.py": 2,
        "src/core/pipeline.py": 1,
    },
    "network_claim_blocked_total": {"src/agent/network_claim_guard.py": 2},
    "tire_claim_refused_total": {"src/agent/tire_claim_guard.py": 1},
    "guard_refusal_repeated_total": {"src/agent/streaming_loop.py": 2},
    "bot_filler_stripped_total": {"src/core/pipeline.py": 2},
    "transfer_promise_suppressed_total": {"src/agent/streaming_loop.py": 1},
    "reopened_time_choice_total": {"src/agent/streaming_loop.py": 1},
    # Tool handlers: the model's call was refused. They run inside the turn
    # (the tool round of `run_turn`), so the turn's trace is theirs too.
    "book_fitting_unchosen_time_total": {"src/main.py": 1},
    "krok1_regression_blocked_total": {"src/main.py": 1},
}

#: Counters of a guard that decided NOT to step in. The model's words went out
#: unchanged, so nothing «fired»: writing it would make
#: `jsonb_array_length(guards_fired) > 0` mean something other than «code
#: changed what the model said or called».
NOT_TRACED: dict[str, dict[str, int]] = {
    "settled_question_redirect_skipped_total": {"src/agent/streaming_loop.py": 1},
}


def _code(counter: str) -> str:
    return counter.removesuffix("_total")


def _increment(stmt: ast.stmt) -> tuple[str, list[ast.keyword]] | None:
    """`<counter>.labels(...).inc()` / `<counter>.inc()` as a statement → (name, labels)."""
    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
        return None
    func = stmt.value.func
    if not (isinstance(func, ast.Attribute) and func.attr == "inc"):
        return None
    target = func.value
    if isinstance(target, ast.Name):
        return target.id, []
    if (
        isinstance(target, ast.Call)
        and isinstance(target.func, ast.Attribute)
        and target.func.attr == "labels"
        and isinstance(target.func.value, ast.Name)
    ):
        return target.func.value.id, target.keywords
    return None


def _record(stmt: ast.stmt | None) -> tuple[str, list[ast.keyword]] | None:
    """`guard_trace.record("<code>", ...)` as a statement → (code, labels)."""
    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
        return None
    func = stmt.value.func
    if not (
        isinstance(func, ast.Attribute)
        and func.attr == "record"
        and isinstance(func.value, ast.Name)
        and func.value.id == "guard_trace"
    ):
        return None
    args = stmt.value.args
    assert len(args) == 1 and isinstance(args[0], ast.Constant), ast.unparse(stmt)
    return str(args[0].value), stmt.value.keywords


def _labels(keywords: list[ast.keyword]) -> dict[str | None, str]:
    return {kw.arg: ast.dump(kw.value) for kw in keywords}


def _blocks(tree: ast.AST):
    """Every statement list in the module (bodies, else-branches, handlers…)."""
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
                yield block


def _scan() -> tuple[list[tuple[str, int, str, bool]], list[tuple[str, int, str]], list[str]]:
    """(increments of listed counters, record calls, problems) over `src/`."""
    listed = TRACED.keys() | NOT_TRACED.keys()
    increments: list[tuple[str, int, str, bool]] = []
    records: list[tuple[str, int, str]] = []
    problems: list[str] = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        if "guard_trace" not in source and not any(name in source for name in listed):
            continue
        for block in _blocks(ast.parse(source)):
            for index, stmt in enumerate(block):
                recorded = _record(stmt)
                if recorded is not None:
                    records.append((rel, stmt.lineno, recorded[0]))
                    before = _increment(block[index - 1]) if index else None
                    if before is None or _code(before[0]) != recorded[0]:
                        problems.append(
                            f"{rel}:{stmt.lineno}: guard_trace.record({recorded[0]!r}) does not "
                            "follow that guard's counter increment"
                        )
                inc = _increment(stmt)
                if inc is None or inc[0] not in listed:
                    continue
                counter, labels = inc
                after = _record(block[index + 1] if index + 1 < len(block) else None)
                increments.append((rel, stmt.lineno, counter, after is not None))
                if counter in NOT_TRACED:
                    if after is not None:
                        problems.append(f"{rel}:{stmt.lineno}: {counter} must not be traced")
                    continue
                if after is None:
                    problems.append(
                        f"{rel}:{stmt.lineno}: {counter} is incremented with no "
                        f"guard_trace.record({_code(counter)!r}, …) as the next statement"
                    )
                elif after[0] != _code(counter):
                    problems.append(
                        f"{rel}:{stmt.lineno}: {counter} is traced as {after[0]!r}, "
                        f"expected {_code(counter)!r}"
                    )
                elif _labels(after[1]) != _labels(labels):
                    problems.append(
                        f"{rel}:{stmt.lineno}: {counter} trace labels differ from the "
                        f"counter's: {ast.unparse(block[index + 1])}"
                    )
    return increments, records, problems


class TestRegistry:
    def test_every_guard_counter_increment_has_its_trace_beside_it(self) -> None:
        """Reads the sources: each `.inc()` of a counter in `TRACED` is followed by
        `guard_trace.record()` with that guard's code and the very same label
        expressions, so the trace and the counter cannot drift apart.

        What this does NOT catch — said plainly, because the list is explicit:
        a **new** guard counter that nobody adds to `TRACED`. The test knows
        the counters by name; a counter it has never heard of is just another
        metric to it. Adding a guard means adding its line here. Known and
        deliberately unlisted today: `transfer_promise_unbacked_total` (one
        increment for two outcomes, and «spoken» is the guard standing aside)
        and the filters that have no counter at all (`drop_repeated_sentences`,
        `_strip_leaked_system_block`, `_strip_greeting`, the sales filters in
        their own modules). So `[]` reads «none of the listed guards fired».
        """
        _increments, _records, problems = _scan()
        assert problems == []

    def test_the_list_matches_the_sources_site_for_site(self) -> None:
        """A new increment of a listed counter — in any file under `src/` — or a
        removed one changes these numbers and has to be looked at."""
        increments, records, _problems = _scan()
        found: dict[str, dict[str, int]] = {}
        for rel, _line, counter, _traced in increments:
            found.setdefault(counter, {}).setdefault(rel, 0)
            found[counter][rel] += 1
        assert found == {**TRACED, **NOT_TRACED}
        assert len(records) == sum(n for sites in TRACED.values() for n in sites.values()) == 18

    def test_the_guard_that_stood_aside_is_not_a_firing(self) -> None:
        increments, records, _problems = _scan()
        skipped = [i for i in increments if i[2] in NOT_TRACED]
        assert [traced for *_rest, traced in skipped] == [False]
        codes = {code for _rel, _line, code in records}
        assert codes == {_code(counter) for counter in TRACED}
        assert not any("skipped" in code for code in codes)
