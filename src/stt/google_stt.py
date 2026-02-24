"""Google Cloud Speech-to-Text v2 streaming implementation."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING

from google.cloud.speech_v2 import SpeechAsyncClient
from google.cloud.speech_v2.types import cloud_speech

from src.stt.base import STTConfig, Transcript

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

# Google STT streaming session limit (~5 min)
_SESSION_RESTART_SECONDS = 290  # restart slightly before 5 min limit

# Google STT v2 inline PhraseSet limit (latest_short model)
_MAX_PHRASE_HINTS = 1200


def _build_adaptation(
    phrase_hints: tuple[str, ...],
) -> cloud_speech.SpeechAdaptation | None:
    """Build SpeechAdaptation with inline PhraseSet from phrase hints."""
    if not phrase_hints:
        return None

    phrases = phrase_hints[:_MAX_PHRASE_HINTS]
    phrase_set = cloud_speech.SpeechAdaptation.AdaptationPhraseSet(
        inline_phrase_set=cloud_speech.PhraseSet(
            phrases=[cloud_speech.PhraseSet.Phrase(value=p) for p in phrases],
        ),
    )
    return cloud_speech.SpeechAdaptation(phrase_sets=[phrase_set])


class GoogleSTTEngine:
    """Google Cloud Speech-to-Text v2 streaming engine.

    Handles streaming recognition with automatic session restart
    every ~5 minutes (Google's streaming limit). Supports multilingual
    recognition (uk-UA primary, ru-RU alternative).
    """

    def __init__(self, project_id: str = "") -> None:
        self._project_id = project_id
        self._client: SpeechAsyncClient | None = None
        self._config: STTConfig | None = None
        self._audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._transcript_queue: asyncio.Queue[Transcript | None] = asyncio.Queue()
        self._stream_task: asyncio.Task[None] | None = None
        self._session_start: float = 0.0
        self._running = False

    async def start_stream(self, config: STTConfig) -> None:
        """Start a new recognition stream."""
        self._config = config
        self._client = SpeechAsyncClient()
        self._audio_queue = asyncio.Queue()
        self._transcript_queue = asyncio.Queue()
        self._running = True
        self._session_start = time.monotonic()
        self._stream_task = asyncio.create_task(self._recognition_loop())
        logger.info(
            "STT stream started: lang=%s, alternatives=%s",
            config.language_code,
            config.alternative_languages,
        )

    async def feed_audio(self, chunk: bytes) -> None:
        """Feed an audio chunk to the recognition stream."""
        if not self._running:
            return

        # Check if session needs restart (approaching 5-min limit)
        elapsed = time.monotonic() - self._session_start
        if elapsed >= _SESSION_RESTART_SECONDS:
            logger.debug("STT session restart (elapsed %.1fs)", elapsed)
            await self._restart_session()

        await self._audio_queue.put(chunk)

    async def get_transcripts(self) -> AsyncIterator[Transcript]:
        """Yield transcripts as they become available."""
        while self._running or not self._transcript_queue.empty():
            try:
                transcript = await asyncio.wait_for(self._transcript_queue.get(), timeout=0.1)
            except TimeoutError:
                continue

            if transcript is None:
                break

            yield transcript

    async def stop_stream(self) -> None:
        """Stop the recognition stream and release resources."""
        self._running = False

        # Signal the audio generator to stop
        await self._audio_queue.put(None)
        await self._transcript_queue.put(None)

        if self._stream_task is not None:
            self._stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stream_task
            self._stream_task = None

        if self._client is not None:
            # SpeechAsyncClient doesn't have an explicit close
            self._client = None

        logger.info("STT stream stopped")

    async def _restart_session(self) -> None:
        """Restart the streaming session (5-min limit workaround)."""
        # Signal current stream to end
        await self._audio_queue.put(None)

        if self._stream_task is not None:
            try:
                await asyncio.wait_for(self._stream_task, timeout=2.0)
            except (TimeoutError, asyncio.CancelledError):
                self._stream_task.cancel()

        # Start new stream
        self._audio_queue = asyncio.Queue()
        self._session_start = time.monotonic()
        self._stream_task = asyncio.create_task(self._recognition_loop())

    def _build_recognition_config(
        self, *, with_adaptation: bool = True
    ) -> cloud_speech.RecognitionConfig:
        """Build RecognitionConfig, optionally including phrase adaptation."""
        assert self._config is not None
        adaptation = None
        if with_adaptation:
            adaptation = _build_adaptation(self._config.phrase_hints)
        return cloud_speech.RecognitionConfig(
            explicit_decoding_config=cloud_speech.ExplicitDecodingConfig(
                encoding=cloud_speech.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=self._config.sample_rate_hertz,
                audio_channel_count=1,
            ),
            language_codes=[self._config.language_code, *self._config.alternative_languages],
            model=self._config.model,
            adaptation=adaptation,
        )

    async def _recognition_loop(self) -> None:
        """Main recognition loop: sends audio, receives transcripts."""
        if self._client is None or self._config is None:
            return

        recognizer = f"projects/{self._project_id}/locations/global/recognizers/_"

        # Try with adaptation first; if recognizer doesn't support it, retry without
        for attempt, use_adaptation in enumerate((True, False)):
            if attempt > 0 and not self._config.phrase_hints:
                # No point retrying without adaptation if there were no hints
                break

            recognition_config = self._build_recognition_config(with_adaptation=use_adaptation)
            streaming_config = cloud_speech.StreamingRecognitionConfig(
                config=recognition_config,
                streaming_features=cloud_speech.StreamingRecognitionFeatures(
                    interim_results=self._config.interim_results,
                ),
            )

            try:
                await self._run_streaming(recognizer, streaming_config)
                return  # normal exit
            except asyncio.CancelledError:
                raise
            except Exception:
                if attempt == 0 and self._config.phrase_hints:
                    logger.warning(
                        "STT recognition failed with adaptation, retrying without phrase hints"
                    )
                    continue
                logger.exception("STT recognition error")
                if self._running:
                    await self._transcript_queue.put(None)

    async def _run_streaming(
        self,
        recognizer: str,
        streaming_config: cloud_speech.StreamingRecognitionConfig,
    ) -> None:
        """Run a single streaming recognition session."""
        assert self._client is not None
        assert self._config is not None

        async def request_generator() -> AsyncIterator[cloud_speech.StreamingRecognizeRequest]:
            # First request: config + recognizer
            yield cloud_speech.StreamingRecognizeRequest(
                recognizer=recognizer,
                streaming_config=streaming_config,
            )

            # Subsequent requests: audio content
            while True:
                chunk = await self._audio_queue.get()
                if chunk is None:
                    break
                yield cloud_speech.StreamingRecognizeRequest(
                    audio=chunk,
                )

        responses = await self._client.streaming_recognize(
            requests=request_generator(),
        )

        async for response in responses:
            if not self._running:
                break

            for result in response.results:
                if not result.alternatives:
                    continue

                best = result.alternatives[0]
                language = (
                    result.language_code if result.language_code else self._config.language_code
                )

                transcript = Transcript(
                    text=best.transcript.strip(),
                    is_final=result.is_final,
                    confidence=best.confidence if result.is_final else 0.0,
                    language=language,
                )

                if transcript.text:
                    await self._transcript_queue.put(transcript)

                    if result.is_final:
                        logger.info(
                            "STT final: '%s' (lang=%s, conf=%.2f)",
                            transcript.text,
                            language,
                            best.confidence,
                        )
