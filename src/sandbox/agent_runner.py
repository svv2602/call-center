"""Sandbox agent factory and turn processor.

Creates a lightweight LLMAgent for sandbox testing. Supports two modes:
- Direct Anthropic API (default, no LLM router)
- LLM Router with provider_override (when router + provider key provided)

Captures tool calls and token metrics per turn.
"""

from __future__ import annotations

import copy
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.agent.agent import LLMAgent
from src.agent.prompt_manager import (
    PromptManager,
    format_few_shot_section,
    format_safety_rules_section,
    get_few_shot_examples,
    get_pronunciation_rules,
    get_safety_rules_for_prompt,
    inject_pronunciation_rules,
)
from src.agent.tool_loader import get_tools_with_overrides
from src.config import get_settings
from src.sandbox.mock_tools import build_mock_tool_router

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncEngine

    from src.llm.router import LLMRouter

logger = logging.getLogger(__name__)


@dataclass
class ToolCallRecord:
    """Record of a single tool call during a sandbox turn."""

    tool_name: str
    tool_args: dict[str, Any]
    tool_result: Any
    duration_ms: int
    is_mock: bool


@dataclass
class SandboxTurnResult:
    """Result of processing a single sandbox turn."""

    response_text: str
    updated_history: list[dict[str, Any]]
    latency_ms: int
    input_tokens: int
    output_tokens: int
    model: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)


async def create_sandbox_agent(
    engine: AsyncEngine,
    prompt_version_id: UUID | None = None,
    tool_mode: str = "mock",
    model: str | None = None,
    llm_router: LLMRouter | None = None,
    provider_override: str | None = None,
    redis: Any | None = None,
) -> LLMAgent:
    """Create an LLMAgent configured for sandbox testing.

    Args:
        engine: Database engine for loading prompts and tool overrides.
        prompt_version_id: Specific prompt version to use (None = active).
        tool_mode: 'mock' for static responses, 'live' for real Store API.
        model: LLM model ID to use (None = default from settings).
        llm_router: Optional LLM router for multi-provider routing.
        provider_override: If set with llm_router, routes all calls to
            this specific provider (e.g. "gemini-flash").
        redis: Optional Redis client for loading pronunciation rules.

    Returns:
        Configured LLMAgent instance.
    """
    settings = get_settings()
    pm = PromptManager(engine)

    # Load prompt
    system_prompt = None
    prompt_version_name = None

    if prompt_version_id is not None:
        version = await pm.get_version(prompt_version_id)
        if version:
            system_prompt = version["system_prompt"]
            prompt_version_name = version["name"]
    else:
        active = await pm.get_active_prompt()
        if active.get("id") is not None:
            system_prompt = active["system_prompt"]
            prompt_version_name = active["name"]

    # Load tools with DB overrides
    tools = await get_tools_with_overrides(engine)

    # Build tool router
    if tool_mode == "mock":
        tool_router = build_mock_tool_router()
    else:
        # Live mode: create a minimal StoreClient-backed router
        # For now, fall back to mock if Store API not available
        logger.warning("Live tool mode requested but using mock fallback in sandbox")
        tool_router = build_mock_tool_router()

    # Inject pronunciation rules from Redis
    if redis is not None and system_prompt is not None:
        pron_rules = await get_pronunciation_rules(redis)
        system_prompt = inject_pronunciation_rules(system_prompt, pron_rules)

    # Load few-shot examples and safety rules
    few_shot_context = None
    safety_context = None
    try:
        few_shot_examples = await get_few_shot_examples(engine, redis)
        few_shot_context = format_few_shot_section(few_shot_examples)
    except Exception:
        logger.debug("Sandbox: few-shot loading failed", exc_info=True)
    try:
        safety_rules = await get_safety_rules_for_prompt(engine, redis)
        safety_context = format_safety_rules_section(safety_rules)
    except Exception:
        logger.debug("Sandbox: safety rules loading failed", exc_info=True)

    # Sandbox defaults to Haiku (cheap/fast) independently of ANTHROPIC_MODEL
    # which may be set to a more expensive model for production calls.
    sandbox_default_model = "claude-haiku-4-5-20251001"

    return LLMAgent(
        api_key=settings.anthropic.api_key,
        model=model or sandbox_default_model,
        tool_router=tool_router,
        tools=tools,
        llm_router=llm_router if provider_override else None,
        system_prompt=system_prompt,
        prompt_version_name=prompt_version_name,
        provider_override=provider_override,
        few_shot_context=few_shot_context,
        safety_context=safety_context,
    )


async def process_sandbox_turn(
    agent: LLMAgent,
    user_text: str,
    history: list[dict[str, Any]],
    is_mock: bool = True,
    pattern_context: str | None = None,
) -> SandboxTurnResult:
    """Process a single sandbox turn, capturing metrics and tool calls.

    Args:
        agent: The sandbox LLMAgent.
        user_text: Customer message text.
        history: Conversation history (Anthropic format). Will be copied.
        is_mock: Whether tools are in mock mode.
        pattern_context: Optional pattern injection text for system prompt.

    Returns:
        SandboxTurnResult with response, updated history, and metrics.
    """
    history_copy = copy.deepcopy(history)
    tool_calls_log: list[ToolCallRecord] = []

    # Wrap tool router to capture calls
    original_execute = agent.tool_router.execute

    async def _capturing_execute(name: str, args: dict[str, Any]) -> Any:
        start = time.monotonic()
        result = await original_execute(name, args)
        duration_ms = int((time.monotonic() - start) * 1000)
        tool_calls_log.append(
            ToolCallRecord(
                tool_name=name,
                tool_args=copy.deepcopy(args),
                tool_result=copy.deepcopy(result),
                duration_ms=duration_ms,
                is_mock=is_mock,
            )
        )
        return result

    agent.tool_router.execute = _capturing_execute  # type: ignore[assignment]

    try:
        start = time.monotonic()
        response_text, updated_history = await agent.process_message(
            user_text, history_copy, pattern_context=pattern_context,
        )
        latency_ms = int((time.monotonic() - start) * 1000)
    finally:
        # Restore original execute
        agent.tool_router.execute = original_execute  # type: ignore[assignment]

    # Estimate token counts from history changes
    # The real counts come from the Claude API response, but we approximate here
    input_tokens = 0
    output_tokens = 0
    # Check last assistant message in updated history for rough estimate
    for msg in reversed(updated_history):
        if msg.get("role") == "assistant":
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        output_tokens += len(block.get("text", "")) // 4
            break

    input_tokens = len(str(history_copy)) // 4 + len(user_text) // 4

    return SandboxTurnResult(
        response_text=response_text,
        updated_history=updated_history,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=agent._model,
        tool_calls=tool_calls_log,
    )
