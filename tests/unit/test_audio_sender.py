"""Tests for StreamingAudioSender (Layer 4 — streaming audio delivery)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from src.core.audio_sender import (
    CollectedToolCall,
    SendResult,
    StreamingAudioSender,
    send_audio_stream,
)
from src.llm.models import StreamDone, ToolCallDelta, ToolCallEnd, ToolCallStart, Usage
from src.tts.streaming_tts import AudioReady
from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection


async def _events(*items: Any) -> AsyncIterator:
    """Helper — yield items as async iterator."""
    for item in items:
        yield item


# ---------------------------------------------------------------------------
# Data class tests
# ---------------------------------------------------------------------------


class TestCollectedToolCall:
    def test_frozen_and_fields(self) -> None:
        tc = CollectedToolCall(id="tc1", name="search", arguments_json='{"q":"a"}')
        assert tc.id == "tc1"
        assert tc.name == "search"
        assert tc.arguments_json == '{"q":"a"}'
        with pytest.raises(AttributeError):
            tc.id = "tc2"  # type: ignore[misc]


class TestSendResult:
    def test_defaults(self) -> None:
        r = SendResult(spoken_text="hello")
        assert r.spoken_text == "hello"
        assert r.tool_calls == []
        assert r.stop_reason == "end_turn"
        assert r.usage == Usage(0, 0)
        assert r.interrupted is False
        assert r.disconnected is False

    def test_frozen(self) -> None:
        r = SendResult(spoken_text="x")
        with pytest.raises(AttributeError):
            r.spoken_text = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Basic sending
# ---------------------------------------------------------------------------


class TestBasicSending:
    @pytest.mark.asyncio
    async def test_single_audio_event(self) -> None:
        conn = MockAudioSocketConnection()
        sender = StreamingAudioSender(conn)
        stream = _events(
            AudioReady(audio=b"\x00\x01", text="Привіт"),
            StreamDone(stop_reason="end_turn", usage=Usage(10, 5)),
        )
        result = await sender.send(stream)

        assert len(conn.sent_chunks) == 1
        assert conn.sent_chunks[0] == b"\x00\x01"
        assert result.spoken_text == "Привіт"
        assert result.interrupted is False
        assert result.disconnected is False

    @pytest.mark.asyncio
    async def test_two_audio_events(self) -> None:
        conn = MockAudioSocketConnection()
        sender = StreamingAudioSender(conn)
        stream = _events(
            AudioReady(audio=b"\x01", text="Раз"),
            AudioReady(audio=b"\x02", text="Два"),
            StreamDone(stop_reason="end_turn", usage=Usage(0, 0)),
        )
        result = await sender.send(stream)

        assert len(conn.sent_chunks) == 2
        assert result.spoken_text == "Раз Два"

    @pytest.mark.asyncio
    async def test_empty_stream(self) -> None:
        conn = MockAudioSocketConnection()
        sender = StreamingAudioSender(conn)
        stream = _events(
            StreamDone(stop_reason="end_turn", usage=Usage(0, 0)),
        )
        result = await sender.send(stream)

        assert conn.sent_chunks == []
        assert result.spoken_text == ""


# ---------------------------------------------------------------------------
# Tool call collection
# ---------------------------------------------------------------------------


class TestToolCallCollection:
    @pytest.mark.asyncio
    async def test_single_tool_call(self) -> None:
        conn = MockAudioSocketConnection()
        sender = StreamingAudioSender(conn)
        stream = _events(
            ToolCallStart(id="t1", name="search_tires"),
            ToolCallDelta(id="t1", arguments_chunk='{"wid'),
            ToolCallDelta(id="t1", arguments_chunk='th": 205}'),
            ToolCallEnd(id="t1"),
            StreamDone(stop_reason="tool_use", usage=Usage(20, 10)),
        )
        result = await sender.send(stream)

        assert len(result.tool_calls) == 1
        tc = result.tool_calls[0]
        assert tc.id == "t1"
        assert tc.name == "search_tires"
        assert tc.arguments_json == '{"width": 205}'

    @pytest.mark.asyncio
    async def test_two_tool_calls(self) -> None:
        conn = MockAudioSocketConnection()
        sender = StreamingAudioSender(conn)
        stream = _events(
            ToolCallStart(id="t1", name="search_tires"),
            ToolCallDelta(id="t1", arguments_chunk="{}"),
            ToolCallEnd(id="t1"),
            ToolCallStart(id="t2", name="check_availability"),
            ToolCallDelta(id="t2", arguments_chunk='{"id":1}'),
            ToolCallEnd(id="t2"),
            StreamDone(stop_reason="tool_use", usage=Usage(0, 0)),
        )
        result = await sender.send(stream)

        assert len(result.tool_calls) == 2
        assert result.tool_calls[0].name == "search_tires"
        assert result.tool_calls[1].name == "check_availability"
        assert result.tool_calls[1].arguments_json == '{"id":1}'

    @pytest.mark.asyncio
    async def test_tool_calls_between_audio(self) -> None:
        conn = MockAudioSocketConnection()
        sender = StreamingAudioSender(conn)
        stream = _events(
            AudioReady(audio=b"\x01", text="Шукаю"),
            ToolCallStart(id="t1", name="search_tires"),
            ToolCallDelta(id="t1", arguments_chunk="{}"),
            ToolCallEnd(id="t1"),
            AudioReady(audio=b"\x02", text="Знайшов"),
            StreamDone(stop_reason="tool_use", usage=Usage(0, 0)),
        )
        result = await sender.send(stream)

        assert len(conn.sent_chunks) == 2
        assert result.spoken_text == "Шукаю Знайшов"
        assert len(result.tool_calls) == 1


# ---------------------------------------------------------------------------
# Barge-in
# ---------------------------------------------------------------------------


class TestBargeIn:
    @pytest.mark.asyncio
    async def test_barge_in_skips_remaining_audio(self) -> None:
        conn = MockAudioSocketConnection()
        barge_in = asyncio.Event()
        sender = StreamingAudioSender(conn, barge_in_event=barge_in)

        async def _stream() -> AsyncIterator:
            yield AudioReady(audio=b"\x01", text="Перше")
            barge_in.set()
            yield AudioReady(audio=b"\x02", text="Друге")
            yield AudioReady(audio=b"\x03", text="Третє")
            yield StreamDone(stop_reason="end_turn", usage=Usage(0, 0))

        result = await sender.send(_stream())

        assert len(conn.sent_chunks) == 1
        assert result.spoken_text == "Перше"
        assert result.interrupted is True

    @pytest.mark.asyncio
    async def test_barge_in_still_collects_tool_calls(self) -> None:
        conn = MockAudioSocketConnection()
        barge_in = asyncio.Event()
        barge_in.set()  # pre-set — all audio skipped
        sender = StreamingAudioSender(conn, barge_in_event=barge_in)
        stream = _events(
            AudioReady(audio=b"\x01", text="Skipped"),
            ToolCallStart(id="t1", name="search_tires"),
            ToolCallDelta(id="t1", arguments_chunk="{}"),
            ToolCallEnd(id="t1"),
            StreamDone(stop_reason="tool_use", usage=Usage(5, 3)),
        )
        result = await sender.send(stream)

        assert conn.sent_chunks == []
        assert result.interrupted is True
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "search_tires"


# ---------------------------------------------------------------------------
# Connection closed
# ---------------------------------------------------------------------------


class TestConnectionClosed:
    @pytest.mark.asyncio
    async def test_disconnect_mid_stream(self) -> None:
        conn = MockAudioSocketConnection()
        sender = StreamingAudioSender(conn)

        async def _stream() -> AsyncIterator:
            yield AudioReady(audio=b"\x01", text="Перше")
            conn.close_connection()
            yield AudioReady(audio=b"\x02", text="Друге")
            yield StreamDone(stop_reason="end_turn", usage=Usage(0, 0))

        result = await sender.send(_stream())

        assert len(conn.sent_chunks) == 1
        assert result.disconnected is True
        assert result.spoken_text == "Перше"


# ---------------------------------------------------------------------------
# StreamDone
# ---------------------------------------------------------------------------


class TestStreamDone:
    @pytest.mark.asyncio
    async def test_captures_stop_reason_and_usage(self) -> None:
        conn = MockAudioSocketConnection()
        sender = StreamingAudioSender(conn)
        stream = _events(
            StreamDone(stop_reason="max_tokens", usage=Usage(100, 200)),
        )
        result = await sender.send(stream)

        assert result.stop_reason == "max_tokens"
        assert result.usage == Usage(100, 200)


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------


class TestConvenienceFunction:
    @pytest.mark.asyncio
    async def test_send_audio_stream(self) -> None:
        conn = MockAudioSocketConnection()
        stream = _events(
            AudioReady(audio=b"\xaa", text="Тест"),
            StreamDone(stop_reason="end_turn", usage=Usage(1, 2)),
        )
        result = await send_audio_stream(stream, conn)

        assert result.spoken_text == "Тест"
        assert result.stop_reason == "end_turn"
        assert result.usage == Usage(1, 2)
        assert conn.sent_chunks == [b"\xaa"]


# ---------------------------------------------------------------------------
# Integration-style
# ---------------------------------------------------------------------------


class TestIntegration:
    @pytest.mark.asyncio
    async def test_realistic_sequence(self) -> None:
        """Two sentences, a tool call, one more sentence, then done."""
        conn = MockAudioSocketConnection()
        sender = StreamingAudioSender(conn)
        stream = _events(
            AudioReady(audio=b"\x01" * 100, text="Вітаю! Я ваш помічник."),
            AudioReady(audio=b"\x02" * 80, text="Зараз подивлюсь."),
            ToolCallStart(id="tc1", name="search_tires"),
            ToolCallDelta(id="tc1", arguments_chunk='{"width":'),
            ToolCallDelta(id="tc1", arguments_chunk=' 205, "profile": 55}'),
            ToolCallEnd(id="tc1"),
            AudioReady(audio=b"\x03" * 60, text="Знайшов 3 варіанти."),
            StreamDone(stop_reason="end_turn", usage=Usage(50, 30)),
        )
        result = await sender.send(stream)

        assert len(conn.sent_chunks) == 3
        assert conn.sent_chunks[0] == b"\x01" * 100
        assert conn.sent_chunks[1] == b"\x02" * 80
        assert conn.sent_chunks[2] == b"\x03" * 60
        assert result.spoken_text == ("Вітаю! Я ваш помічник. Зараз подивлюсь. Знайшов 3 варіанти.")
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "search_tires"
        assert result.tool_calls[0].arguments_json == '{"width": 205, "profile": 55}'
        assert result.stop_reason == "end_turn"
        assert result.usage == Usage(50, 30)
        assert result.interrupted is False
        assert result.disconnected is False
