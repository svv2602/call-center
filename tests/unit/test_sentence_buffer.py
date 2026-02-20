"""Tests for the sentence buffer (LLM streaming → sentence chunks for TTS)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from src.core.sentence_buffer import (
    BufferEvent,
    SentenceBuffer,
    SentenceReady,
    buffer_sentences,
)
from src.llm.models import (
    StreamDone,
    StreamEvent,
    TextDelta,
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
    Usage,
)


# ── Helpers ──────────────────────────────────────────────────────────────


async def _events_from(*deltas: StreamEvent) -> AsyncIterator[StreamEvent]:
    """Create async generator from a list of StreamEvents."""
    for d in deltas:
        yield d


async def _collect(gen: AsyncIterator[BufferEvent]) -> list[BufferEvent]:
    return [e async for e in gen]


def _done() -> StreamDone:
    return StreamDone(stop_reason="end_turn", usage=Usage(10, 20))


# ── SentenceReady dataclass ─────────────────────────────────────────────


class TestSentenceReady:
    def test_frozen(self):
        sr = SentenceReady(text="hello")
        with pytest.raises(AttributeError):
            sr.text = "other"  # type: ignore[misc]

    def test_text_attribute(self):
        sr = SentenceReady(text="Привіт!")
        assert sr.text == "Привіт!"


# ── Basic sentence splitting ────────────────────────────────────────────


class TestBasicSentenceSplitting:
    @pytest.mark.asyncio
    async def test_single_sentence(self):
        """Single complete sentence → 1 SentenceReady + StreamDone."""
        buf = SentenceBuffer()
        stream = _events_from(
            TextDelta(text="Добрий день. "),
            _done(),
        )
        result = await _collect(buf.process(stream))
        assert result == [
            SentenceReady(text="Добрий день."),
            _done(),
        ]

    @pytest.mark.asyncio
    async def test_two_sentences(self):
        """Two sentences → 2 SentenceReady events."""
        buf = SentenceBuffer()
        stream = _events_from(
            TextDelta(text="Перше речення. Друге речення. "),
            _done(),
        )
        result = await _collect(buf.process(stream))
        assert result == [
            SentenceReady(text="Перше речення."),
            SentenceReady(text="Друге речення."),
            _done(),
        ]

    @pytest.mark.asyncio
    async def test_punctuation_split_across_deltas(self):
        """Period in one delta, space in next → flushes correctly."""
        buf = SentenceBuffer()
        stream = _events_from(
            TextDelta(text="Привіт."),
            TextDelta(text=" Як справи?"),
            _done(),
        )
        result = await _collect(buf.process(stream))
        assert result == [
            SentenceReady(text="Привіт."),
            SentenceReady(text="Як справи?"),
            _done(),
        ]

    @pytest.mark.asyncio
    async def test_exclamation_mark(self):
        buf = SentenceBuffer()
        stream = _events_from(
            TextDelta(text="Чудово! Дякую. "),
            _done(),
        )
        result = await _collect(buf.process(stream))
        assert result == [
            SentenceReady(text="Чудово!"),
            SentenceReady(text="Дякую."),
            _done(),
        ]

    @pytest.mark.asyncio
    async def test_question_mark(self):
        buf = SentenceBuffer()
        stream = _events_from(
            TextDelta(text="Як розмір? "),
            TextDelta(text="205/55R16."),
            _done(),
        )
        result = await _collect(buf.process(stream))
        assert result == [
            SentenceReady(text="Як розмір?"),
            SentenceReady(text="205/55R16."),
            _done(),
        ]

    @pytest.mark.asyncio
    async def test_multiple_sentences_in_single_delta(self):
        buf = SentenceBuffer()
        stream = _events_from(
            TextDelta(text="Раз. Два. Три."),
            _done(),
        )
        result = await _collect(buf.process(stream))
        assert result == [
            SentenceReady(text="Раз."),
            SentenceReady(text="Два."),
            SentenceReady(text="Три."),
            _done(),
        ]


# ── Clause flush (min_clause_chars) ─────────────────────────────────────


class TestClauseFlush:
    @pytest.mark.asyncio
    async def test_short_text_not_flushed_at_comma(self):
        """Short text with comma (< 40 chars) → NOT flushed, waits for period."""
        buf = SentenceBuffer()
        stream = _events_from(
            TextDelta(text="Привіт, друже"),
            _done(),
        )
        result = await _collect(buf.process(stream))
        assert result == [
            SentenceReady(text="Привіт, друже"),
            _done(),
        ]

    @pytest.mark.asyncio
    async def test_long_text_flushed_at_comma(self):
        """Long text with comma (>= 40 chars) → flushed at comma."""
        buf = SentenceBuffer()
        # 45 chars before comma+space
        long_text = "Це дуже довгий текст для тестування буфера, залишок"
        stream = _events_from(
            TextDelta(text=long_text),
            _done(),
        )
        result = await _collect(buf.process(stream))
        # Should flush at the comma
        assert len(result) == 3  # SentenceReady + SentenceReady + StreamDone
        assert isinstance(result[0], SentenceReady)
        assert result[0].text.endswith(",")
        assert isinstance(result[1], SentenceReady)
        assert result[1].text == "залишок"
        assert isinstance(result[2], StreamDone)

    @pytest.mark.asyncio
    async def test_custom_min_clause_chars(self):
        """Custom min_clause_chars works."""
        buf = SentenceBuffer(min_clause_chars=10)
        stream = _events_from(
            TextDelta(text="Коротко, але достатньо"),
            _done(),
        )
        result = await _collect(buf.process(stream))
        # "Коротко, " is > 10 chars, should flush at comma
        assert isinstance(result[0], SentenceReady)
        assert result[0].text == "Коротко,"

    @pytest.mark.asyncio
    async def test_semicolon_clause_flush(self):
        """Semicolon also triggers clause flush for long text."""
        buf = SentenceBuffer(min_clause_chars=10)
        stream = _events_from(
            TextDelta(text="Перша частина; друга частина"),
            _done(),
        )
        result = await _collect(buf.process(stream))
        assert isinstance(result[0], SentenceReady)
        assert result[0].text == "Перша частина;"


# ── Tool call handling ──────────────────────────────────────────────────


class TestToolCallHandling:
    @pytest.mark.asyncio
    async def test_text_then_tool_call_flushes(self):
        """TextDelta + ToolCallStart → SentenceReady (partial), then ToolCallStart."""
        buf = SentenceBuffer()
        stream = _events_from(
            TextDelta(text="Зараз перевірю"),
            ToolCallStart(id="tc1", name="search_tires"),
            ToolCallDelta(id="tc1", arguments_chunk='{"size":'),
            ToolCallDelta(id="tc1", arguments_chunk='"205"}'),
            ToolCallEnd(id="tc1"),
            _done(),
        )
        result = await _collect(buf.process(stream))
        assert result == [
            SentenceReady(text="Зараз перевірю"),
            ToolCallStart(id="tc1", name="search_tires"),
            ToolCallDelta(id="tc1", arguments_chunk='{"size":'),
            ToolCallDelta(id="tc1", arguments_chunk='"205"}'),
            ToolCallEnd(id="tc1"),
            _done(),
        ]

    @pytest.mark.asyncio
    async def test_tool_call_empty_buffer(self):
        """ToolCallStart with empty buffer → only ToolCallStart, no SentenceReady."""
        buf = SentenceBuffer()
        stream = _events_from(
            ToolCallStart(id="tc1", name="check_availability"),
            ToolCallEnd(id="tc1"),
            _done(),
        )
        result = await _collect(buf.process(stream))
        assert result == [
            ToolCallStart(id="tc1", name="check_availability"),
            ToolCallEnd(id="tc1"),
            _done(),
        ]

    @pytest.mark.asyncio
    async def test_tool_call_delta_and_end_passthrough(self):
        """ToolCallDelta and ToolCallEnd pass through unchanged."""
        buf = SentenceBuffer()
        tc_delta = ToolCallDelta(id="tc1", arguments_chunk='{"key": "val"}')
        tc_end = ToolCallEnd(id="tc1")
        stream = _events_from(tc_delta, tc_end, _done())
        result = await _collect(buf.process(stream))
        assert result == [tc_delta, tc_end, _done()]


# ── StreamDone handling ─────────────────────────────────────────────────


class TestStreamDoneHandling:
    @pytest.mark.asyncio
    async def test_flush_remaining_text(self):
        """StreamDone flushes remaining text without sentence punctuation."""
        buf = SentenceBuffer()
        stream = _events_from(
            TextDelta(text="Незакінчена фраза без крапки"),
            _done(),
        )
        result = await _collect(buf.process(stream))
        assert result == [
            SentenceReady(text="Незакінчена фраза без крапки"),
            _done(),
        ]

    @pytest.mark.asyncio
    async def test_empty_buffer_no_sentence(self):
        """Empty buffer → only StreamDone, no SentenceReady."""
        buf = SentenceBuffer()
        stream = _events_from(_done())
        result = await _collect(buf.process(stream))
        assert result == [_done()]


# ── Edge cases ──────────────────────────────────────────────────────────


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_text_delta(self):
        """Empty TextDelta(text='') → no output."""
        buf = SentenceBuffer()
        stream = _events_from(
            TextDelta(text=""),
            _done(),
        )
        result = await _collect(buf.process(stream))
        assert result == [_done()]

    @pytest.mark.asyncio
    async def test_whitespace_only_buffer(self):
        """Whitespace-only buffer at StreamDone → no SentenceReady."""
        buf = SentenceBuffer()
        stream = _events_from(
            TextDelta(text="   "),
            _done(),
        )
        result = await _collect(buf.process(stream))
        assert result == [_done()]

    @pytest.mark.asyncio
    async def test_whitespace_only_before_tool_call(self):
        """Whitespace-only buffer before ToolCallStart → no SentenceReady."""
        buf = SentenceBuffer()
        stream = _events_from(
            TextDelta(text="  "),
            ToolCallStart(id="tc1", name="search_tires"),
            ToolCallEnd(id="tc1"),
            _done(),
        )
        result = await _collect(buf.process(stream))
        assert result == [
            ToolCallStart(id="tc1", name="search_tires"),
            ToolCallEnd(id="tc1"),
            _done(),
        ]


# ── buffer_sentences convenience function ───────────────────────────────


class TestBufferSentencesFunction:
    @pytest.mark.asyncio
    async def test_matches_class_behavior(self):
        """buffer_sentences() convenience function matches class behavior."""
        stream = _events_from(
            TextDelta(text="Перше. Друге."),
            _done(),
        )
        result = await _collect(buffer_sentences(stream))
        assert result == [
            SentenceReady(text="Перше."),
            SentenceReady(text="Друге."),
            _done(),
        ]

    @pytest.mark.asyncio
    async def test_custom_min_clause_chars(self):
        """buffer_sentences() passes min_clause_chars."""
        stream = _events_from(
            TextDelta(text="Коротко, але достатньо"),
            _done(),
        )
        result = await _collect(buffer_sentences(stream, min_clause_chars=10))
        assert isinstance(result[0], SentenceReady)
        assert result[0].text == "Коротко,"


# ── Integration-style: realistic word-by-word stream ────────────────────


class TestRealisticStream:
    @pytest.mark.asyncio
    async def test_word_by_word_with_tool_call(self):
        """Realistic stream: word-by-word → sentences + tool call → more text → done."""
        buf = SentenceBuffer()
        stream = _events_from(
            TextDelta(text="Зараз "),
            TextDelta(text="перевірю "),
            TextDelta(text="наявність. "),
            TextDelta(text="Зачекайте"),
            ToolCallStart(id="tc1", name="check_availability"),
            ToolCallDelta(id="tc1", arguments_chunk='{"sku": "T123"}'),
            ToolCallEnd(id="tc1"),
            TextDelta(text="Є в наявності! "),
            TextDelta(text="Бажаєте замовити?"),
            _done(),
        )
        result = await _collect(buf.process(stream))
        assert result == [
            SentenceReady(text="Зараз перевірю наявність."),
            SentenceReady(text="Зачекайте"),
            ToolCallStart(id="tc1", name="check_availability"),
            ToolCallDelta(id="tc1", arguments_chunk='{"sku": "T123"}'),
            ToolCallEnd(id="tc1"),
            SentenceReady(text="Є в наявності!"),
            SentenceReady(text="Бажаєте замовити?"),
            _done(),
        ]
