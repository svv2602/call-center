"""A turn no LLM provider answered: the caller is told, then an operator is tried.

FF_LLM_OUTAGE_TRANSFER (off by default). The turn counts as unanswered when
the round raised or the 45 s turn timeout fired and nothing was spoken; an
empty reply, a barge-in and a hangup do not. The pipeline first says
`LLM_OUTAGE_TRANSFER_TEXT` — an announced attempt, written and spoken before
the redirect is asked for, because an accepted redirect takes the channel
away — and then asks the one transfer executor. Accepted: nothing more is
said and the loop stops. Not accepted: `LLM_OUTAGE_TRANSFER_FAILED_TEXT` and
the call goes on; the transfer is tried once per call.

Every test drives the real `CallPipeline._transcript_processor_loop` (the
stand of `test_guard_trace.py`: a real `CallLogger` whose statements are
kept), on the streaming path and on the blocking one, and checks *whose*
branch ended the turn — the counter, the transfer reason, the rows written
and their order against the executor call — not only the phrase.

No network, no database, no Redis. The executor is a hand-written stand-in
for the `transfer_to_operator` closure of `src/main.py` that mirrors its side
effects on the session, because the pipeline reads the outcome off the session
and not off the returned dict.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import MagicMock

import pytest
from prometheus_client import REGISTRY

from src.agent import guard_trace
from src.agent.prompts import (
    EMPTY_RESPONSE_SOFT_TEXT,
    ERROR_TEXT,
    LLM_OUTAGE_TRANSFER_FAILED_TEXT,
    LLM_OUTAGE_TRANSFER_TEXT,
    TRANSFER_TEXT,
    WAIT_TEXT,
)
from src.agent.streaming_loop import TurnResult
from src.agent.tools import ALL_TOOLS
from src.config import FeatureFlagSettings
from src.core.call_session import MAX_EMPTY_RESPONSES_BEFORE_ESCALATE
from src.core.pipeline import LLM_OUTAGE_TRANSFER_REASON
from src.llm.models import Usage
from tests.unit.test_guard_trace import Stand

FLAG = "FF_LLM_OUTAGE_TRANSFER"
METRIC = "callcenter_llm_outage_transfers_total"
REPLY = "Добре, підкажіть, будь ласка, місто."
ANNOUNCED = LLM_OUTAGE_TRANSFER_TEXT
FAILED = LLM_OUTAGE_TRANSFER_FAILED_TEXT
TRANSFER_CALL = ("transfer_to_operator", {"reason": LLM_OUTAGE_TRANSFER_REASON})
GUARD_ENTRY = {"guard": "false_transfer_blocked", "reason": "customer_request"}

#: Every way the executor of `src/main.py` reports «the caller stays with the bot».
REFUSALS = ["after_hours", "error", "unavailable"]


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(FLAG, raising=False)
    guard_trace.discard()
    yield
    guard_trace.discard()


@pytest.fixture
def flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FLAG, "true")


@pytest.fixture
def plain_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """The blocking path's wait phrase rotates through a pool; pin it to the template."""
    monkeypatch.setattr("src.core.pipeline._select_wait_message", lambda _text, default: default)


@pytest.fixture
def short_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.core.pipeline.AGENT_PROCESSING_TIMEOUT_SEC", 0.05)


def _count(outcome: str, cause: str) -> float:
    return REGISTRY.get_sample_value(METRIC, {"outcome": outcome, "cause": cause}) or 0.0


class Counted:
    """The counter's movement over a test, by (outcome, cause)."""

    KEYS = tuple((o, c) for o in ("transferred", "transfer_failed") for c in ("error", "timeout"))

    def __init__(self) -> None:
        self._before = {key: _count(*key) for key in self.KEYS}

    def delta(self) -> dict[tuple[str, str], float]:
        moved = {key: _count(*key) - self._before[key] for key in self.KEYS}
        return {key: value for key, value in moved.items() if value}


