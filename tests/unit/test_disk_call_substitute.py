"""The substituted `search_disks` call: its arguments, its audit row, its result.

Goldset №3 (2026-09-28, `disk_*`): after the refusal hint the model went to the
knowledge base and answered «потрібні розболтовка, виліт…» without calling
`search_disks`. Now the tyre-side call is replaced by `search_disks` in both
loops — diameter from the caller's words or the call, the car only from
`get_vehicle_tire_sizes` arguments, and no diameter → a question, never a
`search_disks` call with none.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, create_autospec

import pytest

from scripts.configure_tenants import TVOYA_SHINA_CONFIG_PATCH
from src.agent.agent import LLMAgent, ToolRouter
from src.agent.disk_intent import DISK_ASK_DIAMETER, disk_diameter, disk_vehicle
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

OCTAVIA = "потрібні литі диски шістнадцятий радіус на Шкоду Октавію"
TAVRIA = "диски тринадцятий радіус на Таврію є?"
NO_DIAMETER = "потрібні литі диски на Октавію"
DISK_ITEM = "Disk-Model-From-Handler"

_TOOL_NAMES = ("search_disks", "get_vehicle_tire_sizes", "search_tires", "search_knowledge_base")
_TOOLS = [{"name": n, "description": "", "input_schema": {"type": "object"}} for n in _TOOL_NAMES]

_CAR_ARGS = {"brand": "Skoda", "model": "Octavia", "year": 2018}


def _policy(sales: bool) -> NetworkPolicy:
    return NetworkPolicy.from_tenant_config({**TVOYA_SHINA_CONFIG_PATCH, "sales_enabled": sales})


class _Router:
    """A real ToolRouter with recording handlers and a recording audit hook."""

    def __init__(self) -> None:
        self.router = ToolRouter()
        self.ran: list[tuple[str, dict[str, Any]]] = []
        self.audited: list[tuple[str, dict[str, Any]]] = []
        for name in _TOOL_NAMES:
            self.router.register(name, self._handler(name))

        async def _hook(name: str, args: dict[str, Any], *_: Any) -> None:
            self.audited.append((name, dict(args)))

        self.router.set_execute_hook(_hook)

    def _handler(self, name: str) -> Any:
        async def _run(**kwargs: Any) -> dict[str, Any]:
            self.ran.append((name, kwargs))
            if name == "search_disks":
                return {"total": 1, "items": [{"model": DISK_ITEM}]}
            return {"items": []}

        return _run

    def names(self) -> list[str]:
        return [n for n, _ in self.ran]


def _results(history: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for msg in history:
        content = msg.get("content")
        if msg.get("role") == "user" and isinstance(content, list):
            out.extend(str(p.get("content", "")) for p in content if p.get("type") == "tool_result")
    return out


def _stream(user_text: str, calls: list[list[tuple[str, dict[str, Any]]]], *, sales: bool = True):
    rounds: list[list[Any]] = []
    for i, round_calls in enumerate(calls):
        events: list[Any] = []
        for j, (name, args) in enumerate(round_calls):
            tid = f"s{i}{j}"
            events += [
                ToolCallStart(id=tid, name=name),
                ToolCallDelta(id=tid, arguments_chunk=json.dumps(args)),
                ToolCallEnd(id=tid),
            ]
        rounds.append([*events, StreamDone(stop_reason="tool_use", usage=Usage(1, 1))])
    rounds.append([TextDelta(text="Ок."), StreamDone(stop_reason="end_turn", usage=Usage(1, 1))])
    rec = _Router()
    loop = StreamingAgentLoop(
        llm_router=MockLLMRouter(rounds),
        tool_router=rec.router,
        tts=MockTTSEngine(),
        conn=MockAudioSocketConnection(),
        barge_in_event=asyncio.Event(),
        tools=_TOOLS,
        system_prompt="Test system prompt",
        network_policy=_policy(sales),
    )
    history: list[dict[str, Any]] = []
    asyncio.run(loop.run_turn(user_text, history))
    return rec, history


def _text(user_text: str, calls: list[list[tuple[str, dict[str, Any]]]], *, sales: bool = True):
    responses = [
        LLMResponse(
            text="",
            tool_calls=[
                ToolCall(id=f"t{i}{j}", name=name, arguments=args)
                for j, (name, args) in enumerate(round_calls)
            ],
            stop_reason="tool_use",
            usage=Usage(1, 1),
            provider="test",
        )
        for i, round_calls in enumerate(calls)
    ]
    responses.append(LLMResponse(text="Ок.", usage=Usage(1, 1), provider="test"))
    llm_router = create_autospec(LLMRouter, instance=True)
    llm_router.complete = AsyncMock(side_effect=responses)
    rec = _Router()
    agent = LLMAgent(
        api_key="test-key",
        system_prompt="base",
        llm_router=llm_router,
        tool_router=rec.router,
        tools=_TOOLS,
        network_policy=_policy(sales),
    )
    _, history = asyncio.run(agent.process_message(user_text, []))
    return rec, history


LOOPS = pytest.mark.parametrize("run", [_stream, _text], ids=["streaming", "text"])


# ── Both loops: what runs instead ────────────────────────────────────


@LOOPS
def test_car_from_vehicle_sizes_call_and_diameter_from_words(run: Any) -> None:
    rec, history = run(OCTAVIA, [[("get_vehicle_tire_sizes", _CAR_ARGS)]])
    assert rec.ran == [("search_disks", {"diameter": 16, "vehicle": _CAR_ARGS})]
    [result] = _results(history)
    assert "замість get_vehicle_tire_sizes виконано search_disks" in result
    assert DISK_ITEM in result


@LOOPS
def test_knowledge_base_call_gets_diameter_from_words_and_no_car(run: Any) -> None:
    rec, history = run(TAVRIA, [[("search_knowledge_base", {"query": "диски на Таврію"})]])
    assert rec.ran == [("search_disks", {"diameter": 13})]
    assert DISK_ITEM in _results(history)[0]


@LOOPS
def test_substituted_call_is_audited_as_search_disks(run: Any) -> None:
    rec, _ = run(TAVRIA, [[("search_tires", {"diameter": 13})]])
    assert rec.audited == [("search_disks", {"diameter": 13})]


@LOOPS
def test_no_diameter_anywhere_asks_and_runs_nothing(run: Any) -> None:
    rec, history = run(NO_DIAMETER, [[("search_knowledge_base", {"query": "диски"})]])
    assert rec.ran == []
    assert rec.audited == []
    [result] = _results(history)
    assert DISK_ASK_DIAMETER in result


@LOOPS
def test_diameter_from_the_tyre_call_when_words_name_none(run: Any) -> None:
    rec, _ = run(NO_DIAMETER, [[("search_tires", {"diameter": 17, "width": 205})]])
    assert rec.ran == [("search_disks", {"diameter": 17})]


@LOOPS
def test_words_win_over_the_call_diameter(run: Any) -> None:
    rec, _ = run(OCTAVIA, [[("search_tires", {"diameter": 15})]])
    assert rec.ran == [("search_disks", {"diameter": 16})]


@LOOPS
def test_once_per_turn_after_the_question(run: Any) -> None:
    rec, history = run(
        NO_DIAMETER,
        [[("search_knowledge_base", {"query": "a"})], [("search_knowledge_base", {"query": "b"})]],
    )
    assert rec.names() == ["search_knowledge_base"]
    assert sum(DISK_ASK_DIAMETER in r for r in _results(history)) == 1


@LOOPS
def test_sales_off_runs_the_tyre_call_as_asked(run: Any) -> None:
    rec, history = run(OCTAVIA, [[("get_vehicle_tire_sizes", _CAR_ARGS)]], sales=False)
    assert rec.ran == [("get_vehicle_tire_sizes", _CAR_ARGS)]
    assert not any("search_disks" in r for r in _results(history))


# ── Argument derivation ──────────────────────────────────────────────


class TestDiskDiameter:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            (OCTAVIA, 16),
            (TAVRIA, 13),
            ("нужны диски семнадцатого радиуса", 17),
            ("литые диски 15 радиус", 15),
            ("диски р16 на гольф 2015 року", 16),
        ],
    )
    def test_from_words(self, text: str, expected: int) -> None:
        assert disk_diameter(text, {}) == expected

    def test_year_is_not_a_diameter(self) -> None:
        assert disk_diameter("диски на Шкоду Октавію 2018 року", {}) is None

    @pytest.mark.parametrize("value", [None, "", "abc", 0, 5, 99, 16.5])
    def test_implausible_call_diameter_is_none(self, value: Any) -> None:
        assert disk_diameter(NO_DIAMETER, {"diameter": value}) is None

    def test_call_diameter_as_string(self) -> None:
        assert disk_diameter(NO_DIAMETER, {"diameter": "18"}) == 18


class TestDiskVehicle:
    def test_from_vehicle_sizes_call(self) -> None:
        assert disk_vehicle("get_vehicle_tire_sizes", _CAR_ARGS) == _CAR_ARGS

    def test_other_tools_never_name_a_car(self) -> None:
        assert disk_vehicle("search_tires", _CAR_ARGS) is None
        assert disk_vehicle("search_knowledge_base", _CAR_ARGS) is None

    def test_no_brand_or_model_no_car(self) -> None:
        assert disk_vehicle("get_vehicle_tire_sizes", {"year": 2018}) is None
        assert disk_vehicle("get_vehicle_tire_sizes", {"brand": "  "}) is None

    def test_bad_year_is_dropped(self) -> None:
        got = disk_vehicle("get_vehicle_tire_sizes", {"brand": "Skoda", "year": "нещодавно"})
        assert got == {"brand": "Skoda"}
