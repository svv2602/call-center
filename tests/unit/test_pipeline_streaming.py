"""Tests for streaming pipeline integration in CallPipeline."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.prompts import ERROR_TEXT
from src.agent.streaming_loop import TurnResult
from src.core.call_session import CallSession, CallState
from src.core.pipeline import CallPipeline
from src.llm.models import Usage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pipeline(
    *,
    streaming_loop: Any = None,
    session: CallSession | None = None,
) -> CallPipeline:
    """Create a CallPipeline with mocked STT/TTS/Agent and optional streaming loop."""
    conn = AsyncMock()
    conn.is_closed = False
    stt = AsyncMock()
    tts = AsyncMock()
    agent = AsyncMock()
    if session is None:
        session = CallSession(uuid.uuid4())

    return CallPipeline(
        conn=conn,
        stt=stt,
        tts=tts,
        agent=agent,
        session=session,
        streaming_loop=streaming_loop,
    )


def _make_turn_result(
    spoken_text: str = "Відповідь від стрімінгу.",
    tool_calls_made: int = 0,
    stop_reason: str = "end_turn",
) -> TurnResult:
    return TurnResult(
        spoken_text=spoken_text,
        tool_calls_made=tool_calls_made,
        stop_reason=stop_reason,
        total_usage=Usage(50, 30),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStreamingLoopCalled:
    @pytest.mark.asyncio
    async def test_streaming_loop_called_when_present(self) -> None:
        """When streaming_loop is set, run_turn is called with correct args."""
        mock_loop = AsyncMock()
        mock_loop.run_turn = AsyncMock(return_value=_make_turn_result())

        session = CallSession(uuid.uuid4())
        session.caller_phone = "+380501234567"
        session.order_id = "ORD-1"

        pipeline = _make_pipeline(streaming_loop=mock_loop, session=session)

        # Simulate the transcript processor branch directly
        # We access the internal method for unit testing
        transcript = MagicMock()
        transcript.text = "Шукаю шини"
        transcript.confidence = 0.95
        transcript.language = "uk-UA"

        # Call the streaming branch by patching _wait_for_final_transcript
        # to return our transcript once, then None to stop
        call_count = 0

        async def _fake_wait():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return transcript
            # Trigger end on second call
            pipeline._conn.is_closed = True
            return None

        pipeline._wait_for_final_transcript = _fake_wait
        pipeline._session.add_user_turn(content="test")

        await pipeline._transcript_processor_loop()

        mock_loop.run_turn.assert_called_once()
        call_kwargs = mock_loop.run_turn.call_args
        assert call_kwargs.kwargs["user_text"] == "Шукаю шини"
        assert call_kwargs.kwargs["conversation_history"] is pipeline._llm_history
        assert call_kwargs.kwargs["caller_phone"] == "+380501234567"
        assert call_kwargs.kwargs["order_id"] == "ORD-1"


class TestNoWaitFiller:
    @pytest.mark.asyncio
    async def test_no_wait_filler_in_streaming_mode(self) -> None:
        """WAIT_TEXT is NOT spoken before LLM response in streaming mode."""
        mock_loop = AsyncMock()
        mock_loop.run_turn = AsyncMock(return_value=_make_turn_result())

        pipeline = _make_pipeline(streaming_loop=mock_loop)

        transcript = MagicMock()
        transcript.text = "Привіт"
        transcript.confidence = 0.9
        transcript.language = "uk-UA"

        call_count = 0

        async def _fake_wait():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return transcript
            pipeline._conn.is_closed = True
            return None

        pipeline._wait_for_final_transcript = _fake_wait

        # Track all _speak calls
        speak_calls: list[str] = []
        original_speak = pipeline._speak

        async def _track_speak(text: str) -> None:
            speak_calls.append(text)

        pipeline._speak = _track_speak

        await pipeline._transcript_processor_loop()

        # No wait filler should have been spoken
        from src.agent.prompts import WAIT_TEXT

        assert not any(WAIT_TEXT in call for call in speak_calls)


class TestFallbackOnEmptyResult:
    @pytest.mark.asyncio
    async def test_fallback_on_empty_streaming_result(self) -> None:
        """Error template spoken when run_turn returns empty spoken_text."""
        mock_loop = AsyncMock()
        mock_loop.run_turn = AsyncMock(return_value=_make_turn_result(spoken_text=""))

        pipeline = _make_pipeline(streaming_loop=mock_loop)

        transcript = MagicMock()
        transcript.text = "Привіт"
        transcript.confidence = 0.9
        transcript.language = "uk-UA"

        call_count = 0

        async def _fake_wait():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return transcript
            pipeline._conn.is_closed = True
            return None

        pipeline._wait_for_final_transcript = _fake_wait

        speak_calls: list[str] = []

        async def _track_speak(text: str) -> None:
            speak_calls.append(text)

        pipeline._speak = _track_speak

        await pipeline._transcript_processor_loop()

        # Error fallback should have been spoken
        assert any(ERROR_TEXT in call for call in speak_calls)


class TestFallbackOnTimeout:
    @pytest.mark.asyncio
    async def test_fallback_on_streaming_timeout(self) -> None:
        """Error template spoken when run_turn times out."""
        mock_loop = AsyncMock()

        async def _slow_turn(**kwargs: Any) -> TurnResult:
            await asyncio.sleep(100)
            return _make_turn_result()

        mock_loop.run_turn = _slow_turn

        pipeline = _make_pipeline(streaming_loop=mock_loop)

        transcript = MagicMock()
        transcript.text = "Привіт"
        transcript.confidence = 0.9
        transcript.language = "uk-UA"

        call_count = 0

        async def _fake_wait():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return transcript
            pipeline._conn.is_closed = True
            return None

        pipeline._wait_for_final_transcript = _fake_wait

        speak_calls: list[str] = []

        async def _track_speak(text: str) -> None:
            speak_calls.append(text)

        pipeline._speak = _track_speak

        # Reduce timeout for test speed
        with patch("src.core.pipeline.AGENT_PROCESSING_TIMEOUT_SEC", 0.1):
            await pipeline._transcript_processor_loop()

        assert any(ERROR_TEXT in call for call in speak_calls)


class TestBlockingPathUnchanged:
    @pytest.mark.asyncio
    async def test_blocking_path_unchanged(self) -> None:
        """streaming_loop=None → existing blocking behavior."""
        pipeline = _make_pipeline(streaming_loop=None)

        transcript = MagicMock()
        transcript.text = "Привіт"
        transcript.confidence = 0.9
        transcript.language = "uk-UA"

        pipeline._agent.process_message = AsyncMock(return_value=("Добрий день!", []))

        call_count = 0

        async def _fake_wait():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return transcript
            pipeline._conn.is_closed = True
            return None

        pipeline._wait_for_final_transcript = _fake_wait

        speak_calls: list[str] = []

        async def _track_speak(text: str) -> None:
            speak_calls.append(text)

        pipeline._speak = _track_speak
        pipeline._speak_streaming = _track_speak

        await pipeline._transcript_processor_loop()

        # Blocking path speaks wait filler first
        from src.agent.prompts import WAIT_TEXT

        assert any(WAIT_TEXT in call for call in speak_calls)
        # Agent was called via blocking path
        pipeline._agent.process_message.assert_called_once()


class TestLLMHistoryPersists:
    @pytest.mark.asyncio
    async def test_llm_history_persists_across_turns(self) -> None:
        """_llm_history accumulates across multiple turns."""
        mock_loop = AsyncMock()
        mock_loop.run_turn = AsyncMock(return_value=_make_turn_result())

        # Make run_turn mutate conversation_history like the real one does
        async def _mutating_turn(**kwargs: Any) -> TurnResult:
            history = kwargs["conversation_history"]
            history.append({"role": "user", "content": kwargs["user_text"]})
            history.append({"role": "assistant", "content": [{"type": "text", "text": "Reply"}]})
            return _make_turn_result()

        mock_loop.run_turn = _mutating_turn

        pipeline = _make_pipeline(streaming_loop=mock_loop)

        transcripts = [MagicMock() for _ in range(3)]
        for i, t in enumerate(transcripts):
            t.text = f"Turn {i}"
            t.confidence = 0.9
            t.language = "uk-UA"

        call_count = 0

        async def _fake_wait():
            nonlocal call_count
            if call_count < len(transcripts):
                t = transcripts[call_count]
                call_count += 1
                return t
            pipeline._conn.is_closed = True
            return None

        pipeline._wait_for_final_transcript = _fake_wait

        await pipeline._transcript_processor_loop()

        # History should have 2 entries per turn (user + assistant) = 6
        assert len(pipeline._llm_history) == 6
        assert pipeline._llm_history[0]["role"] == "user"
        assert pipeline._llm_history[0]["content"] == "Turn 0"
        assert pipeline._llm_history[1]["role"] == "assistant"
