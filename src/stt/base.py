"""STT engine abstract interface and data types."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class STTEvent(enum.StrEnum):
    """Events emitted by the STT engine."""

    TRANSCRIPT = "transcript"
    SILENCE_TIMEOUT = "silence_timeout"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Transcript:
    """A speech recognition result."""

    text: str
    is_final: bool
    confidence: float = 0.0
    language: str = "uk-UA"


@dataclass(frozen=True, slots=True)
class STTConfig:
    """Configuration for the STT engine."""

    language_code: str = "uk-UA"
    alternative_languages: list[str] = field(default_factory=lambda: ["ru-RU"])
    sample_rate_hertz: int = 8000
    interim_results: bool = True
    model: str = "latest_short"
    enable_punctuation: bool = True
    phrase_hints: tuple[str, ...] = ()


@runtime_checkable
class STTEngine(Protocol):
    """Protocol for speech-to-text engines.

    Implementations must support streaming: audio chunks are fed
    continuously and transcripts are yielded as they become available.
    """

    async def start_stream(self, config: STTConfig) -> None:
        """Start a new recognition stream."""
        ...

    async def feed_audio(self, chunk: bytes) -> None:
        """Feed an audio chunk to the recognition stream."""
        ...

    def get_transcripts(self) -> AsyncIterator[Transcript]:
        """Yield transcripts as they become available.

        Yields both interim and final results depending on config.
        """
        ...

    async def stop_stream(self) -> None:
        """Stop the recognition stream and release resources."""
        ...