class Executor:
    """Stands in for the `transfer_to_operator` closure: same marks on the session.

    `outcome="transferring"` is an accepted redirect (`mark_transfer`); any
    other string is a refusal returned as a dict (`mark_transfer_failed`) —
    not an exception, exactly like the real one. `raises` is thrown after the
    marks, which is where a cancellation lands on a torn-down channel.

    What the caller had heard, what was already in `call_turns` and what the
    session history held are captured at the moment the redirect is asked for:
    after an accepted one nothing more reaches the caller.
    """

    def __init__(
        self,
        stand: Stand,
        outcome: str | None = "transferring",
        raises: BaseException | None = None,
    ) -> None:
        self._stand = stand
        self._outcome = outcome
        self._raises = raises
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.spoken_before: list[list[str]] = []
        self.bot_rows_before: list[list[tuple[str, Any]]] = []
        self.history_before: list[list[str]] = []

    async def execute(self, name: str, args: dict[str, Any] | None = None) -> dict[str, str]:
        self.calls.append((name, dict(args or {})))
        self.spoken_before.append(list(self._stand.h.spoken))
        self.bot_rows_before.append(self._stand.bot_rows())
        self.history_before.append(list(self._stand.h.assistant_texts))
        session = self._stand.h.session
        reason = str((args or {}).get("reason", ""))
        if self._outcome == "transferring":
            session.mark_transfer(reason)
        elif self._outcome is not None:
            session.mark_transfer_failed(reason, self._outcome)
        if self._raises is not None:
            raise self._raises
        return {"status": self._outcome or ""}


def _turn(text: str = "", *, stop_reason: str = "end_turn", **kw: Any) -> TurnResult:
    return TurnResult(
        spoken_text=text, tool_calls_made=0, stop_reason=stop_reason, total_usage=Usage(10, 5), **kw
    )


async def _failed() -> TurnResult:
    """`StreamingAgentLoop.run_turn` after an exception in the round, nothing spoken."""
    return _turn(stop_reason="error")


async def _guarded_then_failed() -> TurnResult:
    """A guard fired on the turn before the round raised."""
    guard_trace.record("false_transfer_blocked", reason="customer_request")
    return _turn(stop_reason="error")


async def _empty() -> TurnResult:
    """An empty reply that survived the retries: no error, no text."""
    return _turn()


async def _hangs() -> TurnResult:
    await asyncio.sleep(30)
    raise AssertionError("the turn timeout did not cancel the turn")


def streaming(
    *replies: Any, outcome: str | None = "transferring", **kw: Any
) -> tuple[Stand, Executor]:
    stand = Stand(*replies)
    executor = Executor(stand, outcome, **kw)
    stand.h.tool_router.execute = executor.execute
    return stand, executor


def blocking(
    *replies: Any,
    outcome: str | None = "transferring",
    agent_spec: tuple[str, ...] = ("process_message", "last_error", "tool_router"),
) -> tuple[Stand, Executor]:
    """No streaming loop. A reply is `(text, last_error)` or a coroutine function.

    The agent is spec'd to what the pipeline may touch: with the flag off that
    is `process_message` alone, and a read of `last_error` raises.
    """
    stand = Stand()
    executor = Executor(stand, outcome)
    queue = list(replies)
    agent = MagicMock(spec=list(agent_spec))
    if "last_error" in agent_spec:
        agent.last_error = None
    if "tool_router" in agent_spec:
        agent.tool_router = MagicMock(spec=["execute"])
        agent.tool_router.execute = executor.execute

    async def process_message(**kw: Any) -> tuple[str, None]:
        reply = queue.pop(0)
        if callable(reply):
            return await reply(), None
        text, error = reply
        if "last_error" in agent_spec:
            # `LLMAgent.process_message` resets it at the start of every turn.
            agent.last_error = error
        return text, None

    agent.process_message = process_message
    stand.h.pipeline._agent = agent
    stand.h.pipeline._streaming_loop = None
    return stand, executor


#: `LLMAgent.process_message` on a provider failure: the error text as a reply.
PROVIDER_FAILED = (ERROR_TEXT, "LLM router: all providers failed")


