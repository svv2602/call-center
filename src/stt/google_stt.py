"""Google Cloud Speech-to-Text v2 streaming implementation."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator

from google.cloud.speech_v2 import SpeechAsyncClient
from google.cloud.speech_v2.types import cloud_speech

from src.stt.base import STTConfig, Transcript

logger = logging.getLogger(__name__)

# Google STT streaming session limit (~5 min)
_SESSION_RESTART_SECONDS = 290  # restart slightly before 5 min limit


class GoogleSTTEngine:
    """Google Cloud Speech-to-Text v2 streaming engine.

    Handles streaming recognition with automatic session restart
    every ~5 minutes (Google's streaming limit). Supports multilingual
    recognition (uk-UA primary, ru-RU alternative).
    """

    def __init__(self) -> None:
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
                transcript = await asyncio.wait_for(
                    self._transcript_queue.get(), timeout=0.1
                )
            except asyncio.TimeoutError:
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
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass
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
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._stream_task.cancel()

        # Start new stream
        self._audio_queue = asyncio.Queue()
        self._session_start = time.monotonic()
        self._stream_task = asyncio.create_task(self._recognition_loop())

    async def _recognition_loop(self) -> None:
        """Main recognition loop: sends audio, receives transcripts."""
        if self._client is None or self._config is None:
            return

        recognition_config = cloud_speech.RecognitionConfig(
            auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
            language_codes=[self._config.language_code]
            + self._config.alternative_languages,
            model=self._config.model,
            features=cloud_speech.RecognitionFeatures(
                enable_automatic_punctuation=self._config.enable_punctuation,
            ),
        )

        streaming_config = cloud_speech.StreamingRecognitionConfig(
            config=recognition_config,
            streaming_features=cloud_speech.StreamingRecognitionFeatures(
                interim_results=self._config.interim_results,
            ),
        )

        try:
            # Build request generator
            async def request_generator() -> AsyncIterator[
                cloud_speech.StreamingRecognizeRequest
            ]:
                # First request: config only
                yield cloud_speech.StreamingRecognizeRequest(
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
                        result.language_code
                        if result.language_code
                        else self._config.language_code
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

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("STT recognition error")
            if self._running:
                await self._transcript_queue.put(None)
