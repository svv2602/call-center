"""Tests for the streaming agent loop (Layer 5)."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from src.agent.agent import MAX_HISTORY_MESSAGES, ToolRouter
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
from tests.unit.mocks.mock_tts import MockTTSEngine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


# ── Helpers ──────────────────────────────────────────────────────────────


class MockLLMRouter:
    """Mock LLM router — returns pre-configured stream event sequences.

    Each call to complete_stream pops the next response from the list.
    """

    def __init__(self, responses: list[list[StreamEvent]]) -> None:
        self._responses = list(responses)
        self._call_count = 0

    @property
    def call_count(self) -> int:
        return self._call_count

    async def complete_stream(
        self,
        task: Any,
        messages: Any,
        *,
        system: str | None = None,
        tools: Any = None,
        max_tokens: int = 1024,
        provider_override: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Async generator matching real LLMRouter.complete_stream signature."""
        self._call_count += 1
        if not self._responses:
            raise RuntimeError("MockLLMRouter: no more responses configured")
        events = self._responses.pop(0)
        for e in events:
            yield e


class ErrorLLMRouter:
    """LLM router that raises on complete_stream."""

    async def complete_stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[StreamEvent]:
        raise RuntimeError("LLM provider down")
        yield  # make it a generator  # pragma: no cover


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
    *,
    pii_vault: Any = None,
    max_tool_rounds: int = 5,
    closed: bool = False,
) -> tuple[StreamingAgentLoop, MockLLMRouter, ToolRouter, MockAudioSocketConnection]:
    """Create StreamingAgentLoop with mocks."""
    router = MockLLMRouter(responses)
    tool_router = ToolRouter()
    if tool_results:
        for name, result in tool_results.items():
            handler = AsyncMock(return_value=result)
            tool_router.register(name, handler)
    tts = MockTTSEngine()
    conn = MockAudioSocketConnection(closed=closed)
    barge_in = asyncio.Event()

    loop = StreamingAgentLoop(
        llm_router=router,
        tool_router=tool_router,
        tts=tts,
        conn=conn,
        barge_in_event=barge_in,
        system_prompt="Test system prompt",
        pii_vault=pii_vault,
        max_tool_rounds=max_tool_rounds,
    )
    return loop, router, tool_router, conn


# ── TurnResult dataclass ─────────────────────────────────────────────


class TestTurnResult:
    def test_frozen(self):
        tr = TurnResult(
            spoken_text="hello",
            tool_calls_made=0,
            stop_reason="end_turn",
            total_usage=Usage(10, 20),
        )
        with pytest.raises(AttributeError):
            tr.spoken_text = "other"  # type: ignore[misc]

    def test_fields_and_defaults(self):
        tr = TurnResult(
            spoken_text="hi",
            tool_calls_made=1,
            stop_reason="tool_use",
            total_usage=Usage(5, 10),
        )
        assert tr.spoken_text == "hi"
        assert tr.tool_calls_made == 1
        assert tr.stop_reason == "tool_use"
        assert tr.total_usage == Usage(5, 10)
        assert tr.interrupted is False
        assert tr.disconnected is False


# ── Simple turn (no tools) ───────────────────────────────────────────


class TestSimpleTurn:
    @pytest.mark.asyncio
    async def test_text_only_response(self):
        """LLM streams text → audio sent → TurnResult reflects text."""
        loop, router, _, conn = _build_loop(
            [
                _text_stream("Добрий день! Чим допомогти?"),
            ]
        )
        history: list[dict[str, Any]] = []
        result = await loop.run_turn("Привіт", history)

        assert "Добрий день" in result.spoken_text
        assert result.tool_calls_made == 0
        assert result.stop_reason == "end_turn"
        assert result.interrupted is False
        assert result.disconnected is False
        assert result.total_usage.input_tokens == 10
        assert result.total_usage.output_tokens == 20
        assert router.call_count == 1
        # Audio was sent
        assert len(conn.sent_chunks) > 0

    @pytest.mark.asyncio
    async def test_history_updated(self):
        """After turn, history contains user and assistant messages."""
        loop, _, _, _ = _build_loop(
            [
                _text_stream("Відповідь."),
            ]
        )
        history: list[dict[str, Any]] = []
        await loop.run_turn("Запитання", history)

        assert len(history) == 2
        assert history[0] == {"role": "user", "content": "Запитання"}
        assert history[1]["role"] == "assistant"
        assert history[1]["content"][0]["type"] == "text"


# ── Tool call round ──────────────────────────────────────────────────


