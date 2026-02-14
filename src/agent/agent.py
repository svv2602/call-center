"""LLM agent: Claude API with tool calling.

Manages conversation flow, tool routing, and context window.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import anthropic

from src.agent.prompts import PROMPT_VERSION, SYSTEM_PROMPT
from src.agent.tools import ALL_TOOLS
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
    ) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._tool_router = tool_router or ToolRouter()
        self._pii_vault = pii_vault

    @property
    def tool_router(self) -> ToolRouter:
        return self._tool_router

    async def process_message(
        self,
        user_text: str,
        conversation_history: list[dict[str, Any]],
        caller_phone: str | None = None,
        order_id: str | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Process a user message and return the agent's text response.

        Args:
            user_text: The user's transcribed speech.
            conversation_history: List of previous messages (mutated in place).
            caller_phone: CallerID phone number (if available).
            order_id: Current order draft ID (if in progress).

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
                conversation_history[:1]
                + conversation_history[-(MAX_HISTORY_MESSAGES - 1) :]
            )

        # Build system prompt with caller context (mask caller phone)
        masked_phone = caller_phone
        if self._pii_vault is not None and caller_phone:
            masked_phone = self._pii_vault.mask(caller_phone)
        system = self._build_system_prompt(masked_phone, order_id)

        response_text = ""
        tool_call_count = 0

        while tool_call_count <= MAX_TOOL_CALLS_PER_TURN:
            start = time.monotonic()

            try:
                response = await self._client.messages.create(
                    model=self._model,
                    max_tokens=300,
                    system=system,
                    tools=ALL_TOOLS,
                    messages=conversation_history,
                )
            except anthropic.APIStatusError as exc:
                logger.error("Claude API error: %s", exc)
                return "", conversation_history

            latency_ms = int((time.monotonic() - start) * 1000)
            logger.info(
                "Claude response: stop=%s, latency=%dms, tokens_in=%d, tokens_out=%d",
                response.stop_reason,
                latency_ms,
                response.usage.input_tokens,
                response.usage.output_tokens,
            )

            # Process response content blocks
            assistant_content: list[dict[str, Any]] = []
            tool_uses: list[dict[str, Any]] = []

            for block in response.content:
                if block.type == "text":
                    response_text += block.text
                    assistant_content.append(
                        {"type": "text", "text": block.text}
                    )
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
            conversation_history.append(
                {"role": "assistant", "content": assistant_content}
            )

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

                result = await self._tool_router.execute(
                    tool_use["name"], args
                )

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

            conversation_history.append(
                {"role": "user", "content": tool_results}
            )

            # If we hit the tool call limit, break
            if tool_call_count >= MAX_TOOL_CALLS_PER_TURN:
                logger.warning("Max tool calls reached (%d)", MAX_TOOL_CALLS_PER_TURN)
                break

        return response_text, conversation_history

    @staticmethod
    def _build_system_prompt(
        caller_phone: str | None = None,
        order_id: str | None = None,
    ) -> str:
        """Build system prompt with caller context appended."""
        parts = [SYSTEM_PROMPT]

        if caller_phone or order_id:
            parts.append("\n## Контекст дзвінка")
            if caller_phone:
                parts.append(f"- CallerID клієнта: {caller_phone}")
            if order_id:
                parts.append(f"- Поточне замовлення (чорновик): {order_id}")

        return "\n".join(parts)

    @staticmethod
    def prompt_version() -> str:
        """Return the current prompt version."""
        return PROMPT_VERSION
