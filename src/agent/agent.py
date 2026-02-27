"""LLM agent: Claude API with tool calling.

Manages conversation flow, tool routing, and context window.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any

import anthropic

from src.agent.history_compressor import summarize_old_messages
from src.agent.prompts import (
    ERROR_TEXT,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_system_prompt_with_context,
)
from src.agent.tool_result_compressor import compress_tool_result
from src.agent.tools import ALL_TOOLS, filter_tools_by_state

if TYPE_CHECKING:
    from src.llm.router import LLMRouter
    from src.logging.pii_vault import PIIVault

logger = logging.getLogger(__name__)

# Limits
MAX_TOOL_CALLS_PER_TURN = 5
MAX_HISTORY_MESSAGES = 40
_TOOL_TIMEOUT_SEC = 15  # Per-tool execution timeout


class ToolRouter:
    """Routes tool_use calls to concrete implementations.

    Tool handlers are registered as async callables.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, Any] = {}
        self._on_execute: Any = (
            None  # optional async callback(name, args, result, duration_ms, success)
        )

    def set_execute_hook(self, callback: Any) -> None:
        """Set an async callback invoked after each tool execution."""
        self._on_execute = callback

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
            if self._on_execute is not None:
                with contextlib.suppress(Exception):
                    await self._on_execute(name, args, result, duration_ms, True)
            return result
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.exception("Tool %s failed after %dms", name, duration_ms)
            if self._on_execute is not None:
                with contextlib.suppress(Exception):
                    await self._on_execute(name, args, {"error": str(exc)}, duration_ms, False)
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
        few_shot_context: str | None = None,
        safety_context: str | None = None,
        promotions_context: str | None = None,
        is_modular: bool = False,
        agent_name: str | None = None,
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
        self._few_shot_context = few_shot_context
        self._safety_context = safety_context
        self._promotions_context = promotions_context
        self._is_modular = is_modular
        self._agent_name = agent_name
        # Accumulated usage from last process_message call (all LLM rounds)
        self.last_input_tokens: int = 0
        self.last_output_tokens: int = 0
        self.last_provider_key: str = ""
        # Last error message (if LLM call failed) — consumed by sandbox
        self.last_error: str | None = None

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
        order_stage: str | None = None,
        caller_history: str | None = None,
        storage_context: str | None = None,
        customer_profile: str | None = None,
        fitting_booked: bool = False,
        tools_called: set[str] | None = None,
        scenario: str | None = None,
        active_scenarios: set[str] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Process a user message and return the agent's text response.

        Args:
            user_text: The user's transcribed speech.
            conversation_history: List of previous messages (mutated in place).
            caller_phone: CallerID phone number (if available).
            order_id: Current order draft ID (if in progress).
            pattern_context: Optional pattern injection text for system prompt.
            order_stage: Current order stage (None, "draft", "delivery_set", "confirmed").
            caller_history: Formatted caller history section.
            storage_context: Formatted storage contracts section.
            customer_profile: Formatted customer profile section.
            fitting_booked: Whether a fitting has already been booked this call.
            tools_called: Set of tool names invoked during this call (for module expansion).
            scenario: Current IVR scenario (for module expansion).
            active_scenarios: All detected scenarios (for topic switching).

        Returns:
            Tuple of (response_text, updated_conversation_history).
        """
        # Mask PII before sending to LLM
        if self._pii_vault is not None:
            user_text = self._pii_vault.mask(user_text)

        # Add user message (skip if already present — pipeline may pre-add)
        if not (
            conversation_history
            and conversation_history[-1]["role"] == "user"
            and conversation_history[-1].get("content") == user_text
        ):
            conversation_history.append({"role": "user", "content": user_text})

        # Claude API requires first message to be user role.
        # The greeting (assistant) may be first — prepend a synthetic turn.
        if conversation_history and conversation_history[0]["role"] != "user":
            conversation_history.insert(0, {"role": "user", "content": "(початок дзвінка)"})

        # Trim history if too long
        if len(conversation_history) > MAX_HISTORY_MESSAGES:
            # Keep first message (context) + recent messages
            conversation_history[:] = (
                conversation_history[:1] + conversation_history[-(MAX_HISTORY_MESSAGES - 1) :]
            )

        # Compress/summarize old messages to save tokens
        conversation_history[:] = summarize_old_messages(conversation_history)

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
            self._tools, order_stage=order_stage, fitting_booked=fitting_booked
        )

        response_text = ""
        tool_call_count = 0
        self.last_input_tokens = 0
        self.last_output_tokens = 0
        self.last_provider_key = ""
        self.last_error = None

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
                        tools=tools,
                        max_tokens=1024,
                        provider_override=self._provider_override,
                    )

                    latency_ms = int((time.monotonic() - start) * 1000)
                    self.last_input_tokens += llm_response.usage.input_tokens
                    self.last_output_tokens += llm_response.usage.output_tokens
                    self.last_provider_key = llm_response.provider
                    logger.info(
                        "LLM response: provider=%s, stop=%s, latency=%dms, tokens_in=%d, tokens_out=%d",
                        llm_response.provider,
                        llm_response.stop_reason,
                        latency_ms,
                        llm_response.usage.input_tokens,
                        llm_response.usage.output_tokens,
                    )

                    if llm_response.text:
                        if response_text:
                            response_text += "\n\n"
                        response_text += llm_response.text

                    # Convert LLMResponse to Anthropic content blocks for history
                    assistant_content = llm_response_to_anthropic_blocks(llm_response)
                    for tc in llm_response.tool_calls:
                        tool_uses.append(
                            {
                                "id": tc.id,
                                "name": tc.name,
                                "input": tc.arguments,
                            }
                        )
                except Exception as exc:
                    logger.exception("LLM router error: %s", exc)
                    self.last_error = f"LLM router: {exc}"
                    return ERROR_TEXT, conversation_history
            else:
                # Legacy path: direct Anthropic SDK
                try:
                    response = await self._client.messages.create(
                        model=self._model,
                        max_tokens=1024,
                        system=system,
                        tools=tools,  # type: ignore[arg-type]
                        messages=conversation_history,  # type: ignore[arg-type]
                    )
                except anthropic.APIStatusError as exc:
                    logger.exception("Claude API error: %s", exc)
                    self.last_error = f"Claude API: {exc.status_code} {exc.message}"
                    return ERROR_TEXT, conversation_history

                latency_ms = int((time.monotonic() - start) * 1000)
                self.last_input_tokens += response.usage.input_tokens
                self.last_output_tokens += response.usage.output_tokens
                logger.info(
                    "Claude response: stop=%s, latency=%dms, tokens_in=%d, tokens_out=%d",
                    response.stop_reason,
                    latency_ms,
                    response.usage.input_tokens,
                    response.usage.output_tokens,
                )

                for block in response.content:
                    if block.type == "text":
                        if response_text:
                            response_text += "\n\n"
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

            # Deduplicate tool calls (same name + same args → skip)
            seen_keys: set[str] = set()
            unique_tool_uses: list[dict[str, Any]] = []
            for tu in tool_uses:
                dedup_key = tu["name"] + ":" + json.dumps(tu["input"], sort_keys=True)
                if dedup_key in seen_keys:
                    logger.warning(
                        "Skipping duplicate tool call: %s(%s)",
                        tu["name"],
                        json.dumps(tu["input"], ensure_ascii=False)[:200],
                    )
                    continue
                seen_keys.add(dedup_key)
                unique_tool_uses.append(tu)

            # Execute tool calls in parallel (with per-tool timeout)
            async def _execute_one(tu: dict[str, Any]) -> dict[str, Any]:
                args = tu["input"]
                if self._pii_vault is not None:
                    args = self._pii_vault.restore_in_args(args)
                try:
                    raw = await asyncio.wait_for(
                        self._tool_router.execute(tu["name"], args),
                        timeout=_TOOL_TIMEOUT_SEC,
                    )
                except TimeoutError:
                    logger.error(
                        "Tool %s timed out after %ds", tu["name"], _TOOL_TIMEOUT_SEC
                    )
                    raw = {"error": "Сервіс тимчасово не відповідає, спробуйте ще раз"}
                content = compress_tool_result(tu["name"], raw)
                if self._pii_vault is not None:
                    content = self._pii_vault.mask(content)
                return {"type": "tool_result", "tool_use_id": tu["id"], "content": content}

            tool_results = list(await asyncio.gather(*[_execute_one(tu) for tu in unique_tool_uses]))
            tool_call_count += len(tool_results)

            conversation_history.append({"role": "user", "content": tool_results})

            # If we hit the tool call limit, break
            if tool_call_count >= MAX_TOOL_CALLS_PER_TURN:
                logger.warning("Max tool calls reached (%d)", MAX_TOOL_CALLS_PER_TURN)
                break

        return response_text, conversation_history

    @property
    def prompt_version_name(self) -> str:
        """Return the current prompt version name."""
        return self._prompt_version_name
