"""Mock TTS engine for unit tests."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator

from src.tts.base import TTSConfig

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

# 20ms of silence at 16kHz 16-bit mono = 640 bytes of zeros
_SILENCE_FRAME = b"\x00" * 640


class MockTTSEngine:
    """Mock TTS engine that returns silence of appropriate duration.

    Usage:
        engine = MockTTSEngine()
        audio = await engine.synthesize("Привіт")
        # audio is 640 bytes of silence per word
    """

    def __init__(
        self,
        frames_per_word: int = 8,
        delay: float = 0.0,
        error: Exception | None = None,
    ) -> None:
        self._frames_per_word = frames_per_word
        self._delay = delay
        self._error = error
        self._synthesize_count = 0
        self._config: TTSConfig | None = None

    async def initialize(self) -> None:
        """No-op initialization."""

    async def synthesize(self, text: str) -> bytes:
        """Return silence proportional to word count."""
        if self._error is not None:
            raise self._error

        if self._delay > 0:
            await asyncio.sleep(self._delay)

        self._synthesize_count += 1
        word_count = max(1, len(text.split()))
        return _SILENCE_FRAME * (word_count * self._frames_per_word)

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        """Yield silence sentence by sentence."""
        sentences = _SENTENCE_RE.split(text)
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            audio = await self.synthesize(sentence)
            yield audio

    @property
    def synthesize_count(self) -> int:
        """Number of synthesize calls made."""
        return self._synthesize_count
