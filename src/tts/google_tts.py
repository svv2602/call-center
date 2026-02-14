"""Google Cloud Text-to-Speech implementation with caching."""

from __future__ import annotations

import hashlib
import logging
import re
from typing import TYPE_CHECKING

from google.cloud import texttospeech_v1 as texttospeech

from src.tts.base import TTSConfig

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

# Sentence-splitting pattern (split on ., !, ? followed by space or end)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

# Phrases to pre-cache at startup
CACHED_PHRASES = [
    "Добрий день! Інтернет-магазин шин. Чим можу допомогти?",
    "Зачекайте, будь ласка, я шукаю інформацію.",
    "Зараз з'єдную вас з оператором. Залишайтесь на лінії.",
    "Дякую за дзвінок! До побачення!",
    "Ви ще на лінії?",
    "Перепрошую, виникла технічна помилка. З'єдную з оператором.",
    "Цей дзвінок обробляється автоматичною системою.",
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
            return self._cache[key]

        self._cache_misses += 1
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

        logger.debug("TTS synthesized %d bytes for '%s'", len(response.audio_content), text[:50])
        return response.audio_content
