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
        )
        assert "city" not in mapped
        assert mapped["date"] == "5 серпня"


# ---------------------------------------------------------------------------
# Mapping seam
# ---------------------------------------------------------------------------


class TestCompoundToFsmMapping:
    def test_hint_keys_are_translated_to_state_field_names(self) -> None:
        mapped = map_compound_fields_to_fsm(
            {"date_hint": "5 серпня", "time_hint": "10:00", "city": "Дніпро"}
        )
        assert mapped == {"date": "5 серпня", "time": "10:00", "city": "Дніпро"}

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

        assert fsm_hops(h.session) == [], "a capped turn moved the FSM anyway"
        assert h.session.fsm_state == FsmState.CITY.value
        assert h.llm_turns == [PRICE_TEXT]

    async def test_low_confidence_never_freezes(self) -> None:
        h = await self._run(interrupt(), confidence=0.2)

        assert fsm_hops(h.session) == [], "a turn below the confidence floor moved the FSM"
        assert h.session.fsm_state == FsmState.CITY.value
        assert h.llm_turns == [PRICE_TEXT]

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
