"""Wave 4-A — FSM wiring in CallPipeline.

The first attempt at this wire (2fae3b6) reached production and was reverted
wholesale (c8c6601). These tests pin the four things that failed then:

  * a real kill switch (``FSM_ENABLED=false`` → the engine is never touched);
  * shadow mode that provably cannot reach the customer;
  * a short-circuit that requires *proven* progress, capped at the pipeline
    level independently of the handler's own bookkeeping;
  * ``session_updates`` applied through a whitelist, never ``setattr`` over
    whatever the handler happened to return.

Every collaborator is a ``MagicMock(spec=[...])`` on purpose: a bare
``AsyncMock()`` answers any attribute, which is how 65 tests once went green
against a production method that did not exist.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import inspect
import logging
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.fitting_fsm import FROZEN_STATES, STATES, FsmEngine, FsmEvent, FsmState
from src.agent.intent_classifier import IntentResult
from src.agent.interrupts import InterruptResult
from src.agent.streaming_loop import TurnResult
from src.core.call_session import CallSession
from src.core.pipeline import (
    _PIPELINE_DISPATCH_STREAK_KEY,
    _PIPELINE_DISPATCH_TOTAL_KEY,
    FSM_MODE_LIVE,
    FSM_MODE_OFF,
    FSM_MODE_SHADOW,
    MAX_CONSECUTIVE_PIPELINE_INTERRUPT_TURNS,
    MAX_PIPELINE_INTERRUPT_TURNS,
    CallPipeline,
    map_compound_fields_to_fsm,
)
from src.llm.models import Usage
from src.stt.base import Transcript

LLM_REPLY = "Відповідь від LLM."

#: Pinned «today» for the mapping seam. Wave 6-B routes `date` through
#: `date_parser`, which refuses to read the process clock: a test that cannot
#: pin today cannot assert what «завтра» resolves to. A Monday, so a weekday
#: hint has an unambiguous next occurrence.
_NOW = datetime.datetime(2026, 8, 3, 10, 0, tzinfo=datetime.UTC)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def fsm_flags(
    *,
    enabled: bool,
    shadow_mode: bool = True,
    enabled_tenants: list[str] | None = None,
):
    """Patch ``src.config.get_settings`` with a fixed FSM flag triple."""
    fsm = SimpleNamespace(
        enabled=enabled,
        shadow_mode=shadow_mode,
        log_transitions=True,
        enabled_tenants=",".join(enabled_tenants or []),
        enabled_tenant_list=list(enabled_tenants or []),
    )
    settings = SimpleNamespace(fsm=fsm)
    with patch("src.config.get_settings", return_value=settings):
        yield


class Harness:
    """A CallPipeline plus the recorders needed to assert on customer impact."""

    def __init__(
        self,
        session: CallSession | None = None,
        *,
        db_engine: Any = None,
    ) -> None:
        self.spoken: list[str] = []
        self.llm_turns: list[str] = []
        #: Every kwarg the pipeline handed the streaming loop, per turn. The
        #: LLM's whole picture of the call arrives through these, so a test that
        #: asks «what does the LLM know here» has to read them rather than the
        #: session — the two are not the same thing, and that gap is a defect
        #: class of its own (`3e8f3589`, 2026-09-10).
        self.llm_kwargs: list[dict[str, Any]] = []

        conn = MagicMock(spec=["is_closed"])
        conn.is_closed = False

        llm_router = MagicMock(spec=["complete"])
        tool_router = MagicMock(spec=["execute"])

        streaming_loop = MagicMock(spec=["run_turn", "_llm_router", "_tool_router"])
        streaming_loop._llm_router = llm_router
        streaming_loop._tool_router = tool_router

        async def _run_turn(**kwargs: Any) -> TurnResult:
            self.llm_turns.append(kwargs.get("user_text", ""))
            self.llm_kwargs.append(kwargs)
            return TurnResult(
                spoken_text=LLM_REPLY,
                tool_calls_made=0,
                stop_reason="end_turn",
                total_usage=Usage(10, 5),
            )

        streaming_loop.run_turn = _run_turn

        self.conn = conn
        self.llm_router = llm_router
        self.tool_router = tool_router
        self.streaming_loop = streaming_loop

        self.session = session or CallSession(uuid.uuid4())
        self.pipeline = CallPipeline(
            conn=conn,
            stt=MagicMock(spec=[]),
            tts=MagicMock(spec=[]),
            agent=MagicMock(spec=[]),
            session=self.session,
            streaming_loop=streaming_loop,
            db_engine=db_engine,
        )

        async def _speak(text: str) -> None:
            self.spoken.append(text)

        self.pipeline._speak = _speak  # type: ignore[method-assign]
        self.pipeline._speak_streaming = _speak  # type: ignore[method-assign]

        # Keep the loop hermetic: the real corrector reaches for Redis.
        async def _no_corrections(transcript: Transcript) -> Transcript:
            return transcript

        self.pipeline._apply_stt_corrections = _no_corrections  # type: ignore[method-assign]

        async def _no_buffer(transcript: Transcript) -> Transcript:
            return transcript

        self.pipeline._drain_transcript_buffer = _no_buffer  # type: ignore[method-assign]

    @property
    def assistant_texts(self) -> list[str]:
        """What the bot said this call, from the session transcript.

        Note: on the streaming path the reply audio is emitted by
        StreamingAgentLoop itself, so it never passes through `_speak`. The
        session transcript is the only place both paths converge.
        """
        return [
            t.content
            for t in self.session.dialog_history
            if t.speaker == "assistant" and t.content
        ]

    @property
    def customer_texts(self) -> list[str]:
        return [
            t.content
            for t in self.session.dialog_history
            if t.speaker != "assistant" and t.content
        ]

    async def run(self, *texts: str) -> None:
        """Drive `_transcript_processor_loop` over `texts`, then stop.

        The connection is closed as the LAST transcript is handed over, so the
        loop exits cleanly after that turn instead of taking an extra silence
        round that would pollute `spoken` with a re-prompt template.
        """
        queue = [
            Transcript(text=t, is_final=True, confidence=0.95, language="uk-UA")
            for t in texts
        ]
        pipeline = self.pipeline
        conn = self.conn

        async def _wait() -> Transcript | None:
            if queue:
                transcript = queue.pop(0)
                if not queue:
                    conn.is_closed = True
                return transcript
            conn.is_closed = True
            return None

        pipeline._wait_for_final_transcript = _wait  # type: ignore[method-assign]
        await pipeline._transcript_processor_loop()


def intent(primary: str, confidence: float = 0.9) -> IntentResult:
    return IntentResult(primary_intent=primary, confidence=confidence)  # type: ignore[arg-type]


def interrupt(
    *,
    handled: bool = True,
    reply: str = "Монтаж R17 коштує 500 гривень.",
    advanced: bool = True,
    session_updates: dict[str, Any] | None = None,
    resume_state: str | None = None,
) -> InterruptResult:
    return InterruptResult(
        handled=handled,
        reply_to_customer=reply,
        resume_state=resume_state,
        session_updates=session_updates if session_updates is not None else {},
        advanced=advanced,
    )


FITTING_TEXT = "хочу записатися на шиномонтаж у Дніпрі"


# ---------------------------------------------------------------------------
# TestFlagOff — the rollback path
# ---------------------------------------------------------------------------


class TestFlagOff:
    async def test_engine_and_classifier_never_invoked(self) -> None:
        h = Harness()
        with (
            fsm_flags(enabled=False),
            patch("src.agent.fitting_fsm.FsmEngine") as engine_cls,
            patch("src.agent.compound_parse.compound_parse") as parse,
            patch("src.agent.intent_classifier.classify_intent") as classify,
        ):
            await h.run(FITTING_TEXT)

        assert h.pipeline._fsm_mode() == FSM_MODE_OFF
        engine_cls.assert_not_called()
        parse.assert_not_called()
        classify.assert_not_called()

    async def test_session_fsm_state_untouched(self) -> None:
        h = Harness()
        with fsm_flags(enabled=False):
            await h.run(FITTING_TEXT)

        assert h.session.fsm_state is None
        assert h.session.fsm_filled_fields == {}
        assert h.session.fsm_history == []

    async def test_llm_still_owns_the_turn(self) -> None:
        h = Harness()
        with fsm_flags(enabled=False):
            await h.run(FITTING_TEXT)

        assert h.llm_turns == [FITTING_TEXT]
        assert LLM_REPLY in h.assistant_texts

    async def test_tenant_allow_list_keeps_untargeted_calls_off(self) -> None:
        h = Harness()
        h.session.tenant_id = "tenant-a"
        with fsm_flags(enabled=True, shadow_mode=True, enabled_tenants=["tenant-b"]):
            mode = h.pipeline._fsm_mode()
        assert mode == FSM_MODE_OFF

    async def test_broken_config_degrades_to_off_not_live(self, caplog) -> None:
        h = Harness()
        with (
            caplog.at_level(logging.ERROR, logger="src.core.pipeline"),
            patch("src.config.get_settings", side_effect=RuntimeError("boom")),
        ):
            mode = h.pipeline._fsm_mode()
        assert mode == FSM_MODE_OFF
        assert any(r.levelno >= logging.ERROR for r in caplog.records)


# ---------------------------------------------------------------------------
# TestShadowMode — computes, observes, never speaks
# ---------------------------------------------------------------------------


class TestShadowMode:
    async def test_customer_output_identical_to_flag_off(self) -> None:
        """The load-bearing proof: same input, two flag states, same output.

        Compares both what the caller hears (`spoken`) and what the LLM was
        asked to do (`llm_turns` — the tool-call sequence proxy, since the
        streaming loop owns tool execution and is driven only by `user_text`).
        """
        texts = (FITTING_TEXT, "на пʼяте серпня о десятій, шини з собою")

        baseline = Harness()
        with fsm_flags(enabled=False):
            await baseline.run(*texts)

        shadow = Harness()
        with fsm_flags(enabled=True, shadow_mode=True):
            await shadow.run(*texts)

        assert shadow.spoken == baseline.spoken
        assert shadow.llm_turns == baseline.llm_turns
        assert shadow.assistant_texts == baseline.assistant_texts
        assert shadow.customer_texts == baseline.customer_texts

    async def test_no_llm_and_no_store_api_calls(self) -> None:
        h = Harness()
        with (
            fsm_flags(enabled=True, shadow_mode=True),
            patch("src.agent.intent_classifier.classify_intent") as classify,
            patch("src.agent.interrupts.handle_price_interrupt") as price,
            patch("src.agent.interrupts.handle_cancel_interrupt") as cancel,
        ):
            await h.run(FITTING_TEXT)

        classify.assert_not_called()
        price.assert_not_called()
        cancel.assert_not_called()
        h.llm_router.complete.assert_not_called()
        h.tool_router.execute.assert_not_called()

    async def test_fsm_actually_advances(self) -> None:
        h = Harness()
        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run(FITTING_TEXT)

        assert h.session.fsm_state not in (None, "welcome")
        assert h.session.fsm_filled_fields.get("intent") == "fitting"
        assert h.session.fsm_history

    async def test_shadow_reply_is_not_on_the_customer_path(self) -> None:
        h = Harness()
        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run(FITTING_TEXT)

        # The FSM computed a question…
        assert h.pipeline._fsm_shadow_reply is not None
        # …and nobody said it, nor stored it where the prompt could pick it up.
        assert h.pipeline._fsm_shadow_reply not in h.spoken
        assert not hasattr(h.session, "fsm_shadow_reply")
        assert h.pipeline._fsm_shadow_reply not in h.session.to_dict().values()

    async def test_engine_failure_is_logged_at_error_and_the_call_survives(
        self, caplog
    ) -> None:
        h = Harness()
        with (
            caplog.at_level(logging.ERROR, logger="src.core.pipeline"),
            fsm_flags(enabled=True, shadow_mode=True),
            patch(
                "src.agent.compound_parse.compound_parse",
                side_effect=RuntimeError("parser exploded"),
            ),
        ):
            await h.run(FITTING_TEXT)

        assert any(
            r.levelno >= logging.ERROR and "FSM deterministic step failed" in r.message
            for r in caplog.records
        ), "a broken FSM must be loud, never contextlib.suppress'ed"
        # The caller still got their answer.
        assert h.llm_turns == [FITTING_TEXT]
        assert LLM_REPLY in h.assistant_texts


# ---------------------------------------------------------------------------
# TestLiveMode
# ---------------------------------------------------------------------------


class TestLiveMode:
    async def test_price_interrupt_owns_the_turn(self) -> None:
        h = Harness()
        ir = interrupt(reply="Монтаж R17 коштує 500 гривень.")
        with (
            fsm_flags(enabled=True, shadow_mode=False),
            patch(
                "src.agent.intent_classifier.classify_intent",
                AsyncMock(return_value=intent("PRICE")),
            ),
            patch(
                "src.agent.interrupts.handle_price_interrupt",
                AsyncMock(return_value=ir),
            ),
        ):
            await h.run("скільки коштує монтаж")

        assert "Монтаж R17 коштує 500 гривень." in h.spoken
        assert h.llm_turns == [], "the LLM turn must be skipped, not duplicated"
        assert h.session.interrupt_counts[_PIPELINE_DISPATCH_TOTAL_KEY] == 1

    async def test_dispatch_increments_the_wave_1c_interrupt_counter(self) -> None:
        # Wave 1-C shipped fsm_interrupt_total but nothing ever incremented it.
        # Without this the D1 shadow/live rollout has no interrupt-rate signal.
        from src.monitoring.metrics import fsm_interrupt_total

        def value() -> float:
            return fsm_interrupt_total.labels(interrupt_type="price")._value.get()

        before = value()
        h = Harness()
        with (
            fsm_flags(enabled=True, shadow_mode=False),
            patch(
                "src.agent.intent_classifier.classify_intent",
                AsyncMock(return_value=intent("PRICE")),
            ),
            patch(
                "src.agent.interrupts.handle_price_interrupt",
                AsyncMock(return_value=interrupt()),
            ),
        ):
            await h.run("скільки коштує монтаж")

        assert value() == before + 1

    async def test_customer_turn_is_still_recorded(self) -> None:
        h = Harness()
        with (
            fsm_flags(enabled=True, shadow_mode=False),
            patch(
                "src.agent.intent_classifier.classify_intent",
                AsyncMock(return_value=intent("PRICE")),
            ),
            patch(
                "src.agent.interrupts.handle_price_interrupt",
                AsyncMock(return_value=interrupt()),
            ),
        ):
            await h.run("скільки коштує монтаж")

        assert h.customer_texts == ["скільки коштує монтаж"]
        assert h.assistant_texts == ["Монтаж R17 коштує 500 гривень."]

    async def test_transfer_intent_marks_transfer_and_stops_the_loop(self) -> None:
        h = Harness()
        with (
            fsm_flags(enabled=True, shadow_mode=False),
            patch(
                "src.agent.intent_classifier.classify_intent",
                AsyncMock(return_value=intent("TRANSFER")),
            ),
        ):
            await h.run("дайте оператора", "ще одна фраза")

        assert h.session.transferred is True
        assert h.session.transfer_reason == "intent_classifier_transfer"
        assert h.llm_turns == []

    async def test_book_intent_falls_through_to_the_llm(self) -> None:
        h = Harness()
        with (
            fsm_flags(enabled=True, shadow_mode=False),
            patch(
                "src.agent.intent_classifier.classify_intent",
                AsyncMock(return_value=intent("BOOK")),
            ),
            patch("src.agent.interrupts.handle_price_interrupt") as price,
        ):
            await h.run(FITTING_TEXT)

        price.assert_not_called()
        assert h.llm_turns == [FITTING_TEXT]

    async def test_classifier_failure_falls_through_to_the_llm(self, caplog) -> None:
        h = Harness()
        with (
            caplog.at_level(logging.ERROR, logger="src.core.pipeline"),
            fsm_flags(enabled=True, shadow_mode=False),
            patch(
                "src.agent.intent_classifier.classify_intent",
                AsyncMock(side_effect=RuntimeError("router down")),
            ),
        ):
            await h.run(FITTING_TEXT)

        assert h.llm_turns == [FITTING_TEXT]
        assert any(r.levelno >= logging.ERROR for r in caplog.records)


# ---------------------------------------------------------------------------
# TestShortCircuitRequiresProgress
# ---------------------------------------------------------------------------


class TestShortCircuitRequiresProgress:
    @staticmethod
    async def _run(ir: InterruptResult) -> Harness:
        h = Harness()
        with (
            fsm_flags(enabled=True, shadow_mode=False),
            patch(
                "src.agent.intent_classifier.classify_intent",
                AsyncMock(return_value=intent("PRICE")),
            ),
            patch(
                "src.agent.interrupts.handle_price_interrupt",
                AsyncMock(return_value=ir),
            ),
        ):
            await h.run("скільки коштує монтаж")
        return h

    async def test_handled_but_not_advanced_and_no_updates_falls_through(self) -> None:
        h = await self._run(
            interrupt(reply="Те саме речення.", advanced=False, session_updates={})
        )
        assert h.llm_turns == ["скільки коштує монтаж"]
        assert "Те саме речення." not in h.spoken
        assert LLM_REPLY in h.assistant_texts

    async def test_advanced_alone_is_enough(self) -> None:
        h = await self._run(
            interrupt(reply="Ціна 500 гривень.", advanced=True, session_updates={})
        )
        assert "Ціна 500 гривень." in h.spoken
        assert h.llm_turns == []

    async def test_session_updates_alone_is_enough(self) -> None:
        h = await self._run(
            interrupt(
                reply="Який діаметр?",
                advanced=False,
                session_updates={"pending_price_interrupt_needs_diameter": True},
            )
        )
        assert "Який діаметр?" in h.spoken
        assert h.llm_turns == []

    async def test_not_handled_falls_through(self) -> None:
        h = await self._run(interrupt(handled=False, reply="", advanced=False))
        assert h.llm_turns == ["скільки коштує монтаж"]

    async def test_empty_reply_falls_through(self) -> None:
        h = await self._run(interrupt(reply="", advanced=True))
        assert h.llm_turns == ["скільки коштує монтаж"]

    async def test_fallthrough_does_not_burn_the_dispatch_budget(self) -> None:
        h = await self._run(interrupt(advanced=False, session_updates={}))
        assert h.session.interrupt_counts.get(_PIPELINE_DISPATCH_TOTAL_KEY, 0) == 0


# ---------------------------------------------------------------------------
# TestInterruptCapAtPipeline
# ---------------------------------------------------------------------------


class TestInterruptCapAtPipeline:
    async def test_exhausted_total_budget_skips_classification_entirely(self) -> None:
        h = Harness()
        h.session.interrupt_counts[_PIPELINE_DISPATCH_TOTAL_KEY] = (
            MAX_PIPELINE_INTERRUPT_TURNS
        )
        with (
            fsm_flags(enabled=True, shadow_mode=False),
            patch("src.agent.intent_classifier.classify_intent") as classify,
            patch("src.agent.interrupts.handle_price_interrupt") as price,
        ):
            await h.run("скільки коштує монтаж")

        classify.assert_not_called()
        price.assert_not_called()
        assert h.llm_turns == ["скільки коштує монтаж"]

    async def test_consecutive_streak_forces_an_llm_turn(self) -> None:
        """The exact revert scenario: a handler that always claims progress.

        Without a pipeline-side streak cap this loops forever; with it the 4th
        turn in a row is handed back to the LLM.
        """
        h = Harness()
        with (
            fsm_flags(enabled=True, shadow_mode=False),
            patch(
                "src.agent.intent_classifier.classify_intent",
                AsyncMock(return_value=intent("PRICE")),
            ),
            patch(
                "src.agent.interrupts.handle_price_interrupt",
                AsyncMock(return_value=interrupt(reply="Одна й та сама відповідь.")),
            ),
        ):
            await h.run(*["скільки коштує монтаж"] * 5)

        dispatched = h.spoken.count("Одна й та сама відповідь.")
        assert dispatched <= MAX_CONSECUTIVE_PIPELINE_INTERRUPT_TURNS + 1
        assert h.llm_turns, "the loop must be broken by at least one LLM turn"
        assert h.session.interrupt_counts[_PIPELINE_DISPATCH_STREAK_KEY] <= (
            MAX_CONSECUTIVE_PIPELINE_INTERRUPT_TURNS
        )

    async def test_total_budget_is_never_exceeded_over_a_long_call(self) -> None:
        h = Harness()
        with (
            fsm_flags(enabled=True, shadow_mode=False),
            patch(
                "src.agent.intent_classifier.classify_intent",
                AsyncMock(return_value=intent("PRICE")),
            ),
            patch(
                "src.agent.interrupts.handle_price_interrupt",
                AsyncMock(return_value=interrupt(reply="Відповідь про ціну.")),
            ),
        ):
            await h.run(*["скільки коштує монтаж"] * 12)

        assert (
            h.session.interrupt_counts[_PIPELINE_DISPATCH_TOTAL_KEY]
            <= MAX_PIPELINE_INTERRUPT_TURNS
        )
        assert h.spoken.count("Відповідь про ціну.") <= MAX_PIPELINE_INTERRUPT_TURNS

    async def test_an_llm_turn_resets_the_streak(self) -> None:
        h = Harness()
        h.session.interrupt_counts[_PIPELINE_DISPATCH_STREAK_KEY] = 2
        with (
            fsm_flags(enabled=True, shadow_mode=False),
            patch(
                "src.agent.intent_classifier.classify_intent",
                AsyncMock(return_value=intent("BOOK")),
            ),
        ):
            await h.run(FITTING_TEXT)

        assert h.session.interrupt_counts[_PIPELINE_DISPATCH_STREAK_KEY] == 0

    async def test_counters_survive_the_redis_roundtrip(self) -> None:
        session = CallSession(uuid.uuid4())
        session.interrupt_counts[_PIPELINE_DISPATCH_TOTAL_KEY] = 3
        session.interrupt_counts[_PIPELINE_DISPATCH_STREAK_KEY] = 2
        restored = CallSession.from_dict(session.to_dict())
        assert restored.interrupt_counts[_PIPELINE_DISPATCH_TOTAL_KEY] == 3
        assert restored.interrupt_counts[_PIPELINE_DISPATCH_STREAK_KEY] == 2


# ---------------------------------------------------------------------------
# TestSessionUpdatesWhitelist
# ---------------------------------------------------------------------------


class TestSessionUpdatesWhitelist:
    async def test_whitelisted_key_is_applied(self) -> None:
        h = Harness()
        applied = h.pipeline._apply_interrupt_session_updates(
            {"pending_price_interrupt_needs_diameter": True}
        )
        assert applied == 1
        assert h.session.pending_price_interrupt_needs_diameter is True

    async def test_unknown_key_is_refused_and_logged_at_error(self, caplog) -> None:
        h = Harness()
        with caplog.at_level(logging.ERROR, logger="src.core.pipeline"):
            applied = h.pipeline._apply_interrupt_session_updates(
                {"transferred": True, "order_id": "ORD-666"}
            )

        assert applied == 0
        assert h.session.transferred is False
        assert h.session.order_id is None
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(errors) == 2, "each refused key must be reported, not batched away"

    async def test_partial_application_still_applies_the_good_keys(self, caplog) -> None:
        h = Harness()
        with caplog.at_level(logging.ERROR, logger="src.core.pipeline"):
            applied = h.pipeline._apply_interrupt_session_updates(
                {"fitting_booked": True, "definitely_not_a_field": 1}
            )
        assert applied == 1
        assert h.session.fitting_booked is True

    async def test_live_path_uses_the_whitelist(self) -> None:
        h = Harness()
        with (
            fsm_flags(enabled=True, shadow_mode=False),
            patch(
                "src.agent.intent_classifier.classify_intent",
                AsyncMock(return_value=intent("CANCEL")),
            ),
            patch(
                "src.agent.interrupts.handle_cancel_interrupt",
                AsyncMock(
                    return_value=interrupt(
                        reply="Скасувала запис.",
                        advanced=True,
                        session_updates={
                            "fitting_booked": False,
                            "caller_phone": "+380000000000",
                        },
                    )
                ),
            ),
        ):
            await h.run("скасуйте запис")

        assert "Скасувала запис." in h.spoken
        assert h.session.caller_phone != "+380000000000"


# ---------------------------------------------------------------------------
# TestLowConfidenceFallback
# ---------------------------------------------------------------------------


class TestLowConfidenceFallback:
    async def test_low_confidence_never_reaches_a_handler(self) -> None:
        h = Harness()
        with (
            fsm_flags(enabled=True, shadow_mode=False),
            patch(
                "src.agent.intent_classifier.classify_intent",
                AsyncMock(return_value=intent("PRICE", confidence=0.3)),
            ),
            patch("src.agent.interrupts.handle_price_interrupt") as price,
        ):
            await h.run("скільки коштує монтаж")

        price.assert_not_called()
        assert h.llm_turns == ["скільки коштує монтаж"]

    async def test_classifier_fallback_marker_falls_through(self) -> None:
        """`primary=BOOK, confidence=0.0` is the documented "LLM is down" marker."""
        h = Harness()
        with (
            fsm_flags(enabled=True, shadow_mode=False),
            patch(
                "src.agent.intent_classifier.classify_intent",
                AsyncMock(return_value=intent("BOOK", confidence=0.0)),
            ),
        ):
            await h.run(FITTING_TEXT)

        assert h.llm_turns == [FITTING_TEXT]

    async def test_low_confidence_compound_fields_are_not_written_to_the_fsm(
        self,
    ) -> None:
        mapped = map_compound_fields_to_fsm(
            {"city": "Дніпро", "date_hint": "5 серпня"},
            {"city": 0.4, "date_hint": 0.9},
            customer_text="Дніпро, 5 серпня",
            now=_NOW,
        )
        assert "city" not in mapped
        # Wave 6-B: `date` no longer echoes the label — it is an ISO date or it
        # is absent. `date_hint` is not in COMPOUND_TO_FSM_FIELD at all.
        assert mapped["date"] == "2026-08-05"


# ---------------------------------------------------------------------------
# TestOpenSubFlowOwnsTheAnswer
# ---------------------------------------------------------------------------


class TestOpenSubFlowOwnsTheAnswer:
    """Call `7d4b1f18` (2026-09-10) — the answer to a bot question got lost.

    Both handlers in `src/agent/interrupts.py` are multi-turn: they park a
    marker on the session, ask a question and expect the next utterance to
    answer it. Their own default-deny gates are applied only on ``entry``,
    precisely so a bare «так» or a bare diameter counts as a continuation. But
    nothing consulted those markers on the way in, so the continuation was
    unreachable: the classifier decides the turn, and it cannot label «так» —
    the word has no intent, only a referent, and the referent lives on the
    session.

    In prod that read as BOOK at 0.40, fell under the floor and reached a main
    flow parked in CITY, which asked «У якому місті вам зручніше?». The refusal
    of *that* question then reached the still-open confirmation and was read as
    «ні», so the bot said «Добре, запис залишаємо» to a caller who had just
    agreed to cancel.
    """

    CONFIRMING = "awaiting_confirmation:bb561d2e-acf7-11f1-a21c-000c29c2a50f"

    async def test_a_bare_yes_reaches_the_cancel_handler(self) -> None:
        """The regression itself, with the classifier verdict prod produced."""
        h = Harness()
        h.session.pending_cancel_action = self.CONFIRMING
        cancel = AsyncMock(return_value=interrupt(reply="Скасувала запис."))
        with (
            fsm_flags(enabled=True, shadow_mode=False),
            patch(
                "src.agent.intent_classifier.classify_intent",
                AsyncMock(return_value=intent("BOOK", confidence=0.40)),
            ),
            patch("src.agent.interrupts.handle_cancel_interrupt", cancel),
        ):
            await h.run("так")

        cancel.assert_awaited_once()
        assert cancel.await_args.kwargs["customer_text"] == "так"
        assert "Скасувала запис." in h.spoken
        assert h.llm_turns == [], "the answer must not reach the main flow"

    async def test_a_bare_diameter_reaches_the_price_handler(self) -> None:
        """`pending_price_interrupt_needs_diameter` is the same shape.

        Fixed together with CANCEL rather than after it: a guard written for a
        named subset leaves an escape hatch of identical shape behind.
        """
        h = Harness()
        h.session.pending_price_interrupt_needs_diameter = True
        price = AsyncMock(return_value=interrupt(reply="Монтаж R18 — 600 гривень."))
        with (
            fsm_flags(enabled=True, shadow_mode=False),
            patch(
                "src.agent.intent_classifier.classify_intent",
                AsyncMock(return_value=intent("BOOK")),
            ),
            patch("src.agent.interrupts.handle_price_interrupt", price),
        ):
            await h.run("вісімнадцять")

        price.assert_awaited_once()
        assert "Монтаж R18 — 600 гривень." in h.spoken
        assert h.llm_turns == []

    async def test_no_open_sub_flow_still_falls_through(self) -> None:
        """The same words, no marker — nothing may claim the turn.

        Without this the fix reads as «low confidence now dispatches», which is
        the opposite of what it says.
        """
        h = Harness()
        with (
            fsm_flags(enabled=True, shadow_mode=False),
            patch(
                "src.agent.intent_classifier.classify_intent",
                AsyncMock(return_value=intent("BOOK", confidence=0.40)),
            ),
            patch("src.agent.interrupts.handle_cancel_interrupt") as cancel,
            patch("src.agent.interrupts.handle_price_interrupt") as price,
        ):
            await h.run("так")

        cancel.assert_not_called()
        price.assert_not_called()
        assert h.llm_turns == ["так"]

    async def test_transfer_outranks_an_open_sub_flow(self) -> None:
        """A caller who asks for a human mid-confirmation gets one."""
        h = Harness()
        h.session.pending_cancel_action = self.CONFIRMING
        with (
            fsm_flags(enabled=True, shadow_mode=False),
            patch(
                "src.agent.intent_classifier.classify_intent",
                AsyncMock(return_value=intent("TRANSFER")),
            ),
            patch("src.agent.interrupts.handle_cancel_interrupt") as cancel,
        ):
            await h.run("дайте оператора")

        cancel.assert_not_called()
        assert h.session.transferred is True
        assert h.session.transfer_reason == "intent_classifier_transfer"

    async def test_classifier_failure_still_dispatches_an_open_sub_flow(self) -> None:
        """No verdict is not a verdict of BOOK.

        An open sub-flow does not need the classifier at all, so a router
        outage must not strand the caller inside a confirmation.
        """
        h = Harness()
        h.session.pending_cancel_action = self.CONFIRMING
        cancel = AsyncMock(return_value=interrupt(reply="Скасувала запис."))
        with (
            fsm_flags(enabled=True, shadow_mode=False),
            patch(
                "src.agent.intent_classifier.classify_intent",
                AsyncMock(side_effect=RuntimeError("router down")),
            ),
            patch("src.agent.interrupts.handle_cancel_interrupt", cancel),
        ):
            await h.run("так")

        cancel.assert_awaited_once()
        assert h.llm_turns == []

    async def test_the_sub_flow_closes_when_the_handler_clears_its_marker(self) -> None:
        """The marker is re-read every turn, never cached across turns.

        `_apply` in `src/agent/interrupts.py` writes onto the session in place,
        so a handler that declines — including via its own `_is_capped`
        loop-breaker — clears `pending_cancel_action` even on the no-progress
        path, where the pipeline does not replay `session_updates`. That is the
        second ceiling on an open sub-flow, and it only works if the pipeline
        asks the session again rather than remembering last turn's answer.
        """
        h = Harness()
        h.session.pending_cancel_action = self.CONFIRMING

        async def _decline(**kwargs: Any) -> InterruptResult:
            kwargs["session"].pending_cancel_action = None
            return interrupt(handled=False, advanced=False)

        cancel = AsyncMock(side_effect=_decline)
        with (
            fsm_flags(enabled=True, shadow_mode=False),
            patch(
                "src.agent.intent_classifier.classify_intent",
                AsyncMock(return_value=intent("BOOK", confidence=0.40)),
            ),
            patch("src.agent.interrupts.handle_cancel_interrupt", cancel),
        ):
            await h.run("так", "а ще питання")

        assert cancel.await_count == 1, "the closed sub-flow must not claim a second turn"
        assert h.llm_turns == ["так", "а ще питання"]


# ---------------------------------------------------------------------------
# TestContinuationAndTheCaps
# ---------------------------------------------------------------------------


class TestContinuationAndTheCaps:
    """Which of the two pipeline caps a continuation is exempt from.

    The streak cap breaks loops by forcing an LLM turn, and an open sub-flow is
    the one case where that is the bug rather than the cure — a cancel with
    several bookings is legitimately list → pick → confirm, three consecutive
    dispatches, so the cap fires at exactly the length of a *correct*
    interaction. The total cap still counts continuations: it is the only
    unconditional ceiling, and without it the reverted Wave 13 shape would have
    no backstop at all.

    This mirrors the `MAX_INTERRUPT_FIRES` / `MAX_INTERRUPT_FOLLOWUPS` split
    the handlers in `src/agent/interrupts.py` already make.
    """

    CONFIRMING = TestOpenSubFlowOwnsTheAnswer.CONFIRMING

    @staticmethod
    def _cancel_patches(handler: AsyncMock):
        return (
            fsm_flags(enabled=True, shadow_mode=False),
            patch(
                "src.agent.intent_classifier.classify_intent",
                AsyncMock(return_value=intent("BOOK", confidence=0.40)),
            ),
            patch("src.agent.interrupts.handle_cancel_interrupt", handler),
        )

    async def test_a_continuation_does_not_burn_the_streak(self) -> None:
        h = Harness()
        h.session.pending_cancel_action = self.CONFIRMING
        cancel = AsyncMock(return_value=interrupt(reply="Ще раз, будь ласка."))
        flags, classify, handler = self._cancel_patches(cancel)
        with flags, classify, handler:
            await h.run("так", "так", "так")

        # Read with a default: a continuation leaves the streak untouched —
        # neither bumped nor reset — so on a call that only ever continued a
        # sub-flow the key is never written at all.
        assert h.session.interrupt_counts.get(_PIPELINE_DISPATCH_STREAK_KEY, 0) == 0
        assert cancel.await_count == 3

    async def test_the_streak_cap_does_not_stop_an_open_sub_flow(self) -> None:
        """A sub-flow that starts on an already-exhausted streak still runs."""
        h = Harness()
        h.session.pending_cancel_action = self.CONFIRMING
        h.session.interrupt_counts[_PIPELINE_DISPATCH_STREAK_KEY] = (
            MAX_CONSECUTIVE_PIPELINE_INTERRUPT_TURNS
        )
        cancel = AsyncMock(return_value=interrupt(reply="Скасувала запис."))
        flags, classify, handler = self._cancel_patches(cancel)
        with flags, classify, handler:
            await h.run("так")

        cancel.assert_awaited_once()
        assert h.llm_turns == []

    async def test_a_continuation_still_burns_the_total_budget(self) -> None:
        h = Harness()
        h.session.pending_cancel_action = self.CONFIRMING
        cancel = AsyncMock(return_value=interrupt(reply="Скасувала запис."))
        flags, classify, handler = self._cancel_patches(cancel)
        with flags, classify, handler:
            await h.run("так")

        assert h.session.interrupt_counts[_PIPELINE_DISPATCH_TOTAL_KEY] == 1

    async def test_the_total_cap_still_stops_an_open_sub_flow(self) -> None:
        """The unconditional ceiling. Nothing, including a sub-flow, is exempt."""
        h = Harness()
        h.session.pending_cancel_action = self.CONFIRMING
        h.session.interrupt_counts[_PIPELINE_DISPATCH_TOTAL_KEY] = MAX_PIPELINE_INTERRUPT_TURNS
        cancel = AsyncMock(return_value=interrupt(reply="Скасувала запис."))
        flags, classify, handler = self._cancel_patches(cancel)
        with flags, classify, handler:
            await h.run("так")

        cancel.assert_not_called()
        assert h.llm_turns == ["так"]

    async def test_an_endless_sub_flow_is_still_bounded(self) -> None:
        """Exempting the streak must not remove the ceiling by the back door."""
        h = Harness()
        h.session.pending_cancel_action = self.CONFIRMING
        cancel = AsyncMock(return_value=interrupt(reply="Ще раз, будь ласка."))
        flags, classify, handler = self._cancel_patches(cancel)
        with flags, classify, handler:
            await h.run(*["так"] * 12)

        assert cancel.await_count == MAX_PIPELINE_INTERRUPT_TURNS
        assert h.llm_turns, "the caller must reach the LLM once the budget is out"

    async def test_main_flow_intent_is_now_logged(self, caplog) -> None:
        """The branch used to return silently.

        Four turns of call `7d4b1f18` produced no log line at all, which is why
        the first pass of the postmortem misattributed them.
        """
        h = Harness()
        with (
            caplog.at_level(logging.INFO, logger="src.core.pipeline"),
            fsm_flags(enabled=True, shadow_mode=False),
            patch(
                "src.agent.intent_classifier.classify_intent",
                AsyncMock(return_value=intent("BOOK")),
            ),
        ):
            await h.run(FITTING_TEXT)

        assert any(
            "main flow" in r.getMessage() and "BOOK" in r.getMessage() for r in caplog.records
        ), "a fall-through must be attributable from the logs alone"


# ---------------------------------------------------------------------------
# Mapping seam
# ---------------------------------------------------------------------------


class TestCompoundToFsmMapping:
    def test_hint_keys_are_translated_to_state_field_names(self) -> None:
        mapped = map_compound_fields_to_fsm(
            {"date_hint": "5 серпня", "time_hint": "10:00", "city": "Дніпро"},
            customer_text="Дніпро, 5 серпня о 10:00",
            now=_NOW,
        )
        assert mapped == {"date": "2026-08-05", "time": "10:00", "city": "Дніпро"}

    def test_date_hint_is_not_in_the_table_at_all(self) -> None:
        """The raw label must be unreachable, not merely filtered.

        `_detect_date_hint` returns «завтра» at confidence 1.0. While the key
        sat in the table, that label went straight into
        `fsm_filled_fields["date"]`, DATE's `auto_skip_if` read it as a pinned
        date, and the state was skipped on a value nothing can book — the
        `c8c6601` defect class. Removing the key makes it structurally
        impossible for a later edit to reintroduce.
        """
        from src.core.pipeline import COMPOUND_TO_FSM_FIELD

        assert "date_hint" not in COMPOUND_TO_FSM_FIELD
        assert "date" not in COMPOUND_TO_FSM_FIELD.values()

    def test_no_reference_date_means_no_date(self) -> None:
        """Default-deny: without `now` the seam produces no `date` key."""
        mapped = map_compound_fields_to_fsm(
            {"date_hint": "завтра"}, customer_text="давайте завтра"
        )
        assert "date" not in mapped

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("давайте сьогодні", "2026-08-03"),
            ("можна завтра", "2026-08-04"),
            ("післязавтра зручно", "2026-08-05"),
            ("хочу 15 березня", "2027-03-15"),
            ("запишіть на 05.08", "2026-08-05"),
            ("у п'ятницю", "2026-08-07"),
        ],
    )
    def test_every_above_threshold_hint_becomes_a_parsable_iso_date(
        self, text: str, expected: str
    ) -> None:
        mapped = map_compound_fields_to_fsm({}, {}, customer_text=text, now=_NOW)
        assert mapped["date"] == expected
        # The point of the wave in one line: whatever lands in the field, the
        # tool layer can parse it as a calendar date.
        datetime.date.fromisoformat(mapped["date"])

    def test_a_hint_below_the_threshold_yields_no_date(self) -> None:
        """«найближча» — the caller handed the choice back and named no day."""
        mapped = map_compound_fields_to_fsm(
            {}, {}, customer_text="давайте найближчу дату", now=_NOW
        )
        assert "date" not in mapped

    def test_station_hint_is_dropped_without_resolution(self) -> None:
        mapped = map_compound_fields_to_fsm({"station_hint": "біля цирку"})
        assert "station_id" not in mapped
        assert "station_hint" not in mapped

    def test_diameter_and_name_have_no_main_flow_state(self) -> None:
        mapped = map_compound_fields_to_fsm({"diameter": 17, "name": "Валерій"})
        assert mapped == {}

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("шини з собою", "own"),
            ("привезу свої шини", "own"),
            ("шини у вас на зберіганні", "contract"),
            ("заберіть зі зберігання", "contract"),
            ("хочу записатися", None),
            ("шини з собою чи зі зберігання", None),
        ],
    )
    def test_storage_choice_is_derived_from_the_raw_utterance(
        self, text: str, expected: str | None
    ) -> None:
        mapped = map_compound_fields_to_fsm({}, {}, customer_text=text)
        assert mapped.get("storage_choice") == expected

    def test_mapping_is_pure(self) -> None:
        fields = {"city": "Дніпро", "date_hint": "5 серпня"}
        confidence = {"city": 1.0, "date_hint": 0.9}
        map_compound_fields_to_fsm(fields, confidence, customer_text="шини з собою")
        assert fields == {"city": "Дніпро", "date_hint": "5 серпня"}
        assert confidence == {"city": 1.0, "date_hint": 0.9}


# ---------------------------------------------------------------------------
# Targeted pass (Wave 6-B)
# ---------------------------------------------------------------------------


class TestTargetedBeforeBroad:
    """Why the wave exists, in one class.

    The broad sweep sees a bare «16» as a diameter, an hour and a day of the
    month at once and grades it 0.6 — below the floor, correctly refused. The
    state that just asked «який діаметр?» has no such ambiguity, so the *same*
    detector on the *same* text is worth 1.0. One detector, two call sites,
    two confidences (§3.1/§3.3).
    """

    TEXT = "на 16"
    QUESTION = "Який діаметр коліс, підкажіть будь ласка?"

    def test_broad_pass_refuses_a_bare_number(self) -> None:
        from src.agent.compound_parse import compound_parse

        result = compound_parse(self.TEXT)
        assert result.fields.get("diameter") == 16
        assert result.fields_confidence["diameter"] < 0.7, (
            "the broad pass must not be sure about a homonym"
        )

    def test_targeted_pass_is_certain_once_the_bot_has_asked(self) -> None:
        from src.agent.parsers import diameter_parser
        from src.agent.parsers.base import ParseContext

        asked = diameter_parser.PARSER.parse(
            ParseContext(customer_text=self.TEXT, last_bot_utterance=self.QUESTION)
        )
        assert asked.status == "value"
        assert asked.value == 16

        unasked = diameter_parser.PARSER.parse(
            ParseContext(customer_text=self.TEXT, last_bot_utterance="У якому місті?")
        )
        assert unasked.status == "unresolved", (
            "without the question the same utterance must stay unresolved"
        )

    async def test_the_seam_hands_the_parser_the_real_last_bot_turn(self) -> None:
        """The context gates are the whole mechanism — an empty string kills them.

        `is_diameter_question`, `is_name_question` and `_bot_is_asking_storage`
        all read `ctx.last_bot_utterance`. A seam that forgets to fill it makes
        the targeted pass a slower copy of the broad one, and every test above
        would still be green.
        """
        seen: list[Any] = []
        h = Harness(booking_in_progress(FsmState.CITY))
        h.session.add_assistant_turn(self.QUESTION)

        from src.agent.parsers import city_parser

        real_parse = city_parser.PARSER.parse

        def _spy(ctx: Any) -> Any:
            seen.append(ctx)
            return real_parse(ctx)

        with (
            fsm_flags(enabled=True, shadow_mode=True),
            patch.object(city_parser.PARSER, "parse", _spy),
        ):
            await h.run(self.TEXT)

        assert seen, "the targeted parser was never called"
        assert seen[0].last_bot_utterance == self.QUESTION
        assert seen[0].state is FsmState.CITY
        assert seen[0].session is h.session
        assert seen[0].now is not None, "date_parser cannot resolve without it"

    async def test_the_broad_pass_never_overrides_the_targeted_refusal(self) -> None:
        """A deliberate «we could not pin this» is not a hole for `setdefault`.

        CITY is open, the caller names a city the resolver does not recognise.
        The targeted parser claims `city` for the turn; if the broad sweep were
        then allowed to `setdefault` its own guess, the FSM would pin exactly
        the value the targeted parser declined to pin and skip the state — the
        `c8c6601` defect class through the back door.
        """
        from src.agent.parsers import city_parser
        from src.agent.parsers.base import unresolved

        h = Harness(booking_in_progress(FsmState.CITY))
        h.session.fsm_filled_fields.pop("city", None)

        with (
            fsm_flags(enabled=True, shadow_mode=True),
            patch.object(city_parser.PARSER, "parse", lambda ctx: unresolved(0.5)),
        ):
            await h.run("у Дніпрі")

        assert "city" not in h.session.fsm_filled_fields, (
            "the broad pass wrote over the targeted parser's refusal"
        )


class TestStationAutoPinInTheSeam:
    """Wave 6-D — the rule was right, the moment it was asked was wrong.

    `STATES[STATION].auto_skip_if` is the only predicate in the table whose
    input (`session.fitting_station_ids`) is written by the tool router rather
    than by `apply_field`. `_follow_auto_skips()` had a single call site, inside
    `apply_field`, which fires on the turn the caller names the city — before
    `get_fitting_stations` has run. The predicate was asked once, at the one
    moment it is guaranteed false. Measured cost: three of the sixteen replayed
    tester calls ended in TRANSFER holding exactly one station id and no
    `station_id`.
    """

    @staticmethod
    def _tool_has_returned_one_station(session: CallSession) -> None:
        """What `get_fitting_stations` leaves in the session (`src/main.py`).

        Both halves, because the tool writes both in one loop
        (`src/main.py:2543-2545`): the id set `auto_skip_if` reads and the dict
        snapshot the resolver reads. The snapshot used to arrive from
        `booking_in_progress`, which meant every test in the file carried one.
        """
        session.fitting_station_ids = {"ST-1"}
        session.fitting_stations_seen = list(ONE_CITY_SNAPSHOT)

    async def test_the_pin_lands_on_the_turn_after_the_tool_ran(self) -> None:
        """The main test of the phase.

        The FSM is parked in STATION from an earlier turn. The stations arrived
        *between* turns, so no `apply_field` will ever be called for
        `station_id` — nothing can parse a station the caller never named. If
        the seam does not recompute, the turn is written off as a failed
        STATION answer and the call walks towards TRANSFER.
        """
        h = Harness(booking_in_progress(FsmState.STATION))
        self._tool_has_returned_one_station(h.session)

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("так, добре")

        assert h.session.fsm_filled_fields.get("station_id") == "ST-1", (
            "the single station was never pinned — auto_skip_if is still being "
            "asked only from apply_field"
        )
        assert h.session.fsm_state != FsmState.STATION.value, "pinned but never left the state"

    async def test_the_pinned_turn_is_not_charged_to_station_parser_null(self) -> None:
        """Ordering, not just presence: the pin runs ahead of the parsers.

        A pin that landed *after* the targeted pass would still fill the field,
        yet the same turn would already have been counted as a failed STATION
        answer — three of those and the call is escalated.
        """
        h = Harness(booking_in_progress(FsmState.STATION))
        self._tool_has_returned_one_station(h.session)

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("так, добре")

        assert not h.session.fsm_parser_null_counts.get(FsmState.STATION.value)

    async def test_two_stations_still_ask_and_still_charge_the_null(self) -> None:
        """The negative half: the recompute is not a blanket skip of STATION."""
        session = booking_in_progress(FsmState.STATION)
        session.fitting_station_ids = {"ST-1", "ST-2"}
        session.fitting_stations_seen = [
            {"id": "ST-1", "name": "Оболонь", "city": "Київ"},
            {"id": "ST-2", "name": "Позняки", "city": "Київ"},
        ]
        h = Harness(session)

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("так, добре")

        assert "station_id" not in h.session.fsm_filled_fields
        assert h.session.fsm_state == FsmState.STATION.value
        assert h.session.fsm_parser_null_counts.get(FsmState.STATION.value) == 1

    async def test_no_stations_yet_leaves_the_turn_exactly_as_before(self) -> None:
        """Turn N — the caller named the city, the tool has not run yet."""
        h = Harness(booking_in_progress(FsmState.STATION))

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("так, добре")

        assert "station_id" not in h.session.fsm_filled_fields
        assert h.session.fsm_state == FsmState.STATION.value
        assert h.session.fsm_parser_null_counts.get(FsmState.STATION.value) == 1

    async def test_the_pin_is_not_gated_on_live_mode(self) -> None:
        """Shadow must measure the machine live mode will actually have.

        `advance` gates the two moves the observer must not manufacture
        (`on_parser_null`, `on_interrupt_turn`). The pin is neither: it reads
        the session and writes a session field. Gating it would make every
        shadow number a number about a different state machine.
        """
        for shadow in (True, False):
            h = Harness(booking_in_progress(FsmState.STATION))
            self._tool_has_returned_one_station(h.session)
            with fsm_flags(enabled=True, shadow_mode=shadow):
                await h.run("так, добре")
            assert h.session.fsm_filled_fields.get("station_id") == "ST-1", (
                f"pin missing with shadow_mode={shadow}"
            )

    async def test_the_recompute_adds_no_await_to_the_seam(self) -> None:
        """Invariant §1.1 re-checked at the new call site.

        `TestShadowStaysOffline` asserts the same thing for the method as a
        whole; this one names `refresh_auto_skips` so that a future version of
        it that needs a DB connection breaks here with the reason attached.
        """
        import ast
        import pathlib

        import src.core.pipeline as pipeline_module

        tree = ast.parse(pathlib.Path(pipeline_module.__file__).read_text(encoding="utf-8"))
        step = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == "_run_fsm_deterministic_step"
        )
        calls = {
            n.func.attr
            for n in ast.walk(step)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        assert "refresh_auto_skips" in calls, "the recompute lost its call site"
        assert not any(isinstance(n, ast.Await) for n in ast.walk(step))
        assert not inspect.iscoroutinefunction(FsmEngine.refresh_auto_skips)


class TestShadowStaysOffline:
    """«no await → no network» — the invariant the shadow rollout rests on."""

    def test_the_deterministic_step_is_not_a_coroutine(self) -> None:
        assert not inspect.iscoroutinefunction(CallPipeline._run_fsm_deterministic_step)

    def test_no_parser_can_be_awaited_from_the_deterministic_step(self) -> None:
        """`aresolve` is the only I/O door in the package; it stays shut.

        Checked structurally rather than by mocking: `parse()` is the sole
        entry point the seam names, and `ParseContext.conn` — the gate a
        parser would need before touching the DB — defaults to `None`.
        """
        import ast
        import pathlib

        import src.core.pipeline as pipeline_module

        source = pathlib.Path(pipeline_module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        step = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == "_run_fsm_deterministic_step"
        )
        assert not isinstance(step, ast.AsyncFunctionDef)
        assert not any(isinstance(n, ast.Await) for n in ast.walk(step))
        called = {
            n.func.attr for n in ast.walk(step) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        assert "aresolve" not in called

    async def test_the_context_carries_no_connection(self) -> None:
        seen: list[Any] = []
        h = Harness(booking_in_progress(FsmState.CITY))

        from src.agent.parsers import city_parser

        real_parse = city_parser.PARSER.parse

        def _spy(ctx: Any) -> Any:
            seen.append(ctx)
            return real_parse(ctx)

        with (
            fsm_flags(enabled=True, shadow_mode=True),
            patch.object(city_parser.PARSER, "parse", _spy),
        ):
            await h.run("у Дніпрі")

        assert seen and seen[0].conn is None

    async def test_shadow_never_advances_the_machine_on_a_null(self) -> None:
        """Shadow counts the null but does not walk into `escalate_target`.

        An observer that escalates itself lands in TERMINAL, where every later
        turn of the call is skipped — so it would go blind on exactly the calls
        it is there to measure. WELCOME's default budget of 3 would be spent in
        the first three turns of any call whose intent is not auto-detected.
        """
        h = Harness(booking_in_progress(FsmState.CITY))
        h.session.fsm_filled_fields.pop("city", None)
        # Four charges need four city questions. A bot that asks once and then
        # talks about something else is a bot that is no longer asking, and
        # from the off-question exemption the budget correctly stops at one.
        bot_keeps_asking(h, "То в якому місті вам зручніше?")

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("ага", "угу", "ну", "добре")

        assert h.session.fsm_state == FsmState.CITY.value, (
            "shadow escalated a live call's FSM off the main flow"
        )
        assert h.session.fsm_parser_null_counts["CITY"] == 4, (
            "shadow must still count what it would have done"
        )


# ---------------------------------------------------------------------------
# Shared helpers extracted in Wave 4-A
# ---------------------------------------------------------------------------


class TestSharedProgressHelpers:
    def test_fitting_progress_has_a_single_builder(self) -> None:
        h = Harness()
        h.session.fitting_customer_name = "Валерій"
        h.session.fitting_stations_seen = [
            {"id": "1", "city": "Дніпро", "address": "вул. Шинна, 1"}
        ]
        progress = h.pipeline._build_fitting_progress(
            h.pipeline._resolve_selected_station(), krok8_confirmed=False
        )
        assert progress["customer_name"] == "Валерій"
        assert progress["city"] == "Дніпро"
        assert progress["station_address"] == "вул. Шинна, 1"

    def test_building_progress_does_not_consume_the_one_shot_flag(self) -> None:
        h = Harness()
        h.session.krok8_confabulation_pending = True
        h.pipeline._build_fitting_progress(None, krok8_confirmed=False)
        assert h.session.krok8_confabulation_pending is True

    def test_snapshot_merges_legacy_fields_and_fsm_fields(self) -> None:
        h = Harness()
        h.session.selected_fitting_date = "2026-09-10"
        h.session.fsm_filled_fields["intent"] = "fitting"
        snapshot = h.pipeline._fsm_filled_fields_snapshot()
        assert snapshot["date"] == "2026-09-10"
        assert snapshot["intent"] == "fitting"


# ---------------------------------------------------------------------------
# Mode resolution
# ---------------------------------------------------------------------------


class TestModeResolution:
    async def test_three_states(self) -> None:
        cases = [
            ({"enabled": False, "shadow_mode": True}, FSM_MODE_OFF),
            ({"enabled": False, "shadow_mode": False}, FSM_MODE_OFF),
            ({"enabled": True, "shadow_mode": True}, FSM_MODE_SHADOW),
            ({"enabled": True, "shadow_mode": False}, FSM_MODE_LIVE),
        ]
        for flags, expected in cases:
            h = Harness()
            with fsm_flags(**flags):  # type: ignore[arg-type]
                assert h.pipeline._fsm_mode() == expected

    async def test_mode_cannot_flip_mid_call(self) -> None:
        h = Harness()
        with fsm_flags(enabled=False):
            assert h.pipeline._fsm_mode() == FSM_MODE_OFF
        with fsm_flags(enabled=True, shadow_mode=False):
            assert h.pipeline._fsm_mode() == FSM_MODE_OFF


# ---------------------------------------------------------------------------
# Wave 4-B — freeze / resume around the handler
# ---------------------------------------------------------------------------


PRICE_TEXT = "скільки коштує монтаж"


#: What `get_fitting_stations` leaves behind once it has run for one city.
ONE_CITY_SNAPSHOT = [
    {"id": "ST-1", "name": "Шиномонтаж №1", "city": "Київ", "address": "вул. Тестова, 1"}
]


def booking_in_progress(
    state: FsmState = FsmState.CITY,
    *,
    stations: list[dict[str, Any]] | None = None,
) -> CallSession:
    """A session parked mid-booking, the way a live PRICE interrupt finds it.

    The station snapshot is opt-in. It used to be seeded for everyone, because
    the station tests below need it — but from Wave 6-H a snapshot is *itself*
    evidence: a set of offered points that agree on one city tells the seam
    which city the caller is in, and the machine leaves CITY on the strength of
    it. That is a real prod shape (call `011277ef`), not an artefact, so the
    seam is right to act on it. It just has no business being in the background
    of a test about the freeze lifecycle or about passive fills, where it turns
    every turn into a city answer and hides what the test is actually asserting.

    Tests that mean to exercise the snapshot pass `stations=ONE_CITY_SNAPSHOT`.

    The state's own question is seeded as the last thing the bot said, because
    that is the only way a live call is ever parked in a state: the machine is
    in CITY *because* it asked for a city. An empty dialog history here was an
    artefact, and from the off-question exemption it became a load-bearing one
    — `bot_is_asking` reads the last assistant turn, and with none to read
    every «this turn is still charged» test below was asserting the exemption
    rather than the charge. Tests that need a different question on screen
    call `add_assistant_turn` themselves, which lands after this one.
    """
    session = CallSession(uuid.uuid4())
    session.caller_phone = "+380671234567"
    session.last_fitting_station_id = "ST-1"
    session.fitting_stations_seen = list(stations) if stations else []
    session.fsm_state = state.value
    session.fsm_filled_fields["intent"] = "fitting"
    question = STATES[state].question_template
    if question:
        session.add_assistant_turn(question)
    return session


def bot_keeps_asking(h: Harness, question: str) -> None:
    """Make every LLM reply be the same question again.

    The default harness reply is neutral prose, so from turn two on
    `last_bot_utterance` is no longer the state's question and every exemption
    that reads it — the confirmation one, the off-question one — fires for a
    reason the test did not intend. That would make a budget test pass, or
    fail, for the wrong reason.

    It is also the real shape. A charge makes the FSM re-ask, and
    `_maybe_speak_fsm_question` refuses to say the same sentence twice in a
    row (`outcome="repeat"`, the `c8c6601` symptom), so the LLM rephrases the
    same question instead — which is precisely what `question_markers` are
    built to recognise. `fe1857ba` asked «Записуємо туди?» three times.
    """

    async def _run_turn(**kwargs: Any) -> TurnResult:
        h.llm_turns.append(kwargs.get("user_text", ""))
        h.llm_kwargs.append(kwargs)
        return TurnResult(
            spoken_text=question,
            tool_calls_made=0,
            stop_reason="end_turn",
            total_usage=Usage(10, 5),
        )

    h.streaming_loop.run_turn = _run_turn


def bot_says(h: Harness, *replies: str) -> None:
    """Script the bot's side of the dialogue, one reply per turn.

    `bot_keeps_asking` covers the case where the bot repeats itself; a replay of
    a real call needs the bot to say a *different* thing each turn, because what
    the caller is answering changes underneath a motionless FSM. That is the
    whole shape of the four transfers below.

    Replies run out silently and the last one repeats, so a caller turn with no
    bot line after it does not need a filler.
    """

    async def _run_turn(**kwargs: Any) -> TurnResult:
        h.llm_turns.append(kwargs.get("user_text", ""))
        h.llm_kwargs.append(kwargs)
        index = min(len(h.llm_turns) - 1, len(replies) - 1)
        return TurnResult(
            spoken_text=replies[index] if replies else LLM_REPLY,
            tool_calls_made=0,
            stop_reason="end_turn",
            total_usage=Usage(10, 5),
        )

    h.streaming_loop.run_turn = _run_turn


def replay(state: FsmState, turns: list[tuple[str, str]]) -> Harness:
    """Build a harness that replays `(bot said, caller said)` pairs in `state`.

    The bot line of pair N is what is on screen when caller line N arrives, so
    the first one is seeded into the history and the rest are handed to the LLM
    mock as the reply it produces *after* the preceding caller turn.
    """
    h = Harness(booking_in_progress(state))
    h.session.add_assistant_turn(turns[0][0])
    bot_says(h, *[bot for bot, _ in turns[1:]])
    return h


def fsm_hops(session: CallSession) -> list[tuple[str | None, str]]:
    return [(h["from"], h["to"]) for h in session.fsm_history]


def freeze_hops(session: CallSession) -> list[tuple[str | None, str]]:
    """Hops in and out of a side-state, i.e. the freeze/resume pair only.

    Wave 6-B: `fsm_history` is no longer empty on a turn the FSM did not own.
    A PARSER_NULL re-ask is recorded as a self-transition (`CITY → CITY`), so
    `fsm_hops(...) == []` stopped meaning «the FSM stayed out of it» and
    started meaning «the FSM never even looked at the turn». The freeze
    lifecycle is what this class is about, so it asserts on the freeze hops.
    """
    return [(a, b) for a, b in fsm_hops(session) if a != b and (a in FROZEN or b in FROZEN)]


FROZEN = {s.value for s in FROZEN_STATES}


class TestPipelineFreezeLifecycle:
    """The FSM enters the side-state for the handler — and always leaves it."""

    @staticmethod
    async def _run(
        ir: InterruptResult | Exception,
        *,
        state: FsmState = FsmState.CITY,
        confidence: float = 0.9,
        session: CallSession | None = None,
    ) -> Harness:
        h = Harness(session or booking_in_progress(state))
        handler = (
            AsyncMock(side_effect=ir)
            if isinstance(ir, Exception)
            else AsyncMock(return_value=ir)
        )
        with (
            fsm_flags(enabled=True, shadow_mode=False),
            patch(
                "src.agent.intent_classifier.classify_intent",
                AsyncMock(return_value=intent("PRICE", confidence)),
            ),
            patch("src.agent.interrupts.handle_price_interrupt", handler),
        ):
            await h.run(PRICE_TEXT)
        return h

    async def test_live_interrupt_freezes_speaks_and_comes_back(self) -> None:
        h = await self._run(interrupt(reply="Монтаж R17 коштує 500 гривень."))

        assert "Монтаж R17 коштує 500 гривень." in h.spoken
        hops = fsm_hops(h.session)
        assert ("CITY", "PRICE_INTERRUPT") in hops, "the side-state was never entered"
        assert ("PRICE_INTERRUPT", "CITY") in hops, "the caller was never brought back"
        assert h.session.fsm_state == FsmState.CITY.value
        assert h.session.fsm_prev_state is None

    async def test_no_progress_unfreezes_and_hands_the_turn_to_the_llm(self) -> None:
        h = await self._run(interrupt(reply="Те саме речення.", advanced=False, session_updates={}))

        assert h.llm_turns == [PRICE_TEXT], "the fallthrough to the LLM must still happen"
        assert h.session.fsm_state != FsmState.PRICE_INTERRUPT.value, (
            "a handler that proved nothing left the FSM stranded in the side-state"
        )
        assert h.session.fsm_state == FsmState.CITY.value
        assert h.session.fsm_prev_state is None

    async def test_handler_exception_unfreezes_and_is_logged_at_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.ERROR, logger="src.core.pipeline"):
            h = await self._run(RuntimeError("handler blew up"))

        assert h.llm_turns == [PRICE_TEXT]
        assert h.session.fsm_state == FsmState.CITY.value
        assert h.session.fsm_prev_state is None
        assert any(r.levelno >= logging.ERROR for r in caplog.records)

    async def test_exhausted_cap_never_freezes(self) -> None:
        session = booking_in_progress()
        session.interrupt_counts[_PIPELINE_DISPATCH_TOTAL_KEY] = MAX_PIPELINE_INTERRUPT_TURNS
        h = await self._run(interrupt(), session=session)

        assert freeze_hops(h.session) == [], "a capped turn froze the FSM anyway"
        assert h.session.fsm_state == FsmState.CITY.value
        assert h.llm_turns == [PRICE_TEXT]

    async def test_low_confidence_never_freezes(self) -> None:
        h = await self._run(interrupt(), confidence=0.2)

        assert freeze_hops(h.session) == [], "a turn below the confidence floor froze the FSM"
        assert h.session.fsm_state == FsmState.CITY.value
        assert h.llm_turns == [PRICE_TEXT]

    async def test_a_dispatched_interrupt_does_not_cost_the_state_a_parser_null(
        self,
    ) -> None:
        """The freeze/resume pair pays back the null the seam charged.

        `_run_fsm_deterministic_step` runs *before* `_maybe_handle_intent`, so
        a PRICE question does spend one of CITY's three `max_parser_null`
        attempts on the way in. Leaving CITY for PRICE_INTERRUPT clears the
        state's budget, and coming back leaves it at zero — so a caller who
        asks about price three times does **not** get escalated for it.
        """
        h = await self._run(interrupt(reply="Монтаж R17 коштує 500 гривень."))

        assert h.session.fsm_parser_null_counts.get(FsmState.CITY.value) in (None, 0)

    async def test_an_undispatched_interrupt_costs_an_interrupt_turn_not_a_null(
        self,
    ) -> None:
        """Wave 6-B «Finding 3», now fixed.

        When the pipeline *declines* to dispatch (cap spent, or the classifier
        below the confidence floor) there is no freeze to pay the null back, so
        the turn used to land on the LLM **and** cost CITY one of its three
        `max_parser_null` attempts — three price questions and the caller met
        an operator. The seam now recognises the question by markers, which it
        can do in shadow too, and charges the separate interrupt budget.
        """
        h = await self._run(interrupt(), confidence=0.2)

        assert h.session.fsm_parser_null_counts.get(FsmState.CITY.value) in (None, 0)
        assert h.session.fsm_interrupt_turn_counts.get(FsmState.CITY.value) == 1
        assert h.llm_turns == [PRICE_TEXT], "the fallthrough to the LLM must still happen"

    async def test_repeated_undispatched_questions_do_not_reach_an_operator(
        self,
    ) -> None:
        """The whole point of the change, end to end at the seam."""
        session = booking_in_progress(FsmState.CITY)
        h = Harness(session)
        with (
            fsm_flags(enabled=True, shadow_mode=False),
            patch(
                "src.agent.intent_classifier.classify_intent",
                AsyncMock(return_value=intent("PRICE", 0.2)),
            ),
        ):
            await h.run(PRICE_TEXT, "а скільки це коштує?", "що по ціні")

        assert h.session.fsm_state == FsmState.CITY.value
        assert h.session.fsm_interrupt_turn_counts[FsmState.CITY.value] == 3

    async def test_the_interrupt_budget_is_itself_bounded(self) -> None:
        """An uncharged escape from a loop-breaker is the same loop one level
        up — the shape of `c8c6601`. A caller circling CITY with questions the
        pipeline will not dispatch still ends up with a human."""
        session = booking_in_progress(FsmState.CITY)
        h = Harness(session)
        cap = STATES[FsmState.CITY].max_interrupt_turns
        with (
            fsm_flags(enabled=True, shadow_mode=False),
            patch(
                "src.agent.intent_classifier.classify_intent",
                AsyncMock(return_value=intent("PRICE", 0.2)),
            ),
        ):
            await h.run(*[PRICE_TEXT] * cap)

        assert h.session.fsm_state == STATES[FsmState.CITY].escalate_target.value

    async def test_a_genuine_non_answer_still_costs_a_parser_null(self) -> None:
        """The mirror defect: if the detector fired on ordinary speech, every
        misheard answer would be exempt and the state would re-ask forever."""
        session = booking_in_progress(FsmState.CITY)
        session.fsm_filled_fields.pop("city", None)
        h = Harness(session)
        with (
            fsm_flags(enabled=True, shadow_mode=False),
            patch(
                "src.agent.intent_classifier.classify_intent",
                AsyncMock(return_value=intent("OTHER", 0.9)),
            ),
        ):
            await h.run("ну")

        assert h.session.fsm_parser_null_counts.get(FsmState.CITY.value) == 1
        assert h.session.fsm_interrupt_turn_counts.get(FsmState.CITY.value) in (None, 0)

    async def test_freeze_and_resume_survive_the_redis_roundtrip(self) -> None:
        # Mid-interrupt snapshot: exactly what a session looks like between the
        # freeze and the resume, then round-tripped through Redis.
        session = booking_in_progress()
        engine = FsmEngine(session)
        engine.freeze_for_interrupt(FsmEvent.INTERRUPT_PRICE)
        assert session.fsm_prev_state == "CITY"

        revived = CallSession.from_dict(session.to_dict())
        assert revived.fsm_state == FsmState.PRICE_INTERRUPT.value
        assert revived.fsm_prev_state == "CITY"

        FsmEngine(revived).resume()
        assert revived.fsm_state == FsmState.CITY.value
        assert revived.fsm_prev_state is None

    async def test_flow_that_cannot_be_resumed_is_never_frozen(self) -> None:
        # WELCOME has nothing to return to. Freezing it would make the eventual
        # resume() fall through to DONE and end a call that never started.
        h = await self._run(interrupt(), state=FsmState.WELCOME)

        assert h.session.fsm_prev_state is None
        assert ("WELCOME", "PRICE_INTERRUPT") not in fsm_hops(h.session)
        assert h.session.fsm_state != FsmState.DONE.value


class TestFreezeDoesNotDoubleTheResumePhrase:
    """The Wave 3-B ↔ 4-B seam (README §4), asserted on the real handler."""

    @staticmethod
    async def _run(session: CallSession, text: str = PRICE_TEXT) -> Harness:
        h = Harness(session)
        h.tool_router = MagicMock(spec=["get_fitting_price"])
        h.tool_router.get_fitting_price = AsyncMock(
            return_value={"prices": [{"service": "Комплекс R17", "price": "450"}]}
        )
        h.streaming_loop._tool_router = h.tool_router
        with (
            fsm_flags(enabled=True, shadow_mode=False),
            patch(
                "src.agent.intent_classifier.classify_intent",
                AsyncMock(return_value=intent("PRICE")),
            ),
        ):
            await h.run(text)
        return h

    async def test_the_caller_is_told_where_the_booking_resumes(self) -> None:
        # The silent bug: freeze puts the session in PRICE_INTERRUPT, which is
        # not in FROZEN_STATES, so the handler used to resolve (None, "") and
        # quote a price with no word about the booking. handled=True, non-empty
        # reply, made_progress satisfied — nothing else catches it.
        session = booking_in_progress()
        session.fitting_diameter_client = 17
        h = await self._run(session)

        assert h.spoken, "the interrupt reply never reached the caller"
        reply = h.spoken[-1]
        assert STATES[FsmState.CITY].resume_phrase in reply, reply

    async def test_the_resume_phrase_is_said_exactly_once(self) -> None:
        session = booking_in_progress()
        session.fitting_diameter_client = 17
        h = await self._run(session)

        reply = h.spoken[-1]
        phrase = STATES[FsmState.CITY].resume_phrase
        assert reply.count(phrase) == 1, f"resume phrase repeated: {reply!r}"

    async def test_the_price_is_still_quoted_alongside_it(self) -> None:
        session = booking_in_progress()
        session.fitting_diameter_client = 17
        h = await self._run(session)

        reply = h.spoken[-1]
        assert "R17" in reply
        assert "450" in reply

    async def test_a_call_with_no_booking_hears_no_resume_phrase(self) -> None:
        # FSM live but the flow never reached a resumable state: the c8c6601
        # invitation to continue a booking that does not exist must not appear.
        # A price question with no fitting keyword in it, on a session with no
        # intent pinned, leaves the deterministic step parked in WELCOME.
        session = CallSession(uuid.uuid4())
        session.caller_phone = "+380671234567"
        session.fitting_diameter_client = 17
        h = await self._run(session, text="а яка ціна")

        assert h.session.fsm_state == FsmState.WELCOME.value
        assert h.session.fsm_prev_state is None
        reply = h.spoken[-1] if h.spoken else ""
        assert reply, "the price answer itself must still be spoken"
        for state in FROZEN_STATES:
            assert STATES[state].resume_phrase not in reply, reply


# ---------------------------------------------------------------------------
# Wave 6-C — the passive pass
# ---------------------------------------------------------------------------


NAME_QUESTION = "Як до вас звертатися?"
DIAMETER_QUESTION = "Який діаметр коліс, підкажіть будь ласка?"


def with_registry(passive: tuple[str, ...], extra: dict[str, Any] | None = None):
    """Patch `PASSIVE_PARSERS` (and optionally `PARSERS`) for one test.

    The seam imports both names from `src.agent.parsers.registry` *inside* the
    method, so patching the module attributes is what the production code
    actually reads — not a copy that only the test sees.
    """
    from src.agent.parsers import registry

    parsers = dict(registry.PARSERS)
    parsers.update(extra or {})
    return (
        patch.object(registry, "PASSIVE_PARSERS", passive),
        patch.object(registry, "PARSERS", parsers),
    )


class TestPassivePass:
    """`PASSIVE_PARSERS` had zero call sites in `src/` — this is the one.

    `name` and `diameter` belong to no MAIN_FLOW state, so before Wave 6-C
    nothing ever wrote them into `fsm_filled_fields`, while CONFIRM lists
    `name` in `required_context`. The measured cost: the bot asks «Як до вас
    звертатися?» with the FSM parked in CITY, and the correct answer is charged
    to CITY's `max_parser_null` — 8 times in one day.
    """

    async def test_the_name_answer_lands_while_the_fsm_waits_for_a_city(self) -> None:
        h = Harness(booking_in_progress(FsmState.CITY))
        h.session.fsm_filled_fields.pop("city", None)
        h.session.add_assistant_turn(NAME_QUESTION)

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("Юра")

        assert h.session.fsm_filled_fields.get("name") == "Юра"

    async def test_the_machine_does_not_move_on_a_passive_fill(self) -> None:
        """A passive parser must never reach `apply_field`.

        `apply_field` advances to the *current* state's `next_state` whatever
        field it is handed, so `apply_field("name", …)` in CITY would hop the
        FSM to STATION on a turn that said nothing about a station — `c8c6601`
        through the back door (`registry.py:22-25`).
        """
        h = Harness(booking_in_progress(FsmState.CITY))
        h.session.fsm_filled_fields.pop("city", None)
        h.session.add_assistant_turn(NAME_QUESTION)

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("Юра")

        assert h.session.fsm_state == FsmState.CITY.value
        assert ("CITY", "STATION") not in fsm_hops(h.session)

    async def test_answering_the_name_question_is_not_charged_to_the_city(self) -> None:
        """The 8 write-offs. Same exemption shape as an interrupt turn.

        The caller answered the question the bot actually asked. Charging that
        to CITY's `max_parser_null` is what walked calls towards an operator
        three turns early.
        """
        h = Harness(booking_in_progress(FsmState.CITY))
        h.session.fsm_filled_fields.pop("city", None)
        h.session.add_assistant_turn(NAME_QUESTION)

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("Юра")

        assert h.session.fsm_parser_null_counts.get("CITY", 0) == 0

    async def test_the_passive_fill_happens_once_and_does_not_overwrite(self) -> None:
        """The loop-breaker is structural, not a cap to remember.

        A passive parser runs only while its field is empty, so `name` can be
        written once and a later turn cannot move it.

        This used to assert the *charge* bound — «the second turn costs one» —
        and that reading is no longer reachable. `name_parser` is gated on the
        bot having asked for a name (`is_name_question`), and a turn where the
        bot asked for a name is off-question for CITY, so the off-question
        branch takes it before the passive bound can matter. The passive
        exemption's own cap is therefore dead code for `name` today; what is
        still live, and still worth holding, is that the fill is one-shot.
        """
        h = Harness(booking_in_progress(FsmState.CITY))
        h.session.fsm_filled_fields.pop("city", None)
        bot_keeps_asking(h, NAME_QUESTION)
        h.session.add_assistant_turn(NAME_QUESTION)

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("Юра", "Оксана")

        assert h.session.fsm_filled_fields.get("name") == "Юра"

    async def test_a_turn_with_no_passive_fill_is_still_charged(self) -> None:
        h = Harness(booking_in_progress(FsmState.CITY))
        h.session.fsm_filled_fields.pop("city", None)

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("ага")

        assert h.session.fsm_parser_null_counts.get("CITY", 0) == 1

    async def test_the_name_question_is_excused_by_the_off_question_branch(self) -> None:
        """What the passive exemption used to be the only cover for.

        The 8 write-offs this class was written for came from the bot asking
        «Як до вас звертатися?» with the FSM parked in CITY. The passive pass
        excused the turn where the caller gave a name; it could do nothing
        about the turn where they said «ага». Both are off-question now, and
        neither costs CITY anything — the caller was never asked for a city.
        """
        h = Harness(booking_in_progress(FsmState.CITY))
        h.session.fsm_filled_fields.pop("city", None)
        bot_keeps_asking(h, NAME_QUESTION)
        h.session.add_assistant_turn(NAME_QUESTION)

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("Юра", "ага")

        assert h.session.fsm_parser_null_counts.get("CITY", 0) == 0

    async def test_a_bare_ambivalent_landmark_does_not_pin_a_city(self) -> None:
        """b394f6c1, turn 1 — the first half of the chain that killed the call.

        The caller named «перемозі» and nothing else. That landmark exists in
        two cities, so the seam must leave `city` empty and let the bot ask.
        It used to write Запоріжжя at 0.9 (over the 0.7 threshold), and because
        filled fields are written with `setdefault` the caller's explicit
        «Днепро» four turns later could never overwrite it.
        """
        h = Harness(booking_in_progress(FsmState.CITY))
        h.session.fsm_filled_fields.pop("city", None)

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("запишіть мене на монтаж на перемозі")

        assert "city" not in h.session.fsm_filled_fields

    async def test_the_diameter_is_captured_from_the_main_flow(self) -> None:
        """`diameter_parser` is state-bound *and* passive.

        No MAIN_FLOW state owns `diameter` — only the PRICE_INTERRUPT side
        door does — so without the passive pass a diameter named in CITY is
        lost to the FSM entirely.
        """
        h = Harness(booking_in_progress(FsmState.CITY))
        h.session.fsm_filled_fields.pop("city", None)
        h.session.add_assistant_turn(DIAMETER_QUESTION)

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("R16")

        assert h.session.fsm_filled_fields.get("diameter") == 16
        assert h.session.fsm_state == FsmState.CITY.value

    async def test_the_state_that_owns_the_field_runs_its_parser_once(self) -> None:
        """PRICE_INTERRUPT owns `diameter`; the passive pass must skip it.

        `field_name == own_field` is the skip, and `claimed` is only ever
        `own_field`, so this one condition is also what keeps the passive pass
        from reaching around the targeted parser's refusal.
        """
        from src.agent.parsers import diameter_parser

        calls: list[Any] = []
        real_parse = diameter_parser.PARSER.parse

        def _spy(ctx: Any) -> Any:
            calls.append(ctx)
            return real_parse(ctx)

        h = Harness(booking_in_progress(FsmState.PRICE_INTERRUPT))
        h.session.add_assistant_turn(DIAMETER_QUESTION)

        with (
            fsm_flags(enabled=True, shadow_mode=True),
            patch.object(diameter_parser.PARSER, "parse", _spy),
        ):
            # A turn with no diameter in it on purpose: the targeted pass then
            # leaves `diameter` empty, so the «field already filled» skip can
            # not be what stops the second run. Only the `own_field` skip can.
            await h.run("ага")

        assert len(calls) == 1, "the passive pass ran the state's own parser again"

    async def test_the_passive_pass_does_not_reach_around_a_refusal(self) -> None:
        """The semantic half of the same skip.

        The targeted parser refuses `diameter` in PRICE_INTERRUPT. If the
        passive pass re-ran it, a second answer would land on a field the state
        deliberately declined to pin — exactly what `claimed` exists to stop.
        The spy answers `unresolved` first and a value second, so the two
        behaviours are distinguishable by the field alone.
        """
        from src.agent.parsers import diameter_parser
        from src.agent.parsers.base import graded, unresolved

        answers = [unresolved(0.6, value=16), graded(16, 1.0)]

        def _spy(ctx: Any) -> Any:
            return answers.pop(0) if answers else graded(16, 1.0)

        h = Harness(booking_in_progress(FsmState.PRICE_INTERRUPT))
        h.session.add_assistant_turn(DIAMETER_QUESTION)

        with (
            fsm_flags(enabled=True, shadow_mode=True),
            patch.object(diameter_parser.PARSER, "parse", _spy),
        ):
            await h.run("на 16")

        assert "diameter" not in h.session.fsm_filled_fields

    async def test_a_filled_field_is_not_reparsed(self) -> None:
        """«Run on every turn *while the field is empty*» (`registry.py:83`)."""
        from src.agent.parsers import name_parser

        calls: list[Any] = []

        def _spy(ctx: Any) -> Any:
            calls.append(ctx)
            raise AssertionError("the passive pass reparsed a filled field")

        h = Harness(booking_in_progress(FsmState.CITY))
        h.session.fsm_filled_fields.pop("city", None)
        h.session.fsm_filled_fields["name"] = "Олена"
        h.session.add_assistant_turn(NAME_QUESTION)

        with (
            fsm_flags(enabled=True, shadow_mode=True),
            patch.object(name_parser.PARSER, "parse", _spy),
        ):
            await h.run("Юра")

        assert not calls
        assert h.session.fsm_filled_fields["name"] == "Олена"

    async def test_two_passive_parsers_on_one_field_do_not_overwrite(self) -> None:
        """The first write of the turn wins, whoever the second writer is.

        Honest about what stops it: the «field already filled» guard, re-checked
        on every iteration, is what makes the second parser a no-op here —
        `setdefault` is the second layer and does not change the outcome. The
        one input where the two layers disagree is pinned separately, in
        `test_an_empty_string_already_in_the_map_is_left_alone`.
        """
        from src.agent.parsers.base import graded

        class _SecondNameParser:
            name = "second_name_parser"
            field_name = "name"
            aresolve = None

            def parse(self, ctx: Any) -> Any:
                return graded("Другий", 1.0)

        passive_patch, parsers_patch = with_registry(
            ("name_parser", "second_name_parser"),
            {"second_name_parser": _SecondNameParser()},
        )

        h = Harness(booking_in_progress(FsmState.CITY))
        h.session.fsm_filled_fields.pop("city", None)
        h.session.add_assistant_turn(NAME_QUESTION)

        with fsm_flags(enabled=True, shadow_mode=True), passive_patch, parsers_patch:
            await h.run("Юра")

        assert h.session.fsm_filled_fields["name"] == "Юра"

    async def test_an_empty_string_already_in_the_map_is_left_alone(self) -> None:
        """The one input that tells `setdefault` from a plain assignment.

        The «already filled» guard reads the *value* (`not in (None, "")`), so
        a key sitting there with an empty string passes it. `setdefault` reads
        the *key*, so the passive pass still does not write. That is the
        intended order of precedence: whoever put the key in `fsm_filled_fields`
        did so deliberately, and the passive pass is the lowest-priority writer
        in this seam — it must not be the one that decides another writer was
        wrong. Without this test the two guards fully overlap and
        `setdefault → =` is an undetectable mutation.
        """
        h = Harness(booking_in_progress(FsmState.CITY))
        h.session.fsm_filled_fields.pop("city", None)
        h.session.fsm_filled_fields["name"] = ""
        h.session.add_assistant_turn(NAME_QUESTION)

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("Юра")

        assert h.session.fsm_filled_fields["name"] == ""

    @pytest.mark.parametrize("text", ["Ммм", "Що?", "Завтра", "Оболонь"])
    async def test_without_the_question_a_filler_is_not_a_name(self, text: str) -> None:
        """The `is_name_question` gate is not optional.

        Ungated, `detect_name` accepts every one of these, and a wrong name is
        how «Марина» — the bot's own name — reached `book_fitting` on call
        fcfb26a9.
        """
        h = Harness(booking_in_progress(FsmState.CITY))
        h.session.fsm_filled_fields.pop("city", None)
        h.session.add_assistant_turn("У якому місті вам зручніше?")

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run(text)

        assert "name" not in h.session.fsm_filled_fields

    async def test_an_explicit_self_introduction_still_counts(self) -> None:
        h = Harness(booking_in_progress(FsmState.CITY))
        h.session.fsm_filled_fields.pop("city", None)
        h.session.add_assistant_turn("У якому місті вам зручніше?")

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("мене звати Олена")

        assert h.session.fsm_filled_fields.get("name") == "Олена"

    async def test_the_name_never_reaches_the_seams_log_lines(self, caplog) -> None:
        """`name` is PII: the seam's own lines print fields, not values.

        Scoped to the lines this seam emits. The legacy blocks further down
        `_transcript_processor_loop` («got transcript», «Name auto-detected»)
        already print the raw utterance and are not this wave's to change — but
        the FSM seam must not become a second place that does.
        """
        h = Harness(booking_in_progress(FsmState.CITY))
        h.session.fsm_filled_fields.pop("city", None)
        h.session.add_assistant_turn(NAME_QUESTION)

        with (
            caplog.at_level(logging.INFO, logger="src.core.pipeline"),
            fsm_flags(enabled=True, shadow_mode=True),
        ):
            await h.run("Юра")

        seam = [
            r.getMessage()
            for r in caplog.records
            if r.getMessage().startswith(
                ("fsm_passive_fill", "fsm_parser_null_excused", "fsm_shadow")
            )
        ]
        assert any(m.startswith("fsm_passive_fill") for m in seam), (
            "a passive fill must be visible in the log"
        )
        for message in seam:
            assert "Юра" not in message, message

    async def test_the_passive_pass_carries_no_connection(self) -> None:
        """Same `ctx` as the targeted pass — no second `ParseContext`, no `conn`."""
        seen: list[Any] = []

        from src.agent.parsers import name_parser

        real_parse = name_parser.PARSER.parse

        def _spy(ctx: Any) -> Any:
            seen.append(ctx)
            return real_parse(ctx)

        h = Harness(booking_in_progress(FsmState.CITY))
        h.session.fsm_filled_fields.pop("city", None)
        h.session.add_assistant_turn(NAME_QUESTION)

        with (
            fsm_flags(enabled=True, shadow_mode=True),
            patch.object(name_parser.PARSER, "parse", _spy),
        ):
            await h.run("Юра")

        assert seen and seen[0].conn is None
        assert seen[0].last_bot_utterance == NAME_QUESTION


class TestPassivePassStructure:
    """The one rule that has to hold whatever the loop is rewritten into."""

    def test_no_apply_field_between_the_claim_and_the_broad_pass(self) -> None:
        """`apply_field` is called at most once, and never by the passive pass.

        Asserted on the source because the behavioural test above can only
        catch the states where a stray hop is observable; this catches it
        everywhere.
        """
        import ast
        import pathlib

        import src.core.pipeline as pipeline_module

        source = pathlib.Path(pipeline_module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        step = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == "_run_fsm_deterministic_step"
        )
        passive_loops = [
            node
            for node in ast.walk(step)
            if isinstance(node, ast.For)
            and isinstance(node.iter, ast.Name)
            and node.iter.id == "PASSIVE_PARSERS"
        ]
        assert len(passive_loops) == 1, "the passive pass is not a single loop"
        called = {
            n.func.attr
            for n in ast.walk(passive_loops[0])
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        assert "apply_field" not in called
        assert "on_parser_null" not in called
        assert "transition" not in called

    def test_the_registry_still_lists_exactly_two_passive_parsers(self) -> None:
        """A third one would need its own `own_field`/PII review first."""
        from src.agent.parsers.registry import PASSIVE_PARSERS

        assert PASSIVE_PARSERS == ("name_parser", "diameter_parser")


# ---------------------------------------------------------------------------
# Wave 6-F — an answer to another question is not a failed answer
# ---------------------------------------------------------------------------


class TestAnsweredElsewhereExemption:
    """Class D: the bot ran ahead, and the FSM charged the caller for it.

    On all four class-D calls of the 16-call corpus the caller named a field
    *below* the current state in MAIN_FLOW — `b394f6c1` answered the colour
    while the FSM waited on a date, because one bot utterance had merged the
    storage and colour questions. Four calls reached an operator that way.

    The value was never lost: the broad pass stores it and `auto_skip_if`
    skips that state later. Only the charge was wrong.
    """

    def _in_date(self) -> Harness:
        h = Harness(booking_in_progress(FsmState.DATE))
        h.session.fsm_filled_fields["city"] = "Дніпро"
        h.session.fsm_filled_fields["station_id"] = "ST-1"
        h.session.fsm_filled_fields["storage_choice"] = "own"
        return h

    async def test_the_colour_answer_is_not_charged_to_the_date(self) -> None:
        h = self._in_date()

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("белый")

        assert h.session.fsm_filled_fields.get("color") == "білий"
        assert h.session.fsm_parser_null_counts.get("DATE", 0) == 0

    async def test_repeating_an_answer_the_fsm_already_holds_is_charged(self) -> None:
        """The loop-breaker, and the exact turn sequence of `b394f6c1`.

        Only a field going empty → filled excuses a turn, and a field can do
        that once per call. So the first «белый» is free and the two restatements
        the caller made when the bot kept re-asking are charged normally — the
        bound is `setdefault`, not a constant someone must remember to lower.
        """
        h = self._in_date()
        bot_keeps_asking(h, "На яку дату записуємо?")

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("белый", "oler белый", "колер белый")

        assert h.session.fsm_parser_null_counts.get("DATE", 0) == 2

    async def test_a_turn_that_answers_nothing_is_still_charged(self) -> None:
        h = self._in_date()

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("Алло")

        assert h.session.fsm_parser_null_counts.get("DATE", 0) == 1

    async def test_the_states_own_field_cannot_excuse_itself(self) -> None:
        """`claimed` already locks the broad pass out of `own_field`.

        Were it not locked, a targeted parser's deliberate refusal («they spoke
        about a date and named none») could be laundered into an exemption by
        the very sweep that is forbidden to overwrite it — `c8c6601` arriving
        through the back door, this time as a free turn instead of a value.
        """
        h = Harness(booking_in_progress(FsmState.CITY))
        h.session.fsm_filled_fields.pop("city", None)

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("не знаю навіть")

        assert h.session.fsm_filled_fields.get("city") in (None, "")
        assert h.session.fsm_parser_null_counts.get("CITY", 0) == 1

    async def test_the_exemption_does_not_move_the_machine(self) -> None:
        """Excusing changes the counter and nothing else.

        `apply_field` is still called at most once, for the current state's own
        field. A colour answered in DATE must not hop the FSM to TIME.
        """
        h = self._in_date()

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("белый")

        assert h.session.fsm_state == FsmState.DATE.value
        assert ("DATE", "TIME") not in fsm_hops(h.session)

    async def test_live_mode_excuses_it_too(self) -> None:
        """The exemption is about what the turn *was*, not about the mode.

        Shadow is the observer, live is the one that escalates — a rule that
        held in only one of them would mean the shadow numbers do not describe
        the thing that would ship.
        """
        h = self._in_date()

        with fsm_flags(enabled=True, shadow_mode=False):
            await h.run("белый")

        assert h.session.fsm_parser_null_counts.get("DATE", 0) == 0
        assert h.session.fsm_state == FsmState.DATE.value

    async def test_an_interrupt_still_wins_over_this_branch(self) -> None:
        """Order in the ladder: interrupt first, then passive, then this.

        The text has to be *both* to test anything. A bare «скільки коштує
        монтаж» maps no field at all, so it takes the interrupt branch whatever
        order the ladder is in — an earlier version of this test asserted
        nothing and passed under the mutation it was written to catch.

        Interrupts have their own capped budget, which escalates to a human
        rather than defaulting a field. A turn booked as an answer instead
        would stop spending it, and a caller circling one state with price
        questions would never reach anyone.
        """
        h = self._in_date()

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("скільки коштує монтаж на білому")

        assert h.session.fsm_interrupt_turn_counts.get("DATE", 0) == 1
        assert h.session.fsm_parser_null_counts.get("DATE", 0) == 0

    async def test_the_log_line_names_the_field_and_not_its_value(self, caplog) -> None:
        """`name` cannot reach `mapped`, but the logger must not rely on that.

        `COMPOUND_TO_FSM_FIELD` omits `name` on purpose; that is a property of
        a table one edit away from changing, not a property of this log line.
        """
        h = self._in_date()

        with caplog.at_level(logging.INFO), fsm_flags(enabled=True, shadow_mode=True):
            await h.run("белый")

        lines = [r.getMessage() for r in caplog.records if "answered_elsewhere" in r.getMessage()]
        assert lines, "the exemption must be visible in prod logs"
        assert "color" in lines[0]
        assert "білий" not in lines[0]


#: `fe1857ba` / `cf43d623` turn 10 — a confirmation belonging to no MAIN_FLOW state.
STATION_CONFIRM = "Записуємо туди?"


class TestConfirmedElsewhereExemption:
    """The third shape of «answered a question, just not this state's one».

    The two exemptions above both key on a field being written, and a bare «так»
    writes nothing, so neither branch can see this turn at all. What the caller
    answered was a confirmation the LLM asked out of its own checklist while the
    FSM sat somewhere else entirely:

    * `fe1857ba`, `cf43d623` (2026-09-10): STATION was waiting for `station_id`,
      the bot asked «Записуємо туди?», the caller said «так» / «записуємо». Both
      calls ran STATION's budget out and reached an operator while cooperating on
      every single turn.
    * `b034315e`: «Пропоную понеділок, чотирнадцяте вересня. Підходить?» → «так»,
      charged to TIME.

    The stations snapshot is left empty throughout. With one the proposal pass
    (Wave 6-H) resolves «так» into a `station_id` several branches earlier and
    every test here would be green without the exemption existing.
    """

    def _in_station(self, question: str = STATION_CONFIRM) -> Harness:
        h = Harness(booking_in_progress(FsmState.STATION))
        h.session.fsm_filled_fields["city"] = "Дніпро"
        h.session.add_assistant_turn(question)
        return h

    _bot_keeps_asking = staticmethod(bot_keeps_asking)

    async def test_agreeing_to_another_steps_question_is_not_charged(self) -> None:
        h = self._in_station()

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("так")

        assert h.session.fsm_parser_null_counts.get("STATION", 0) == 0
        assert h.session.fsm_confirmation_excused_states == ["STATION"]

    async def test_the_same_yes_after_an_open_question_is_charged(self) -> None:
        """The baseline every assertion in this class rests on.

        Same state, same word, same empty snapshot — only the bot's question
        differs. If this ever goes to zero the exemption has stopped being
        conditional on anything and the tests above prove nothing.
        """
        h = self._in_station("У якому районі зручніше?")

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("так")

        assert h.session.fsm_parser_null_counts.get("STATION", 0) == 1
        assert h.session.fsm_confirmation_excused_states == []

    async def test_a_yes_to_a_two_option_question_is_off_question_not_confirmed(self) -> None:
        """`fe1857ba` said «так» to a question STATION never asked.

        The bot had run ahead to «Шини привозите свої з собою чи ті, що у нас на
        зберіганні?» while the machine still stood in STATION. «так» picks
        neither option, so the confirmation exemption refuses it — the allow-list
        being an allow-list, not the « чи » veto: removing the veto leaves this
        green (mutation M6) because the storage question carries no STATION
        marker to begin with. The veto covers the other case, where the LLM
        rephrases a *listed* question into a choice, and
        `test_the_choice_veto_outranks_the_marker` in `test_confirm_detect.py`
        is the test that holds it.

        The turn is nonetheless free, and by the branch after it: nobody asked
        STATION anything, so STATION's budget has nothing to charge for. That is
        the `39469f9f` shape — three turns of price and diameter talk spent the
        whole station budget and transferred the call.

        The empty `fsm_confirmation_excused_states` is what tells the two apart.
        Had the confirmation exemption been what forgave the turn it would have
        spent that state's one allowance; the off-question branch spends nothing,
        so a later genuine «так» to a real station question is still free.
        """
        h = self._in_station("Шини привозите свої з собою чи ті, що у нас на зберіганні?")

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("так")

        assert h.session.fsm_parser_null_counts.get("STATION", 0) == 0
        assert h.session.fsm_confirmation_excused_states == []

    async def test_only_the_first_yes_per_state_is_free(self) -> None:
        """The loop-breaker, and the reason it has to be counted rather than
        structural.

        The passive exemption runs out when its field fills and the broad one
        when a field goes empty → filled. A confirmation leaves the machine
        exactly where it stood, so an unbounded version would answer a genuinely
        stuck call by never escalating at all — the caller says «так», the bot
        re-asks, forever (`feedback_guard_needs_loop_breaker`).
        """
        h = self._in_station()
        self._bot_keeps_asking(h, STATION_CONFIRM)

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("так", "так", "так")

        assert h.session.fsm_parser_null_counts.get("STATION", 0) == 2
        assert h.session.fsm_confirmation_excused_states == ["STATION"]

    async def test_the_cancel_subflow_is_covered_too(self) -> None:
        """`4a687e9a` (2026-09-10) — the first live cancellation, measured on
        the deploy that shipped this branch.

        The exemption worked and still missed: `_YES_NO_ASK_MARKERS` was a list
        of phrasings, and the cancel sub-flow contributed none of them, so «так
        так» to «Скасувати? Скажіть «так» або «ні».» was charged to CITY. It is
        here rather than only in `test_confirm_detect` because the prod symptom
        was a charged attempt, not a `False` from a predicate.
        """
        h = Harness(booking_in_progress(FsmState.CITY))
        h.session.add_assistant_turn(
            "У вас запис на 14 вересня о 14:20. Скасувати? Скажіть «так» або «ні»."
        )

        with fsm_flags(enabled=True, shadow_mode=False):
            await h.run("так так")

        assert h.session.fsm_parser_null_counts.get("CITY", 0) == 0
        assert h.session.fsm_confirmation_excused_states == ["CITY"]

    async def test_each_state_gets_its_own(self) -> None:
        """Per state, not per call. `b034315e` spent one in TIME after the bot
        had already asked a station confirmation earlier in the same call — a
        call-wide budget would have charged the TIME answer that this whole
        branch exists to excuse.
        """
        h = self._in_station()
        h.session.fsm_confirmation_excused_states = ["CITY", "TIME"]

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("так")

        assert h.session.fsm_parser_null_counts.get("STATION", 0) == 0
        assert h.session.fsm_confirmation_excused_states == ["CITY", "TIME", "STATION"]

    async def test_an_answer_that_carries_information_is_never_excused(self) -> None:
        """`is_confirmation` caps at four tokens for exactly this.

        «так, але я ще не вирішив куди саме» is an answer with content in it, and
        the content is a refusal to pick the very field this state wants.
        Excusing it would be the broad pass's job if it mapped anything — it must
        not be laundered into a free turn by the word «так» at the front.

        The phrasing avoids «у центрі» on purpose: «цен» is a price-interrupt
        marker and «центрі» starts with it, so the obvious wording takes the
        interrupt branch and this test would assert nothing.
        """
        h = self._in_station()

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("так, але я ще не вирішив куди саме")

        assert h.session.fsm_parser_null_counts.get("STATION", 0) == 1
        assert h.session.fsm_confirmation_excused_states == []

    async def test_the_exemption_does_not_move_the_machine(self) -> None:
        """Excusing changes the counter and nothing else — the caller has still
        not named a station, so STATION is still the right place to be."""
        h = self._in_station()

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("так")

        assert h.session.fsm_state == FsmState.STATION.value
        assert h.session.fsm_filled_fields.get("station_id") in (None, "")

    async def test_live_mode_excuses_it_too(self) -> None:
        """Shadow observes, live escalates. A rule holding in one only would mean
        the shadow numbers do not describe the thing that ships."""
        h = self._in_station()

        with fsm_flags(enabled=True, shadow_mode=False):
            await h.run("так")

        assert h.session.fsm_parser_null_counts.get("STATION", 0) == 0
        assert h.session.fsm_state == FsmState.STATION.value

    async def test_the_mark_survives_the_redis_round_trip(self) -> None:
        """The exemption is spent on one turn and enforced on the next, and the
        Call Processor rebuilds the session from Redis in between. A list kept
        only on the in-memory object re-arms the cap every turn, which silently
        turns «one per state» into «always»."""
        h = self._in_station()

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("так")

        revived = CallSession.from_dict(h.session.to_dict())
        assert revived.fsm_confirmation_excused_states == ["STATION"]

    async def test_it_is_visible_in_the_prod_logs(self, caplog) -> None:
        """Its own line, like the other two exemptions: they answer different
        questions in prod and a shared line would hide whichever is regressing.
        """
        h = self._in_station()

        with caplog.at_level(logging.INFO), fsm_flags(enabled=True, shadow_mode=True):
            await h.run("так")

        lines = [r.getMessage() for r in caplog.records if "confirmed_elsewhere" in r.getMessage()]
        assert lines, "the exemption must be visible in prod logs"
        assert "STATION" in lines[0]
        assert "station_id" in lines[0]


class TestBroadPassCannotInventASlot:
    """Wave 6-G: the broad pass may fill `time` only from the offered list.

    TIME carries `auto_skip_if=_filled`, so a time written by the broad pass
    does not merely fill a field — it skips the state that would have checked
    it, and the value rides into CONFIRM and BOOK unvalidated. The targeted
    `time_parser` has validated against `fitting_slots_offered` since Wave 14
    («can pin an existing slot but never invent one»); this is the same
    contract for the sweep beside it.
    """

    def _in_date(self, offered: list[dict[str, str]] | None) -> Harness:
        h = Harness(booking_in_progress(FsmState.DATE))
        h.session.fsm_filled_fields["city"] = "Дніпро"
        h.session.fsm_filled_fields["station_id"] = "ST-1"
        h.session.fsm_filled_fields["storage_choice"] = "own"
        h.session.fsm_filled_fields["date"] = "2026-09-09"
        h.session.fitting_slots_offered = offered or []
        return h

    async def test_an_offered_hour_is_stored_and_skips_the_time_state(self) -> None:
        """`8e5fe347` turn 22, which is the whole reason for the wave.

        The bot had already read the list and asked the time; the FSM was still
        in DATE. The answer must survive the state it was not asked in.
        """
        h = self._in_date([{"date": "2026-09-09", "time": "17:00"}])

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("на 5 вечера")

        assert h.session.fsm_filled_fields.get("time") == "17:00"
        assert h.session.fsm_state == FsmState.COLOR.value

    async def test_an_hour_that_was_never_offered_is_not_stored(self) -> None:
        h = self._in_date([{"date": "2026-09-09", "time": "17:00"}])

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("на 6 вечера")

        assert h.session.fsm_filled_fields.get("time") in (None, "")

    async def test_a_refused_hour_does_not_excuse_the_null(self) -> None:
        """Otherwise an impossible time buys free turns.

        The Wave 6-F exemption fires on any field the broad pass newly filled.
        A time that is dropped must therefore be dropped *before* the emptiness
        read, or a caller naming hours that do not exist would never escalate.
        """
        h = self._in_date([{"date": "2026-09-09", "time": "17:00"}])
        h.session.fsm_filled_fields.pop("date", None)

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("на 6 вечера")

        assert h.session.fsm_parser_null_counts.get("DATE", 0) == 1

    async def test_nothing_offered_yet_means_nothing_is_accepted(self) -> None:
        """Default-deny includes the empty set, and confidence does not buy in.

        «на 17:00» is the highest-confidence form there is (1.0, a literal
        HH:MM). It still cannot be stored before a slot list exists, because
        storing it would cancel the state that fetches one.
        """
        h = self._in_date([])

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("на 17:00")

        assert h.session.fsm_filled_fields.get("time") in (None, "")

    async def test_the_guard_is_confined_to_time(self) -> None:
        """Colour and brand have no slot list and must not inherit the check."""
        h = self._in_date([])

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("білий Volkswagen")

        assert h.session.fsm_filled_fields.get("color") == "білий"
        assert h.session.fsm_filled_fields.get("brand") == "Volkswagen"

    async def test_the_refusal_is_visible_in_prod_logs(self, caplog) -> None:
        h = self._in_date([{"date": "2026-09-09", "time": "17:00"}])

        with caplog.at_level(logging.INFO), fsm_flags(enabled=True, shadow_mode=True):
            await h.run("на 6 вечера")

        lines = [r.getMessage() for r in caplog.records if "fsm_time_not_offered" in r.getMessage()]
        assert lines, "a silently dropped field is how a regression stays invisible"
        assert "DATE" in lines[0]
        assert "18:00" in lines[0]
        # The count, never the list: the slots are a payload, not a log line.
        assert "17:00" not in lines[0]

    async def test_the_targeted_parser_inside_time_is_untouched(self) -> None:
        """The guard covers the sweep, not the state that owns the field."""
        h = self._in_date([{"date": "2026-09-09", "time": "17:00"}])
        h.session.fsm_state = FsmState.TIME.value

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("на 17:00")

        assert h.session.fsm_filled_fields.get("time") == "17:00"


# ---------------------------------------------------------------------------
# Wave 6-C — STATION stops being a dead end
# ---------------------------------------------------------------------------


def station_in_progress() -> CallSession:
    """Parked in STATION with the snapshot `get_fitting_stations` would leave.

    Every entry carries `city`, because `main.py:2516` builds it that way
    unconditionally and 603 stored payloads have it on all 1026 stations. An
    earlier draft of this fixture put the city in `district` and left `city`
    absent, which is a shape prod does not produce — and a resolver written to
    pass it grows an escape hatch for a snapshot that cannot occur.
    """
    session = CallSession(uuid.uuid4())
    session.caller_phone = "+380671234567"
    session.fsm_state = FsmState.STATION.value
    session.fsm_filled_fields["intent"] = "fitting"
    session.fsm_filled_fields["city"] = "Київ"
    session.fitting_stations_seen = [
        {"id": "st-1", "name": "Оболонь", "district": "Оболонський", "city": "Київ"},
        {"id": "st-2", "name": "Позняки", "district": "Дарницький", "city": "Київ"},
    ]
    # Same reason as `booking_in_progress`: the machine is in STATION because
    # it read the districts out and asked which one.
    session.add_assistant_turn(STATES[FsmState.STATION].question_template)
    return session


class TestStationResolvedInTheSeam:
    """12 of 16 replayed calls died here: `parse()` cannot return an id.

    `station_parser.parse` hands back the landmark by construction, and until
    Wave 6-C the only code that could turn one into a `station_id` —
    `_aresolve_station` — had zero call sites in `src/`.
    """

    async def test_the_landmark_becomes_a_station_id(self) -> None:
        h = Harness(station_in_progress())

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("на Оболоні")

        assert h.session.fsm_filled_fields.get("station_id") == "st-1"

    async def test_the_fsm_leaves_station(self) -> None:
        """The dead end, gone.

        Shadow advances on a *filled field* — `advance` gates the parser_null
        and interrupt paths only (see `TestShadowMode.test_fsm_actually_advances`).
        """
        h = Harness(station_in_progress())

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("на Оболоні")

        assert h.session.fsm_state != FsmState.STATION.value
        assert ("STATION", "STORAGE") in fsm_hops(h.session)

    async def test_live_mode_resolves_it_too(self) -> None:
        h = Harness(station_in_progress())

        with (
            fsm_flags(enabled=True, shadow_mode=False),
            patch(
                "src.agent.intent_classifier.classify_intent",
                AsyncMock(return_value=intent("BOOKING", confidence=0.1)),
            ),
        ):
            await h.run("на Оболоні")

        assert h.session.fsm_filled_fields.get("station_id") == "st-1"
        assert h.session.fsm_state != FsmState.STATION.value

    async def test_an_ambiguous_landmark_is_not_guessed(self) -> None:
        """Two «Перемоги» inside the *pinned* city — city narrowing cannot help.

        Deliberately same-city: a snapshot spanning two cities would be refused
        by the city filter before the ambiguity was ever reached, and the test
        would go green without exercising the rule it names. Cross-city refusal
        has its own coverage in `test_parsers_station.py`.
        """
        h = Harness(station_in_progress())
        h.session.fitting_stations_seen = [
            {"id": "st-a", "name": "Перемоги 72Б", "city": "Київ"},
            {"id": "st-b", "name": "Перемоги 15", "city": "Київ"},
        ]

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("на Перемоги")

        assert "station_id" not in h.session.fsm_filled_fields
        assert h.session.fsm_state == FsmState.STATION.value

    async def test_no_snapshot_means_no_id(self) -> None:
        h = Harness(station_in_progress())
        h.session.fitting_stations_seen = []

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("на Оболоні")

        assert "station_id" not in h.session.fsm_filled_fields
        assert h.session.fsm_state == FsmState.STATION.value

    async def test_a_turn_without_a_landmark_is_still_charged(self) -> None:
        """STATION keeps its budget — the wave lifts the dead end, not the guard."""
        h = Harness(station_in_progress())

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("ага")

        assert h.session.fsm_parser_null_counts.get("STATION", 0) == 1
        assert "station_id" not in h.session.fsm_filled_fields

    async def test_the_broad_pass_is_still_locked_out_of_the_field(self) -> None:
        """The resolve finishes the targeted parser's own work.

        So `claimed` must still point at `station_id`: an unresolved landmark
        is a refusal, and `setdefault` from the broad sweep must not fill it.
        """
        from src.agent.parsers import station_parser

        h = Harness(station_in_progress())
        h.session.fitting_stations_seen = []

        with (
            fsm_flags(enabled=True, shadow_mode=True),
            patch.dict(
                "src.core.pipeline.COMPOUND_TO_FSM_FIELD",
                {"station_hint": "station_id"},
            ),
        ):
            await h.run("на Оболоні")

        assert "station_id" not in h.session.fsm_filled_fields, (
            "the broad pass wrote a landmark over the targeted refusal"
        )
        assert station_parser.PARSER.field_name == "station_id"

    async def test_only_the_station_field_is_resolved_this_way(self) -> None:
        """The resolver is dispatched on `field_name`, and that guard is load-bearing.

        `unresolved(value=…)` is a documented pattern, not station-only: any
        parser may hand back a hint it could not pin (`base.py:133-149`). Run
        the station resolver over one of *those* and the seam writes a
        `station_id` into a field that is not `station_id` — a value from a
        different domain entirely, pinned hard enough to skip the state.
        `c8c6601`, with an extra step.
        """
        from src.agent.parsers import city_parser
        from src.agent.parsers.base import unresolved

        h = Harness(station_in_progress())
        h.session.fsm_state = FsmState.CITY.value
        h.session.fsm_filled_fields.pop("city", None)

        with (
            fsm_flags(enabled=True, shadow_mode=True),
            patch.object(
                city_parser.PARSER,
                "parse",
                lambda ctx: unresolved(0.5, value="Оболонь"),
            ),
        ):
            await h.run("десь на Оболоні")

        assert h.session.fsm_filled_fields.get("city") != "st-1", (
            "a station id was pinned as the city"
        )
        assert h.session.fsm_state == FsmState.CITY.value

    async def test_the_seam_still_awaits_nothing(self) -> None:
        """The resolver is sync; `ParseContext.conn` stays the gate for the rest."""
        seen: list[Any] = []

        from src.agent.parsers import station_parser

        real_parse = station_parser.PARSER.parse

        def _spy(ctx: Any) -> Any:
            seen.append(ctx)
            return real_parse(ctx)

        h = Harness(station_in_progress())

        with (
            fsm_flags(enabled=True, shadow_mode=True),
            patch.object(station_parser.PARSER, "parse", _spy),
            patch.object(
                station_parser,
                "_aresolve_station",
                AsyncMock(side_effect=AssertionError("the seam awaited a resolver")),
            ),
        ):
            await h.run("на Оболоні")

        assert seen and seen[0].conn is None
        assert h.session.fsm_filled_fields.get("station_id") == "st-1"


#: A real proposal turn and the real snapshot row it names, both verbatim from
#: call `3412071b` and from the store payload (they are the same pair
#: `test_parsers_station.py` resolves against). Invented ones do not work and
#: must not be made to: the first draft of these tests used «Знайшла точку на
#: вул. Тестовій, 1 у Києві» over the fixture's «вул. Тестова, 1», and
#: `_detect_station_hint` returned `None`. Tuning the wording until it matched
#: would have been fitting the test to the code — the pair below is the one
#: production actually produces.
PROPOSAL = "Знайшла точку біля Оболоні, на вулиці Маршала Тимошенка, 7. Записуємо туди?"
PROPOSED_STATION = {
    "id": "000000006",
    "name": "4К (Киев, ул. М. Тимошенко, 7)",
    "address": "м. Київ, вул. Маршала Тимошенка, 7",
    "city": "Київ",
    "district": "Оболонь, Правий берег",
    "landmarks": (
        "магазин Еко, метро Мінське, район Оболоні, Магазин Эко, метро Минское, "
        "Лукьяненко, Лук'яненко, Левка Лук'яненка, вул. Тимошенка, Маршала Тимошенка"
    ),
    "description": "Левка Лукьяненко 7",
}

#: The hidden `action_required: ask_district` payload — the catalog the tool
#: returns when the bot did *not* know the city. Nine points across five
#: cities, so the unanimity rule must refuse it. Kept short here; the shape
#: that matters is «more than one distinct city», not the count.
FIVE_CITY_CATALOG = [
    {"id": "c-1", "name": "1К", "city": "Київ"},
    {"id": "c-2", "name": "1Д", "city": "Дніпро"},
    {"id": "c-3", "name": "1Л", "city": "Львів"},
    {"id": "c-4", "name": "1О", "city": "Одеса"},
    {"id": "c-5", "name": "1Х", "city": "Харків"},
]


def proposal_pending(state: FsmState, *, stations: list[dict[str, Any]]) -> CallSession:
    """A session whose newest bot turn is a proposal of one specific point.

    `add_assistant_turn` rather than a hand-set `last_bot_utterance`, because
    the detector reads `recent_bot_utterances(ctx, 2)` and that walks
    `dialog_history` — a fixture that only sets the scalar can never exercise
    the second turn of the window.
    """
    session = booking_in_progress(state, stations=stations)
    session.fsm_filled_fields.pop("city", None)
    session.add_assistant_turn(PROPOSAL)
    return session


class TestTheProposalIsConfirmedInTheSeam:
    """Wave 6-H, first half: the bot offered a point and the caller said yes."""

    async def test_the_city_state_gets_both_the_station_and_the_city(self) -> None:
        """`011277ef`: the call dies in CITY, so the fix cannot live in STATION.

        «на перемозі» is a landmark in two cities, `_detect_city` returns
        `None`, and the FSM never leaves CITY. On the turn the caller agrees to
        the offered point the targeted parser is `city_parser`, not
        `station_parser` — a rule inside `StationParser.parse` would never run.

        The snapshot deliberately spans several cities. With a single-city one
        the unanimity rule fills `city` with the same «Київ» from the other
        side of the seam, and deleting the proposal's own city write left this
        test green — measured, not guessed (mutation M2 scored zero). A
        multi-city snapshot is refused by unanimity, so the proposal is the
        only thing that can supply the city. It is also the real shape:
        `7462c08b` resolves a proposal out of nine points across five cities.
        """
        h = Harness(
            proposal_pending(FsmState.CITY, stations=[PROPOSED_STATION, *FIVE_CITY_CATALOG])
        )

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("так")

        assert h.session.fsm_filled_fields.get("station_id") == "000000006"
        assert h.session.fsm_filled_fields.get("city") == "Київ"
        assert h.session.fsm_state != FsmState.CITY.value

    async def test_the_station_state_gets_the_same_treatment(self) -> None:
        h = Harness(proposal_pending(FsmState.STATION, stations=[PROPOSED_STATION]))

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("так")

        assert h.session.fsm_filled_fields.get("station_id") == "000000006"
        assert h.session.fsm_state != FsmState.STATION.value

    async def test_an_already_chosen_point_is_not_replaced(self) -> None:
        """`setdefault`, never assignment.

        The Krok 8 summary re-states the point that was already picked. The
        rule firing there is harmless only as long as it cannot overwrite —
        otherwise a stale snapshot entry could displace the caller's choice.
        """
        session = proposal_pending(FsmState.STATION, stations=[PROPOSED_STATION])
        session.fsm_filled_fields["station_id"] = "000000009"
        h = Harness(session)

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("так")

        assert h.session.fsm_filled_fields["station_id"] == "000000009"

    async def test_without_a_proposal_nothing_is_pinned(self) -> None:
        """The agreement alone is not evidence — the pending question must be.

        Same snapshot, same «так», only the bot's turn differs. If this pinned
        a station, the rule would be «one point in the snapshot wins», which is
        `auto_skip_if`'s job and is gated on the caller having been asked.
        """
        session = booking_in_progress(FsmState.STATION, stations=[PROPOSED_STATION])
        session.add_assistant_turn("Для зміни міста потрібне підтвердження. Замінюємо?")
        h = Harness(session)

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("так")

        assert "station_id" not in h.session.fsm_filled_fields

    async def test_the_step_reaches_no_network_in_shadow(self) -> None:
        """The seam invariant the wave inherits: sync only, `conn is None`.

        `_run_fsm_deterministic_step` is synchronous by contract
        (`pipeline.py:991`, «no await → no network»), and that is what makes
        shadow mode safe to run on live calls. The resolver reads the snapshot
        the tool already left in the session, so it needs neither.
        """
        from src.agent.parsers import station_parser

        seen: list[Any] = []
        real_parse = station_parser.PARSER.parse

        def _spy(ctx: Any) -> Any:
            seen.append(ctx)
            return real_parse(ctx)

        h = Harness(proposal_pending(FsmState.STATION, stations=[PROPOSED_STATION]))

        with (
            fsm_flags(enabled=True, shadow_mode=True),
            patch.object(station_parser.PARSER, "parse", _spy),
            patch.object(
                station_parser,
                "_aresolve_station",
                AsyncMock(side_effect=AssertionError("the seam awaited a resolver")),
            ),
        ):
            await h.run("так")

        assert seen and all(ctx.conn is None for ctx in seen)
        assert h.session.fsm_filled_fields.get("station_id") == "000000006"


class TestTheCityComesFromAUnanimousSnapshot:
    """Wave 6-H, second half: every offered point agrees on one city.

    The snapshot is filled by `get_fitting_stations(city=...)`, so a snapshot
    that agrees on one city is the city the bot already knew and already passed
    to the tool. Provenance checked against the transcript and `tool_args` on
    `011277ef` — not an LLM guess.
    """

    async def test_one_city_fills_and_advances(self) -> None:
        h = Harness(booking_in_progress(FsmState.CITY, stations=ONE_CITY_SNAPSHOT))
        h.session.fsm_filled_fields.pop("city", None)

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("ага")

        assert h.session.fsm_filled_fields.get("city") == "Київ"
        assert h.session.fsm_state != FsmState.CITY.value

    async def test_five_cities_refuse(self) -> None:
        """The `ask_district` catalog means the bot did NOT know the city."""
        h = Harness(booking_in_progress(FsmState.CITY, stations=FIVE_CITY_CATALOG))
        h.session.fsm_filled_fields.pop("city", None)

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("ага")

        assert "city" not in h.session.fsm_filled_fields

    async def test_an_empty_snapshot_refuses(self) -> None:
        h = Harness(booking_in_progress(FsmState.CITY))
        h.session.fsm_filled_fields.pop("city", None)

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("ага")

        assert "city" not in h.session.fsm_filled_fields

    async def test_a_station_without_a_city_refuses_the_whole_snapshot(self) -> None:
        """Default-deny over the set, not over the majority.

        One entry missing `city` is not «four out of five agree»; it is a
        snapshot we cannot read. A rule that shrugged the gap off would be a
        named-subset guard with the gap as its escape hatch.
        """
        stations = [*ONE_CITY_SNAPSHOT, {"id": "ST-2", "name": "Шиномонтаж №2"}]
        h = Harness(booking_in_progress(FsmState.CITY, stations=stations))
        h.session.fsm_filled_fields.pop("city", None)

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("ага")

        assert "city" not in h.session.fsm_filled_fields

    async def test_the_city_the_caller_just_named_beats_the_snapshot(self) -> None:
        """`bd95036c` in miniature — and a test of placement, not of a string.

        The snapshot agrees on Київ; the caller asks for Дніпро out loud. The
        snapshot was built on earlier turns, the utterance is now, and the
        pass that reads the utterance must get the slot first.

        The state is STATION, not CITY, and that is the whole point. In CITY the
        targeted parser *is* `city_parser`, it resolves «у Дніпрі» itself, and
        it runs ahead of both candidate placements — so the ordering is never
        exercised and the test passes either way. Measured: the first draft sat
        in CITY and mutation M5 (rule moved back above the broad pass) scored
        zero red. Outside CITY nobody but the broad pass reads the city, so the
        placement is the only thing standing between this caller and the wrong
        town.
        """
        h = Harness(booking_in_progress(FsmState.STATION, stations=ONE_CITY_SNAPSHOT))
        h.session.fsm_filled_fields.pop("city", None)

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("у Дніпрі")

        assert h.session.fsm_filled_fields.get("city") == "Дніпро"

    async def test_a_city_spoken_but_not_recognised_silences_the_snapshot(self) -> None:
        """Ordering alone is not enough, and this is the hole it leaves.

        When the caller names a city no resolver can pin, nobody writes the
        field — and the stale snapshot wins by default, silently, which is the
        same wrong-town outcome as above. `unresolved` is precisely the
        parser contract's word for «they said something we could not pin down»
        (`base.py:88`), as opposed to `not_mentioned`. So the state's own
        parser refusing its own field silences the snapshot for that turn.
        """
        from src.agent.parsers import city_parser
        from src.agent.parsers.base import unresolved

        h = Harness(booking_in_progress(FsmState.CITY, stations=ONE_CITY_SNAPSHOT))
        h.session.fsm_filled_fields.pop("city", None)

        with (
            fsm_flags(enabled=True, shadow_mode=True),
            patch.object(city_parser.PARSER, "parse", lambda ctx: unresolved(0.5)),
        ):
            await h.run("у Кам'янському-на-Дніпрі")

        assert "city" not in h.session.fsm_filled_fields
        assert h.session.fsm_state == FsmState.CITY.value

    async def test_a_silent_turn_does_not_silence_the_snapshot(self) -> None:
        """The other side of the interlock, so it cannot be widened by accident.

        «так» is `not_mentioned`, not `unresolved` — the caller said nothing
        about a city at all. If the interlock keyed on «the targeted parser
        returned no value» instead of on the refusal, it would swallow this
        turn too, and `011277ef` would stay dead.
        """
        h = Harness(booking_in_progress(FsmState.CITY, stations=ONE_CITY_SNAPSHOT))
        h.session.fsm_filled_fields.pop("city", None)

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("так")

        assert h.session.fsm_filled_fields.get("city") == "Київ"


class TestTheInferredCityCanBeTakenBack:
    """Wave 6-H phase 05 — `bd95036c` at full length.

    The unanimity rule above is measured to fire on the *diameter* answer on
    every one of the four corpus calls it touches: it is the first turn after
    `get_fitting_stations` filled the catalog, and the caller has not said
    anything about a city yet. `setdefault` then guards the slot for good.

    On `bd95036c` the caller asks for Черкаси four times against a Дніпро
    snapshot and the FSM records none of it — the slot is taken, and by then
    the machine has left CITY, so the targeted `city_parser` never runs again
    either. The verdict does not change (that call transfers for want of a
    station), but a wrong city surviving into BOOK is the failure this system
    can least afford, and the next wave is meant to make STATION resolvable.

    So the inference is held revocably rather than gated harder. Gating was
    measured and rejected: every firing is on a diameter answer, so «only fire
    on a locative turn» would have removed all four, including the three that
    are right.

    Multi-turn tests hand every utterance to a single `run()`. The harness
    closes the connection as it releases the LAST transcript, so a second
    `run()` finds it closed and the seam never executes — a first draft did
    that and read as «revocation does not fire».
    """

    async def test_the_caller_takes_the_slot_back(self) -> None:
        h = Harness(booking_in_progress(FsmState.CITY, stations=ONE_CITY_SNAPSHOT))
        h.session.fsm_filled_fields.pop("city", None)

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("17", "тільки мені в Черкасах треба")

        assert h.session.fsm_filled_fields.get("city") == "Черкаси"
        assert h.session.fsm_inferred_fields == []

    async def test_the_diameter_answer_is_what_takes_the_slot(self) -> None:
        """The premise of the class, asserted rather than assumed.

        If the snapshot rule stopped firing on «17» this whole class would go
        green for the wrong reason — nothing would ever be marked, so nothing
        could be wrongly kept.
        """
        h = Harness(booking_in_progress(FsmState.CITY, stations=ONE_CITY_SNAPSHOT))
        h.session.fsm_filled_fields.pop("city", None)

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("17")

        assert h.session.fsm_filled_fields.get("city") == "Київ"
        assert h.session.fsm_inferred_fields == ["city"]

    async def test_a_heard_city_is_then_kept(self) -> None:
        """Revocation is once, not a standing licence.

        Without clearing the mark the broad pass would own the slot for the
        rest of the call and every later mention would move the booking again —
        trading a stale inference for a value that changes under the caller's
        feet, which is worse.
        """
        h = Harness(booking_in_progress(FsmState.STATION, stations=ONE_CITY_SNAPSHOT))
        h.session.fsm_filled_fields["city"] = "Київ"
        h.session.fsm_inferred_fields = ["city"]

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("тільки мені в Черкасах треба", "а може у Дніпрі")

        assert h.session.fsm_filled_fields.get("city") == "Черкаси"

    async def test_the_targeted_parser_also_clears_the_mark(self) -> None:
        """The other way the slot stops being an inference.

        The targeted pass assigns rather than `setdefault`s, so it overwrites an
        inferred value already. What it must also do is drop the mark — else the
        broad pass keeps its licence to overwrite the state's own parser on some
        later turn, which is `c8c6601` arriving through the door this wave just
        opened.

        The marked session is built directly instead of being walked into: the
        snapshot rule leaves the machine past CITY, and CITY is the only state
        where `city_parser` is the targeted one.
        """
        h = Harness(booking_in_progress(FsmState.CITY, stations=ONE_CITY_SNAPSHOT))
        h.session.fsm_filled_fields["city"] = "Київ"
        h.session.fsm_inferred_fields = ["city"]

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("у Черкасах")

        assert h.session.fsm_filled_fields.get("city") == "Черкаси"
        assert h.session.fsm_inferred_fields == []

    async def test_a_city_the_caller_named_is_never_marked(self) -> None:
        """Default-deny: only the snapshot rule may mark, so only its guess is
        revocable. A heard city that got marked would stay overwritable by the
        broad pass for the rest of the call."""
        h = Harness(booking_in_progress(FsmState.STATION, stations=ONE_CITY_SNAPSHOT))
        h.session.fsm_filled_fields.pop("city", None)

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("у Дніпрі")

        assert h.session.fsm_filled_fields.get("city") == "Дніпро"
        assert h.session.fsm_inferred_fields == []

    async def test_an_unmarked_city_is_still_never_overwritten(self) -> None:
        """The guard the mark must not dissolve.

        Same two utterances as the revocation test, but nothing marked the
        slot. The broad pass must leave it alone — otherwise the mark is
        decorative and the pass has simply been given a general licence to
        overwrite, which is the `c8c6601` defect class.
        """
        h = Harness(booking_in_progress(FsmState.STATION, stations=ONE_CITY_SNAPSHOT))
        h.session.fsm_filled_fields["city"] = "Київ"
        h.session.fsm_inferred_fields = []

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("тільки мені в Черкасах треба")

        assert h.session.fsm_filled_fields.get("city") == "Київ"

    async def test_the_mark_survives_the_redis_round_trip(self) -> None:
        """The write and the revocation happen on different turns, and the Call
        Processor rebuilds the session from Redis in between. A mark kept only
        on the in-memory object is the `c8c6601` shape: correct rule, reset
        before the turn that would have used it.
        """
        h = Harness(booking_in_progress(FsmState.CITY, stations=ONE_CITY_SNAPSHOT))
        h.session.fsm_filled_fields.pop("city", None)

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("17")
        assert h.session.fsm_inferred_fields == ["city"]

        revived = CallSession.from_dict(h.session.to_dict())
        assert revived.fsm_inferred_fields == ["city"]


# ---------------------------------------------------------------------------
# TestNetworkResolve — `FieldParser.aresolve` finally has a call site
# ---------------------------------------------------------------------------

#: Call `9ebf8351`, 2026-09-10 turn 17. The caller named their car and the FSM
#: charged them for it: `brand_parser.parse` knows the curated brands only, so
#: a *model* name comes back `not_mentioned` → `BRAND parser_null 1/2`. The
#: Wave 8 alias table (`e67c6d7`) has held `tiguan → Volkswagen` since Wave 8
#: and had no reachable call site in `src/` to be asked through.
TIGUAN = "Tiguan"


class _FakeConn:
    """A SQLAlchemy `AsyncConnection` stand-in that answers nothing.

    Every test here patches `resolve_by_alias`, so the connection is only ever
    passed around, never queried. Giving it no methods is the assertion: if a
    resolver starts issuing SQL of its own, these tests fail loudly instead of
    silently exercising a mock.
    """


class FakeEngine:
    """What `src/main.py` hands the pipeline, reduced to the one method it uses.

    Deliberately not a `MagicMock`: the pipeline opens the connection with
    `async with`, and a bare mock hands back another mock from `__aenter__` —
    a connection that was never opened would still look open, and
    `test_the_connection_is_always_returned` would pass against a pipeline that
    leaks one per turn (`codetrap_asyncmock_hides_missing_api`).
    """

    def __init__(self) -> None:
        self.opened = 0
        self.closed = 0

    def connect(self) -> Any:
        engine = self

        class _Cm:
            async def __aenter__(self) -> _FakeConn:
                engine.opened += 1
                return _FakeConn()

            async def __aexit__(self, *exc: Any) -> bool:
                engine.closed += 1
                return False

        return _Cm()


@contextlib.contextmanager
def alias_table(
    mapping: dict[str, Any],
    *,
    raises: BaseException | None = None,
    delay: float = 0.0,
):
    """Replace the alias table's SQL and nothing above it.

    Patched at `resolve_by_alias` — the lowest point that touches the database
    — so `brand_parser`'s candidate generation, its ambiguity handling and the
    engine's call rules all still run for real. Yields the list of candidates
    the resolver actually asked about, which is how the «`parse()` first» rule
    is asserted: an empty list means the resolver was never reached.
    """
    from src.agent import vehicle_alias_lookup

    asked: list[str] = []

    async def _resolve(conn: Any, utterance: str) -> Any:
        asked.append(utterance)
        if delay:
            await asyncio.sleep(delay)
        if raises is not None:
            raise raises
        return mapping.get(utterance.strip().lower(), vehicle_alias_lookup.ResolveResult())

    with patch.object(vehicle_alias_lookup, "resolve_by_alias", _resolve):
        yield asked


def volkswagen() -> Any:
    """The row prod actually holds: `tiguan|Volkswagen|Tiguan|auto_model_name`."""
    from src.agent.vehicle_alias_lookup import ResolveResult

    return ResolveResult(
        brand_id=1,
        brand_name="Volkswagen",
        model_id=2,
        model_name="Tiguan",
        source="auto_model_name",
    )


class TestNetworkResolve:
    """`_run_fsm_network_resolve` — the FSM's only step that may do I/O.

    It exists because `_run_fsm_deterministic_step` may not: «no await → no
    network» is what makes shadow mode safe to run against live traffic, so the
    network half of §3.2 gets its own step in front of the seam rather than a
    connection threaded into it. In front, because the seam decides
    `apply_field` vs `on_parser_null` in one pass — a resolver behind it would
    have to undo a charge instead of preventing it.
    """

    def _in_brand(self, *, engine: FakeEngine | None = None) -> Harness:
        h = Harness(booking_in_progress(FsmState.BRAND), db_engine=engine or FakeEngine())
        h.session.fsm_filled_fields["color"] = "чорний"
        return h

    async def test_the_alias_table_fills_the_field_the_parser_missed(self) -> None:
        """The prod case, end to end: «Tiguan» → Volkswagen, no attempt spent."""
        h = self._in_brand()

        with (
            fsm_flags(enabled=True, shadow_mode=False),
            alias_table({"tiguan": volkswagen()}) as asked,
        ):
            await h.run(TIGUAN)

        assert h.session.fsm_filled_fields.get("brand") == "Volkswagen"
        assert h.session.fsm_parser_null_counts.get("BRAND", 0) == 0
        assert h.session.fsm_state != FsmState.BRAND.value
        assert asked[0] == "tiguan"

    async def test_without_the_wire_the_same_turn_is_charged(self, caplog) -> None:
        """The baseline the fix is measured against — and the rollback shape.

        No engine is the state every test outside this class runs in, so if this
        went green with the field filled, the whole class would be asserting
        something the pipeline does unconditionally.

        «No engine» has to be a *decision*, which is why the log is asserted
        empty: reaching `None.connect()` and catching the AttributeError
        produces the same session state as declining to run, and would turn
        every turn of every engine-less deployment into a WARNING.
        """
        h = Harness(booking_in_progress(FsmState.BRAND), db_engine=None)

        with (
            caplog.at_level(logging.WARNING),
            fsm_flags(enabled=True, shadow_mode=False),
            alias_table({"tiguan": volkswagen()}) as asked,
        ):
            await h.run(TIGUAN)

        assert h.session.fsm_filled_fields.get("brand") in (None, "")
        assert h.session.fsm_parser_null_counts.get("BRAND", 0) == 1
        assert asked == []
        assert not [r for r in caplog.records if "FSM network resolve" in r.getMessage()]

    async def test_the_step_refuses_shadow_when_called_directly(self) -> None:
        """The mode check is inside the step as well as around its call site.

        What reaches a future caller — a replay harness, a retry, the next wave
        — is the method, not the `if` in front of it. A guard that lives only at
        one call site is one refactor away from being no guard at all.
        """
        engine = FakeEngine()
        h = self._in_brand(engine=engine)
        transcript = Transcript(text=TIGUAN, is_final=True, confidence=0.95, language="uk-UA")

        with (
            fsm_flags(enabled=True, shadow_mode=True),
            alias_table({"tiguan": volkswagen()}) as asked,
        ):
            await h.pipeline._run_fsm_network_resolve(transcript)

        assert engine.opened == 0
        assert asked == []
        assert h.session.fsm_filled_fields.get("brand") in (None, "")

    async def test_shadow_never_opens_a_connection(self) -> None:
        """§3.2 rule 3. Shadow runs against live traffic on the strength of
        «no await → no network»; one connection opened here and the mode stops
        being an observer."""
        engine = FakeEngine()
        h = self._in_brand(engine=engine)

        with (
            fsm_flags(enabled=True, shadow_mode=True),
            alias_table({"tiguan": volkswagen()}) as asked,
        ):
            await h.run(TIGUAN)

        assert engine.opened == 0
        assert asked == []
        assert h.session.fsm_parser_null_counts.get("BRAND", 0) == 1

    async def test_the_kill_switch_covers_it_too(self) -> None:
        engine = FakeEngine()
        h = self._in_brand(engine=engine)

        with fsm_flags(enabled=False), alias_table({"tiguan": volkswagen()}) as asked:
            await h.run(TIGUAN)

        assert engine.opened == 0
        assert asked == []

    async def test_a_resolved_parse_is_not_second_guessed(self) -> None:
        """Rule 1: `parse()` runs first and alone decides if anything is left.

        «Фольксваген» is curated, so the deterministic answer is already there.
        Asking the database anyway would let a table edit override a hand-picked
        brand — and would spend a connection on every BRAND turn.
        """
        engine = FakeEngine()
        h = self._in_brand(engine=engine)

        with (
            fsm_flags(enabled=True, shadow_mode=False),
            alias_table({"фольксваген": volkswagen()}) as asked,
        ):
            await h.run("Фольксваген")

        assert h.session.fsm_filled_fields.get("brand") == "Volkswagen"
        assert asked == []
        assert engine.closed == engine.opened

    async def test_a_filled_field_opens_nothing(self) -> None:
        """The field is checked before the connection, not after: BRAND is
        reachable with `brand` already set by the broad pass on an earlier turn.
        """
        engine = FakeEngine()
        h = self._in_brand(engine=engine)
        h.session.fsm_filled_fields["brand"] = "Renault"

        with (
            fsm_flags(enabled=True, shadow_mode=False),
            alias_table({"tiguan": volkswagen()}) as asked,
        ):
            await h.run(TIGUAN)

        assert engine.opened == 0
        assert asked == []
        assert h.session.fsm_filled_fields["brand"] == "Renault"

    async def test_a_state_whose_parser_has_no_resolver_opens_nothing(self) -> None:
        """Dispatch is on `parser.aresolve is not None`. Eleven of the thirteen
        parsers declare `None`, and those turns must cost no connection at all.
        """
        engine = FakeEngine()
        h = Harness(booking_in_progress(FsmState.DATE), db_engine=engine)

        with fsm_flags(enabled=True, shadow_mode=False):
            await h.run("хмм")

        assert engine.opened == 0

    async def test_an_ambiguous_alias_resolves_nothing(self) -> None:
        """«500» spans Fiat and someone else's 500. `brand_parser` already
        refuses those; the wire must not turn a refusal into a value."""
        from src.agent.vehicle_alias_lookup import ResolveResult

        h = self._in_brand()

        with (
            fsm_flags(enabled=True, shadow_mode=False),
            alias_table({"tiguan": ResolveResult(ambiguous=True)}),
        ):
            await h.run(TIGUAN)

        # Absent, not `None`: an unresolved field that exists as a key reads as
        # «asked and answered nothing» to everything downstream that walks
        # `fsm_filled_fields`, and `book_fitting` is one of those readers.
        assert "brand" not in h.session.fsm_filled_fields
        assert h.session.fsm_parser_null_counts.get("BRAND", 0) == 1

    async def test_a_failing_database_costs_one_field_not_the_call(self) -> None:
        """§3.2 rule 4. The caller is on the phone: the turn continues on the
        `parse()` result and the FSM stays where it was."""
        h = self._in_brand()

        with (
            fsm_flags(enabled=True, shadow_mode=False),
            alias_table({}, raises=RuntimeError("pool exhausted")),
        ):
            await h.run(TIGUAN)

        assert h.session.fsm_state == FsmState.BRAND.value
        assert h.session.fsm_parser_null_counts.get("BRAND", 0) == 1
        assert LLM_REPLY in h.assistant_texts

    async def test_the_failure_is_logged_with_its_traceback(self, caplog) -> None:
        """Not `contextlib.suppress`, and not a bare DEBUG line: a silently
        swallowed failure on this path is how 3/3 bookings were lost invisibly
        (`37fb2d0`). The traceback is what tells a pool timeout from a typo.
        """
        h = self._in_brand()

        with (
            caplog.at_level(logging.WARNING),
            fsm_flags(enabled=True, shadow_mode=False),
            alias_table({}, raises=RuntimeError("pool exhausted")),
        ):
            await h.run(TIGUAN)

        failures = [r for r in caplog.records if "FSM network resolve failed" in r.getMessage()]
        assert len(failures) == 1
        assert failures[0].levelno == logging.WARNING
        assert failures[0].exc_info is not None

    async def test_a_hung_database_cannot_hold_the_turn(self) -> None:
        """The real risk on a voice turn is not a wrong answer, it is silence.
        A resolver that never returns must time out into the same «continue on
        `parse()`» path as any other failure.
        """
        h = self._in_brand()

        with (
            patch("src.core.pipeline._FSM_ARESOLVE_TIMEOUT_SEC", 0.01),
            fsm_flags(enabled=True, shadow_mode=False),
            alias_table({"tiguan": volkswagen()}, delay=1.0),
        ):
            await h.run(TIGUAN)

        assert h.session.fsm_filled_fields.get("brand") in (None, "")
        assert h.session.fsm_state == FsmState.BRAND.value

    async def test_the_connection_is_always_returned(self) -> None:
        """Opened with `async with`, so a raising resolver still gives it back.
        A per-turn leak exhausts a pool of fifteen inside one busy call."""
        engine = FakeEngine()
        h = self._in_brand(engine=engine)

        with (
            fsm_flags(enabled=True, shadow_mode=False),
            alias_table({}, raises=RuntimeError("boom")),
        ):
            await h.run(TIGUAN)

        assert engine.opened == 1
        assert engine.closed == 1

    async def test_the_resolved_field_drops_its_inferred_mark(self) -> None:
        """Same bookkeeping as the seam's targeted pass. The mark exists so the
        broad pass may hand a guessed slot back; leaving it on a value the
        caller actually named lets a later sweep overwrite it.
        """
        h = self._in_brand()
        h.session.fsm_filled_fields.pop("brand", None)
        h.session.fsm_inferred_fields.append("brand")

        with (
            fsm_flags(enabled=True, shadow_mode=False),
            alias_table({"tiguan": volkswagen()}),
        ):
            await h.run(TIGUAN)

        assert h.session.fsm_filled_fields.get("brand") == "Volkswagen"
        assert "brand" not in h.session.fsm_inferred_fields

    async def test_it_is_visible_in_the_prod_logs(self, caplog) -> None:
        """The one line that says a field came from the database rather than
        from the utterance. Without it a regression in the alias table reads as
        a regression in the parser."""
        h = self._in_brand()

        with (
            caplog.at_level(logging.INFO),
            fsm_flags(enabled=True, shadow_mode=False),
            alias_table({"tiguan": volkswagen()}),
        ):
            await h.run(TIGUAN)

        lines = [r.getMessage() for r in caplog.records if "fsm_aresolve" in r.getMessage()]
        assert len(lines) == 1
        assert "field=brand" in lines[0]
        assert "value=Volkswagen" in lines[0]
        assert "state=BRAND" in lines[0]

    async def test_station_agrees_with_the_seam(self) -> None:
        """`station_parser` comes through here too — dispatch is on the declared
        resolver, not on a list of states someone has to remember to extend
        (`feedback_guards_need_default_deny`).

        Its resolver is a thin wrapper over the same
        `resolve_station_from_session` the seam calls, so wiring it must be a
        no-op on the answer. This pins that: the landmark resolves to the same
        id it did before the wire, and it does so without asking the database.
        """
        engine = FakeEngine()
        # Two points, not one: with a single station `refresh_auto_skips` pins
        # it outright and the test would pass without any resolver at all.
        stations = [
            {"id": "ST-1", "name": "Донецьке шосе", "city": "Дніпро", "address": "Донецьке шосе"},
            {"id": "ST-2", "name": "Запорізьке шосе", "city": "Дніпро", "address": "Запорізьке"},
        ]
        h = Harness(
            booking_in_progress(FsmState.STATION, stations=stations),
            db_engine=engine,
        )
        h.session.fsm_filled_fields["city"] = "Дніпро"

        with (
            fsm_flags(enabled=True, shadow_mode=False),
            alias_table({}) as asked,
        ):
            await h.run("Донецьке шосе")

        assert h.session.fsm_filled_fields.get("station_id") == "ST-1"
        assert asked == []

    def test_production_actually_hands_the_pipeline_an_engine(self) -> None:
        """`src/main.py` is the only place a real engine comes from.

        Every other test in this class builds the pipeline itself, so all of
        them stay green against a production call site that leaves `db_engine`
        at its default — which is the exact shape of the hole this wave closes:
        a resolver that works and has no caller. Read as text rather than
        imported: importing `src.main` starts the world.
        """
        source = (Path(__file__).resolve().parents[2] / "src" / "main.py").read_text(
            encoding="utf-8"
        )
        construction = source.split("pipeline = CallPipeline(", 1)[1].split(")", 1)[0]
        assert "db_engine=_db_engine" in construction


class TestTheFiveTransfersReplayedThroughTheSeam:
    """The four wrongly-transferred calls of 2026-09-10, driven through the pipeline.

    `test_fsm_question_markers.py` holds the same corpus, but it calls
    `bot_is_asking` directly. That left the *wiring* covered by two tests:
    deleting the whole exemption from the chain in `pipeline.py` and running
    everything took only those two down, so the corpus could have gone on
    reporting «four of five saved» with the fix no longer connected to anything.
    These replay the turns through `_run_fsm_deterministic_step` instead, which
    is the only place the answer reaches a budget.

    Shadow mode, because the assertion is about what the budget *counts*; the
    escalation it feeds is a plain `>=` on that count and has its own tests.
    """

    async def test_the_reschedule_spends_nothing_from_city(self) -> None:
        """`bba035ff` — the caller was cancelling a booking to move it.

        The bot ran a cancel sub-flow and then read out a slot list. It never
        asked for a city, and the machine was parked in CITY because the call
        began there.
        """
        cancel_readback = (
            "Знайшла запис у Харкові на 11 вересня о 15:40, на вулиці "
            "Холодногірська, 11. Скасовуємо для перенесення?"
        )
        h = replay(
            FsmState.CITY,
            [
                (cancel_readback, "перенесли"),
                (cancel_readback, "переносимо"),
                (
                    "Вільний час на вівторок 15 вересня: 9:00, 10:20, 11:40, 13:00, "
                    "14:20, 15:40. Який час зручний?",
                    "900",
                ),
            ],
        )

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("перенесли", "переносимо", "900")

        assert h.session.fsm_parser_null_counts.get("CITY", 0) == 0

    async def test_the_price_question_spends_nothing_from_station(self) -> None:
        """`39469f9f` — three turns of price talk, charged to the station budget.

        The middle turn is the one that needs the seam and not just the
        predicate: «так» to «Ви хочете дізнатися вартість…?» is a confirmation,
        and the chain has to reach the off-question branch without the
        confirmation exemption spending STATION's one allowance on it.
        """
        h = replay(
            FsmState.STATION,
            [
                (
                    "Шиномонтаж R19 у місті Дніпро: легкові — 474 грн, позашляховики "
                    "— 528 грн. Повертаємось до вибору точки шиномонтажу.",
                    "так в Черкасах",
                ),
                ("Ви хочете дізнатися вартість шиномонтажу у Черкасах?", "так"),
                ("Який діаметр коліс у вас?", "19"),
            ],
        )

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("так в Черкасах", "так", "19")

        assert h.session.fsm_parser_null_counts.get("STATION", 0) == 0
        assert h.session.fsm_confirmation_excused_states == []

    async def test_the_diameter_question_spends_nothing_from_station(self) -> None:
        """`380a280d` — the same shape, and the reason «вулиц» is not a marker.

        Its second bot turn is a price quote that names a street. A STATION
        marker list containing «вулиц» would read that as the station question
        and charge «а легкова часу» to it.
        """
        h = replay(
            FsmState.STATION,
            [
                ("Який діаметр коліс у вас?", "12"),
                (
                    "Комплексний шиномонтаж R21-22 у Харкові, на вулиці "
                    "Холодногірській, 11, коштує 534 гривні за колесо. Записуємо "
                    "на монтаж?",
                    "а легкова часу",
                ),
                ("Я на зв'язку. Якщо маєте запитання — я слухаю.", "є запитання"),
            ],
        )

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("12", "а легкова часу", "є запитання")

        assert h.session.fsm_parser_null_counts.get("STATION", 0) == 0

    async def test_the_phone_sub_flow_spends_nothing_from_date(self) -> None:
        """`30dd42fa` — `find_storage` asked for a phone number three times.

        The first of those requests contains «здати», which carries the DATE
        stem «дат». Word-anchored markers are what keep it out.
        """
        h = replay(
            FsmState.DATE,
            [
                (
                    "Не знайшла ваш договір за цим номером. Продиктуйте, будь ласка, "
                    "номер телефону, за яким могли здати шини — український "
                    "мобільний, 10 цифр.",
                    "936 52 18",
                ),
                (
                    "Не знайшла ваш договір за цим номером. Можливо, договір "
                    "оформлено на інший номер? Назвіть, будь ласка, інший телефон "
                    "або скажіть, що не пам'ятаєте номер.",
                    "я номер",
                ),
                (
                    "Будь ласка, продиктуйте номер телефону, за яким могли здати "
                    "шини — український мобільний, 10 цифр.",
                    "095 9362 18",
                ),
            ],
        )
        h.session.fsm_filled_fields["storage_choice"] = "contract"

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("936 52 18", "я номер", "095 9362 18")

        assert h.session.fsm_parser_null_counts.get("DATE", 0) == 0
        assert h.session.fsm_filled_fields.get("date") is None

    async def test_the_city_the_bot_really_asked_for_is_still_charged(self) -> None:
        """The control, from the same call as the test above.

        `30dd42fa` was asked «У якому місті…?», answered «места изюм», was told
        Ізюм has no points, was asked again and answered «арки». Both are real
        misses and both must still cost. An exemption that took these would be
        measuring nothing.
        """
        h = replay(
            FsmState.CITY,
            [
                (
                    "Перепрошую, Віталію! У якому місті вам зручніше записатися на "
                    "шиномонтаж?",
                    "места изюм",
                ),
                (
                    "За містом Ізюм точок шиномонтажу не знайшла. Назвіть, будь "
                    "ласка, інше місто для запису.",
                    "арки",
                ),
            ],
        )

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("места изюм", "арки")

        assert h.session.fsm_parser_null_counts.get("CITY", 0) == 2

    async def test_the_bare_day_answer_fills_the_date_instead_of_costing(self) -> None:
        """`a83655c5` — the fifth transfer, and the only one Fix A alone misses.

        Two of its three DATE charges are off-question: a storage clarification
        and «Ви ще на лінії?». The middle one answered «На яку дату записуємо?»
        with «на 11», which is a real answer the parser could not read. Both
        fixes have to be present for this call to survive.
        """
        h = replay(
            FsmState.DATE,
            [
                (
                    "Правильно розумію: потрібно, щоб ми доставили ваші шини зі "
                    "зберігання?",
                    "и я привезу с собою",
                ),
                ("На яку дату записуємо?", "на 11"),
                ("Ви ще на лінії?", "Я хочу выйти из надписью запись успешный"),
            ],
        )
        h.session.fsm_filled_fields["storage_choice"] = "own"

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("и я привезу с собою", "на 11", "Я хочу выйти из надписью запись успешный")

        assert h.session.fsm_parser_null_counts.get("DATE", 0) == 0
        assert h.session.fsm_filled_fields.get("date", "").endswith("-11")