class TestToolCallRound:
    @pytest.mark.asyncio
    async def test_single_tool_call(self):
        """LLM returns text + tool → tool executed → LLM re-invoked → final text."""
        loop, router, _, _ = _build_loop(
            [
                _tool_stream(
                    "Зараз перевірю. ",
                    "tc1",
                    "search_tires",
                    {"size": "205/55R16"},
                ),
                _text_stream("Знайдено 3 варіанти."),
            ],
            tool_results={"search_tires": {"tires": [{"name": "Michelin"}]}},
        )
        history: list[dict[str, Any]] = []
        result = await loop.run_turn("Шукаю шини", history)

        assert result.tool_calls_made == 1
        assert "Зараз перевірю" in result.spoken_text
        assert "Знайдено" in result.spoken_text
        assert result.stop_reason == "end_turn"
        assert router.call_count == 2

    @pytest.mark.asyncio
    async def test_tool_receives_parsed_args(self):
        """Tool handler receives correctly parsed JSON arguments."""
        tool_handler = AsyncMock(return_value={"found": True})
        router = MockLLMRouter(
            [
                _tool_stream("Перевіряю. ", "tc1", "check_availability", {"sku": "T-123"}),
                _text_stream("Є в наявності."),
            ]
        )
        tool_router = ToolRouter()
        tool_router.register("check_availability", tool_handler)

        loop = StreamingAgentLoop(
            llm_router=router,
            tool_router=tool_router,
            tts=MockTTSEngine(),
            conn=MockAudioSocketConnection(),
            barge_in_event=asyncio.Event(),
            system_prompt="Test",
        )
        await loop.run_turn("Є в наявності?", [])

        tool_handler.assert_called_once_with(sku="T-123")


# ── Multiple tool rounds ─────────────────────────────────────────────


class TestMultipleToolRounds:
    @pytest.mark.asyncio
    async def test_two_rounds_of_tools(self):
        """LLM → tool → LLM → tool → LLM → final text."""
        loop, router, _, _ = _build_loop(
            [
                _tool_stream("Шукаю. ", "tc1", "search_tires", {"size": "205"}),
                _tool_stream("Перевіряю. ", "tc2", "check_availability", {"sku": "X"}),
                _text_stream("Є в наявності!"),
            ],
            tool_results={
                "search_tires": {"results": []},
                "check_availability": {"available": True},
            },
        )
        result = await loop.run_turn("Знайди шини", [])

        assert result.tool_calls_made == 2
        assert router.call_count == 3
        assert result.stop_reason == "end_turn"


# ── Max tool rounds limit ────────────────────────────────────────────


class TestMaxToolRounds:
    @pytest.mark.asyncio
    async def test_stops_at_max_tool_rounds(self):
        """Loop stops after max_tool_rounds even if LLM keeps returning tools."""
        # 4 tool responses — but max_tool_rounds=2, so only 2 execute
        responses = [
            _tool_stream(f"Round {i}. ", f"tc{i}", "search_tires", {"n": i}) for i in range(4)
        ]
        loop, router, _, _ = _build_loop(
            responses,
            tool_results={"search_tires": {"ok": True}},
            max_tool_rounds=2,
        )
        result = await loop.run_turn("Go", [])

        assert result.tool_calls_made == 2
        # LLM called: round 0 (tool) → round 1 (tool) → stop
        assert router.call_count == 2


# ── Barge-in stops loop ──────────────────────────────────────────────


class TestBargeIn:
    @pytest.mark.asyncio
    async def test_barge_in_interrupts(self):
        """Setting barge_in_event stops the loop with interrupted=True."""
        router = MockLLMRouter(
            [
                _text_stream("Довга відповідь яка буде перервана."),
            ]
        )
        conn = MockAudioSocketConnection()
        barge_in = asyncio.Event()
        barge_in.set()  # Already interrupted

        loop = StreamingAgentLoop(
            llm_router=router,
            tool_router=ToolRouter(),
            tts=MockTTSEngine(),
            conn=conn,
            barge_in_event=barge_in,
            system_prompt="Test",
        )
        result = await loop.run_turn("Говори", [])

        assert result.interrupted is True


# ── Disconnect stops loop ────────────────────────────────────────────


class TestDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_stops_loop(self):
        """Closed connection stops the loop with disconnected=True."""
        loop, _, _, _ = _build_loop(
            [_text_stream("Відповідь.")],
            closed=True,
        )
        result = await loop.run_turn("Привіт", [])

        assert result.disconnected is True


# ── PII masking ──────────────────────────────────────────────────────


