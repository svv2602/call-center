"""Wheel consultation answered from the knowledge base (`disk_intent.is_disk_consult`).

Goldset `disk_type_consult`: «що краще ставити на зиму — штамповку чи литі
диски?» — the `search_disks` redirect asked for a diameter. Under sales the
code searches the knowledge base (category ``wheels``) before round 1 and the
redirect stands down for the turn, in both loops.
"""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Any
from unittest.mock import AsyncMock, create_autospec

import pytest

from scripts.configure_tenants import TVOYA_SHINA_CONFIG_PATCH
from src.agent.agent import LLMAgent, ToolRouter
from src.agent.disk_intent import (
    KB_TOOL,
    KB_WHEELS_CATEGORY,
    DiskToolRedirect,
    disk_consult_args,
    is_disk_consult,
)
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

CONSULT = "що краще ставити на зиму — штамповку чи литі диски?"
PURCHASE = "диски 16 на Октавію"

CONSULTS = [
    CONSULT,
    "чим відрізняються литі диски від кованих?",
    "что лучше на зиму, литые или штампованные?",
    "які диски обрати?",
    "чи варто брати ковані диски",
    "посоветуйте диски",
    "а какие плюсы у литых дисков?",
]
NOT_CONSULTS = [
    PURCHASE,
    "потрібні литі диски шістнадцятий радіус на Шкоду Октавію",
    "які диски обрати на r16?",
    "потрібні литі диски на Шкоду Октавію",  # a purchase: no advice, no comparison  # advice with a size — a purchase, search_disks serves it
    "гальмівні диски що краще",
    "що краще — Michelin чи Continental?",
    "",
]

_ALL_TOOLS = ("search_tires", "search_disks", "get_vehicle_tire_sizes", KB_TOOL)


def _tools(names: tuple[str, ...] = _ALL_TOOLS) -> list[dict[str, Any]]:
    return [{"name": n, "description": "", "input_schema": {"type": "object"}} for n in names]


def _policy(sales: bool = True) -> NetworkPolicy:
    return NetworkPolicy.from_tenant_config({**TVOYA_SHINA_CONFIG_PATCH, "sales_enabled": sales})


class TestPredicate:
    @pytest.mark.parametrize("text", CONSULTS)
    def test_consult(self, text: str) -> None:
        assert is_disk_consult(text)

    @pytest.mark.parametrize("text", NOT_CONSULTS)
    def test_not_consult(self, text: str) -> None:
        assert not is_disk_consult(text)

    def test_args(self) -> None:
        args = disk_consult_args(CONSULT, sales_enabled=True, tools=_tools())
        assert args == {"query": CONSULT, "category": KB_WHEELS_CATEGORY}
        assert KB_WHEELS_CATEGORY == "wheels"

    def test_no_args_without_sales(self) -> None:
        assert disk_consult_args(CONSULT, sales_enabled=False, tools=_tools()) is None

    def test_no_args_without_the_kb_tool(self) -> None:
        tools = _tools(("search_tires", "search_disks"))
        assert disk_consult_args(CONSULT, sales_enabled=True, tools=tools) is None

    def test_no_args_for_a_purchase(self) -> None:
        assert disk_consult_args(PURCHASE, sales_enabled=True, tools=_tools()) is None


class TestRedirectStandsDown:
    def _history(self, text: str) -> list[dict[str, Any]]:
        return [{"role": "user", "content": text}]

    def test_consult_turn_is_not_substituted(self) -> None:
        red = DiskToolRedirect(sales_enabled=True, tools=_tools(), consult=True)
        assert red.check("search_tires", {"diameter": 16}, self._history(CONSULT)) is None

    def test_default_still_substitutes(self) -> None:
        red = DiskToolRedirect(sales_enabled=True, tools=_tools())
        assert red.check("search_tires", {"diameter": 16}, self._history(CONSULT)) is not None


# ── Both loops ────────────────────────────────────────────────────────


class _Router:
    def __init__(self) -> None:
        self.router = ToolRouter()
        self.ran: list[tuple[str, dict[str, Any]]] = []
        for name in _ALL_TOOLS:
            self.router.register(name, self._handler(name))

    def _handler(self, name: str) -> Any:
        async def _run(**kwargs: Any) -> dict[str, Any]:
            self.ran.append((name, dict(kwargs)))
            if name == KB_TOOL:
                return {
                    "total": 1,
                    "articles": [
                        {
                            "title": "Литі чи штамповані диски",
                            "category": "wheels",
                            "content": "Штамповані диски дешевші й ремонтопридатні взимку.",
                        }
                    ],
                }
            return {"items": []}

        return _run


