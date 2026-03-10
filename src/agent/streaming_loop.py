"""Streaming agent loop — LLM ↔ tool execution with real-time audio.

Each LLM invocation streams through Layers 2→3→4 (sentence buffer →
TTS → audio sender). If the result contains tool_calls, execute them
and loop back to the LLM with results.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.agent.agent import MAX_HISTORY_MESSAGES, MAX_TOOL_CALLS_PER_TURN
from src.agent.history_compressor import summarize_old_messages
from src.agent.prompts import (
    SYSTEM_PROMPT,
    WAIT_AVAILABILITY_POOL,
    WAIT_BOOKING_POOL,
    WAIT_CANCEL_POOL,
    WAIT_DEFAULT_POOL,
    WAIT_FITTING_POOL,
    WAIT_FITTING_PRICE_POOL,
    WAIT_KNOWLEDGE_POOL,
    WAIT_SEARCH_POOL,
    WAIT_STATUS_POOL,
    WAIT_STORAGE_POOL,
    WAIT_THINKING_POOL,
    build_system_prompt_with_context,
)
from src.agent.tool_result_compressor import compress_tool_result
from src.agent.tools import filter_tools_by_state
from src.core.audio_sender import send_audio_stream
from src.core.sentence_buffer import buffer_sentences
from src.llm.models import LLMTask, Usage
from src.tts.streaming_tts import synthesize_stream

# Per-tool execution timeout (seconds). Prevents a single slow 1C/API call
# from blocking the entire agent turn. On timeout, tool returns an error
# message so the LLM can respond gracefully.
_TOOL_TIMEOUT_SEC = 15

# Delay (seconds) before playing a filler phrase when LLM is slow to respond
_FILLER_DELAY_SEC = 2.0

# Max retries when LLM returns empty (0 text, 0 tools). On the last retry,
# automatically switch to a fallback provider.
_MAX_EMPTY_RETRIES = 2

if TYPE_CHECKING:
    from src.agent.agent import ToolRouter
    from src.core.audio_socket import AudioSocketConnection
    from src.core.echo_canceller import EchoCanceller
    from src.llm.router import LLMRouter
    from src.logging.pii_vault import PIIVault
    from src.tts.base import TTSEngine

logger = logging.getLogger(__name__)

# Map tool names to contextual wait-phrase pools.
# When the LLM emits a tool call without preceding text, the streaming
# loop speaks one of these phrases while the tool executes.
_TOOL_WAIT_POOLS: dict[str, list[str]] = {
    "search_tires": WAIT_SEARCH_POOL,
    "check_availability": WAIT_AVAILABILITY_POOL,
    "get_order_status": WAIT_STATUS_POOL,
    "get_fitting_stations": WAIT_FITTING_POOL,
    "get_fitting_slots": WAIT_FITTING_POOL,
    "book_fitting": WAIT_BOOKING_POOL,
    "cancel_fitting": WAIT_CANCEL_POOL,
    "get_fitting_price": WAIT_FITTING_PRICE_POOL,
    "get_customer_bookings": WAIT_FITTING_POOL,
    "search_knowledge_base": WAIT_KNOWLEDGE_POOL,
    "find_storage": WAIT_STORAGE_POOL,
}


def _pick_tool_wait_phrase(tool_names: list[str]) -> str:
    """Choose a contextual wait phrase based on the tool(s) being called."""
    for name in tool_names:
        pool = _TOOL_WAIT_POOLS.get(name)
        if pool:
            return random.choice(pool)
    return random.choice(WAIT_DEFAULT_POOL)


@dataclass(frozen=True)
class TurnResult:
    """Result of one complete conversation turn (possibly multi-round)."""

    spoken_text: str
    tool_calls_made: int
    stop_reason: str
    total_usage: Usage
    provider_key: str = ""
    interrupted: bool = False
    disconnected: bool = False
    wait_phrase: str = ""


class StreamingAgentLoop:
    """Streaming agent loop — LLM ↔ tool execution with real-time audio.

    Each LLM invocation streams through Layers 2→3→4 (sentence buffer →
    TTS → audio sender). If the result contains tool_calls, execute them
    and loop back to the LLM with results.
    """

    def __init__(
        self,
        llm_router: LLMRouter,
        tool_router: ToolRouter,
        tts: TTSEngine,
        conn: AudioSocketConnection,
        barge_in_event: asyncio.Event,
        *,
        tools: list[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
        pii_vault: PIIVault | None = None,
        provider_override: str | None = None,
        max_tool_rounds: int = MAX_TOOL_CALLS_PER_TURN,
        few_shot_context: str | None = None,
        safety_context: str | None = None,
        promotions_context: str | None = None,
        is_modular: bool = False,
        agent_name: str | None = None,
        echo_canceller: EchoCanceller | None = None,
    ) -> None:
        self._llm_router = llm_router
        self._tool_router = tool_router
        self._tts_initial = tts
        self._conn = conn
        self._barge_in = barge_in_event
        self._tools = tools
        self._system_prompt = system_prompt or SYSTEM_PROMPT
        self._pii_vault = pii_vault
        self._provider_override = provider_override
        self._max_tool_rounds = max_tool_rounds
        self._few_shot_context = few_shot_context
        self._safety_context = safety_context
        self._promotions_context = promotions_context
        self._is_modular = is_modular
        self._agent_name = agent_name
        self._echo_canceller = echo_canceller
        self._thinking_counter = 0

    @property
    def _tts(self) -> TTSEngine:
        """Return the current global TTS engine (picks up hot-reloaded config)."""
        from src.tts import get_engine

        return get_engine() or self._tts_initial

    def _get_fallback_provider(self) -> str | None:
        """Return the first fallback provider key for the agent task, or None."""
        try:
            chain = self._llm_router._resolve_chain(LLMTask.AGENT, None)
            # chain[0] is primary, chain[1:] are fallbacks
            if len(chain) > 1:
                return chain[1]
        except Exception:
            logger.debug("Failed to resolve fallback provider chain", exc_info=True)
        return None

    async def _request_summary_fallback(
        self,
        system: str,
        conversation_history: list[dict[str, Any]],
    ) -> str:
        """Ask LLM to summarize tool results when max tool rounds exhausted.

        Returns a short customer-facing summary or a static fallback.
        """
        _summary_timeout_sec = 5
        _summary_prompt = (
            "Ти вичерпав ліміт викликів інструментів. "
            "Підсумуй для клієнта те, що вдалося дізнатися, "
            "в 1-2 реченнях українською. Не використовуй інструменти."
        )
        _fallback_text = (
            "Перепрошую, мені потрібно трохи більше часу. "
            "Спробуйте, будь ласка, уточнити ваше питання."
        )

        summary_history = [*conversation_history, {"role": "user", "content": _summary_prompt}]

        try:
            llm_resp = await asyncio.wait_for(
                self._llm_router.complete(
                    LLMTask.AGENT,
                    summary_history,
                    system=system,
                    tools=[],
                    max_tokens=256,
                ),
                timeout=_summary_timeout_sec,
            )
            if llm_resp.text and llm_resp.text.strip():
                logger.info("Streaming summary fallback produced text")
                return llm_resp.text.strip()
        except TimeoutError:
            logger.warning("Streaming summary fallback timed out (%ds)", _summary_timeout_sec)
        except Exception:
            logger.warning("Streaming summary fallback failed", exc_info=True)

        return _fallback_text

    async def run_turn(
        self,
        user_text: str,
        conversation_history: list[dict[str, Any]],
        caller_phone: str | None = None,
        order_id: str | None = None,
        pattern_context: str | None = None,
        order_stage: str | None = None,
        caller_history: str | None = None,
        storage_context: str | None = None,
        customer_profile: str | None = None,
        fitting_booked: bool = False,
        tools_called: set[str] | None = None,
        scenario: str | None = None,
        active_scenarios: set[str] | None = None,
    ) -> TurnResult:
        """Run a full conversation turn with streaming audio output.

        May loop multiple times if the LLM returns tool calls.
        Mutates conversation_history in place.
        """
        # Mask PII before sending to LLM
        if self._pii_vault is not None:
            user_text = self._pii_vault.mask(user_text)

        # Add user message
        conversation_history.append({"role": "user", "content": user_text})

        # Compress/summarize old messages to save tokens (BEFORE trim so
        # early context like customer name / topic is captured in the summary)
        conversation_history[:] = summarize_old_messages(conversation_history)

        # Safety-net trim: if history is still too long after summarization
        if len(conversation_history) > MAX_HISTORY_MESSAGES:
            conversation_history[:] = (
                conversation_history[:1] + conversation_history[-(MAX_HISTORY_MESSAGES - 1) :]
            )

        # Build system prompt with caller context (mask caller phone)
        masked_phone = caller_phone
        if self._pii_vault is not None and caller_phone:
            masked_phone = self._pii_vault.mask(caller_phone)
        system = build_system_prompt_with_context(
            self._system_prompt,
            is_modular=self._is_modular,
            order_stage=order_stage,
            safety_context=self._safety_context,
            few_shot_context=self._few_shot_context,
            promotions_context=self._promotions_context,
            caller_phone=masked_phone,
            order_id=order_id,
            pattern_context=pattern_context,
            agent_name=self._agent_name,
            customer_profile=customer_profile,
            caller_history=caller_history,
            storage_context=storage_context,
            tools_called=tools_called,
            scenario=scenario,
            active_scenarios=active_scenarios,
        )

        # Filter tools by conversation state (remove irrelevant tools)
        tools = filter_tools_by_state(
            self._tools or [], order_stage=order_stage, fitting_booked=fitting_booked
        )

        spoken_parts: list[str] = []
        has_llm_text = False  # True when LLM produced real text (not just wait-phrases)
        wait_phrase_spoken = ""  # Tracked separately from spoken_parts for quality scoring
        total_input_tokens = 0
        total_output_tokens = 0
        tool_calls_made = 0
        stop_reason = "end_turn"
        provider_key = ""
        interrupted = False
        disconnected = False
        empty_retries = 0
        current_provider_override = self._provider_override

        turn_start = time.monotonic()

        tool_round = 0
        while tool_round < self._max_tool_rounds:
            # Stream LLM → sentence buffer → TTS → audio sender
            try:
                stream = self._llm_router.complete_stream(
                    LLMTask.AGENT,
                    conversation_history,
                    system=system,
                    tools=tools,
                    max_tokens=1024,
                    provider_override=current_provider_override,
                )
                buffered = buffer_sentences(stream)
                tts_stream = synthesize_stream(buffered, self._tts)

                # Pre-synthesize filler audio (from cache — instant)
                filler_audio: bytes | None = None
                if not self._conn.is_closed:
                    phrase = WAIT_THINKING_POOL[self._thinking_counter % len(WAIT_THINKING_POOL)]
                    self._thinking_counter += 1
                    try:
                        filler_audio = await self._tts.synthesize(phrase)
                        logger.debug("Pre-synthesized thinking filler: %r (%d bytes)", phrase, len(filler_audio))
                    except Exception:
                        logger.debug("Failed to pre-synthesize filler", exc_info=True)

                result = await send_audio_stream(
                    tts_stream,
                    self._conn,
                    self._barge_in,
                    turn_start_time=turn_start,
                    echo_canceller=self._echo_canceller,
                    filler_audio=filler_audio,
                    filler_delay_sec=_FILLER_DELAY_SEC,
                )
            except Exception:
                logger.exception("LLM streaming error in round %d", tool_round)
                return TurnResult(
                    spoken_text=" ".join(spoken_parts),
                    tool_calls_made=tool_calls_made,
                    stop_reason="error",
                    total_usage=Usage(total_input_tokens, total_output_tokens),
                    interrupted=False,
                    disconnected=False,
                )

            # Accumulate spoken text
            if result.spoken_text:
                spoken_parts.append(result.spoken_text)
                has_llm_text = True

            # Accumulate usage
            total_input_tokens += result.usage.input_tokens
            total_output_tokens += result.usage.output_tokens
            stop_reason = result.stop_reason
            provider_key = result.provider_key or provider_key

            # Build assistant content blocks for history
            assistant_content: list[dict[str, Any]] = []
            if result.spoken_text:
                assistant_content.append({"type": "text", "text": result.spoken_text})
            for tc in result.tool_calls:
                try:
                    args = json.loads(tc.arguments_json) if tc.arguments_json else {}
                except json.JSONDecodeError:
                    args = {}
                assistant_content.append(
                    {
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": args,
                    }
                )

            if assistant_content:
                conversation_history.append({"role": "assistant", "content": assistant_content})

            # Check interruption/disconnection — remove dangling tool_calls from history
            if result.interrupted:
                interrupted = True
                if result.tool_calls and assistant_content:
                    # Remove the assistant message with tool_calls — no tool results will follow.
                    # Without this, OpenAI API returns 400: "tool_calls must be followed by tool messages"
                    conversation_history.pop()
                break
            if result.disconnected:
                disconnected = True
                if result.tool_calls and assistant_content:
                    conversation_history.pop()
                break

            # No tool calls → done (or retry if empty)
            if not result.tool_calls:
                if (
                    not result.spoken_text
                    and not has_llm_text
                    and empty_retries < _MAX_EMPTY_RETRIES
                ):
                    empty_retries += 1
                    # On last retry, switch to fallback provider
                    if empty_retries == _MAX_EMPTY_RETRIES and self._provider_override is None:
                        fallback = self._get_fallback_provider()
                        if fallback:
                            current_provider_override = fallback
                            logger.warning(
                                "Empty LLM response (retry %d/%d), "
                                "stop=%s, out_tokens=%d — switching to fallback %s",
                                empty_retries,
                                _MAX_EMPTY_RETRIES,
                                result.stop_reason,
                                result.usage.output_tokens,
                                fallback,
                            )
                            continue
                    logger.warning(
                        "Empty LLM response (retry %d/%d), "
                        "stop=%s, out_tokens=%d — retrying same provider",
                        empty_retries,
                        _MAX_EMPTY_RETRIES,
                        result.stop_reason,
                        result.usage.output_tokens,
                    )
                    continue
                break

            # Deduplicate tool calls (same name + same args → skip)
            seen_keys: set[str] = set()
            unique_tool_calls: list[Any] = []
            for tc in result.tool_calls:
                try:
                    args_parsed = json.loads(tc.arguments_json) if tc.arguments_json else {}
                except json.JSONDecodeError:
                    args_parsed = {}
                dedup_key = tc.name + ":" + json.dumps(args_parsed, sort_keys=True)
                if dedup_key in seen_keys:
                    logger.warning(
                        "Skipping duplicate tool call: %s(%s)",
                        tc.name,
                        json.dumps(args_parsed, ensure_ascii=False)[:200],
                    )
                    continue
                seen_keys.add(dedup_key)
                unique_tool_calls.append(tc)

            # Execute tool calls in parallel (with per-tool timeout).
            # If LLM produced no text before the tool call, speak a contextual
            # wait-phrase in parallel so the caller doesn't hear silence.
            async def _execute_one_tool(tc: Any) -> dict[str, Any]:
                try:
                    args = json.loads(tc.arguments_json) if tc.arguments_json else {}
                except json.JSONDecodeError:
                    args = {}
                if self._pii_vault is not None:
                    args = self._pii_vault.restore_in_args(args)
                try:
                    raw = await asyncio.wait_for(
                        self._tool_router.execute(tc.name, args),
                        timeout=_TOOL_TIMEOUT_SEC,
                    )
                except TimeoutError:
                    logger.error("Tool %s timed out after %ds", tc.name, _TOOL_TIMEOUT_SEC)
                    raw = {"error": "Сервіс тимчасово не відповідає, спробуйте ще раз"}
                content = compress_tool_result(tc.name, raw)
                if self._pii_vault is not None:
                    content = self._pii_vault.mask(content)
                return {"type": "tool_result", "tool_use_id": tc.id, "content": content}

            # Speak wait-phrase during tool execution.
            # Always play when tool calls are present — even if LLM already spoke
            # text, because tool execution + next LLM round can take 10+ seconds.
            need_wait_phrase = not interrupted and not disconnected
            if need_wait_phrase and not self._conn.is_closed:
                tool_names = [tc.name for tc in unique_tool_calls]
                wait_phrase = _pick_tool_wait_phrase(tool_names)
                logger.info("Speaking wait-phrase during tool exec: %r", wait_phrase)

                async def _speak_wait(_phrase: str = wait_phrase) -> None:
                    try:
                        tts = self._tts
                        audio = await tts.synthesize(_phrase)
                        if not (self._barge_in and self._barge_in.is_set()):
                            if self._echo_canceller is not None:
                                self._echo_canceller.record_far_end(audio)
                            await self._conn.send_audio(audio, cancel_event=self._barge_in)
                    except Exception:
                        logger.debug("Wait-phrase speak failed", exc_info=True)

                # Run wait-phrase and tool execution concurrently
                wait_task = asyncio.create_task(_speak_wait())
                tool_results = list(
                    await asyncio.gather(*[_execute_one_tool(tc) for tc in unique_tool_calls])
                )
                await wait_task
                wait_phrase_spoken = wait_phrase
            else:
                tool_results = list(
                    await asyncio.gather(*[_execute_one_tool(tc) for tc in unique_tool_calls])
                )
            tool_calls_made += len(tool_results)

            conversation_history.append({"role": "user", "content": tool_results})

            tool_round += 1
            if tool_round >= self._max_tool_rounds:
                logger.warning("Max tool rounds reached (%d)", self._max_tool_rounds)
                break

        # Fallback: if max tool rounds exhausted with no spoken text, ask LLM for summary
        if not spoken_parts and tool_round >= self._max_tool_rounds:
            summary = await self._request_summary_fallback(system, conversation_history)
            if summary:
                spoken_parts.append(summary)
                # Synthesize and send the summary audio
                try:
                    tts = self._tts
                    audio = await tts.synthesize(summary)
                    if self._echo_canceller is not None:
                        self._echo_canceller.record_far_end(audio)
                    await self._conn.send_audio(audio, cancel_event=self._barge_in)
                except Exception:
                    logger.debug("Summary fallback audio send failed", exc_info=True)

        return TurnResult(
            spoken_text=" ".join(spoken_parts),
            tool_calls_made=tool_calls_made,
            stop_reason=stop_reason,
            total_usage=Usage(total_input_tokens, total_output_tokens),
            provider_key=provider_key,
            interrupted=interrupted,
            disconnected=disconnected,
            wait_phrase=wait_phrase_spoken,
        )
