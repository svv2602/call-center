"""Mock STT engine for unit tests."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from src.stt.base import STTConfig, Transcript


class MockSTTEngine:
    """Mock STT engine that returns predefined transcripts.

    Usage:
        engine = MockSTTEngine(transcripts=[
            Transcript(text="Привіт", is_final=True, confidence=0.95, language="uk-UA"),
            Transcript(text="Перевірте замовлення", is_final=True, confidence=0.9, language="uk-UA"),
        ])
        await engine.start_stream(config)
        await engine.feed_audio(b"fake_audio")
        async for t in engine.get_transcripts():
            print(t.text)
    """

    def __init__(
        self,
        transcripts: list[Transcript] | None = None,
        delay: float = 0.0,
        error_on_feed: Exception | None = None,
    ) -> None:
        self._transcripts = transcripts or []
        self._delay = delay
        self._error_on_feed = error_on_feed
        self._started = False
        self._feed_count = 0
        self._transcript_index = 0

    async def start_stream(self, config: STTConfig) -> None:
        """Start a mock recognition stream."""
        self._started = True
        self._feed_count = 0
        self._transcript_index = 0

    async def feed_audio(self, chunk: bytes) -> None:
        """Accept an audio chunk (does nothing with it)."""
        if self._error_on_feed is not None:
            raise self._error_on_feed
        self._feed_count += 1

    async def get_transcripts(self) -> AsyncIterator[Transcript]:
        """Yield predefined transcripts."""
        for transcript in self._transcripts:
            if self._delay > 0:
                await asyncio.sleep(self._delay)
            yield transcript

    async def stop_stream(self) -> None:
        """Stop the mock stream."""
        self._started = False

    @property
    def feed_count(self) -> int:
        """Number of audio chunks fed."""
        return self._feed_count
