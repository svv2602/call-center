"""Google Cloud Text-to-Speech implementation with caching."""

from __future__ import annotations

import hashlib
import logging
import re
from typing import TYPE_CHECKING

from google.cloud import texttospeech_v1 as texttospeech

from src.agent.prompts import (
    ERROR_TEXT,
    FAREWELL_ORDER_TEXT,
    FAREWELL_TEXT,
    GREETING_TEXT,
    SILENCE_PROMPT_TEXT,
    TRANSFER_TEXT,
    WAIT_AVAILABILITY_TEXT,
    WAIT_FITTING_TEXT,
    WAIT_KNOWLEDGE_TEXT,
    WAIT_ORDER_TEXT,
    WAIT_SEARCH_TEXT,
    WAIT_STATUS_TEXT,
    WAIT_TEXT,
)
from src.monitoring.metrics import tts_cache_hits_total, tts_cache_misses_total
from src.tts.base import TTSConfig

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

# Sentence-splitting pattern (split on ., !, ? followed by space or end)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

# Phrases to pre-cache at startup (imported from prompts.py to ensure cache key match)
CACHED_PHRASES = [
    GREETING_TEXT,
    WAIT_TEXT,
    TRANSFER_TEXT,
    FAREWELL_TEXT,
    SILENCE_PROMPT_TEXT,
    ERROR_TEXT,
    WAIT_SEARCH_TEXT,
    WAIT_AVAILABILITY_TEXT,
    WAIT_ORDER_TEXT,
    WAIT_FITTING_TEXT,
    WAIT_STATUS_TEXT,
    WAIT_KNOWLEDGE_TEXT,
    FAREWELL_ORDER_TEXT,
]


class GoogleTTSEngine:
    """Google Cloud TTS with in-memory phrase caching.

    Audio format: LINEAR16, 16kHz, mono — matches AudioSocket expectations.
    """

    def __init__(self, config: TTSConfig | None = None) -> None:
        self._config = config or TTSConfig()
        self._client: texttospeech.TextToSpeechAsyncClient | None = None
        self._voice: texttospeech.VoiceSelectionParams | None = None
        self._audio_config: texttospeech.AudioConfig | None = None
        self._cache: dict[str, bytes] = {}
        self._cache_hits = 0
        self._cache_misses = 0

    async def initialize(self) -> None:
        """Initialize the TTS client and pre-cache common phrases."""
        self._client = texttospeech.TextToSpeechAsyncClient()
        self._voice = texttospeech.VoiceSelectionParams(
            language_code=self._config.language_code,
            name=self._config.voice_name,
        )
        self._audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16,
            sample_rate_hertz=self._config.sample_rate_hertz,
            speaking_rate=self._config.speaking_rate,
        )

        # Pre-cache common phrases
        for phrase in CACHED_PHRASES:
            try:
                audio = await self._synthesize_uncached(phrase)
                self._cache[self._cache_key(phrase)] = audio
            except Exception:
                logger.warning("Failed to pre-cache phrase: '%s'", phrase[:40])

        logger.info("TTS initialized, pre-cached %d phrases", len(self._cache))

    async def synthesize(self, text: str) -> bytes:
        """Synthesize text into raw PCM audio bytes."""
        key = self._cache_key(text)

        if key in self._cache:
            self._cache_hits += 1
            tts_cache_hits_total.inc()
            return self._cache[key]

        self._cache_misses += 1
        tts_cache_misses_total.inc()
        audio = await self._synthesize_uncached(text)

        # Cache short phrases (likely to repeat)
        if len(text) < 100:
            self._cache[key] = audio

        return audio

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        """Synthesize text sentence by sentence for streaming playback."""
        sentences = _SENTENCE_RE.split(text)

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            audio = await self.synthesize(sentence)
            yield audio

    @property
    def cache_hit_rate(self) -> float:
        """Cache hit rate as a fraction."""
        total = self._cache_hits + self._cache_misses
        return self._cache_hits / total if total > 0 else 0.0

    @staticmethod
    def _cache_key(text: str) -> str:
        """Generate a cache key for a text string."""
        return hashlib.sha256(text.encode()).hexdigest()

    async def _synthesize_uncached(self, text: str) -> bytes:
        """Call Google TTS API to synthesize text."""
        if self._client is None:
            raise RuntimeError("TTS not initialized — call initialize() first")

        synthesis_input = texttospeech.SynthesisInput(text=text)

        response = await self._client.synthesize_speech(
            input=synthesis_input,
            voice=self._voice,
            audio_config=self._audio_config,
        )

        audio = response.audio_content

        # Strip WAV header if present (Google TTS LINEAR16 includes 44-byte RIFF header)
        if audio[:4] == b"RIFF":
            audio = audio[44:]

        logger.debug("TTS synthesized %d bytes for '%s'", len(audio), text[:50])
        return audio