def _latencies(stand: Stand) -> list[Any]:
    """`llm_latency_ms` of each bot row, in order."""
    return [p["llm_latency_ms"] for _sql, p in stand.rec.written if p["speaker"] == "bot"]


class TestFlagAndTexts:
    def test_off_by_default(self) -> None:
        assert FeatureFlagSettings().llm_outage_transfer is False

    def test_read_from_the_env_at_call_time(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(FLAG, "true")
        assert FeatureFlagSettings().llm_outage_transfer is True

    def test_every_series_of_the_counter_exists_before_the_first_transfer(self) -> None:
        """`increase()` does not see the first increment of a series born at 1."""
        for outcome, cause in Counted.KEYS:
            labels = {"outcome": outcome, "cause": cause}
            assert REGISTRY.get_sample_value(METRIC, labels) is not None, labels

    def test_the_first_phrase_announces_an_attempt_not_a_result(self) -> None:
        """It is spoken before the redirect is asked for: «спробую», never «з'єдную»."""
        assert "спробую з'єднати" in ANNOUNCED.lower()
        assert "з'єдную" not in ANNOUNCED.lower()
        assert "з'єдную" not in FAILED.lower()
        # One apology per turn: the second phrase follows the first at once.
        assert "перепрошую" in ANNOUNCED.lower()
        assert "перепрошую" not in FAILED.lower()

    def test_the_reason_is_one_the_model_cannot_give(self) -> None:
        """`calls.transfer_reason` must tell these transfers from the model's own."""
        (tool,) = [t for t in ALL_TOOLS if t["name"] == "transfer_to_operator"]
        model_reasons = tool["input_schema"]["properties"]["reason"]["enum"]
        assert LLM_OUTAGE_TRANSFER_REASON == "llm_outage"
        assert LLM_OUTAGE_TRANSFER_REASON not in model_reasons


@pytest.mark.usefixtures("flag_on")
class TestStreamingTransfer:
    async def test_failed_round_is_announced_then_transferred(self) -> None:
        stand, executor = streaming(_failed, REPLY)
        counted = Counted()

        await stand.drive("запишіть мене на монтаж", "алло")

        assert executor.calls == [TRANSFER_CALL]
        # By the time the redirect was asked for the caller had heard the
        # phrase, and it was already in `call_turns` and in the history.
        assert executor.spoken_before == [[ANNOUNCED]]
        assert executor.bot_rows_before == [[(ANNOUNCED, [])]]
        assert executor.history_before == [[ANNOUNCED]]
        # …and after the accepted redirect nothing more is said or written.
        assert stand.h.spoken == [ANNOUNCED]
        assert stand.rec.rows() == [
            ("customer", "запишіть мене на монтаж", None),
            ("bot", ANNOUNCED, []),
        ]
        assert stand.h.assistant_texts == [ANNOUNCED]
        assert counted.delta() == {("transferred", "error"): 1}
        assert stand.h.session.transfer_reason == LLM_OUTAGE_TRANSFER_REASON
        assert stand.h.session.transferred is True

    async def test_the_loop_stops_without_a_second_announcement(self) -> None:
        """`_close_turn` would add the `transfer` template after the redirect."""
        stand, _executor = streaming(_failed, REPLY)

        await stand.drive("запишіть мене", "алло")

        assert TRANSFER_TEXT not in stand.h.spoken
        assert FAILED not in stand.h.spoken
        assert [content for content, _trace in stand.bot_rows()] == [ANNOUNCED]
        # The second scripted turn was never taken: its reply is still queued.
        assert stand._replies == [REPLY]
        assert stand.customer_rows() == [("запишіть мене", None)]

    @pytest.mark.usefixtures("short_timeout")
    async def test_turn_timeout_is_transferred(self) -> None:
        stand, executor = streaming(_hangs)
        counted = Counted()

        await stand.drive("запишіть мене")

        assert executor.calls == [TRANSFER_CALL]
        assert executor.spoken_before == [[ANNOUNCED]]
        assert stand.h.spoken == [ANNOUNCED]
        assert stand.bot_rows() == [(ANNOUNCED, [])]
        assert counted.delta() == {("transferred", "timeout"): 1}

    async def test_the_phrase_row_carries_the_turns_guards_and_latency(self) -> None:
        stand, executor = streaming(_guarded_then_failed)

        await stand.drive("з'єднайте з оператором")

        assert executor.bot_rows_before == [[(ANNOUNCED, [GUARD_ENTRY])]]
        assert stand.bot_rows() == [(ANNOUNCED, [GUARD_ENTRY])]
        (latency,) = _latencies(stand)
        assert isinstance(latency, int)

    async def test_the_session_is_persisted_before_the_redirect(self) -> None:
        """After an accepted redirect there may be no next await to do it on."""
        stand, executor = streaming(_failed)
        events: list[str] = []
        real_execute = executor.execute

        async def persist() -> None:
            events.append(f"persist:{stand.h.assistant_texts[-1]}")

        async def execute(name: str, args: dict[str, Any] | None = None) -> dict[str, str]:
            events.append("execute")
            return await real_execute(name, args)

        stand.h.pipeline._persist_session = persist  # type: ignore[method-assign]
        stand.h.tool_router.execute = execute

        await stand.drive("запишіть мене")

        assert events == [f"persist:{ANNOUNCED}", "execute"]


@pytest.mark.usefixtures("flag_on")
class TestStreamingNotAnOutage:
    async def test_empty_reply_keeps_the_soft_reask(self) -> None:
        stand, executor = streaming(_empty, REPLY)
        counted = Counted()

        await stand.drive("запишіть мене", "алло")

        assert executor.calls == []
        assert stand.h.spoken == [EMPTY_RESPONSE_SOFT_TEXT]
        assert stand.bot_rows() == [(EMPTY_RESPONSE_SOFT_TEXT, []), (REPLY, [])]
        assert counted.delta() == {}

    @pytest.mark.parametrize("how", ["interrupted", "disconnected"])
    async def test_barge_in_and_hangup_before_speech_are_not_transferred(self, how: str) -> None:
        async def cut() -> TurnResult:
            return _turn(**{how: True})

        stand, executor = streaming(cut, REPLY)
        counted = Counted()

        await stand.drive("запишіть мене", "алло")

        assert executor.calls == []
        assert stand.h.spoken == []
        assert stand.bot_rows() == [(REPLY, [])]
        assert counted.delta() == {}

    async def test_a_reply_that_failed_midway_stays_a_reply(self) -> None:
        """The caller already heard part of the answer: no transfer on top of it."""

        async def half() -> TurnResult:
            return _turn("Є вільний час о десятій.", stop_reason="error")

        stand, executor = streaming(half)
        counted = Counted()

        await stand.drive("коли можна?")

        assert executor.calls == []
        assert stand.h.spoken == []
        assert stand.bot_rows() == [("Є вільний час о десятій.", [])]
        assert counted.delta() == {}

    async def test_a_call_already_redirected_is_not_redirected_again(self) -> None:
        """The model's own transfer was accepted, then the next round failed."""
        stand, executor = streaming()

        async def transferred_then_failed() -> TurnResult:
            stand.h.session.mark_transfer("customer_request")
            return _turn(stop_reason="error")

        stand._replies.append(transferred_then_failed)
        counted = Counted()

        await stand.drive("з'єднайте з оператором")

        assert executor.calls == []
        assert stand.h.session.transfer_reason == "customer_request"
        # The old path, whole: the fallback, then `_close_turn` stops the loop.
        assert stand.h.spoken == [EMPTY_RESPONSE_SOFT_TEXT, TRANSFER_TEXT]
        assert counted.delta() == {}


@pytest.mark.usefixtures("flag_on")
class TestStreamingTransferRefused:
    @pytest.mark.parametrize("outcome", REFUSALS)
    async def test_the_caller_is_told_and_the_call_goes_on(self, outcome: str) -> None:
        stand, executor = streaming(_failed, REPLY, outcome=outcome)
        counted = Counted()

        await stand.drive("запишіть мене", "алло")

        assert executor.calls == [TRANSFER_CALL]
        # The refusal is spoken only after the attempt, never ahead of it.
        assert executor.spoken_before == [[ANNOUNCED]]
        assert stand.h.spoken == [ANNOUNCED, FAILED]
        assert counted.delta() == {("transfer_failed", "error"): 1}
        assert stand.h.session.transferred is False
        assert stand.h.session.transfer_outcome == outcome
        # The first row ends the model's turn and carries its trace; the
        # second is a phrase spoken by code — NULL, like every such row. The
        # next turn is an ordinary one, answered by the model, which is shown
        # what the caller was told (`dialog_history`).
        assert stand.bot_rows() == [(ANNOUNCED, []), (FAILED, None), (REPLY, [])]
        assert stand.h.assistant_texts == [ANNOUNCED, FAILED, REPLY]
        first, second, _reply = _latencies(stand)
        assert isinstance(first, int)
        assert second is None

    async def test_a_dict_without_the_session_mark_is_a_refusal(self) -> None:
        """«Did not raise» is not «transferred»: only the session marker counts."""
        stand, executor = streaming(_failed, outcome=None)
        counted = Counted()

        await stand.drive("запишіть мене")

        assert executor.calls == [TRANSFER_CALL]
        assert stand.h.spoken == [ANNOUNCED, FAILED]
        assert counted.delta() == {("transfer_failed", "error"): 1}

    async def test_an_executor_that_raises_is_a_refusal(self) -> None:
        stand, executor = streaming(_failed, REPLY, outcome=None, raises=RuntimeError("ami down"))
        counted = Counted()

        await stand.drive("запишіть мене", "алло")

        assert executor.calls == [TRANSFER_CALL]
        assert stand.h.spoken == [ANNOUNCED, FAILED]
        assert stand.bot_rows() == [(ANNOUNCED, []), (FAILED, None), (REPLY, [])]
        assert counted.delta() == {("transfer_failed", "error"): 1}

    async def test_no_tool_router_is_a_refusal(self, caplog: pytest.LogCaptureFixture) -> None:
        stand, executor = streaming(_failed)
        stand.h.streaming_loop._tool_router = None
        counted = Counted()

        with caplog.at_level(logging.ERROR, logger="src.core.pipeline"):
            await stand.drive("запишіть мене")

        assert executor.calls == []
        assert stand.h.spoken == [ANNOUNCED, FAILED]
        assert stand.bot_rows() == [(ANNOUNCED, []), (FAILED, None)]
        assert counted.delta() == {("transfer_failed", "error"): 1}
        # Named for what it is, not caught as an AttributeError on None.
        messages = [record.getMessage() for record in caplog.records]
        assert any("no tool router" in message for message in messages)
        assert not any("executor raised" in message for message in messages)

    async def test_the_error_template_of_the_admin_ui_is_not_used(self) -> None:
        """A DB variant of `error` may itself announce a transfer."""
        stand, _executor = streaming(_failed, outcome="after_hours")
        stand.h.pipeline._templates = {
            **stand.h.pipeline._templates,
            "error": "Виникла помилка. Зараз з'єдную вас з оператором.",
        }

        await stand.drive("запишіть мене")

        assert stand.h.spoken == [ANNOUNCED, FAILED]

    async def test_the_trace_stays_on_the_first_row_and_the_next_turn_starts_clean(self) -> None:
        stand, _executor = streaming(_guarded_then_failed, REPLY, outcome="error")

        await stand.drive("з'єднайте з оператором", "алло")

        assert stand.bot_rows() == [(ANNOUNCED, [GUARD_ENTRY]), (FAILED, None), (REPLY, [])]

    async def test_the_session_is_persisted_after_each_phrase(self) -> None:
        stand, _executor = streaming(_failed, outcome="error")
        persisted: list[str] = []

        async def persist() -> None:
            persisted.append(stand.h.assistant_texts[-1])

        stand.h.pipeline._persist_session = persist  # type: ignore[method-assign]

        await stand.drive("запишіть мене")

        assert persisted == [ANNOUNCED, FAILED]

    async def test_the_transfer_is_tried_once_per_call(self) -> None:
        """A refused transfer is not announced and asked for again on every failing turn."""
        stand, executor = streaming(_failed, _failed, _failed, outcome="error")
        counted = Counted()

        await stand.drive("запишіть мене", "алло", "ви мене чуєте?")

        assert executor.calls == [TRANSFER_CALL]
        # After the one attempt the old branch answers, with its own counter.
        assert stand.h.spoken == [
            ANNOUNCED,
            FAILED,
            EMPTY_RESPONSE_SOFT_TEXT,
            EMPTY_RESPONSE_SOFT_TEXT,
        ]
        assert stand.h.session.empty_response_count == 2
        assert counted.delta() == {("transfer_failed", "error"): 1}


@pytest.mark.usefixtures("flag_on")
class TestCancellation:
    async def test_cancel_after_an_accepted_redirect_still_counts_the_transfer(self) -> None:
        """The redirect tears the channel down; the call task dies inside `execute`."""
        stand, executor = streaming(_failed, raises=asyncio.CancelledError())
        counted = Counted()

        with pytest.raises(asyncio.CancelledError):
            await stand.drive("запишіть мене")

        assert executor.calls == [TRANSFER_CALL]
        assert counted.delta() == {("transferred", "error"): 1}
        # The phrase was spoken and written before the redirect; nothing after.
        assert stand.h.spoken == [ANNOUNCED]
        assert stand.bot_rows() == [(ANNOUNCED, [])]

    async def test_cancel_without_a_redirect_counts_nothing(self) -> None:
        stand, _executor = streaming(_failed, outcome=None, raises=asyncio.CancelledError())
        counted = Counted()

        with pytest.raises(asyncio.CancelledError):
            await stand.drive("запишіть мене")

        assert counted.delta() == {}
        assert stand.h.spoken == [ANNOUNCED]


class TestStreamingFlagOff:
    """Without the flag both failures keep the old ladder, row for row."""

    async def test_failed_round_gets_the_soft_reask(self) -> None:
        stand, executor = streaming(_failed, REPLY)
        counted = Counted()

        await stand.drive("запишіть мене", "алло")

        assert executor.calls == []
        assert stand.h.spoken == [EMPTY_RESPONSE_SOFT_TEXT]
        assert stand.rec.rows() == [
            ("customer", "запишіть мене", None),
            ("bot", EMPTY_RESPONSE_SOFT_TEXT, []),
            ("customer", "алло", None),
            ("bot", REPLY, []),
        ]
        assert _latencies(stand)[0] is None
        assert counted.delta() == {}

    @pytest.mark.usefixtures("short_timeout")
    async def test_turn_timeout_gets_the_soft_reask(self) -> None:
        stand, executor = streaming(_hangs)
        counted = Counted()

        await stand.drive("запишіть мене")

        assert executor.calls == []
        assert stand.h.spoken == [EMPTY_RESPONSE_SOFT_TEXT]
        assert counted.delta() == {}

    async def test_the_escalation_ladder_is_untouched(self) -> None:
        turns = MAX_EMPTY_RESPONSES_BEFORE_ESCALATE
        stand, executor = streaming(*[_failed] * turns)

        await stand.drive(*["алло"] * turns)

        assert executor.calls == []
        assert stand.h.spoken == [*[EMPTY_RESPONSE_SOFT_TEXT] * (turns - 1), ERROR_TEXT]
        assert stand.h.session.transfer_outcome is None


@pytest.mark.usefixtures("flag_on", "plain_wait")
class TestBlockingTransfer:
    async def test_provider_failure_is_announced_and_transferred_instead_of_the_error_text(
        self,
    ) -> None:
        stand, executor = blocking(PROVIDER_FAILED, (REPLY, None))
        counted = Counted()

        await stand.drive("запишіть мене", "алло")

        assert executor.calls == [TRANSFER_CALL]
        assert executor.spoken_before == [[WAIT_TEXT, ANNOUNCED]]
        assert executor.bot_rows_before == [[(WAIT_TEXT, None), (ANNOUNCED, [])]]
        assert stand.h.spoken == [WAIT_TEXT, ANNOUNCED]
        assert stand.rec.rows() == [
            ("bot", WAIT_TEXT, None),
            ("customer", "запишіть мене", None),
            ("bot", ANNOUNCED, []),
        ]
        assert counted.delta() == {("transferred", "error"): 1}
        assert stand.h.session.transfer_reason == LLM_OUTAGE_TRANSFER_REASON

    @pytest.mark.usefixtures("short_timeout")
    async def test_turn_timeout_is_transferred(self) -> None:
        stand, executor = blocking(_hangs)
        counted = Counted()

        await stand.drive("запишіть мене")

        assert executor.calls == [TRANSFER_CALL]
        assert stand.h.spoken == [WAIT_TEXT, ANNOUNCED]
        assert counted.delta() == {("transferred", "timeout"): 1}

    async def test_empty_reply_without_an_error_keeps_the_soft_reask(self) -> None:
        stand, executor = blocking(("", None))
        counted = Counted()

        await stand.drive("запишіть мене")

        assert executor.calls == []
        assert stand.h.spoken == [WAIT_TEXT, EMPTY_RESPONSE_SOFT_TEXT]
        assert counted.delta() == {}

    async def test_an_answer_is_spoken_as_an_answer(self) -> None:
        stand, executor = blocking((REPLY, None))
        counted = Counted()

        await stand.drive("запишіть мене")

        assert executor.calls == []
        assert stand.h.spoken == [WAIT_TEXT, REPLY]
        assert counted.delta() == {}

    async def test_refused_once_then_the_old_branch(self) -> None:
        stand, executor = blocking(PROVIDER_FAILED, PROVIDER_FAILED, outcome="after_hours")
        counted = Counted()

        await stand.drive("запишіть мене", "алло")

        assert executor.calls == [TRANSFER_CALL]
        # The second failure is the old branch: the error text taken for a reply.
        assert stand.h.spoken == [WAIT_TEXT, ANNOUNCED, FAILED, WAIT_TEXT, ERROR_TEXT]
        assert stand.bot_rows() == [
            (WAIT_TEXT, None),
            (ANNOUNCED, []),
            (FAILED, None),
            (WAIT_TEXT, None),
            (ERROR_TEXT, []),
        ]
        assert counted.delta() == {("transfer_failed", "error"): 1}
        assert stand.h.session.transferred is False


@pytest.mark.usefixtures("plain_wait")
class TestBlockingFlagOff:
    async def test_provider_failure_is_spoken_as_the_reply_and_last_error_is_not_read(
        self,
    ) -> None:
        # `last_error` is not in the spec: reading it would raise.
        stand, executor = blocking(
            (ERROR_TEXT, None), (REPLY, None), agent_spec=("process_message", "tool_router")
        )
        counted = Counted()

        await stand.drive("запишіть мене", "алло")

        assert executor.calls == []
        assert stand.h.spoken == [WAIT_TEXT, ERROR_TEXT, WAIT_TEXT, REPLY]
        assert stand.rec.rows() == [
            ("bot", WAIT_TEXT, None),
            ("customer", "запишіть мене", None),
            ("bot", ERROR_TEXT, []),
            ("bot", WAIT_TEXT, None),
            ("customer", "алло", None),
            ("bot", REPLY, []),
        ]
        assert counted.delta() == {}

    @pytest.mark.usefixtures("short_timeout")
    async def test_turn_timeout_gets_the_soft_reask(self) -> None:
        stand, executor = blocking(_hangs, agent_spec=("process_message", "tool_router"))
        counted = Counted()

        await stand.drive("запишіть мене")

        assert executor.calls == []
        assert stand.h.spoken == [WAIT_TEXT, EMPTY_RESPONSE_SOFT_TEXT]
        assert counted.delta() == {}