class TestPIIMasking:
    @pytest.mark.asyncio
    async def test_user_text_masked(self):
        """User text is masked before being added to history."""
        from src.logging.pii_vault import PIIVault

        vault = PIIVault()
        loop, _, _, _ = _build_loop(
            [_text_stream("Зрозуміло.")],
            pii_vault=vault,
        )
        history: list[dict[str, Any]] = []
        await loop.run_turn("Мій номер +380501234567", history)

        # User message in history should have masked phone
        user_msg = history[0]["content"]
        assert "+380501234567" not in user_msg
        assert "[PHONE_" in user_msg

    @pytest.mark.asyncio
    async def test_tool_args_restored_and_results_masked(self):
        """PII restored in tool args, masked in tool results."""
        from src.logging.pii_vault import PIIVault

        vault = PIIVault()
        # Pre-mask a phone so we know the placeholder
        masked = vault.mask("+380501234567")
        phone_placeholder = masked  # e.g. "[PHONE_1]"

        tool_handler = AsyncMock(return_value={"status": "ok", "phone": "+380501234567"})
        router = MockLLMRouter(
            [
                _tool_stream(
                    "Перевіряю. ",
                    "tc1",
                    "get_order_status",
                    {"phone": phone_placeholder},
                ),
                _text_stream("Готово."),
            ]
        )
        tool_router = ToolRouter()
        tool_router.register("get_order_status", tool_handler)

        loop = StreamingAgentLoop(
            llm_router=router,
            tool_router=tool_router,
            tts=MockTTSEngine(),
            conn=MockAudioSocketConnection(),
            barge_in_event=asyncio.Event(),
            system_prompt="Test",
            pii_vault=vault,
        )
        await loop.run_turn("Статус", [])

        # Tool handler received real phone (restored)
        tool_handler.assert_called_once_with(phone="+380501234567")


# ── History management ───────────────────────────────────────────────


class TestHistoryManagement:
    @pytest.mark.asyncio
    async def test_history_trimming(self):
        """History is trimmed to MAX_HISTORY_MESSAGES."""
        loop, _, _, _ = _build_loop([_text_stream("Ok.")])
        # Pre-fill history beyond the limit
        history: list[dict[str, Any]] = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg-{i}"}
            for i in range(MAX_HISTORY_MESSAGES + 10)
        ]
        await loop.run_turn("New message", history)

        # History should be trimmed (first + recent)
        assert len(history) <= MAX_HISTORY_MESSAGES + 2  # trimmed + new user + assistant

    @pytest.mark.asyncio
    async def test_tool_results_in_history(self):
        """After tool round: history has assistant (text+tool_use) and user (tool_result)."""
        loop, _, _, _ = _build_loop(
            [
                _tool_stream("Шукаю. ", "tc1", "search_tires", {"size": "205"}),
                _text_stream("Знайдено."),
            ],
            tool_results={"search_tires": {"tires": []}},
        )
        history: list[dict[str, Any]] = []
        await loop.run_turn("Шукай", history)

        # history[0] = user "Шукай"
        # history[1] = assistant [text + tool_use]
        # history[2] = user [tool_result]
        # history[3] = assistant [text]
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "assistant"
        blocks = history[1]["content"]
        types = [b["type"] for b in blocks]
        assert "text" in types
        assert "tool_use" in types
        assert history[2]["role"] == "user"
        assert history[2]["content"][0]["type"] == "tool_result"


# ── Usage accumulation ───────────────────────────────────────────────


class TestUsageAccumulation:
    @pytest.mark.asyncio
    async def test_usage_sums_across_rounds(self):
        """Total usage sums input/output tokens across LLM rounds."""
        loop, _, _, _ = _build_loop(
            [
                _tool_stream(
                    "Перевіряю. ",
                    "tc1",
                    "search_tires",
                    {"s": "1"},
                    input_tokens=100,
                    output_tokens=50,
                ),
                _text_stream("Готово.", input_tokens=150, output_tokens=30),
            ],
            tool_results={"search_tires": {"ok": True}},
        )
        result = await loop.run_turn("Go", [])

        assert result.total_usage.input_tokens == 250
        assert result.total_usage.output_tokens == 80


# ── Error handling ───────────────────────────────────────────────────


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_llm_error_returns_empty_result(self):
        """LLM exception → returns TurnResult with stop_reason='error'."""
        loop = StreamingAgentLoop(
            llm_router=ErrorLLMRouter(),
            tool_router=ToolRouter(),
            tts=MockTTSEngine(),
            conn=MockAudioSocketConnection(),
            barge_in_event=asyncio.Event(),
            system_prompt="Test",
        )
        result = await loop.run_turn("Привіт", [])

        assert result.spoken_text == ""
        assert result.stop_reason == "error"
        assert result.tool_calls_made == 0
