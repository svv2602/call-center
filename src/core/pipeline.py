"""Pipeline Orchestrator: AudioSocket → STT → LLM → TTS → AudioSocket.

Coordinates real-time data flow between all components for a single call.
Supports barge-in (interrupting TTS when the caller speaks).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING

from src.agent.prompts import (
    ERROR_TEXT,
    FAREWELL_TEXT,
    GREETING_TEXT,
    SILENCE_PROMPT_TEXT,
    TRANSFER_TEXT,
    WAIT_TEXT,
)
from src.core.audio_socket import AudioSocketConnection, PacketType
from src.core.call_session import SILENCE_TIMEOUT_SEC, CallSession, CallState
from src.monitoring.metrics import audiosocket_to_stt_ms, tts_delivery_ms
from src.stt.base import STTConfig, STTEngine, Transcript

# Max time to wait for LLM agent to produce a response (seconds)
AGENT_PROCESSING_TIMEOUT_SEC = 30

# Default template dict (used if no PromptManager or DB unavailable)
_DEFAULT_TEMPLATES: dict[str, str] = {
    "greeting": GREETING_TEXT,
    "farewell": FAREWELL_TEXT,
    "silence_prompt": SILENCE_PROMPT_TEXT,
    "transfer": TRANSFER_TEXT,
    "error": ERROR_TEXT,
    "wait": WAIT_TEXT,
}

if TYPE_CHECKING:
    from src.agent.agent import LLMAgent
    from src.tts.base import TTSEngine

logger = logging.getLogger(__name__)


class CallPipeline:
    """Orchestrates the STT → LLM → TTS pipeline for a single call.

    Lifecycle:
      1. Play greeting
      2. Listen (feed audio to STT)
      3. On final transcript → send to LLM
      4. LLM response → TTS → send audio back
      5. Repeat from step 2
      6. Handle barge-in, silence timeouts, transfer, hangup
    """

    def __init__(
        self,
        conn: AudioSocketConnection,
        stt: STTEngine,
        tts: TTSEngine,
        agent: LLMAgent,
        session: CallSession,
        stt_config: STTConfig | None = None,
        templates: dict[str, str] | None = None,
    ) -> None:
        self._conn = conn
        self._stt = stt
        self._tts = tts
        self._agent = agent
        self._session = session
        self._stt_config = stt_config or STTConfig()
        self._templates = templates or _DEFAULT_TEMPLATES
        self._speaking = False
        self._barge_in_event = asyncio.Event()

    async def run(self) -> None:
        """Run the full call pipeline until hangup or transfer."""
        try:
            # Play greeting first (STT not needed yet)
            await self._play_greeting()

            # Start STT stream after greeting, right before listening
            await self._stt.start_stream(self._stt_config)

            # Main loop
            self._session.transition_to(CallState.LISTENING)

            # Run audio reader and transcript processor concurrently
            audio_task = asyncio.create_task(self._audio_reader_loop())
            transcript_task = asyncio.create_task(self._transcript_processor_loop())

            _done, pending = await asyncio.wait(
                [audio_task, transcript_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancel remaining tasks
            for task in pending:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        except asyncio.CancelledError:
            logger.info("Pipeline cancelled: %s", self._session.channel_uuid)
        except Exception:
            logger.exception("Pipeline error: %s", self._session.channel_uuid)
            await self._speak(self._templates.get("error", ERROR_TEXT))
        finally:
            await self._stt.stop_stream()
            self._session.transition_to(CallState.ENDED)

    async def _play_greeting(self) -> None:
        """Play the greeting message."""
        greeting = self._templates.get("greeting", GREETING_TEXT)
        self._session.transition_to(CallState.GREETING)
        await self._speak(greeting)
        self._session.add_assistant_turn(greeting)

    async def _audio_reader_loop(self) -> None:
        """Continuously read audio from AudioSocket and feed to STT."""
        while not self._conn.is_closed:
            packet = await self._conn.read_audio_packet()
            if packet is None:
                break

            if packet.type == PacketType.HANGUP:
                logger.info("Hangup: %s", self._session.channel_uuid)
                break

            if packet.type == PacketType.AUDIO:
                t0 = time.monotonic()
                await self._stt.feed_audio(packet.payload)
                audiosocket_to_stt_ms.observe((time.monotonic() - t0) * 1000)

            if packet.type == PacketType.ERROR:
                logger.warning("AudioSocket error: %s", self._session.channel_uuid)
                break

    async def _transcript_processor_loop(self) -> None:
        """Process STT transcripts and drive the LLM → TTS flow."""
        while not self._conn.is_closed:
            # Wait for final transcripts with silence timeout
            transcript = await self._wait_for_final_transcript()

            if transcript is None:
                # Silence timeout
                should_end = self._session.record_timeout()
                if should_end:
                    await self._speak(self._templates.get("farewell", FAREWELL_TEXT))
                    break
                else:
                    await self._speak(self._templates.get("silence_prompt", SILENCE_PROMPT_TEXT))
                    continue

            # Got a final transcript — process it
            self._session.add_user_turn(
                content=transcript.text,
                stt_confidence=transcript.confidence,
                detected_language=transcript.language,
            )

            # Process through LLM
            self._session.transition_to(CallState.PROCESSING)
            await self._speak(self._templates.get("wait", WAIT_TEXT))  # filler while processing

            start = time.monotonic()
            try:
                response_text, _ = await asyncio.wait_for(
                    self._agent.process_message(
                        user_text=transcript.text,
                        conversation_history=self._session.messages_for_llm,
                    ),
                    timeout=AGENT_PROCESSING_TIMEOUT_SEC,
                )
            except TimeoutError:
                logger.error(
                    "Agent timeout after %ds: call=%s",
                    AGENT_PROCESSING_TIMEOUT_SEC,
                    self._session.channel_uuid,
                )
                response_text = ""

            llm_latency_ms = int((time.monotonic() - start) * 1000)

            logger.info(
                "Turn completed: call=%s, user='%s', agent='%s', llm=%dms",
                self._session.channel_uuid,
                transcript.text[:50],
                response_text[:50] if response_text else "(empty)",
                llm_latency_ms,
            )

            if response_text:
                self._session.add_assistant_turn(response_text)
                await self._speak_streaming(response_text)
            else:
                # Never leave the caller in silence — speak an error fallback
                logger.warning("Empty agent response: call=%s", self._session.channel_uuid)
                fallback = self._templates.get("error", ERROR_TEXT)
                self._session.add_assistant_turn(fallback)
                await self._speak(fallback)

            # Check if transfer was triggered
            if self._session.transferred:
                await self._speak(self._templates.get("transfer", TRANSFER_TEXT))
                self._session.transition_to(CallState.TRANSFERRING)
                break

            self._session.transition_to(CallState.LISTENING)

    async def _wait_for_final_transcript(self) -> Transcript | None:
        """Wait for a final transcript from STT, with silence timeout.

        Returns None on silence timeout.
        """
        try:
            return await asyncio.wait_for(
                self._get_next_final_transcript(),
                timeout=SILENCE_TIMEOUT_SEC,
            )
        except TimeoutError:
            return None

    async def _get_next_final_transcript(self) -> Transcript:
        """Block until a final transcript with non-empty text arrives."""
        async for transcript in self._stt.get_transcripts():
            if transcript.is_final and transcript.text.strip():
                return transcript
        # STT stream ended without a final transcript
        raise asyncio.CancelledError

    async def _speak(self, text: str) -> None:
        """Synthesize text and send audio to AudioSocket."""
        if self._conn.is_closed:
            return

        self._session.transition_to(CallState.SPEAKING)
        self._speaking = True
        self._barge_in_event.clear()

        try:
            audio = await self._tts.synthesize(text)
            t0 = time.monotonic()
            await self._conn.send_audio(audio)
            tts_delivery_ms.observe((time.monotonic() - t0) * 1000)
        except Exception:
            logger.exception("TTS/send error: %s", self._session.channel_uuid)
        finally:
            self._speaking = False

    async def _speak_streaming(self, text: str) -> None:
        """Synthesize and send audio sentence by sentence.

        Supports barge-in: stops sending if the caller starts speaking.
        """
        if self._conn.is_closed:
            return

        self._session.transition_to(CallState.SPEAKING)
        self._speaking = True
        self._barge_in_event.clear()

        try:
            async for audio_chunk in self._tts.synthesize_stream(text):
                # Check for barge-in
                if self._barge_in_event.is_set():
                    logger.info("Barge-in detected: %s", self._session.channel_uuid)
                    self._barge_in_event.clear()
                    break

                t0 = time.monotonic()
                await self._conn.send_audio(audio_chunk)
                tts_delivery_ms.observe((time.monotonic() - t0) * 1000)
        except Exception:
            logger.exception("TTS streaming error: %s", self._session.channel_uuid)
        finally:
            self._speaking = False
