"""Streaming TTS synthesizer — converts SentenceReady events to PCM audio."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.core.sentence_buffer import SentenceReady
from src.llm.models import StreamDone, ToolCallDelta, ToolCallEnd, ToolCallStart

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from src.core.sentence_buffer import BufferEvent
    from src.tts.base import TTSEngine


@dataclass(frozen=True)
class AudioReady:
    """Raw PCM audio for one sentence, ready for AudioSocket delivery."""

    audio: bytes
    text: str  # original text (for logging/metrics)


TTSEvent = AudioReady | ToolCallStart | ToolCallDelta | ToolCallEnd | StreamDone


class StreamingTTSSynthesizer:
    """Converts SentenceReady → AudioReady via TTSEngine.synthesize().

    All other BufferEvents (tool calls, StreamDone) pass through unchanged.
    """

    def __init__(self, tts: TTSEngine) -> None:
        self._tts = tts

    async def process(
        self,
        stream: AsyncIterator[BufferEvent],
    ) -> AsyncIterator[TTSEvent]:
        """Consume buffer events, synthesizing sentences into audio."""
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
