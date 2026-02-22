"""Sandbox agent factory and turn processor.

Creates a lightweight LLMAgent for sandbox testing. Supports two modes:
- Direct Anthropic API (default, no LLM router)
- LLM Router with provider_override (when router + provider key provided)

Captures tool calls and token metrics per turn.
"""

from __future__ import annotations

import contextlib
import copy
import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.agent.agent import LLMAgent
from src.agent.prompt_manager import (
    PromptManager,
    fetch_tenant_promotions,
    format_few_shot_section,
    format_promotions_context,
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

    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncEngine

    from src.agent.agent import ToolRouter
    from src.llm.router import LLMRouter
    from src.onec_client.client import OneCClient

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
    error: str | None = None


def _register_live_tools(
    router: ToolRouter,
    onec_client: OneCClient | None = None,
    redis_client: Redis | None = None,
    network: str = "ProKoleso",
    knowledge_search: Any = None,
    tenant_id: str = "",
    store_client: Any = None,
) -> None:
    """Override mock handlers with real handlers where available."""
    # Register real knowledge search (pgvector) if available
    if knowledge_search is not None:

        async def _search_kb(**kwargs: Any) -> dict[str, Any]:
            results = await knowledge_search.search(
                query=kwargs.get("query", ""),
                category=kwargs.get("category", ""),
                tenant_id=tenant_id,
            )
            return {"total": len(results), "articles": results}

        router.register("search_knowledge_base", _search_kb)
        logger.info("Live tool registered: search_knowledge_base (pgvector)")

    # Register StoreClient-based tools
    if store_client is not None:
        router.register("get_vehicle_tire_sizes", store_client.get_vehicle_tire_sizes)

        async def _search_tires(**params: Any) -> dict[str, Any]:
            return await store_client.search_tires(network=network, **params)

        async def _check_availability(
            product_id: str = "", query: str = "", **kw: Any
        ) -> dict[str, Any]:
            return await store_client.check_availability(product_id, query, network=network, **kw)

        router.register("search_tires", _search_tires)
        router.register("check_availability", _check_availability)
        router.register("get_order_status", store_client.search_orders)
        router.register("create_order_draft", store_client.create_order)
        router.register("update_order_delivery", store_client.update_delivery)
        router.register("confirm_order", store_client.confirm_order)
        router.register("get_fitting_stations", store_client.get_fitting_stations)
        router.register("get_fitting_slots", store_client.get_fitting_slots)
        router.register("book_fitting", store_client.book_fitting)
        logger.info("Live tools registered: 10 StoreClient tools (network=%s)", network)
    else:
        logger.warning("Live tool mode: no StoreClient — Store tools remain mock")

    if onec_client is not None:

        async def _get_pickup_points(city: str = "") -> dict[str, Any]:
            cache_key = f"onec:points:{network}"

            # 1. Redis cache
            if redis_client:
                try:
                    raw = await redis_client.get(cache_key)
                    if raw:
                        all_points = json.loads(raw if isinstance(raw, str) else raw.decode())
                        if city:
                            all_points = [
                                p
                                for p in all_points
                                if city.lower() in p.get("city", "").lower()
                            ]
                        return {"total": len(all_points), "points": all_points[:15]}
                except Exception:
                    pass

            # 2. 1C API
            data = await onec_client.get_pickup_points(network)
            raw_points = data.get("data", [])
            all_points = [
                {
                    "id": p.get("id", ""),
                    "address": p.get("point", ""),
                    "type": p.get("point_type", ""),
                    "city": p.get("City", ""),
                }
                for p in raw_points
            ]
            if redis_client:
                with contextlib.suppress(Exception):
                    await redis_client.setex(
                        cache_key, 3600, json.dumps(all_points, ensure_ascii=False)
                    )
            if city:
                all_points = [
                    p for p in all_points if city.lower() in p.get("city", "").lower()
                ]
            return {"total": len(all_points), "points": all_points[:15]}

        router.register("get_pickup_points", _get_pickup_points)
        logger.info("Live tool registered: get_pickup_points (network=%s)", network)
    else:
        logger.info("Live tool mode: no OneCClient — get_pickup_points remains mock")


async def create_sandbox_agent(
    engine: AsyncEngine,
    prompt_version_id: UUID | None = None,
    tool_mode: str = "mock",
    model: str | None = None,
    llm_router: LLMRouter | None = None,
    provider_override: str | None = None,
    redis: Any | None = None,
    onec_client: OneCClient | None = None,
    redis_client: Redis | None = None,
    tenant: dict[str, Any] | None = None,
    knowledge_search: Any = None,
    tenant_id: str = "",
    store_client: Any = None,
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
        onec_client: Optional 1C client for live tool mode.
        redis_client: Optional Redis client for tool caching.
        tenant: Optional tenant dict with enabled_tools/prompt_suffix.
        knowledge_search: Optional KnowledgeSearch for pgvector-backed search.

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
    tools = await get_tools_with_overrides(engine, redis=redis)

    # Build tool router
    if tool_mode == "mock":
        tool_router = build_mock_tool_router()
    else:
        # Live mode: start with mock, overlay real handlers where available
        tool_router = build_mock_tool_router()
        network = (tenant or {}).get("network_id") or "ProKoleso"
        _register_live_tools(
            tool_router,
            onec_client=onec_client,
            redis_client=redis_client,
            network=network,
            knowledge_search=knowledge_search,
            tenant_id=tenant_id,
            store_client=store_client,
        )

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

    # Load tenant promotions into prompt context
    promotions_context = None
    if tenant_id:
        promos = await fetch_tenant_promotions(engine, tenant_id, redis=redis)
        promotions_context = format_promotions_context(promos)

    # Apply tenant overrides (same logic as src/main.py)
    if tenant:
        if tenant.get("enabled_tools"):
            allowed = set(tenant["enabled_tools"])
            if tools:
                tools = [t for t in tools if t["name"] in allowed]
        if tenant.get("prompt_suffix") and system_prompt:
            system_prompt = system_prompt + "\n\n" + tenant["prompt_suffix"]

    # Resolve sandbox default model from Redis config → agent primary → hardcode
    sandbox_default_model = "claude-haiku-4-5-20251001"
    if redis_client is not None:
        try:
            from src.llm.router import REDIS_CONFIG_KEY

            raw = await redis_client.get(REDIS_CONFIG_KEY)
            if raw:
                import json as _json

                llm_cfg = _json.loads(raw if isinstance(raw, str) else raw.decode())
                cfg_model = (llm_cfg.get("sandbox") or {}).get("default_model", "")
                if cfg_model:
                    sandbox_default_model = cfg_model
                elif llm_cfg.get("tasks", {}).get("agent", {}).get("primary"):
                    sandbox_default_model = llm_cfg["tasks"]["agent"]["primary"]
        except Exception:
            logger.debug("Failed to read sandbox default model from Redis", exc_info=True)

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
        promotions_context=promotions_context,
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

    agent.tool_router.execute = _capturing_execute  # type: ignore[method-assign]

    try:
        start = time.monotonic()
        response_text, updated_history = await agent.process_message(
            user_text,
            history_copy,
            pattern_context=pattern_context,
        )
        latency_ms = int((time.monotonic() - start) * 1000)
    finally:
        # Restore original execute
        agent.tool_router.execute = original_execute  # type: ignore[method-assign]

    # Use real token counts from the Claude API response (accumulated across tool rounds)
    input_tokens = agent.last_input_tokens
    output_tokens = agent.last_output_tokens

    return SandboxTurnResult(
        response_text=response_text,
        updated_history=updated_history,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=agent._model,
        tool_calls=tool_calls_log,
        error=agent.last_error,
    )
