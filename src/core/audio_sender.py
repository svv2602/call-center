"""Streaming audio sender — consumes TTSEvent stream and sends audio to AudioSocket."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.llm.models import StreamDone, ToolCallDelta, ToolCallEnd, ToolCallStart, Usage
from src.monitoring.metrics import time_to_first_audio_ms, tts_delivery_ms
from src.tts.streaming_tts import AudioReady

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from src.core.audio_socket import AudioSocketConnection
    from src.core.echo_canceller import EchoCanceller
    from src.tts.streaming_tts import TTSEvent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CollectedToolCall:
    """Tool call collected from stream events."""

    id: str
    name: str
    arguments_json: str


@dataclass(frozen=True)
class SendResult:
    """Result of streaming audio to AudioSocket."""

    spoken_text: str
    tool_calls: list[CollectedToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"
    usage: Usage = field(default_factory=lambda: Usage(0, 0))
    provider_key: str = ""
    interrupted: bool = False
    disconnected: bool = False


@dataclass
class _PendingToolCall:
    """Mutable accumulator for tool call arguments being streamed."""

    id: str
    name: str
    chunks: list[str] = field(default_factory=list)


class StreamingAudioSender:
    """Consumes TTSEvent stream, sends audio to AudioSocket.

    - AudioReady → conn.send_audio(), accumulates spoken text
    - ToolCallStart/Delta/End → collects into CollectedToolCall list
    - StreamDone → captures stop_reason + usage
    - Checks barge_in_event and conn.is_closed between sends

    Optional filler phrase: if ``filler_audio`` is provided and the LLM
    hasn't produced any audio within ``filler_delay_sec``, the filler
    audio is played to fill the silence gap.  A lock prevents interleaving
    filler and LLM audio frames on the socket.
    """

    def __init__(
        self,
        conn: AudioSocketConnection,
        barge_in_event: asyncio.Event | None = None,
        turn_start_time: float | None = None,
        echo_canceller: EchoCanceller | None = None,
        filler_audio: bytes | None = None,
        filler_delay_sec: float = 3.0,
    ) -> None:
        self._conn = conn
        self._barge_in = barge_in_event
        self._turn_start_time = turn_start_time
        self._echo_canceller = echo_canceller
        self._first_audio_sent = False
        self._filler_audio = filler_audio
        self._filler_delay_sec = filler_delay_sec
        # Lock prevents concurrent send_audio calls (filler vs LLM audio)
        self._send_lock = asyncio.Lock()

    async def _send_audio_locked(self, audio: bytes) -> bool:
        """Send audio through the connection, serialized via lock.

        Returns True if interrupted by barge-in.
        """
        async with self._send_lock:
            return await self._conn.send_audio(audio, cancel_event=self._barge_in)

    async def send(self, stream: AsyncIterator[TTSEvent]) -> SendResult:
        """Consume entire TTSEvent stream, return result."""
        spoken_parts: list[str] = []
        pending_tool_calls: dict[str, _PendingToolCall] = {}
        completed_tool_calls: list[CollectedToolCall] = []
        interrupted = False
        disconnected = False
        stop_reason = "end_turn"
        usage = Usage(0, 0)
        provider_key = ""

        # Filler phrase timer — plays pre-synthesized audio if LLM is slow
        filler_task: asyncio.Task[None] | None = None
        if self._filler_audio is not None:
            filler_audio = self._filler_audio

            async def _filler_timer() -> None:
                await asyncio.sleep(self._filler_delay_sec)
                if (
                    not self._first_audio_sent
                    and not self._conn.is_closed
                    and not (self._barge_in and self._barge_in.is_set())
                ):
                    logger.info("Sending thinking filler audio (%d bytes)", len(filler_audio))
                    try:
                        if self._echo_canceller is not None:
                            self._echo_canceller.record_far_end(filler_audio)
                        await self._send_audio_locked(filler_audio)
                    except Exception:
                        logger.debug("Filler phrase send failed", exc_info=True)

            filler_task = asyncio.create_task(_filler_timer())

        async for event in stream:
            if isinstance(event, AudioReady):
                if self._barge_in and self._barge_in.is_set():
                    interrupted = True
                    continue
                if self._conn.is_closed:
                    disconnected = True
                    continue
                if self._echo_canceller is not None:
                    self._echo_canceller.record_far_end(event.audio)
                t0 = time.monotonic()
                was_interrupted = await self._send_audio_locked(event.audio)
                tts_delivery_ms.observe((time.monotonic() - t0) * 1000)
                if was_interrupted:
                    interrupted = True
                    continue
                spoken_parts.append(event.text)

                # Record time-to-first-audio once per turn
                if not self._first_audio_sent:
                    # Cancel filler — real audio arrived
                    if filler_task is not None and not filler_task.done():
                        filler_task.cancel()
                    if self._turn_start_time is not None:
                        ttfa = (time.monotonic() - self._turn_start_time) * 1000
                        time_to_first_audio_ms.observe(ttfa)
                    self._first_audio_sent = True

            elif isinstance(event, ToolCallStart):
                pending_tool_calls[event.id] = _PendingToolCall(id=event.id, name=event.name)
            elif isinstance(event, ToolCallDelta):
                if event.id in pending_tool_calls:
                    pending_tool_calls[event.id].chunks.append(event.arguments_chunk)
            elif isinstance(event, ToolCallEnd):
                if event.id in pending_tool_calls:
                    p = pending_tool_calls.pop(event.id)
                    completed_tool_calls.append(
                        CollectedToolCall(
                            id=p.id,
                            name=p.name,
                            arguments_json="".join(p.chunks),
                        )
                    )
            elif isinstance(event, StreamDone):
                stop_reason = event.stop_reason
                usage = event.usage
                provider_key = event.provider_key

        # Cancel filler task if still running
        if filler_task is not None and not filler_task.done():
            filler_task.cancel()

        # Finalize any pending tool calls that never got a ToolCallEnd event.
        # OpenAI-compatible providers (Gemini) don't emit ToolCallEnd — they
        # just send ToolCallStart + ToolCallDelta chunks and then StreamDone.
        for p in pending_tool_calls.values():
            completed_tool_calls.append(
                CollectedToolCall(
                    id=p.id,
                    name=p.name,
                    arguments_json="".join(p.chunks),
                )
            )

        return SendResult(
            spoken_text=" ".join(spoken_parts),
            tool_calls=completed_tool_calls,
            stop_reason=stop_reason,
            usage=usage,
            provider_key=provider_key,
            interrupted=interrupted,
            disconnected=disconnected,
        )


async def send_audio_stream(
    stream: AsyncIterator[TTSEvent],
    conn: AudioSocketConnection,
    barge_in_event: asyncio.Event | None = None,
    turn_start_time: float | None = None,
    echo_canceller: EchoCanceller | None = None,
    filler_audio: bytes | None = None,
    filler_delay_sec: float = 3.0,
) -> SendResult:
    """Convenience wrapper — create sender and consume stream."""
    sender = StreamingAudioSender(
        conn,
        barge_in_event,
        turn_start_time=turn_start_time,
        echo_canceller=echo_canceller,
        filler_audio=filler_audio,
        filler_delay_sec=filler_delay_sec,
    )
    return await sender.send(stream)
