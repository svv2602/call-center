"""Self-hosted Whisper STT engine using Faster-Whisper.

Implements STTEngine protocol for batch (non-streaming) speech recognition.
Uses faster-whisper (CTranslate2) for efficient inference on GPU/CPU.

Cost comparison:
  - Google Cloud STT: ~$900/month (500 calls/day)
  - Faster-Whisper (GPU): ~$150/month
  - Savings: ~$750/month after 300+ calls/day
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.stt.base import STTConfig, Transcript

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)


@dataclass
class WhisperConfig:
    """Configuration for Faster-Whisper engine."""

    model_size: str = "large-v3"
    device: str = "cuda"  # "cuda" or "cpu"
    compute_type: str = "float16"  # "float16" for GPU, "int8" for CPU
    language: str = "uk"
    beam_size: int = 5
    vad_filter: bool = True
    min_silence_duration_ms: int = 500


class WhisperSTTEngine:
    """Batch STT engine using Faster-Whisper.

    Unlike streaming Google STT, Whisper processes buffered audio
    in batch mode. Audio is accumulated until silence is detected,
    then transcribed.

    Conforms to STTEngine Protocol (duck typing).
    """

    def __init__(self, config: WhisperConfig | None = None) -> None:
        self._config = config or WhisperConfig()
        self._model = None
        self._audio_buffer: bytearray = bytearray()
        self._transcripts: asyncio.Queue[Transcript] = asyncio.Queue()
        self._is_streaming = False
        self._buffer_threshold = 16000 * 2 * 2  # 2 seconds of 16kHz 16-bit audio

    async def _ensure_model(self) -> None:
        """Lazily load the Whisper model."""
        if self._model is not None:
            return

        try:
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]

            logger.info(
                "Loading Whisper model: %s on %s (%s)",
                self._config.model_size,
                self._config.device,
                self._config.compute_type,
            )
            self._model = await asyncio.to_thread(  # type: ignore[func-returns-value]
                WhisperModel,
                self._config.model_size,
                device=self._config.device,
                compute_type=self._config.compute_type,
            )
            logger.info("Whisper model loaded successfully")
        except ImportError:
            logger.error("faster-whisper not installed. Install with: pip install faster-whisper")
            raise

    async def start_stream(self, config: STTConfig) -> None:
        """Start a new recognition session."""
        await self._ensure_model()
        self._audio_buffer = bytearray()
        self._transcripts = asyncio.Queue()
        self._is_streaming = True
        logger.debug("Whisper STT stream started")

    async def feed_audio(self, chunk: bytes) -> None:
        """Buffer audio chunk. Transcribe when buffer reaches threshold."""
        if not self._is_streaming:
            return

        self._audio_buffer.extend(chunk)

        if len(self._audio_buffer) >= self._buffer_threshold:
            await self._transcribe_buffer()

    async def _transcribe_buffer(self) -> None:
        """Transcribe buffered audio using Whisper."""
        if not self._audio_buffer or self._model is None:
            return

        import io
        import wave

        # Convert raw PCM to WAV in memory
        audio_data = bytes(self._audio_buffer)
        self._audio_buffer = bytearray()

        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)  # 16-bit
            wav.setframerate(16000)
            wav.writeframes(audio_data)

        wav_buffer.seek(0)

        # Run transcription in thread pool
        segments, info = await asyncio.to_thread(
            self._model.transcribe,
            wav_buffer,
            language=self._config.language,
            beam_size=self._config.beam_size,
            vad_filter=self._config.vad_filter,
        )

        # Collect segments
        text_parts: list[str] = []
        total_confidence = 0.0
        segment_count = 0

        for segment in segments:
            text_parts.append(segment.text.strip())
            total_confidence += getattr(segment, "avg_logprob", -0.5)
            segment_count += 1

        if text_parts:
            full_text = " ".join(text_parts)
            # Convert log probability to confidence (0-1)
            avg_logprob = total_confidence / max(segment_count, 1)
            confidence = min(1.0, max(0.0, 1.0 + avg_logprob))

            detected_lang = getattr(info, "language", self._config.language)

            transcript = Transcript(
                text=full_text,
                is_final=True,
                confidence=confidence,
                language=detected_lang,
            )
            await self._transcripts.put(transcript)

            logger.debug(
                "Whisper transcript: '%s' (confidence=%.2f, lang=%s)",
                full_text[:100],
                confidence,
                detected_lang,
            )

    async def get_transcripts(self) -> AsyncIterator[Transcript]:
        """Yield transcripts as they become available."""
        while self._is_streaming or not self._transcripts.empty():
            try:
                transcript = await asyncio.wait_for(self._transcripts.get(), timeout=0.1)
                yield transcript
            except TimeoutError:
                if not self._is_streaming:
                    break

    async def stop_stream(self) -> None:
        """Stop the recognition stream, transcribe remaining buffer."""
        self._is_streaming = False

        # Transcribe any remaining audio
        if self._audio_buffer:
            await self._transcribe_buffer()

        logger.debug("Whisper STT stream stopped")
