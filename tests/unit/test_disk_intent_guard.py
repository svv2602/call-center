"""Wheels asked for, tyre tool called → `search_disks` runs in its place.

Goldset №2 (2026-09-28): «литі диски R16 на Шкоду Октавію» and «диски R13 на
Таврію» went to `get_vehicle_tire_sizes` / `search_tires` /
`search_knowledge_base` in all four runs. Goldset №3: a refusal hint was not
enough — the model went to the knowledge base instead. The substitution is
code, in both loops (streamed calls and the text path the sandbox and goldset
use), under the sales scope only, and at most once per turn. The arguments of
the substituted call are pinned in `test_disk_call_substitute.py`.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, create_autospec

import pytest

from scripts.configure_tenants import TVOYA_SHINA_CONFIG_PATCH
from src.agent.agent import LLMAgent, ToolRouter
from src.agent.disk_intent import DISK_ASK_DIAMETER, DiskSubstitution, has_disk_intent
from src.agent.network_policy import NetworkPolicy
from src.agent.streaming_loop import StreamingAgentLoop
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

OCTAVIA = "потрібні литі диски шістнадцятий радіус на Шкоду Октавію 2018"
TAVRIA = "диски тринадцятий радіус на Таврію є?"
MARK = "виконано search_disks"

_TOOLS = [
    {"name": name, "description": "", "input_schema": {"type": "object"}}
    for name in ("search_disks", "get_vehicle_tire_sizes", "search_tires", "search_knowledge_base")
]
_NO_DISKS_TOOL = [t for t in _TOOLS if t["name"] != "search_disks"]


def _policy(sales: bool) -> NetworkPolicy:
    return NetworkPolicy.from_tenant_config({**TVOYA_SHINA_CONFIG_PATCH, "sales_enabled": sales})


class _Calls:
    """What the tool router actually ran — real handlers, no mocks."""

    def __init__(self, router: ToolRouter) -> None:
        self.log: list[tuple[str, dict[str, Any]]] = []
        for tool in _TOOLS:
            router.register(tool["name"], self._handler(tool["name"]))

    def _handler(self, name: str) -> Any:
        async def _run(**kwargs: Any) -> dict[str, Any]:
            self.log.append((name, kwargs))
            return {"items": []}

        return _run

    def count(self, name: str) -> int:
        return sum(1 for n, _ in self.log if n == name)


def _tool_results(history: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for msg in history:
        content = msg.get("content")
        if msg.get("role") == "user" and isinstance(content, list):
            out.extend(str(p.get("content", "")) for p in content if p.get("type") == "tool_result")
    return out


def _marked(history: list[dict[str, Any]]) -> list[str]:
    return [r for r in _tool_results(history) if MARK in r]


# ── Predicate ────────────────────────────────────────────────────────


class TestHasDiskIntent:
    @pytest.mark.parametrize(
        "text",
        [
            OCTAVIA,
            TAVRIA,
            "а дисків на сімнадцять немає?",
            "штамповані на Ланос",
            "ковані диски",
            # Russian — a one-language vocabulary reads RU as silence.
            "нужны диски на октавию",
            "литые на шестнадцать есть?",
            "дисков нет в наличии?",
            "штампованные диски",
            "кованые",
            "литьё семнадцатое",
            "гальмівні диски і литі диски на Октавію",
        ],
    )
    def test_wheels_are_heard(self, text: str) -> None:
        assert has_disk_intent(text)

    @pytest.mark.parametrize(
        "text",
        [
            "гальмівні диски",
            "тормозной диск",
            "тормозные диски",
            "диск зчеплення",
            "диски сцепления",
            "шини 205/55 R16 на Октавію",
            "зимові шини на Таврію",
            "шины на дисках храните?",
            "шини під литі диски",
            "",
        ],
    )
    def test_not_wheels(self, text: str) -> None:
        assert not has_disk_intent(text)

    def test_model_facing_texts_carry_no_literal_numbers(self) -> None:
        # A number in a hint becomes an argument of the next tool call.
        for text in (DISK_ASK_DIAMETER, DiskSubstitution("search_tires", None).note):
            assert not any(ch.isdigit() for ch in text)


# ── Streaming loop (live calls) ──────────────────────────────────────


def _done(stop: str = "end_turn") -> StreamDone:
    return StreamDone(stop_reason=stop, usage=Usage(1, 1))


def _round(*calls: tuple[str, str]) -> list[Any]:
    events: list[Any] = []
    for tool_id, name in calls:
        events += [
            ToolCallStart(id=tool_id, name=name),
            ToolCallDelta(id=tool_id, arguments_chunk=json.dumps({"diameter": 16})),
            ToolCallEnd(id=tool_id),
        ]
    return [*events, _done("tool_use")]


def _text(text: str) -> list[Any]:
    return [TextDelta(text=text), _done()]


def _stream_turn(
    rounds: list[list[Any]],
    *,
    sales: bool = True,
    tools: list[dict[str, Any]] = _TOOLS,
    user_text: str = OCTAVIA,
) -> tuple[_Calls, list[dict[str, Any]]]:
    router = ToolRouter()
    calls = _Calls(router)
    loop = StreamingAgentLoop(
        llm_router=MockLLMRouter(rounds),
        tool_router=router,
        tts=MockTTSEngine(),
        conn=MockAudioSocketConnection(),
        barge_in_event=asyncio.Event(),
        tools=tools,
        system_prompt="Test system prompt",
        network_policy=_policy(sales),
    )
    history: list[dict[str, Any]] = []
    asyncio.run(loop.run_turn(user_text, history))
    return calls, history


class TestStreamingLoop:
    @pytest.mark.parametrize(
        "tool", ["get_vehicle_tire_sizes", "search_tires", "search_knowledge_base"]
    )
    def test_tyre_tool_is_replaced_by_search_disks(self, tool: str) -> None:
        calls, history = _stream_turn([_round(("t1", tool)), _text("Добре.")])
        assert calls.count(tool) == 0
        assert calls.count("search_disks") == 1
        [result] = _tool_results(history)
        assert f"замість {tool} {MARK}" in result

    def test_goldset_shape_model_may_still_call_search_disks(self) -> None:
        calls, history = _stream_turn(
            [_round(("t1", "get_vehicle_tire_sizes")), _round(("t2", "search_disks")), _text("Є.")],
            user_text=TAVRIA,
        )
        assert calls.count("get_vehicle_tire_sizes") == 0
        assert calls.count("search_disks") == 2
        assert MARK in _tool_results(history)[0]

    def test_second_tyre_call_in_the_turn_runs(self) -> None:
        # Loop-breaker: a substitution repeated each round leaves no way out.
        calls, history = _stream_turn(
            [_round(("t1", "search_tires")), _round(("t2", "search_tires")), _text("Ось.")]
        )
        assert calls.count("search_tires") == 1
        assert calls.count("search_disks") == 1
        assert len(_marked(history)) == 1

    def test_only_one_substitution_in_a_parallel_round(self) -> None:
        calls, history = _stream_turn(
            [
                _round(("t1", "get_vehicle_tire_sizes"), ("t2", "search_knowledge_base")),
                _text("Ок."),
            ]
        )
        assert len(_marked(history)) == 1
        assert calls.count("search_disks") == 1
        ran = calls.count("get_vehicle_tire_sizes") + calls.count("search_knowledge_base")
        assert ran == 1

    def test_tyre_tool_alongside_search_disks_runs(self) -> None:
        calls, history = _stream_turn(
            [_round(("t1", "search_disks"), ("t2", "get_vehicle_tire_sizes")), _text("Ок.")]
        )
        assert calls.count("get_vehicle_tire_sizes") == 1
        assert calls.count("search_disks") == 1
        assert _marked(history) == []

    def test_after_search_disks_tyre_tool_runs(self) -> None:
        calls, history = _stream_turn(
            [_round(("t1", "search_disks")), _round(("t2", "search_tires")), _text("Ок.")]
        )
        assert calls.count("search_tires") == 1
        assert _marked(history) == []

    def test_sales_off_nothing_changes(self) -> None:
        calls, history = _stream_turn([_round(("t1", "search_tires")), _text("Ок.")], sales=False)
        assert calls.count("search_tires") == 1
        assert calls.count("search_disks") == 0
        assert _marked(history) == []

    def test_without_search_disks_in_tools_nothing_changes(self) -> None:
        calls, _ = _stream_turn(
            [_round(("t1", "search_tires")), _text("Ок.")], tools=_NO_DISKS_TOOL
        )
        assert calls.count("search_tires") == 1
        assert calls.count("search_disks") == 0

    def test_tyre_question_is_not_redirected(self) -> None:
        calls, _ = _stream_turn(
            [_round(("t1", "search_tires")), _text("Ок.")], user_text="шини 205/55 R16"
        )
        assert calls.count("search_tires") == 1
        assert calls.count("search_disks") == 0

    def test_brake_disc_is_not_redirected(self) -> None:
        calls, _ = _stream_turn(
            [_round(("t1", "search_knowledge_base")), _text("Ок.")],
            user_text="тормозные диски меняете?",
        )
        assert calls.count("search_knowledge_base") == 1
        assert calls.count("search_disks") == 0


# ── Text path (sandbox, goldset) ─────────────────────────────────────


def _resp(*names: str, text: str = "") -> LLMResponse:
    return LLMResponse(
        text=text,
        tool_calls=[
            ToolCall(id=f"t{i}-{n}", name=n, arguments={"diameter": 16})
            for i, n in enumerate(names)
        ],
        stop_reason="tool_use" if names else "end_turn",
        usage=Usage(1, 1),
        provider="test",
    )


def _text_turn(
    responses: list[LLMResponse], *, sales: bool = True, user_text: str = OCTAVIA
) -> tuple[_Calls, list[dict[str, Any]]]:
    llm_router = create_autospec(LLMRouter, instance=True)
    llm_router.complete = AsyncMock(side_effect=responses)
    router = ToolRouter()
    calls = _Calls(router)
    agent = LLMAgent(
        api_key="test-key",
        system_prompt="base",
        llm_router=llm_router,
        tool_router=router,
        tools=_TOOLS,
        network_policy=_policy(sales),
    )
    _, history = asyncio.run(agent.process_message(user_text, []))
    return calls, history


class TestTextPath:
    def test_goldset_shape_tyre_tool_replaced_by_search_disks(self) -> None:
        calls, history = _text_turn(
            [_resp("get_vehicle_tire_sizes"), _resp(text="Є диски.")], user_text=TAVRIA
        )
        assert calls.count("get_vehicle_tire_sizes") == 0
        assert calls.count("search_disks") == 1
        assert f"замість get_vehicle_tire_sizes {MARK}" in _tool_results(history)[0]

    def test_second_tyre_call_in_the_turn_runs(self) -> None:
        calls, history = _text_turn(
            [_resp("search_knowledge_base"), _resp("search_knowledge_base"), _resp(text="Ось.")]
        )
        assert calls.count("search_knowledge_base") == 1
        assert calls.count("search_disks") == 1
        assert len(_marked(history)) == 1

    def test_tyre_tool_alongside_search_disks_runs(self) -> None:
        calls, _ = _text_turn([_resp("search_disks", "search_tires"), _resp(text="Ок.")])
        assert calls.count("search_tires") == 1
        assert calls.count("search_disks") == 1

    def test_sales_off_nothing_changes(self) -> None:
        calls, history = _text_turn([_resp("search_tires"), _resp(text="Ок.")], sales=False)
        assert calls.count("search_tires") == 1
        assert calls.count("search_disks") == 0
        assert _marked(history) == []
