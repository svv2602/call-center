"""Streaming agent loop — LLM ↔ tool execution with real-time audio.

Each LLM invocation streams through Layers 2→3→4 (sentence buffer →
TTS → audio sender). If the result contains tool_calls, execute them
and loop back to the LLM with results.
"""

from __future__ import annotations

import asyncio  # noqa: TC003
import datetime
import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.agent.agent import MAX_HISTORY_MESSAGES, MAX_TOOL_CALLS_PER_TURN
from src.agent.prompts import SYSTEM_PROMPT
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

    async def run_turn(
        self,
        user_text: str,
        conversation_history: list[dict[str, Any]],
        caller_phone: str | None = None,
        order_id: str | None = None,
        pattern_context: str | None = None,
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

        # Build system prompt with caller context (mask caller phone)
        masked_phone = caller_phone
        if self._pii_vault is not None and caller_phone:
            masked_phone = self._pii_vault.mask(caller_phone)
        system = self._build_system_prompt(masked_phone, order_id, pattern_context)

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

            # Execute tool calls
            tool_results: list[dict[str, Any]] = []
            for tc in result.tool_calls:
                try:
                    args = json.loads(tc.arguments_json) if tc.arguments_json else {}
                except json.JSONDecodeError:
                    args = {}

                # Restore PII in tool arguments so real values reach Store API
                if self._pii_vault is not None:
                    args = self._pii_vault.restore_in_args(args)

                tool_result = await self._tool_router.execute(tc.name, args)

                # Mask PII in tool results before adding to LLM history
                content = str(tool_result)
                if self._pii_vault is not None:
                    content = self._pii_vault.mask(content)

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tc.id,
                        "content": content,
                    }
                )
                tool_calls_made += 1

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

    def _build_system_prompt(
        self,
        caller_phone: str | None = None,
        order_id: str | None = None,
        pattern_context: str | None = None,
    ) -> str:
        """Build system prompt with caller context and season hint."""
        parts = [self._system_prompt]

        month = datetime.date.today().month
        if month in (11, 12, 1, 2, 3):
            hint = "Зараз зимовий сезон — запитай: «Зимові чи всесезонні?»"
        elif month in (5, 6, 7, 8, 9):
            hint = "Зараз літній сезон — запитай: «Літні чи всесезонні?»"
        else:
            hint = "Зараз міжсезоння — запитай: «Літні, зимові чи всесезонні?»"

        parts.append(f"\n## Підказка по сезону\n- {hint}")
        parts.append("- Якщо клієнт обирає нетиповий сезон — не заперечуй, виконуй запит")

        if caller_phone or order_id:
            parts.append("\n## Контекст дзвінка")
            if caller_phone:
                parts.append(f"- CallerID клієнта: {caller_phone}")
            if order_id:
                parts.append(f"- Поточне замовлення (чорновик): {order_id}")

        if pattern_context:
            parts.append(pattern_context)

        return "\n".join(parts)
