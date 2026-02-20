"""Streaming TTS synthesizer — converts SentenceReady events to PCM audio."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.core.sentence_buffer import SentenceReady
from src.llm.models import StreamDone, ToolCallDelta, ToolCallEnd, ToolCallStart

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from src.core.sentence_buffer import BufferEvent
    from src.tts.base import TTSEngine

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AudioReady:
    """Raw PCM audio for one sentence, ready for AudioSocket delivery."""

    audio: bytes
    text: str  # original text (for logging/metrics)


TTSEvent = AudioReady | ToolCallStart | ToolCallDelta | ToolCallEnd | StreamDone


class StreamingTTSSynthesizer:
    """Converts SentenceReady → AudioReady via TTSEngine.synthesize().

    All other BufferEvents (tool calls, StreamDone) pass through unchanged.

    When prefetch=True (default), uses 1-slot lookahead: starts synthesizing
    the next sentence while the current one is being yielded/played.
    """

    def __init__(self, tts: TTSEngine, *, prefetch: bool = True) -> None:
        self._tts = tts
        self._prefetch = prefetch

    async def process(
        self,
        stream: AsyncIterator[BufferEvent],
    ) -> AsyncIterator[TTSEvent]:
        """Consume buffer events, synthesizing sentences into audio."""
        if not self._prefetch:
            async for event in self._process_sequential(stream):
                yield event
            return

        # Prefetch path: 1-slot lookahead
        pending_task: asyncio.Task[bytes] | None = None
        pending_text: str | None = None

        async for event in stream:
            if isinstance(event, SentenceReady):
                # Launch new task FIRST so it runs while we await the old one
                new_task = asyncio.create_task(self._tts.synthesize(event.text))
                new_text = event.text

                # Now await previous task (new synthesis runs in parallel)
                if pending_task is not None:
                    try:
                        audio = await pending_task
                        yield AudioReady(audio=audio, text=pending_text)  # type: ignore[arg-type]
                    except Exception:
                        logger.warning("Prefetch TTS failed for '%s', skipping", pending_text)

                pending_task = new_task
                pending_text = new_text

            elif isinstance(event, (ToolCallStart, StreamDone)):
                # Flush pending synthesis before control events
                if pending_task is not None:
                    try:
                        audio = await pending_task
                        yield AudioReady(audio=audio, text=pending_text)  # type: ignore[arg-type]
                    except Exception:
                        logger.warning("Prefetch TTS failed for '%s', skipping", pending_text)
                    pending_task = None
                    pending_text = None
                yield event
            else:
                yield event

        # Flush any remaining pending task
        if pending_task is not None:
            try:
                audio = await pending_task
                yield AudioReady(audio=audio, text=pending_text)  # type: ignore[arg-type]
            except Exception:
                logger.warning("Prefetch TTS failed for '%s', skipping", pending_text)

    async def _process_sequential(
        self,
        stream: AsyncIterator[BufferEvent],
    ) -> AsyncIterator[TTSEvent]:
        """Sequential (no-prefetch) processing path."""
        async for event in stream:
            if isinstance(event, SentenceReady):
                audio = await self._tts.synthesize(event.text)
                yield AudioReady(audio=audio, text=event.text)
            else:
                yield event


async def synthesize_stream(
    stream: AsyncIterator[BufferEvent],
    tts: TTSEngine,
) -> AsyncIterator[TTSEvent]:
    """Convenience wrapper — create synthesizer and process stream."""
    synth = StreamingTTSSynthesizer(tts)
    async for event in synth.process(stream):
        yield event
