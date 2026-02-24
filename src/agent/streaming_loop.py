"""Streaming agent loop — LLM ↔ tool execution with real-time audio.

Each LLM invocation streams through Layers 2→3→4 (sentence buffer →
TTS → audio sender). If the result contains tool_calls, execute them
and loop back to the LLM with results.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.agent.agent import MAX_HISTORY_MESSAGES, MAX_TOOL_CALLS_PER_TURN

# Per-tool execution timeout (seconds). Prevents a single slow 1C/API call
# from blocking the entire agent turn. On timeout, tool returns an error
# message so the LLM can respond gracefully.
_TOOL_TIMEOUT_SEC = 15
from src.agent.history_compressor import compress_history
from src.agent.prompts import SYSTEM_PROMPT, build_system_prompt_with_context
from src.agent.tool_result_compressor import compress_tool_result
from src.core.audio_sender import send_audio_stream
from src.core.sentence_buffer import buffer_sentences
from src.llm.models import LLMTask, Usage
from src.tts.streaming_tts import synthesize_stream

if TYPE_CHECKING:
    from src.agent.agent import ToolRouter
    from src.core.audio_socket import AudioSocketConnection
    from src.llm.router import LLMRouter
    from src.logging.pii_vault import PIIVault
    from src.tts.base import TTSEngine

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TurnResult:
    """Result of one complete conversation turn (possibly multi-round)."""

    spoken_text: str
    tool_calls_made: int
    stop_reason: str
    total_usage: Usage
    interrupted: bool = False
    disconnected: bool = False


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
        is_modular: bool = False,
        agent_name: str | None = None,
    ) -> None:
        self._llm_router = llm_router
        self._tool_router = tool_router
        self._tts = tts
        self._conn = conn
        self._barge_in = barge_in_event
        self._tools = tools
        self._system_prompt = system_prompt or SYSTEM_PROMPT
        self._pii_vault = pii_vault
        self._provider_override = provider_override
        self._max_tool_rounds = max_tool_rounds
        self._few_shot_context = few_shot_context
        self._safety_context = safety_context
        self._is_modular = is_modular
        self._agent_name = agent_name

    async def run_turn(
        self,
        user_text: str,
        conversation_history: list[dict[str, Any]],
        caller_phone: str | None = None,
        order_id: str | None = None,
        pattern_context: str | None = None,
        order_stage: str | None = None,
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

        # Trim history if too long
        if len(conversation_history) > MAX_HISTORY_MESSAGES:
            conversation_history[:] = (
                conversation_history[:1] + conversation_history[-(MAX_HISTORY_MESSAGES - 1) :]
            )

        # Compress old tool results to save tokens
        conversation_history[:] = compress_history(conversation_history)

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
            caller_phone=masked_phone,
            order_id=order_id,
            pattern_context=pattern_context,
            agent_name=self._agent_name,
        )

        spoken_parts: list[str] = []
        total_input_tokens = 0
        total_output_tokens = 0
        tool_calls_made = 0
        stop_reason = "end_turn"
        interrupted = False
        disconnected = False

        turn_start = time.monotonic()

        tool_round = 0
        while tool_round <= self._max_tool_rounds:
            # Stream LLM → sentence buffer → TTS → audio sender
            try:
                stream = self._llm_router.complete_stream(
                    LLMTask.AGENT,
                    conversation_history,
                    system=system,
                    tools=self._tools,
                    max_tokens=1024,
                    provider_override=self._provider_override,
                )
                buffered = buffer_sentences(stream)
                tts_stream = synthesize_stream(buffered, self._tts)
                result = await send_audio_stream(
                    tts_stream,
                    self._conn,
                    self._barge_in,
                    turn_start_time=turn_start,
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

            # Accumulate usage
            total_input_tokens += result.usage.input_tokens
            total_output_tokens += result.usage.output_tokens
            stop_reason = result.stop_reason

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

            # Check interruption/disconnection
            if result.interrupted:
                interrupted = True
                break
            if result.disconnected:
                disconnected = True
                break

            # No tool calls → done
            if not result.tool_calls:
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

            # Execute tool calls in parallel (with per-tool timeout)
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
                    logger.error(
                        "Tool %s timed out after %ds", tc.name, _TOOL_TIMEOUT_SEC
                    )
                    raw = {"error": "Сервіс тимчасово не відповідає, спробуйте ще раз"}
                content = compress_tool_result(tc.name, raw)
                if self._pii_vault is not None:
                    content = self._pii_vault.mask(content)
                return {"type": "tool_result", "tool_use_id": tc.id, "content": content}

            tool_results = list(
                await asyncio.gather(*[_execute_one_tool(tc) for tc in unique_tool_calls])
            )
            tool_calls_made += len(tool_results)

            conversation_history.append({"role": "user", "content": tool_results})

            tool_round += 1
            if tool_round >= self._max_tool_rounds:
                logger.warning("Max tool rounds reached (%d)", self._max_tool_rounds)
                break

        return TurnResult(
            spoken_text=" ".join(spoken_parts),
            tool_calls_made=tool_calls_made,
            stop_reason=stop_reason,
            total_usage=Usage(total_input_tokens, total_output_tokens),
            interrupted=interrupted,
            disconnected=disconnected,
        )
