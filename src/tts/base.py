"""TTS engine abstract interface and data types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@dataclass(frozen=True, slots=True)
class TTSConfig:
    """Configuration for the TTS engine."""

    language_code: str = "uk-UA"
    voice_name: str = "uk-UA-Wavenet-A"
    speaking_rate: float = 0.93
    pitch: float = -1.0
    sample_rate_hertz: int = 8000


@runtime_checkable
class TTSEngine(Protocol):
    """Protocol for text-to-speech engines.

    Implementations convert text to raw PCM audio bytes suitable
    for sending through AudioSocket (16kHz, 16-bit signed linear PCM).
    """

    async def synthesize(self, text: str) -> bytes:
        """Synthesize text into raw PCM audio bytes."""
        ...

    def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        """Synthesize text, yielding audio sentence by sentence.

        Splits the text into sentences and synthesizes each one
        separately, allowing playback to begin before the full
        text is processed.
        """
        ...
