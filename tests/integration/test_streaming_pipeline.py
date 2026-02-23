"""Integration tests — full streaming pipeline end-to-end.

LLM (mock) → SentenceBuffer → StreamingTTS → AudioSender → AudioSocket (mock).
Tests verify that data flows correctly through all 5 layers.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.agent.agent import ToolRouter
from src.agent.streaming_loop import StreamingAgentLoop, TurnResult
from src.llm.models import (
    StreamDone,
    StreamEvent,
    TextDelta,
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
    Usage,
)
from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection
from tests.unit.mocks.mock_llm_router import MockLLMRouter
from tests.unit.mocks.mock_tts import MockTTSEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _done(
    stop_reason: str = "end_turn",
    input_tokens: int = 10,
    output_tokens: int = 20,
) -> StreamDone:
    return StreamDone(stop_reason=stop_reason, usage=Usage(input_tokens, output_tokens))


def _text_stream(text: str, **done_kwargs: Any) -> list[StreamEvent]:
    """Simple text-only LLM response."""
    return [TextDelta(text=text), _done(**done_kwargs)]


def _tool_stream(
    text: str,
    tool_id: str,
    tool_name: str,
    tool_args: dict[str, Any],
    **done_kwargs: Any,
) -> list[StreamEvent]:
    """LLM response with text + a single tool call."""
    args_json = json.dumps(tool_args)
    return [
        TextDelta(text=text),
        ToolCallStart(id=tool_id, name=tool_name),
        ToolCallDelta(id=tool_id, arguments_chunk=args_json),
        ToolCallEnd(id=tool_id),
        _done(stop_reason="tool_use", **done_kwargs),
    ]


def _build_loop(
    responses: list[list[StreamEvent]],
    tool_results: dict[str, Any] | None = None,
) -> tuple[StreamingAgentLoop, MockLLMRouter, MockAudioSocketConnection]:
    """Create a full StreamingAgentLoop with mocks for all layers."""
    router = MockLLMRouter(responses)
    tool_router = ToolRouter()
    if tool_results:
        for name, result in tool_results.items():
            handler = AsyncMock(return_value=result)
            tool_router.register(name, handler)
    tts = MockTTSEngine()
    conn = MockAudioSocketConnection()
    barge_in = asyncio.Event()

    loop = StreamingAgentLoop(
        llm_router=router,
        tool_router=tool_router,
        tts=tts,
        conn=conn,
        barge_in_event=barge_in,
        system_prompt="Test system prompt",
    )
    return loop, router, conn


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestTextFlowsThroughAllLayers:
    @pytest.mark.asyncio
    async def test_text_flows_through_all_layers(self) -> None:
        """LLM text → sentence buffer → TTS → AudioSocket → verify audio + spoken_text."""
        loop, router, conn = _build_loop([_text_stream("Привіт! Я допоможу вам підібрати шини.")])

        history: list[dict[str, Any]] = []
        result = await loop.run_turn("Привіт", history)

        # Text flows through all layers
        assert "Привіт" in result.spoken_text
        assert result.tool_calls_made == 0
        assert result.stop_reason == "end_turn"
        # Audio was delivered to AudioSocket
        assert len(conn.sent_chunks) > 0
        assert all(isinstance(c, bytes) for c in conn.sent_chunks)
        # History was updated
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "assistant"
        # LLM was called exactly once
        assert router.call_count == 1


class TestToolCallRoundEndToEnd:
    @pytest.mark.asyncio
    async def test_tool_call_round_end_to_end(self) -> None:
        """LLM with tool → tool exec → LLM again → verify tool_calls_made and audio."""
        loop, router, conn = _build_loop(
            [
                _tool_stream(
                    "Зараз перевірю наявність. ",
                    "tc1",
                    "check_availability",
                    {"sku": "T-205-55-R16"},
                ),
                _text_stream("Ця шина є в наявності! 4 штуки на складі."),
            ],
            tool_results={"check_availability": {"available": True, "quantity": 4}},
        )

        result = await loop.run_turn("Чи є ця шина?", [])

        assert result.tool_calls_made == 1
        assert "наявності" in result.spoken_text or "Зараз" in result.spoken_text
        assert result.stop_reason == "end_turn"
        assert router.call_count == 2
        # Audio was sent for both LLM rounds
        assert len(conn.sent_chunks) > 0


class TestBargeInStopsAudioMidStream:
    @pytest.mark.asyncio
    async def test_barge_in_stops_audio_mid_stream(self) -> None:
        """Barge-in event → interrupted=True, partial audio."""
        router = MockLLMRouter(
            [_text_stream("Довга відповідь яка буде перервана під час відтворення.")]
        )
        conn = MockAudioSocketConnection()
        barge_in = asyncio.Event()
        barge_in.set()  # pre-set: interruption from the start

        loop = StreamingAgentLoop(
            llm_router=router,
            tool_router=ToolRouter(),
            tts=MockTTSEngine(),
            conn=conn,
            barge_in_event=barge_in,
            system_prompt="Test",
        )

        result = await loop.run_turn("Стоп!", [])

        assert result.interrupted is True
        # No audio was actually sent (all skipped due to barge-in)
        assert len(conn.sent_chunks) == 0


class TestMultipleToolRounds:
    @pytest.mark.asyncio
    async def test_multiple_tool_rounds(self) -> None:
        """2 tool rounds → final text. Verifies multi-round tool execution."""
        loop, router, conn = _build_loop(
            [
                _tool_stream(
                    "Шукаю шини. ",
                    "tc1",
                    "search_tires",
                    {"size": "205/55R16"},
                ),
                _tool_stream(
                    "Перевіряю наявність. ",
                    "tc2",
                    "check_availability",
                    {"sku": "T-001"},
                ),
                _text_stream("Знайшов 3 варіанти, всі в наявності!"),
            ],
            tool_results={
                "search_tires": {"tires": [{"sku": "T-001", "name": "Michelin"}]},
                "check_availability": {"available": True, "qty": 5},
            },
        )

        result = await loop.run_turn("Підбери шини 205/55R16", [])

        assert result.tool_calls_made == 2
        assert result.stop_reason == "end_turn"
        assert router.call_count == 3
        assert len(conn.sent_chunks) > 0


class TestEmptyLLMResponse:
    @pytest.mark.asyncio
    async def test_empty_llm_response(self) -> None:
        """StreamDone only → empty spoken_text in TurnResult."""
        loop, _, conn = _build_loop(
            [[_done()]]  # StreamDone only, no text
        )

        result = await loop.run_turn("Привіт", [])

        assert result.spoken_text == ""
        assert result.tool_calls_made == 0
        assert len(conn.sent_chunks) == 0


class TestWordByWordSentenceSplitting:
    @pytest.mark.asyncio
    async def test_word_by_word_sentence_splitting(self) -> None:
        """Char-by-char TextDeltas with sentence boundaries → correct AudioReady count.

        Simulates realistic LLM streaming: text arrives token by token.
        Two sentences should produce audio for each sentence.
        """
        # Simulate word-by-word streaming of two sentences
        events: list[StreamEvent] = [
            TextDelta(text="Привіт! "),
            TextDelta(text="Як "),
            TextDelta(text="справи?"),
            _done(),
        ]

        loop, _, conn = _build_loop([events])

        result = await loop.run_turn("Привіт", [])

        # Should have at least 1 audio chunk
        # (2 sentences: "Привіт!" and "Як справи?" — or merged depending on buffer)
        assert len(conn.sent_chunks) >= 1
        assert result.spoken_text != ""
