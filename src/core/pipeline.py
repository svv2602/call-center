"""Pipeline Orchestrator: AudioSocket → STT → LLM → TTS → AudioSocket.

Coordinates real-time data flow between all components for a single call.
Supports barge-in (interrupting TTS when the caller speaks).
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import logging
import time
import zoneinfo
from typing import TYPE_CHECKING, Any

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
    WAIT_FITTING_PRICE_POOL,
    WAIT_KNOWLEDGE_POOL,
    WAIT_ORDER_POOL,
    WAIT_SEARCH_POOL,
    WAIT_STATUS_POOL,
    WAIT_STORAGE_POOL,
    WAIT_TEXT,
    compute_order_stage,
)
from src.core.audio_socket import AudioSocketConnection, PacketType
from src.core.call_session import SILENCE_TIMEOUT_SEC, CallSession, CallState
from src.monitoring.metrics import audiosocket_to_stt_ms, barge_in_total, tts_delivery_ms
from src.stt.base import STTConfig, STTEngine, Transcript

# Max time to wait for LLM agent to produce a response (seconds)
AGENT_PROCESSING_TIMEOUT_SEC = 30

# Timeout for contextual farewell LLM call (seconds)
_FAREWELL_LLM_TIMEOUT_SEC = 3

# Minimum dialog turns before using contextual farewell
_FAREWELL_MIN_TURNS = 3

# Default template dict (used if no PromptManager or DB unavailable)
_DEFAULT_TEMPLATES: dict[str, str] = {
    "greeting": GREETING_TEXT,
    "farewell": FAREWELL_TEXT,
    "silence_prompt": SILENCE_PROMPT_TEXT,
    "transfer": TRANSFER_TEXT,
    "error": ERROR_TEXT,
    "wait": WAIT_TEXT,
}

# --- Time-of-day greeting ---

_KYIV_TZ = zoneinfo.ZoneInfo("Europe/Kyiv")


def _time_of_day_greeting() -> str:
    """Return a Ukrainian greeting appropriate for the current Kyiv time."""
    hour = datetime.datetime.now(tz=_KYIV_TZ).hour
    if 5 <= hour < 12:
        return "Добрий ранок"
    if 12 <= hour < 18:
        return "Добрий день"
    if 18 <= hour < 23:
        return "Добрий вечір"
    return "Доброї ночі"


# --- Strip duplicate greeting from LLM response ---

_GREETING_PREFIXES = (
    "добрий ранок",
    "добрий день",
    "добрий вечір",
    "доброї ночі",
    "вітаю",
    "привіт",
)


def _strip_greeting(text: str) -> str:
    """Remove greeting prefix from LLM response to avoid double greeting."""
    lowered = text.lstrip()
    for prefix in _GREETING_PREFIXES:
        if lowered.lower().startswith(prefix):
            # Strip the greeting and any following punctuation/whitespace
            rest = lowered[len(prefix):].lstrip(" !.,;:—–-")
            if rest:
                return rest[0].upper() + rest[1:] if rest else ""
            # If only the greeting and nothing else — return as-is
            return text
    return text


# --- Contextual wait-phrase selection with rotation ---

_WAIT_CONTEXT_PATTERNS: list[tuple[list[str], list[str]]] = [
    (["зберігання", "зберіганні", "договір", "забрати шини"], WAIT_STORAGE_POOL),
    (["статус", "де замовлення", "де моє"], WAIT_STATUS_POOL),
    (["замовлення", "замовити", "оформити"], WAIT_ORDER_POOL),
    # Pricing keywords before booking — "ціна монтаж" → pricing, not booking
    (["ціна", "вартість", "скільки коштує", "прайс"], WAIT_FITTING_PRICE_POOL),
    (["запис", "записати", "вільні час"], WAIT_FITTING_POOL),
    (["монтаж", "шиномонтаж"], WAIT_DEFAULT_POOL),
    (["наявність", "є в наявності", "склад"], WAIT_AVAILABILITY_POOL),
    (["шини", "шину", "підібрати", "розмір", "зимов", "літн"], WAIT_SEARCH_POOL),
    (["порівняти", "рекомендац", "відмінн"], WAIT_KNOWLEDGE_POOL),
]

# Per-pool rotation counters (round-robin within a call and across calls)
_wait_counters: dict[int, int] = {}


def _select_wait_message(user_text: str, default: str) -> str:
    """Pick a contextual wait message, rotating through the pool."""
    lowered = user_text.lower()
    for keywords, pool in _WAIT_CONTEXT_PATTERNS:
        if any(kw in lowered for kw in keywords):
            return _rotate(pool)
    return _rotate(WAIT_DEFAULT_POOL)


def _rotate(pool: list[str]) -> str:
    """Round-robin selection from a phrase pool."""
    pool_id = id(pool)
    idx = _wait_counters.get(pool_id, 0)
    phrase = pool[idx % len(pool)]
    _wait_counters[pool_id] = idx + 1
    return phrase


# --- Contextual farewell prompt ---

# --- Scenario-specific greeting suffixes ---

_SCENARIO_GREETING_SUFFIX: dict[str, str] = {
    "tire_search": "Допоможу підібрати шини.",
    "order_status": "Перевірю статус вашого замовлення.",
    "fitting": "Запишу вас на шиномонтаж.",
    "consultation": "Готова відповісти на ваші питання.",
}


_FAREWELL_SYSTEM_PROMPT = (
    "Ти — голосовий асистент інтернет-магазину шин Олена. "
    "Клієнт мовчить. Згенеруй коротке прощання (1 речення українською), "
    "підсумуй результат розмови. Подякуй за дзвінок."
)

if TYPE_CHECKING:
    from src.agent.agent import LLMAgent
    from src.agent.streaming_loop import StreamingAgentLoop
    from src.monitoring.cost_tracker import CostBreakdown
    from src.sandbox.patterns import PatternSearch
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
        pattern_search: PatternSearch | None = None,
        streaming_loop: StreamingAgentLoop | None = None,
        barge_in_event: asyncio.Event | None = None,
        agent_name: str | None = None,
        call_logger: Any = None,
        cost_breakdown: CostBreakdown | None = None,
        caller_history: str | None = None,
        storage_context: str | None = None,
    ) -> None:
        self._conn = conn
        self._stt = stt
        self._tts = tts
        self._agent = agent
        self._session = session
        self._stt_config = stt_config or STTConfig()
        self._templates = templates or _DEFAULT_TEMPLATES
        self._pattern_search = pattern_search
        self._streaming_loop = streaming_loop
        self._agent_name = agent_name
        self._call_logger = call_logger
        self._cost = cost_breakdown
        self._caller_history = caller_history
        self._storage_context = storage_context
        self._turn_counter = 0
        self._llm_history: list[dict[str, Any]] = []  # persistent LLM context for streaming path
        self._speaking = False
        self._barge_in_event = barge_in_event or asyncio.Event()
        self._final_transcript_queue: asyncio.Queue[Transcript | None] = asyncio.Queue()

    async def _log_turn(
        self,
        speaker: str,
        content: str,
        stt_confidence: float | None = None,
        llm_latency_ms: int | None = None,
    ) -> None:
        """Log a turn to the database (fire-and-forget)."""
        if self._call_logger is None:
            return
        turn_number = self._turn_counter
        self._turn_counter += 1
        try:
            await self._call_logger.log_turn(
                call_id=self._session.channel_uuid,
                turn_number=turn_number,
                speaker=speaker,
                content=content,
                stt_confidence=stt_confidence,
                llm_latency_ms=llm_latency_ms,
            )
        except Exception:
            logger.warning("log_turn failed for call %s", self._session.channel_uuid)

    async def run(self) -> None:
        """Run the full call pipeline until hangup or transfer."""
        try:
            # Start STT and audio reader BEFORE greeting so that incoming
            # caller audio is fed to STT in real-time.  Without this, audio
            # accumulates in the TCP buffer during the ~8 s greeting and is
            # then flushed in a burst — which breaks latest_short model.
            await self._stt.start_stream(self._stt_config)
            audio_task = asyncio.create_task(self._audio_reader_loop())

            # Play greeting while STT is already consuming audio
            await self._play_greeting()

            # Main loop
            self._session.transition_to(CallState.LISTENING)

            # Start transcript reader and processor (audio reader already running)
            transcript_reader_task = asyncio.create_task(self._transcript_reader_loop())
            transcript_task = asyncio.create_task(self._transcript_processor_loop())

            _done, pending = await asyncio.wait(
                [audio_task, transcript_reader_task, transcript_task],
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
            error_msg = self._templates.get("error", ERROR_TEXT)
            await self._log_turn("bot", error_msg)
            await self._speak(error_msg)
        finally:
            await self._stt.stop_stream()
            self._session.transition_to(CallState.ENDED)

    async def _play_greeting(self) -> None:
        """Play the greeting message, adapted to the time of day and agent name."""
        greeting = self._templates.get("greeting", GREETING_TEXT)
        greeting = greeting.replace("{time_greeting}", _time_of_day_greeting())
        greeting = greeting.replace("{agent_name}", self._agent_name or "Олена")
        # Append scenario-specific suffix if IVR intent was resolved
        suffix = _SCENARIO_GREETING_SUFFIX.get(self._session.scenario or "")
        if suffix:
            greeting = greeting.rstrip() + " " + suffix
        self._session.transition_to(CallState.GREETING)
        await self._speak(greeting)
        self._session.add_assistant_turn(greeting)
        await self._log_turn("bot", greeting)
        # Seed LLM history so the model knows the greeting was already spoken
        self._llm_history.append({"role": "assistant", "content": greeting})

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

    async def _transcript_reader_loop(self) -> None:
        """Sole consumer of STT transcripts — fan-out to queue and barge-in.

        - Final transcripts with text → ``_final_transcript_queue``
        - Interim transcripts while ``_speaking`` → set ``_barge_in_event``
        - On STT stream end → put ``None`` sentinel to unblock queue reader
        """
        try:
            async for transcript in self._stt.get_transcripts():
                if transcript.is_final and transcript.text.strip():
                    await self._final_transcript_queue.put(transcript)
                elif not transcript.is_final and transcript.text.strip() and self._speaking:
                    self._barge_in_event.set()
                    barge_in_total.inc()
                    logger.info(
                        "Barge-in signal: interim '%s' while speaking: %s",
                        transcript.text[:30],
                        self._session.channel_uuid,
                    )
        finally:
            # Signal queue reader that STT stream has ended
            await self._final_transcript_queue.put(None)

    async def _transcript_processor_loop(self) -> None:
        """Process STT transcripts and drive the LLM → TTS flow."""
        while not self._conn.is_closed:
            # Wait for final transcripts with silence timeout
            transcript = await self._wait_for_final_transcript()

            if transcript is None:
                # Silence timeout
                should_end = self._session.record_timeout()
                if should_end:
                    farewell = await self._generate_contextual_farewell()
                    if farewell is None:
                        farewell = self._templates.get("farewell", FAREWELL_TEXT)
                    self._session.add_assistant_turn(farewell)
                    await self._log_turn("bot", farewell)
                    await self._speak(farewell)
                    break
                else:
                    silence_msg = self._templates.get("silence_prompt", SILENCE_PROMPT_TEXT)
                    await self._log_turn("bot", silence_msg)
                    await self._speak(silence_msg)
                    continue

            # Got a final transcript — reset timeout and track language
            self._session.timeout_count = 0
            if transcript.language:
                self._session.detected_language = transcript.language

            # Search for relevant conversation patterns
            pattern_context = None
            if self._pattern_search is not None:
                try:
                    patterns = await self._pattern_search.search(
                        query=transcript.text,
                        top_k=3,
                        min_similarity=0.6,
                    )
                    pattern_context = await self._pattern_search.format_for_prompt(patterns)
                    if patterns:
                        await self._pattern_search.increment_usage([p["id"] for p in patterns])
                        logger.info(
                            "Pattern injection: call=%s, patterns_found=%d, intents=%s",
                            self._session.channel_uuid,
                            len(patterns),
                            [p["intent_label"] for p in patterns],
                        )
                except Exception:
                    logger.warning("Pattern search failed, continuing without", exc_info=True)

            # Compute order stage for stage-aware prompt injection
            order_stage = compute_order_stage(self._session.order_draft, self._session.order_id)

            if self._streaming_loop is not None:
                # STREAMING PATH — add user turn to session (streaming loop uses separate _llm_history)
                self._session.add_user_turn(
                    content=transcript.text,
                    stt_confidence=transcript.confidence,
                    detected_language=transcript.language,
                )
                await self._log_turn(
                    "customer", transcript.text, stt_confidence=transcript.confidence
                )
                self._session.transition_to(CallState.SPEAKING)
                start = time.monotonic()
                try:
                    result = await asyncio.wait_for(
                        self._streaming_loop.run_turn(
                            user_text=transcript.text,
                            conversation_history=self._llm_history,
                            caller_phone=self._session.caller_phone,
                            order_id=self._session.order_id,
                            pattern_context=pattern_context,
                            order_stage=order_stage,
                            caller_history=self._caller_history,
                            storage_context=self._storage_context,
                            fitting_booked=self._session.fitting_booked,
                            tools_called=self._session.tools_called,
                            scenario=self._session.scenario,
                        ),
                        timeout=AGENT_PROCESSING_TIMEOUT_SEC,
                    )
                except TimeoutError:
                    logger.error(
                        "Streaming agent timeout after %ds: call=%s",
                        AGENT_PROCESSING_TIMEOUT_SEC,
                        self._session.channel_uuid,
                    )
                    result = None

                llm_latency_ms = int((time.monotonic() - start) * 1000)

                # Track LLM token cost (streaming path)
                if self._cost is not None and result is not None:
                    self._cost.add_llm_usage(
                        result.total_usage.input_tokens,
                        result.total_usage.output_tokens,
                    )

                if result is not None and result.spoken_text:
                    self._session.add_assistant_turn(result.spoken_text)
                    await self._log_turn("bot", result.spoken_text, llm_latency_ms=llm_latency_ms)
                    logger.info(
                        "Streaming turn completed: call=%s, user='%s', agent='%s', "
                        "tools=%d, llm=%dms",
                        self._session.channel_uuid,
                        transcript.text[:50],
                        result.spoken_text[:50],
                        result.tool_calls_made,
                        llm_latency_ms,
                    )
                else:
                    logger.warning("Empty streaming response: call=%s", self._session.channel_uuid)
                    fallback = self._templates.get("error", ERROR_TEXT)
                    self._session.add_assistant_turn(fallback)
                    await self._log_turn("bot", fallback)
                    await self._speak(fallback)
            else:
                # BLOCKING PATH — delay add_user_turn so process_message
                # doesn't see it duplicated in messages_for_llm
                self._session.transition_to(CallState.PROCESSING)
                wait_default = self._templates.get("wait", WAIT_TEXT)
                wait_msg = _select_wait_message(transcript.text, wait_default)
                await self._log_turn("bot", wait_msg)
                await self._speak(wait_msg)  # contextual filler while processing

                start = time.monotonic()
                try:
                    response_text, _ = await asyncio.wait_for(
                        self._agent.process_message(
                            user_text=transcript.text,
                            conversation_history=self._session.messages_for_llm,
                            caller_phone=self._session.caller_phone,
                            order_id=self._session.order_id,
                            pattern_context=pattern_context,
                            order_stage=order_stage,
                            caller_history=self._caller_history,
                            storage_context=self._storage_context,
                            fitting_booked=self._session.fitting_booked,
                            tools_called=self._session.tools_called,
                            scenario=self._session.scenario,
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

                # Track LLM token cost (blocking path)
                if self._cost is not None:
                    self._cost.add_llm_usage(
                        self._agent.last_input_tokens,
                        self._agent.last_output_tokens,
                    )

                # Now record the user turn in session (for DB/analytics)
                self._session.add_user_turn(
                    content=transcript.text,
                    stt_confidence=transcript.confidence,
                    detected_language=transcript.language,
                )
                await self._log_turn(
                    "customer", transcript.text, stt_confidence=transcript.confidence
                )

                logger.info(
                    "Turn completed: call=%s, user='%s', agent='%s', llm=%dms",
                    self._session.channel_uuid,
                    transcript.text[:50],
                    response_text[:50] if response_text else "(empty)",
                    llm_latency_ms,
                )

                if response_text:
                    # Strip duplicate greeting that LLM may produce
                    response_text = _strip_greeting(response_text)
                    self._session.add_assistant_turn(response_text)
                    await self._log_turn("bot", response_text, llm_latency_ms=llm_latency_ms)
                    await self._speak_streaming(response_text)
                else:
                    # Never leave the caller in silence — speak an error fallback
                    logger.warning("Empty agent response: call=%s", self._session.channel_uuid)
                    fallback = self._templates.get("error", ERROR_TEXT)
                    self._session.add_assistant_turn(fallback)
                    await self._log_turn("bot", fallback)
                    await self._speak(fallback)

            # Check if transfer was triggered
            if self._session.transferred:
                transfer_msg = self._templates.get("transfer", TRANSFER_TEXT)
                await self._log_turn("bot", transfer_msg)
                await self._speak(transfer_msg)
                self._session.transition_to(CallState.TRANSFERRING)
                break

            self._session.transition_to(CallState.LISTENING)

    async def _generate_contextual_farewell(self) -> str | None:
        """Generate a contextual farewell based on conversation history.

        Returns None if the conversation is too short or LLM fails,
        so the caller can fall back to the standard template.
        """
        # Too short — use default template
        if len(self._session.dialog_history) < _FAREWELL_MIN_TURNS:
            return None

        # Rule: if an order was placed, use a specific farewell
        if self._session.order_id:
            return FAREWELL_ORDER_TEXT

        # LLM fallback: summarise the conversation in a farewell
        history = self._session.messages_for_llm
        try:
            response_text, _ = await asyncio.wait_for(
                self._agent.process_message(
                    user_text=_FAREWELL_SYSTEM_PROMPT,
                    conversation_history=history,
                ),
                timeout=_FAREWELL_LLM_TIMEOUT_SEC,
            )
            if response_text and response_text.strip():
                return response_text.strip()
        except TimeoutError:
            logger.warning("Contextual farewell LLM timed out: %s", self._session.channel_uuid)
        except Exception:
            logger.warning(
                "Contextual farewell LLM failed: %s", self._session.channel_uuid, exc_info=True
            )

        return None

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
        transcript = await self._final_transcript_queue.get()
        if transcript is None:
            # STT stream ended — sentinel from _transcript_reader_loop
            raise asyncio.CancelledError
        return transcript

    async def _speak(self, text: str) -> None:
        """Synthesize text and send audio to AudioSocket.

        Supports barge-in: if the caller starts speaking during synthesis
        or playback, audio sending is interrupted early.
        """
        if self._conn.is_closed:
            return

        # Track TTS character cost
        if self._cost is not None:
            self._cost.add_tts_usage(len(text))

        self._session.transition_to(CallState.SPEAKING)
        self._speaking = True
        self._barge_in_event.clear()

        try:
            audio = await self._tts.synthesize(text)

            # Check if barge-in was detected during TTS synthesis
            if self._barge_in_event.is_set():
                logger.info("Barge-in during TTS synthesis: %s", self._session.channel_uuid)
                return

            t0 = time.monotonic()
            interrupted = await self._conn.send_audio(audio, cancel_event=self._barge_in_event)
            tts_delivery_ms.observe((time.monotonic() - t0) * 1000)

            if interrupted:
                logger.info("Barge-in during audio send: %s", self._session.channel_uuid)
        except Exception:
            logger.exception("TTS/send error: %s", self._session.channel_uuid)
        finally:
            self._speaking = False

    async def _speak_streaming(self, text: str) -> None:
        """Synthesize and send audio sentence by sentence.

        Supports barge-in: stops sending if the caller starts speaking,
        both between sentences (event check) and mid-chunk (cancel_event).
        """
        if self._conn.is_closed:
            return

        # Track TTS character cost
        if self._cost is not None:
            self._cost.add_tts_usage(len(text))

        self._session.transition_to(CallState.SPEAKING)
        self._speaking = True
        self._barge_in_event.clear()

        try:
            async for audio_chunk in self._tts.synthesize_stream(text):
                # Check for barge-in between sentences
                if self._barge_in_event.is_set():
                    logger.info("Barge-in between sentences: %s", self._session.channel_uuid)
                    break

                t0 = time.monotonic()
                interrupted = await self._conn.send_audio(
                    audio_chunk, cancel_event=self._barge_in_event
                )
                tts_delivery_ms.observe((time.monotonic() - t0) * 1000)

                if interrupted:
                    logger.info("Barge-in during chunk send: %s", self._session.channel_uuid)
                    break
        except Exception:
            logger.exception("TTS streaming error: %s", self._session.channel_uuid)
        finally:
            self._speaking = False