def _round_events(calls: list[tuple[str, dict[str, Any]]]) -> list[Any]:
    ev: list[Any] = []
    for j, (name, args) in enumerate(calls):
        tid = f"s{j}"
        ev += [
            ToolCallStart(id=tid, name=name),
            ToolCallDelta(id=tid, arguments_chunk=json.dumps(args)),
            ToolCallEnd(id=tid),
        ]
    return [*ev, StreamDone(stop_reason="tool_use", usage=Usage(1, 1))]


class _Snapshotting(MockLLMRouter):
    def __init__(self, responses: list[Any]) -> None:
        super().__init__(responses)
        self.seen: list[list[dict[str, Any]]] = []

    async def complete_stream(self, task: Any, messages: Any, **kwargs: Any):  # type: ignore[override]
        self.seen.append(copy.deepcopy(messages))
        async for e in super().complete_stream(task, messages, **kwargs):
            yield e


REPLY = "Штампування взимку практичніше."


def _stream(text: str, calls: list[tuple[str, dict[str, Any]]], *, sales: bool = True, tools=None):
    rec = _Router()
    events = [_round_events(calls)] if calls else []
    events.append([TextDelta(text=REPLY), StreamDone(stop_reason="end_turn", usage=Usage(1, 1))])
    llm = _Snapshotting(events)
    loop = StreamingAgentLoop(
        llm_router=llm,
        tool_router=rec.router,
        tts=MockTTSEngine(),
        conn=MockAudioSocketConnection(),
        barge_in_event=asyncio.Event(),
        tools=tools or _tools(),
        system_prompt="Test system prompt",
        network_policy=_policy(sales),
    )
    asyncio.run(loop.run_turn(text, []))
    return rec, llm.seen


def _text(text: str, calls: list[tuple[str, dict[str, Any]]], *, sales: bool = True, tools=None):
    rec = _Router()
    agent = LLMAgent(
        api_key="test-key",
        system_prompt="base",
        llm_router=create_autospec(LLMRouter, instance=True),
        tool_router=rec.router,
        tools=tools or _tools(),
        network_policy=_policy(sales),
    )
    responses: list[LLMResponse] = []
    if calls:
        responses.append(
            LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(id=f"t{j}", name=n, arguments=a) for j, (n, a) in enumerate(calls)
                ],
                stop_reason="tool_use",
                usage=Usage(1, 1),
                provider="test",
            )
        )
    responses.append(LLMResponse(text=REPLY, usage=Usage(1, 1), provider="test"))
    seen: list[list[dict[str, Any]]] = []

    async def _complete(_task: Any, messages: Any, *_a: Any, **_k: Any) -> LLMResponse:
        seen.append(copy.deepcopy(messages))
        return responses.pop(0)

    agent._llm_router.complete = AsyncMock(side_effect=_complete)
    asyncio.run(agent.process_message(text, []))
    return rec, seen


LOOPS = pytest.mark.parametrize("run", [_stream, _text], ids=["streaming", "text"])


def _kb_pair_before(messages: list[dict[str, Any]]) -> bool:
    uses = [
        b
        for m in messages
        if m["role"] == "assistant" and isinstance(m["content"], list)
        for b in m["content"]
        if b.get("type") == "tool_use" and b.get("name") == KB_TOOL
    ]
    results = [
        b
        for m in messages
        if m["role"] == "user" and isinstance(m["content"], list)
        for b in m["content"]
        if b.get("type") == "tool_result"
    ]
    return bool(uses) and any(r["tool_use_id"] == uses[0]["id"] for r in results)


@LOOPS
def test_consult_searches_the_kb_first_and_is_not_redirected(run: Any) -> None:
    # The model reaches for a tyre-side tool, as in the goldset.
    rec, seen = run(CONSULT, [("search_tires", {"diameter": 16})])
    assert rec.ran[0] == (KB_TOOL, {"query": CONSULT, "category": "wheels"})
    # the model saw the KB result before its first round
    assert _kb_pair_before(seen[0])
    # no substitution: the model's own call ran as asked, search_disks never ran
    assert rec.ran[1] == ("search_tires", {"diameter": 16})
    assert all(name != "search_disks" for name, _ in rec.ran)


@LOOPS
def test_purchase_is_not_a_consult_and_still_redirected(run: Any) -> None:
    rec, seen = run(PURCHASE, [("search_tires", {"diameter": 16})])
    assert all(name != KB_TOOL for name, _ in rec.ran)
    assert not _kb_pair_before(seen[0])
    assert rec.ran and rec.ran[0][0] == "search_disks"


@LOOPS
def test_sales_off_no_kb_call(run: Any) -> None:
    rec, seen = run(CONSULT, [], sales=False)
    assert rec.ran == []
    assert not _kb_pair_before(seen[0])


@LOOPS
def test_no_kb_tool_no_kb_call(run: Any) -> None:
    rec, _ = run(CONSULT, [], tools=_tools(("search_tires", "search_disks")))
    assert rec.ran == []
