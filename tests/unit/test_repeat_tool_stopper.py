"""A call this turn already ran with the same arguments is not run again (sales).

Goldset №4 `pickup_both_networks` [ПК]: five identical `get_vehicle_tire_sizes`
in one turn, then the summary fallback. Refused repeats were already stopped
in the streaming loop (`refused_this_turn`); successful ones were re-run every
round in both loops. Sales scope only — the live fitting path is untouched.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, create_autospec

import pytest

from scripts.configure_tenants import PROKOLESO_CONFIG_PATCH
from src.agent.agent import LLMAgent, ToolRouter
from src.agent.network_policy import NetworkPolicy
from src.agent.streaming_loop import StreamingAgentLoop
from src.agent.tool_result_compressor import repeat_call_note
from src.llm.models import (
    LLMResponse,
    StreamDone,
    TextDelta,
    ToolCall,
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
    Usage,
)
from src.llm.router import LLMRouter
from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection
from tests.unit.mocks.mock_llm_router import MockLLMRouter
from tests.unit.mocks.mock_tts import MockTTSEngine

TOOL = "get_vehicle_tire_sizes"
ARGS = {"brand": "Skoda", "model": "Octavia", "year": 2018}
OTHER = {"brand": "Skoda", "model": "Fabia", "year": 2018}
ANSWER = "Для Октавії заводський розмір 205/55 R16."
TOOLS = [{"name": TOOL, "description": "x", "input_schema": {"type": "object"}}]


def _policy(sales: bool) -> NetworkPolicy:
    return NetworkPolicy.from_tenant_config({**PROKOLESO_CONFIG_PATCH, "sales_enabled": sales})


def _router(result: dict[str, Any] | None = None) -> tuple[ToolRouter, list[dict[str, Any]]]:
    router = ToolRouter()
    ran: list[dict[str, Any]] = []

    async def _sizes(**kwargs: Any) -> dict[str, Any]:
        ran.append(kwargs)
        return result if result is not None else {"sizes": ["205/55 R16"]}

    router.register(TOOL, _sizes)
    return router, ran


def _text(calls: list[dict[str, Any]], *, sales: bool = True, result: Any = None):
    replies = [
        LLMResponse(
            text="",
            tool_calls=[ToolCall(id=f"t{i}", name=TOOL, arguments=args)],
            stop_reason="tool_use",
            usage=Usage(1, 1),
            provider="test",
        )
        for i, args in enumerate(calls)
    ] + [LLMResponse(text=ANSWER, usage=Usage(1, 1), provider="test")]
    llm_router = create_autospec(LLMRouter, instance=True)
    llm_router.complete = AsyncMock(side_effect=replies)
    router, ran = _router(result)
    agent = LLMAgent(
        api_key="test-key",
        system_prompt="base",
        llm_router=llm_router,
        tool_router=router,
        tools=TOOLS,
        network_policy=_policy(sales),
    )
    _, history = asyncio.run(agent.process_message("Шкода Октавія 2018", []))
    return ran, history


def _stream(calls: list[dict[str, Any]], *, sales: bool = True, result: Any = None):
    rounds = [
        [
            ToolCallStart(id=f"s{i}", name=TOOL),
            ToolCallDelta(id=f"s{i}", arguments_chunk=json.dumps(args)),
            ToolCallEnd(id=f"s{i}"),
            StreamDone(stop_reason="tool_use", usage=Usage(1, 1)),
        ]
        for i, args in enumerate(calls)
    ] + [[TextDelta(text=ANSWER), StreamDone(stop_reason="end_turn", usage=Usage(1, 1))]]
    router, ran = _router(result)
    loop = StreamingAgentLoop(
        llm_router=MockLLMRouter(rounds),
        tool_router=router,
        tts=MockTTSEngine(),
        conn=MockAudioSocketConnection(),
        barge_in_event=asyncio.Event(),
        tools=TOOLS,
        system_prompt="Test system prompt",
        network_policy=_policy(sales),
    )
    history: list[dict[str, Any]] = []
    asyncio.run(loop.run_turn("Шкода Октавія 2018", history))
    return ran, history


LOOPS = pytest.mark.parametrize("run", [_text, _stream], ids=["text", "streaming"])


def _tool_results(history: list[dict[str, Any]]) -> list[str]:
    out = []
    for msg in history:
        content = msg.get("content")
        if isinstance(content, list):
            out += [
                str(b.get("content"))
                for b in content
                if isinstance(b, dict) and b.get("type") == "tool_result"
            ]
    return out


@LOOPS
def test_the_same_call_runs_once(run: Any) -> None:
    ran, history = run([ARGS, ARGS])
    assert ran == [ARGS]
    assert repeat_call_note(TOOL) in _tool_results(history)


@LOOPS
def test_other_arguments_run(run: Any) -> None:
    ran, _ = run([ARGS, OTHER])
    assert ran == [ARGS, OTHER]


@LOOPS
def test_a_failed_call_may_be_retried(run: Any) -> None:
    # an error is not «done»: a retry after a transient failure is legitimate
    ran, _ = run([ARGS, ARGS], result={"error": "Сервіс тимчасово не відповідає"})
    assert ran == [ARGS, ARGS]


@LOOPS
def test_sales_off_is_unchanged(run: Any) -> None:
    ran, history = run([ARGS, ARGS], sales=False)
    assert ran == [ARGS, ARGS]
    assert repeat_call_note(TOOL) not in _tool_results(history)
