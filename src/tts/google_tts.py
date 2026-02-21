"""Google Cloud Text-to-Speech implementation with caching and SSML."""

from __future__ import annotations

import hashlib
import logging
import re
import xml.sax.saxutils
from typing import TYPE_CHECKING

from google.cloud import texttospeech_v1 as texttospeech

from src.agent.prompts import (
    ERROR_TEXT,
    FAREWELL_ORDER_TEXT,
    FAREWELL_TEXT,
    GREETING_TEXT,
    SILENCE_PROMPT_TEXT,
    TRANSFER_TEXT,
    WAIT_AVAILABILITY_POOL,
    WAIT_DEFAULT_POOL,
    WAIT_FITTING_POOL,
    WAIT_KNOWLEDGE_POOL,
    WAIT_ORDER_POOL,
    WAIT_SEARCH_POOL,
    WAIT_STATUS_POOL,
    WAIT_TEXT,
)
from src.monitoring.metrics import tts_cache_hits_total, tts_cache_misses_total
from src.tts.base import TTSConfig

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

# Sentence-splitting pattern (split on ., !, ? followed by space or end)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

# Combining acute accent (U+0301) used for stress marks in prompts.
# Standard/Wavenet voices use these marks for correct stress placement.
# Chirp3-HD rejects them outright — strip only for Chirp voices.
_COMBINING_ACUTE = "\u0301"

# SSML break insertion patterns (applied after XML escaping)
_BREAK_RULES: list[tuple[re.Pattern[str], str]] = [
    # After comma + space: subtle pause extending natural comma break
    (re.compile(r",(\s)"), r',<break time="100ms"/>\1'),
    # After em-dash + space: thinking/contrast pause
    (re.compile(r"—(\s)"), r'—<break time="150ms"/>\1'),
    # After colon + space: anticipation before explanation/list
    (re.compile(r":(\s)"), r':<break time="200ms"/>\1'),
]

# Phrases to pre-cache at startup — includes all wait pool variants
CACHED_PHRASES = [
    GREETING_TEXT,
    WAIT_TEXT,
    TRANSFER_TEXT,
    FAREWELL_TEXT,
    SILENCE_PROMPT_TEXT,
    ERROR_TEXT,
    FAREWELL_ORDER_TEXT,
    *WAIT_SEARCH_POOL,
    *WAIT_AVAILABILITY_POOL,
    *WAIT_ORDER_POOL,
    *WAIT_FITTING_POOL,
    *WAIT_STATUS_POOL,
    *WAIT_KNOWLEDGE_POOL,
    *WAIT_DEFAULT_POOL,
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
        self._ssml_supported: bool = True
        # Chirp voices reject combining accent marks (U+0301);
        # Standard/Wavenet voices use them for correct stress placement
        self._strip_stress_marks: bool = "chirp" in self._config.voice_name.lower()

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
            pitch=self._config.pitch,
        )

        # Pre-cache common phrases (with auto-detection of voice capabilities)
        for phrase in CACHED_PHRASES:
            try:
                audio = await self._synthesize_uncached(phrase)
                self._cache[self._cache_key(phrase)] = audio
            except Exception as exc:
                exc_msg = str(exc)
                if "pitch" in exc_msg.lower():
                    # Chirp3-HD and some voices don't support pitch parameter
                    logger.info(
                        "Voice %s does not support pitch, retrying without it",
                        self._config.voice_name,
                    )
                    self._audio_config = texttospeech.AudioConfig(
                        audio_encoding=texttospeech.AudioEncoding.LINEAR16,
                        sample_rate_hertz=self._config.sample_rate_hertz,
                        speaking_rate=self._config.speaking_rate,
                    )
                    try:
                        audio = await self._synthesize_uncached(phrase)
                        self._cache[self._cache_key(phrase)] = audio
                    except Exception:
                        logger.warning("Failed to pre-cache phrase: '%s'", phrase[:40])
                    continue
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

    @staticmethod
    def _to_ssml(text: str) -> str:
        """Convert plain text to SSML with natural pauses.

        Escapes XML entities, then inserts <break> tags after commas,
        em-dashes, and colons to extend natural pauses for a more
        human-like cadence.  Global rate/pitch are set via AudioConfig,
        so the SSML only adds breaks.
        """
        ssml = xml.sax.saxutils.escape(text)
        for pattern, replacement in _BREAK_RULES:
            ssml = pattern.sub(replacement, ssml)
        return f"<speak>{ssml}</speak>"

    async def _synthesize_uncached(self, text: str) -> bytes:
        """Call Google TTS API, preferring SSML with automatic plain-text fallback.

        Some voices (e.g. Chirp3-HD) don't support SSML.  On first SSML
        failure we downgrade to plain text for all subsequent calls.
        Combining accent marks (stress hints from prompts) are stripped —
        TTS engines handle pronunciation natively.
        """
        if self._client is None:
            raise RuntimeError("TTS not initialized — call initialize() first")

        if self._strip_stress_marks:
            text = text.replace(_COMBINING_ACUTE, "")

        if self._ssml_supported:
            try:
                return await self._call_tts_api(
                    texttospeech.SynthesisInput(ssml=self._to_ssml(text)), text
                )
            except Exception:
                self._ssml_supported = False
                logger.info(
                    "Voice %s does not support SSML, falling back to plain text",
                    self._config.voice_name,
                )

        return await self._call_tts_api(texttospeech.SynthesisInput(text=text), text)

    async def _call_tts_api(self, synthesis_input: texttospeech.SynthesisInput, text: str) -> bytes:
        """Send synthesis request to Google TTS and return raw PCM audio."""
        response = await self._client.synthesize_speech(  # type: ignore[union-attr]
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
