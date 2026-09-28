"""LLMAgent (text path: sandbox, goldset) carries the relaxed-search caveat.

StreamingAgentLoop speaks the caveat of a relaxed ``search_tires`` answer by
code; LLMAgent is its text-mode twin and must put the same phrase first, and
pass the sales flag to the compressor, or the goldset never sees either.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, create_autospec

from scripts.configure_tenants import TVOYA_SHINA_CONFIG_PATCH
from src.agent.agent import LLMAgent, ToolRouter
from src.agent.network_policy import NetworkPolicy
from src.agent.tool_result_compressor import tire_caveat_phrase
from src.llm.models import LLMResponse, ToolCall, Usage
from src.llm.router import LLMRouter

_RELAXED = {
    "items": [
        {
            "id": "1",
            "brand": "Nokian",
            "model": "Hakkapeliitta R5",
            "size": "205/55 R16",
            "season": "winter",
            "price": 4500,
            "in_stock": True,
        },
    ],
    "relaxed": ["studded"],
    "caveat_key": "no_studded_offer_friction",
}
_ARGS = {"width": 205, "profile": 55, "diameter": 16, "season": "winter", "studded": True}


def _policy(sales: bool) -> NetworkPolicy:
    return NetworkPolicy.from_tenant_config({**TVOYA_SHINA_CONFIG_PATCH, "sales_enabled": sales})


def _run(policy: NetworkPolicy) -> tuple[str, list[dict[str, Any]]]:
    llm_router = create_autospec(LLMRouter, instance=True)
    llm_router.complete = AsyncMock(
        side_effect=[
            LLMResponse(
                text="",
                tool_calls=[ToolCall(id="t1", name="search_tires", arguments=_ARGS)],
                stop_reason="tool_use",
                usage=Usage(10, 5),
                provider="test",
            ),
            LLMResponse(text="Є Nokian Hakkapeliitta R5.", usage=Usage(10, 5), provider="test"),
        ]
    )
    router = ToolRouter()

    async def _search(**_: Any) -> dict[str, Any]:
        return dict(_RELAXED)

    router.register("search_tires", _search)
    agent = LLMAgent(
        api_key="test-key",
        system_prompt="base",
        llm_router=llm_router,
        tool_router=router,
        tools=[{"name": "search_tires", "description": "", "input_schema": {"type": "object"}}],
        network_policy=policy,
    )
    return asyncio.run(agent.process_message("шиповані 205/55 R16", []))


def _tool_result_text(history: list[dict[str, Any]]) -> str:
    for msg in history:
        if msg["role"] == "user" and isinstance(msg["content"], list):
            return str(msg["content"][0]["content"])
    raise AssertionError("no tool_result in history")


def test_sales_on_reply_starts_with_the_caveat() -> None:
    text, _ = _run(_policy(True))
    phrase = tire_caveat_phrase(_RELAXED, _ARGS)
    assert phrase
    assert text.startswith(phrase)
    assert "Nokian" in text


def test_sales_on_tool_result_keeps_the_caveat_key() -> None:
    _, history = _run(_policy(True))
    assert "no_studded_offer_friction" in _tool_result_text(history)


def test_sales_off_reply_has_no_caveat() -> None:
    text, history = _run(_policy(False))
    assert text == "Є Nokian Hakkapeliitta R5."
    assert "no_studded_offer_friction" not in _tool_result_text(history)
