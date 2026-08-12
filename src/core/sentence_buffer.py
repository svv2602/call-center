"""Sentence buffer for LLM streaming — accumulates TextDelta into sentences."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.llm.models import (
    StreamDone,
    StreamEvent,
    TextDelta,
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# Sentence boundary: .!? followed by whitespace (same pattern as google_tts.py)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

# Clause boundary: comma/semicolon/colon + space — for long phrases only
_CLAUSE_RE = re.compile(r"[,;:]\s")


@dataclass(frozen=True)
class SentenceReady:
    """Complete sentence/phrase ready for TTS synthesis."""

    text: str


# Buffer yields sentences + passes through non-text stream events
BufferEvent = SentenceReady | ToolCallStart | ToolCallDelta | ToolCallEnd | StreamDone


class SentenceBuffer:
    """Accumulates TextDelta fragments into sentence-sized chunks.

    Flush triggers (priority order):
    1. ToolCallStart → flush partial text immediately
    2. StreamDone → flush remaining text
    3. Sentence punctuation (.!?) followed by whitespace
    4. Clause punctuation (,;:) after min_clause_chars (long phrases)
    """

    def __init__(self, min_clause_chars: int = 15) -> None:
        self._buffer = ""
        self._min_clause_chars = min_clause_chars

    async def process(
        self,
        stream: AsyncIterator[StreamEvent],
    ) -> AsyncIterator[BufferEvent]:
        """Consume stream events, yielding sentences and pass-through events."""
        async for event in stream:
            if isinstance(event, TextDelta):
                self._buffer += event.text
                for e in self._flush_sentences():
                    yield e
            elif isinstance(event, ToolCallStart):
                # Flush partial text before tool call
                if self._buffer.strip():
                    yield SentenceReady(text=self._buffer.strip())
                    self._buffer = ""
                yield event
            elif isinstance(event, (ToolCallDelta, ToolCallEnd)):
                yield event
            elif isinstance(event, StreamDone):
                # Flush remaining text
                if self._buffer.strip():
                    yield SentenceReady(text=self._buffer.strip())
                    self._buffer = ""
                yield event

    def _flush_sentences(self) -> list[BufferEvent]:
        """Check buffer for complete sentences or long clauses."""
        events: list[BufferEvent] = []
        # Split on sentence-ending punctuation followed by space
        parts = _SENTENCE_RE.split(self._buffer)
        if len(parts) > 1:
            for sentence in parts[:-1]:
                stripped = sentence.strip()
                if stripped:
                    events.append(SentenceReady(text=stripped))
            self._buffer = parts[-1]
        # Long clause flush: comma/semicolon after min_clause_chars
        elif len(self._buffer) >= self._min_clause_chars:
            match = _CLAUSE_RE.search(self._buffer)
            if match:
                split_pos = match.end()
                text = self._buffer[:split_pos].strip()
                if text:
                    events.append(SentenceReady(text=text))
                self._buffer = self._buffer[split_pos:]
        return events


async def buffer_sentences(
    stream: AsyncIterator[StreamEvent],
    min_clause_chars: int = 25,
) -> AsyncIterator[BufferEvent]:
    """Create SentenceBuffer and process stream."""
    buf = SentenceBuffer(min_clause_chars=min_clause_chars)
    async for event in _filter_leaked_progress(buf.process(stream)):
        yield event


# Markers of the internal "## 📋 Прогрес запису … НАСТУПНИЙ КРОК" fitting
# checklist block that lives in the system prompt. If the LLM starts echoing
# it into its own reply (observed in call d6e034f2 on 2026-08-05), we would
# otherwise speak "решётка решётка Прогрес запису, галочка галочка…" to the
# caller. Suppress every sentence between the start marker and the closing
# «НАСТУПНИЙ КРОК» line inclusive; anything after (the real question) passes.
_LEAK_START_MARKER = "Прогрес запису"
_LEAK_END_MARKER = "НАСТУПНИЙ КРОК"


async def _filter_leaked_progress(
    stream: AsyncIterator[BufferEvent],
) -> AsyncIterator[BufferEvent]:
    """Drop sentences belonging to a leaked system-prompt Progress block."""
    suppressing = False
    async for event in stream:
        if isinstance(event, SentenceReady):
            text = event.text
            if not suppressing and _LEAK_START_MARKER in text:
                suppressing = True
                if _LEAK_END_MARKER in text:
                    suppressing = False
                continue
            if suppressing:
                if _LEAK_END_MARKER in text:
                    suppressing = False
                continue
        yield event
