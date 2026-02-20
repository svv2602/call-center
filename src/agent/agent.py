"""LLM agent: Claude API with tool calling.

Manages conversation flow, tool routing, and context window.
"""

from __future__ import annotations

import datetime
import logging
import time
from typing import TYPE_CHECKING, Any

import anthropic

from src.agent.prompts import ERROR_TEXT, PROMPT_VERSION, SYSTEM_PROMPT
from src.agent.tools import ALL_TOOLS

if TYPE_CHECKING:
    from src.llm.router import LLMRouter
    from src.logging.pii_vault import PIIVault

logger = logging.getLogger(__name__)

# Limits
MAX_TOOL_CALLS_PER_TURN = 5
MAX_HISTORY_MESSAGES = 40


class ToolRouter:
    """Routes tool_use calls to concrete implementations.

    Tool handlers are registered as async callables.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, Any] = {}

    def register(self, name: str, handler: Any) -> None:
        """Register a handler for a tool name."""
        self._handlers[name] = handler

    async def execute(self, name: str, args: dict[str, Any]) -> Any:
        """Execute a tool by name. Returns the result dict."""
        handler = self._handlers.get(name)
        if handler is None:
            logger.warning("Unknown tool: %s", name)
            return {"error": f"Unknown tool: {name}"}

        start = time.monotonic()
        try:
            result = await handler(**args)
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.info(
                "Tool %s executed in %dms",
                name,
                duration_ms,
            )
            return result
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.exception("Tool %s failed after %dms", name, duration_ms)
            return {"error": str(exc)}


class LLMAgent:
    """Claude-based conversational agent with tool calling.

    Sends messages to Claude API, handles tool_use responses by
    routing them through ToolRouter, and manages conversation context.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-5-20250929",
        tool_router: ToolRouter | None = None,
        pii_vault: PIIVault | None = None,
        tools: list[dict[str, Any]] | None = None,
        llm_router: LLMRouter | None = None,
        system_prompt: str | None = None,
        prompt_version_name: str | None = None,
        provider_override: str | None = None,
    ) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._tool_router = tool_router or ToolRouter()
        self._pii_vault = pii_vault
        self._tools = tools or list(ALL_TOOLS)
        self._llm_router = llm_router
        self._system_prompt = system_prompt or SYSTEM_PROMPT
        self._prompt_version_name = prompt_version_name or PROMPT_VERSION
        self._provider_override = provider_override

    @property
    def tool_router(self) -> ToolRouter:
        return self._tool_router

    async def process_message(
        self,
        user_text: str,
        conversation_history: list[dict[str, Any]],
        caller_phone: str | None = None,
        order_id: str | None = None,
        pattern_context: str | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Process a user message and return the agent's text response.

        Args:
            user_text: The user's transcribed speech.
            conversation_history: List of previous messages (mutated in place).
            caller_phone: CallerID phone number (if available).
            order_id: Current order draft ID (if in progress).
            pattern_context: Optional pattern injection text for system prompt.

        Returns:
            Tuple of (response_text, updated_conversation_history).
        """
        # Mask PII before sending to LLM
        if self._pii_vault is not None:
            user_text = self._pii_vault.mask(user_text)

        # Add user message
        conversation_history.append({"role": "user", "content": user_text})

        # Trim history if too long
        if len(conversation_history) > MAX_HISTORY_MESSAGES:
            # Keep first message (context) + recent messages
            conversation_history[:] = (
                conversation_history[:1] + conversation_history[-(MAX_HISTORY_MESSAGES - 1) :]
            )

        # Build system prompt with caller context (mask caller phone)
        masked_phone = caller_phone
        if self._pii_vault is not None and caller_phone:
            masked_phone = self._pii_vault.mask(caller_phone)
        system = self._build_system_prompt(masked_phone, order_id, pattern_context)

        response_text = ""
        tool_call_count = 0

        while tool_call_count <= MAX_TOOL_CALLS_PER_TURN:
            start = time.monotonic()

            # Process response content blocks
            assistant_content: list[dict[str, Any]] = []
            tool_uses: list[dict[str, Any]] = []

            if self._llm_router is not None:
                # Router path: multi-provider with fallback
                try:
                    from src.llm.format_converter import llm_response_to_anthropic_blocks
                    from src.llm.models import LLMTask

                    llm_response = await self._llm_router.complete(
                        LLMTask.AGENT,
                        conversation_history,
                        system=system,
                        tools=self._tools,
                        max_tokens=1024,
                        provider_override=self._provider_override,
                    )

                    latency_ms = int((time.monotonic() - start) * 1000)
                    logger.info(
                        "LLM response: provider=%s, stop=%s, latency=%dms, tokens_in=%d, tokens_out=%d",
                        llm_response.provider,
                        llm_response.stop_reason,
                        latency_ms,
                        llm_response.usage.input_tokens,
                        llm_response.usage.output_tokens,
                    )

                    if llm_response.text:
                        response_text += llm_response.text

                    # Convert LLMResponse to Anthropic content blocks for history
                    assistant_content = llm_response_to_anthropic_blocks(llm_response)
                    for tc in llm_response.tool_calls:
                        tool_uses.append({
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.arguments,
                        })
                except Exception as exc:
                    logger.exception("LLM router error: %s", exc)
                    return ERROR_TEXT, conversation_history
            else:
                # Legacy path: direct Anthropic SDK
                try:
                    response = await self._client.messages.create(
                        model=self._model,
                        max_tokens=1024,
                        system=system,
                        tools=self._tools,  # type: ignore[arg-type]
                        messages=conversation_history,  # type: ignore[arg-type]
                    )
                except anthropic.APIStatusError as exc:
                    logger.exception("Claude API error: %s", exc)
                    return ERROR_TEXT, conversation_history

                latency_ms = int((time.monotonic() - start) * 1000)
                logger.info(
                    "Claude response: stop=%s, latency=%dms, tokens_in=%d, tokens_out=%d",
                    response.stop_reason,
                    latency_ms,
                    response.usage.input_tokens,
                    response.usage.output_tokens,
                )

                for block in response.content:
                    if block.type == "text":
                        response_text += block.text
                        assistant_content.append({"type": "text", "text": block.text})
                    elif block.type == "tool_use":
                        tool_uses.append(
                            {
                                "id": block.id,
                                "name": block.name,
                                "input": block.input,
                            }
                        )
                        assistant_content.append(
                            {
                                "type": "tool_use",
                                "id": block.id,
                                "name": block.name,
                                "input": block.input,
                            }
                        )

            # Add assistant response to history
            conversation_history.append({"role": "assistant", "content": assistant_content})

            # If no tool calls, we're done
            if not tool_uses:
                break

            # Execute tool calls and add results
            tool_results: list[dict[str, Any]] = []
            for tool_use in tool_uses:
                # Restore PII in tool arguments so real values reach Store API
                args = tool_use["input"]
                if self._pii_vault is not None:
                    args = self._pii_vault.restore_in_args(args)

                result = await self._tool_router.execute(tool_use["name"], args)

                # Mask PII in tool results before adding to LLM history
                content = str(result)
                if self._pii_vault is not None:
                    content = self._pii_vault.mask(content)

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use["id"],
                        "content": content,
                    }
                )
                tool_call_count += 1

            conversation_history.append({"role": "user", "content": tool_results})

            # If we hit the tool call limit, break
            if tool_call_count >= MAX_TOOL_CALLS_PER_TURN:
                logger.warning("Max tool calls reached (%d)", MAX_TOOL_CALLS_PER_TURN)
                break

        return response_text, conversation_history

    def _build_system_prompt(
        self,
        caller_phone: str | None = None,
        order_id: str | None = None,
        pattern_context: str | None = None,
    ) -> str:
        """Build system prompt with caller context and season hint appended."""
        parts = [self._system_prompt]

        # Dynamic season hint
        month = datetime.date.today().month
        if month in (11, 12, 1, 2, 3):
            hint = "Зараз зимовий сезон — запитай: «Зимові чи всесезонні?»"
        elif month in (5, 6, 7, 8, 9):
            hint = "Зараз літній сезон — запитай: «Літні чи всесезонні?»"
        else:  # April, October
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

    @property
    def prompt_version_name(self) -> str:
        """Return the current prompt version name."""
        return self._prompt_version_name
