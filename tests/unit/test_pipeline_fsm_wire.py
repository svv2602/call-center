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

import contextlib
import datetime
import inspect
import logging
import uuid
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

    def __init__(self, session: CallSession | None = None) -> None:
        self.spoken: list[str] = []
        self.llm_turns: list[str] = []

        conn = MagicMock(spec=["is_closed"])
        conn.is_closed = False

        llm_router = MagicMock(spec=["complete"])
        tool_router = MagicMock(spec=["execute"])

        streaming_loop = MagicMock(spec=["run_turn", "_llm_router", "_tool_router"])
        streaming_loop._llm_router = llm_router
        streaming_loop._tool_router = tool_router

        async def _run_turn(**kwargs: Any) -> TurnResult:
            self.llm_turns.append(kwargs.get("user_text", ""))
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
        """What `get_fitting_stations` leaves in the session (`src/main.py`)."""
        session.fitting_station_ids = {"ST-1"}  # snapshot comes from the fixture

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


def booking_in_progress(state: FsmState = FsmState.CITY) -> CallSession:
    """A session parked mid-booking, the way a live PRICE interrupt finds it."""
    session = CallSession(uuid.uuid4())
    session.caller_phone = "+380671234567"
    session.last_fitting_station_id = "ST-1"
    session.fitting_stations_seen = [
        {"id": "ST-1", "name": "Шиномонтаж №1", "city": "Київ", "address": "вул. Тестова, 1"}
    ]
    session.fsm_state = state.value
    session.fsm_filled_fields["intent"] = "fitting"
    return session


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

    async def test_the_exemption_cannot_be_spent_twice(self) -> None:
        """The loop-breaker is structural, not a cap to remember.

        A passive parser runs only while its field is empty, so `name` can
        excuse exactly one turn. The next unparsable turn is charged normally.
        """
        h = Harness(booking_in_progress(FsmState.CITY))
        h.session.fsm_filled_fields.pop("city", None)
        h.session.add_assistant_turn(NAME_QUESTION)

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("Юра", "ага")

        assert h.session.fsm_filled_fields.get("name") == "Юра"
        assert h.session.fsm_parser_null_counts.get("CITY", 0) == 1

    async def test_a_turn_with_no_passive_fill_is_still_charged(self) -> None:
        h = Harness(booking_in_progress(FsmState.CITY))
        h.session.fsm_filled_fields.pop("city", None)
        h.session.add_assistant_turn(NAME_QUESTION)

        with fsm_flags(enabled=True, shadow_mode=True):
            await h.run("ага")

        assert h.session.fsm_parser_null_counts.get("CITY", 0) == 1

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
# Wave 6-C — STATION stops being a dead end
# ---------------------------------------------------------------------------


def station_in_progress() -> CallSession:
    """Parked in STATION with the snapshot `get_fitting_stations` would leave."""
    session = CallSession(uuid.uuid4())
    session.caller_phone = "+380671234567"
    session.fsm_state = FsmState.STATION.value
    session.fsm_filled_fields["intent"] = "fitting"
    session.fsm_filled_fields["city"] = "Київ"
    session.fitting_stations_seen = [
        {"id": "st-1", "name": "Оболонь", "district": "Оболонський"},
        {"id": "st-2", "name": "Позняки", "district": "Дарницький"},
    ]
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
        """«Перемоги» exists in two cities — cross-city guard `13e9ea4`."""
        h = Harness(station_in_progress())
        h.session.fitting_stations_seen = [
            {"id": "st-zp", "name": "Перемоги 72Б", "district": "Запоріжжя"},
            {"id": "st-dp", "name": "Перемоги 15", "district": "Дніпро"},
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
