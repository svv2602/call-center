"""Fallback STT engine: Whisper with automatic fallback to Google STT.

If Whisper fails to initialize or encounters errors during transcription,
automatically switches to Google Cloud STT for the remainder of the stream.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.monitoring.metrics import (
    stt_provider_requests_total,
    stt_whisper_errors_total,
    stt_whisper_fallback_total,
)
from src.stt.base import STTConfig, Transcript
from src.stt.google_stt import GoogleSTTEngine
from src.stt.whisper_stt import WhisperConfig, WhisperSTTEngine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)


class FallbackSTTEngine:
    """STT engine with Whisper primary and Google STT fallback.

    Conforms to STTEngine Protocol.
    """

    def __init__(self, whisper_config: WhisperConfig | None = None) -> None:
        self._whisper = WhisperSTTEngine(whisper_config)
        self._google = GoogleSTTEngine()
        self._active_engine: WhisperSTTEngine | GoogleSTTEngine = self._whisper
        self._fell_back = False
        self._config: STTConfig | None = None

    @property
    def active_provider(self) -> str:
        """Return name of the currently active provider."""
        return "google" if self._fell_back else "whisper"

    async def start_stream(self, config: STTConfig) -> None:
        """Start stream with Whisper, fall back to Google on failure."""
        self._config = config
        self._fell_back = False

        try:
            await self._whisper.start_stream(config)
            self._active_engine = self._whisper
            logger.info("STT stream started with Whisper")
        except Exception:
            logger.warning("Whisper STT failed to start, falling back to Google STT")
            stt_whisper_errors_total.labels(error_type="connection_error").inc()
            stt_whisper_fallback_total.inc()
            await self._google.start_stream(config)
            self._active_engine = self._google
            self._fell_back = True
            stt_provider_requests_total.labels(provider="google").inc()

    async def feed_audio(self, chunk: bytes) -> None:
        """Feed audio to the active engine."""
        await self._active_engine.feed_audio(chunk)

    async def get_transcripts(self) -> AsyncIterator[Transcript]:
        """Yield transcripts from the active engine."""
        async for transcript in self._active_engine.get_transcripts():
            yield transcript

    async def stop_stream(self) -> None:
        """Stop the active engine's stream."""
        await self._active_engine.stop_stream()
        logger.info("STT stream stopped (provider=%s)", self.active_provider)
