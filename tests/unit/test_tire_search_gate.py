"""The tyre search the code makes when the caller's request is complete (wave 3-G).

Goldset №5: after «шиповані, бренд будь-який, без побажань» the model called
nothing (five runs of five) and answered from the earlier studless search;
after «літні 205/55 R16, бюджет до трьох тисяч за шину» it asked for the car.
Both loops now search before the first round when the parsed request is
complete, was not searched yet, and this utterance itself names the tyres —
once per turn, through ``ToolRouter.execute``.

The handlers answer by their arguments: a constant «relaxed: [studded]» result
would write into the fixture the very caveat under test.
"""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Any, ClassVar
from unittest.mock import AsyncMock, create_autospec

import pytest

from scripts.configure_tenants import TVOYA_SHINA_CONFIG_PATCH
from src.agent.agent import LLMAgent, ToolRouter
from src.agent.network_policy import NetworkPolicy
from src.agent.streaming_loop import StreamingAgentLoop
from src.agent.tire_search_gate import (
    ForcedTireSearch,
    already_searched,
    search_args_from_query,
    turn_names_the_tyres,
)
from src.agent.tool_result_compressor import tire_caveat_phrase
from src.core.pipeline import merge_tire_query
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

WINTER = "потрібні зимові шини 235/55 R19"
STUDDED = "шиповані, бренд будь-який, без побажань"
BUDGET = "літні 205/55 R16, бюджет до трьох тисяч за шину, бренд будь-який"
NO_SEASON = "потрібні шини 205/55 R16"

_WINTER_ARGS = {"width": 235, "profile": 55, "diameter": 19, "season": "winter"}
_ITEMS = [
    {
        "id": "w1",
        "brand": "Bridgestone",
        "model": "Blizzak 6",
        "size": "235/55 R19",
        "season": "winter",
        "price": 6900,
        "in_stock": True,
    },
]
NO_STUDDED_PHRASE = tire_caveat_phrase(
    {"items": _ITEMS, "relaxed": ["studded"], "caveat_key": "no_studded_offer_friction"},
    {"studded": True},
)

_TOOL_NAMES = ("search_tires", "get_vehicle_tire_sizes", "search_disks")
_TOOLS = [{"name": n, "description": "", "input_schema": {"type": "object"}} for n in _TOOL_NAMES]


def _policy(sales: bool) -> NetworkPolicy:
    return NetworkPolicy.from_tenant_config({**TVOYA_SHINA_CONFIG_PATCH, "sales_enabled": sales})


class _Router:
    """A real ToolRouter; ``search_tires`` answers by its arguments, as the ladder does."""

    def __init__(self) -> None:
        self.router = ToolRouter()
        self.ran: list[dict[str, Any]] = []
        self.audited: list[tuple[str, dict[str, Any]]] = []

        async def _search(**kwargs: Any) -> dict[str, Any]:
            self.ran.append(dict(kwargs))
            if kwargs.get("studded") is True:
                return {
                    "total": 1,
                    "items": copy.deepcopy(_ITEMS),
                    "relaxed": ["studded"],
                    "caveat_key": "no_studded_offer_friction",
                }
            return {"total": 1, "items": copy.deepcopy(_ITEMS)}

        async def _other(**_: Any) -> dict[str, Any]:
            return {"items": []}

        self.router.register("search_tires", _search)
        self.router.register("get_vehicle_tire_sizes", _other)
        self.router.register("search_disks", _other)

        async def _hook(name: str, args: dict[str, Any], *_: Any) -> None:
            self.audited.append((name, dict(args)))

        self.router.set_execute_hook(_hook)


Round = list[tuple[str, dict[str, Any]]]  # the model's tool calls; [] = text only


def _has_tool_result(messages: list[dict[str, Any]]) -> bool:
    return any(
        m.get("role") == "user"
        and isinstance(m.get("content"), list)
        and any(p.get("type") == "tool_result" for p in m["content"])
        for m in messages
    )


class _Snapshotting(MockLLMRouter):
    """MockLLMRouter that keeps a copy of the history each round was given."""

    def __init__(self, responses: list[Any]) -> None:
        super().__init__(responses)
        self.seen: list[list[dict[str, Any]]] = []

    async def complete_stream(self, task: Any, messages: Any, **kwargs: Any):  # type: ignore[override]
        self.seen.append(copy.deepcopy(messages))
        async for e in super().complete_stream(task, messages, **kwargs):
            yield e


def _stream_events(rounds: list[Round], reply: str) -> list[list[Any]]:
    out: list[list[Any]] = []
    for i, calls in enumerate(rounds):
        ev: list[Any] = []
        for j, (name, args) in enumerate(calls):
            tid = f"s{i}{j}"
            ev += [
                ToolCallStart(id=tid, name=name),
                ToolCallDelta(id=tid, arguments_chunk=json.dumps(args)),
                ToolCallEnd(id=tid),
            ]
        out.append([*ev, StreamDone(stop_reason="tool_use", usage=Usage(1, 1))])
    out.append([TextDelta(text=reply), StreamDone(stop_reason="end_turn", usage=Usage(1, 1))])
    return out


def _stream(turns: list[tuple[str, list[Round], str]], *, sales: bool = True, query=None):
    """Play turns through StreamingAgentLoop; ``tire_progress`` as the pipeline builds it."""
    rec = _Router()
    history: list[dict[str, Any]] = []
    query = dict(query or {})
    replies: list[str] = []
    seen: list[list[list[dict[str, Any]]]] = []
    for user_text, rounds, reply in turns:
        llm = _Snapshotting(_stream_events(rounds, reply))
        loop = StreamingAgentLoop(
            llm_router=llm,
            tool_router=rec.router,
            tts=MockTTSEngine(),
            conn=MockAudioSocketConnection(),
            barge_in_event=asyncio.Event(),
            tools=_TOOLS,
            system_prompt="Test system prompt",
            network_policy=_policy(sales),
        )
        progress = None
        if sales:
            last_bot = next(
                (
                    b["text"]
                    for m in reversed(history)
                    if m["role"] == "assistant" and isinstance(m["content"], list)
                    for b in m["content"]
                    if b.get("type") == "text"
                ),
                "",
            )
            query = merge_tire_query(query, user_text, last_bot_text=last_bot)
            progress = dict(query) or None
        res = asyncio.run(loop.run_turn(user_text, history, tire_progress=progress))
        replies.append(res.spoken_text)
        seen.append(llm.seen)
    return rec, replies, seen


def _text(turns: list[tuple[str, list[Round], str]], *, sales: bool = True, query=None):
    """Play turns through one LLMAgent (one agent per call, as the goldset does)."""
    rec = _Router()
    history: list[dict[str, Any]] = []
    replies: list[str] = []
    seen: list[list[list[dict[str, Any]]]] = []
    agent = LLMAgent(
        api_key="test-key",
        system_prompt="base",
        llm_router=create_autospec(LLMRouter, instance=True),
        tool_router=rec.router,
        tools=_TOOLS,
        network_policy=_policy(sales),
    )
    agent._tire_query = dict(query or {})  # an earlier part of the call
    for user_text, rounds, reply in turns:
        responses = [
            LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(id=f"t{i}{j}", name=n, arguments=a) for j, (n, a) in enumerate(calls)
                ],
                stop_reason="tool_use",
                usage=Usage(1, 1),
                provider="test",
            )
            for i, calls in enumerate(rounds)
        ]
        responses.append(LLMResponse(text=reply, usage=Usage(1, 1), provider="test"))
        turn_seen: list[list[dict[str, Any]]] = []

        async def _complete(
            _task: Any, messages: Any, *_a: Any, _r=responses, _s=turn_seen, **_k: Any
        ):
            _s.append(copy.deepcopy(messages))
            return _r.pop(0)

        agent._llm_router.complete = AsyncMock(side_effect=_complete)
        text, history = asyncio.run(agent.process_message(user_text, history))
        replies.append(text)
        seen.append(turn_seen)
    return rec, replies, seen


LOOPS = pytest.mark.parametrize("run", [_stream, _text], ids=["streaming", "text"])

# Turn 1 of goldset `studded_none_studless_with_caveat`: the model searched
# without studs (as in goldset №5) and offered what it found.
_TURN_1 = (WINTER, [[("search_tires", _WINTER_ARGS)]], "Є зимові Bridgestone Blizzak 6 за 6900.")


# ── Both loops ────────────────────────────────────────────────────────


@LOOPS
def test_studded_answer_forces_a_studded_search(run: Any) -> None:
    rec, replies, seen = run([_TURN_1, (STUDDED, [], "Підходить якийсь із цих варіантів?")])
    assert rec.ran[0] == _WINTER_ARGS  # the model's own, turn 1
    assert len(rec.ran) == 2
    assert rec.ran[1]["studded"] is True
    assert (rec.ran[1]["width"], rec.ran[1]["profile"], rec.ran[1]["diameter"]) == (235, 55, 19)
    # audited as an ordinary search_tires call
    assert rec.audited[1] == ("search_tires", rec.ran[1])
    # the caveat is the code's and comes first
    assert NO_STUDDED_PHRASE and replies[1].startswith(NO_STUDDED_PHRASE)
    # the model saw the result before its first round of turn 2
    assert _has_tool_result(seen[1][0][-2:])


@LOOPS
def test_studless_turn_1_search_has_no_studded_caveat(run: Any) -> None:
    rec, replies, _ = run([_TURN_1])
    assert rec.ran == [_WINTER_ARGS]  # winter without studs is incomplete: nothing forced
    assert NO_STUDDED_PHRASE not in replies[0]


@LOOPS
def test_complete_request_with_budget_is_searched_before_the_question(run: Any) -> None:
    rec, _, seen = run([(BUDGET, [], "На який автомобіль підбираємо шини?")])
    assert rec.ran == [{"width": 205, "profile": 55, "diameter": 16, "season": "summer"}]
    assert _has_tool_result(seen[0][0])  # searched before round 1, not after it


@LOOPS
def test_no_season_no_search(run: Any) -> None:
    rec, _, seen = run([(NO_SEASON, [], "Вам літні, зимові чи всесезонні?")])
    assert rec.ran == []
    assert not _has_tool_result(seen[0][0])


@LOOPS
def test_the_same_request_already_searched_is_not_repeated(run: Any) -> None:
    summer = {"width": 205, "profile": 55, "diameter": 16, "season": "summer"}
    rec, _, seen = run(
        [
            ("літні 205/55 R16", [], "Є Firestone."),
            ("так, літні 205/55 R16", [], "Яка вас зацікавила?"),
        ]
    )
    assert rec.ran == [summer]  # turn 1 forced; turn 2 repeats it and is not forced
    assert len([m for m in seen[1][0] if _has_tool_result([m])]) == 1


@LOOPS
def test_a_turn_that_names_no_tyres_forces_nothing(run: Any) -> None:
    # A complete request from earlier in the call, never searched; now a
    # delivery question — not a search request.
    rec, _, _ = run(
        [("а скільки коштує доставка?", [], "Доставка безкоштовна.")],
        query={"sizes": ["205/55 R16"], "season": "summer"},
    )
    assert rec.ran == []


@LOOPS
def test_once_per_turn_and_the_model_may_still_search(run: Any) -> None:
    other = {"width": 205, "profile": 55, "diameter": 16, "season": "summer", "brand": "Michelin"}
    rec, _, _ = run([(BUDGET, [[("search_tires", other)]], "Є Michelin.")])
    assert len(rec.ran) == 2  # one forced, then the model's own — not blocked
    assert rec.ran[1] == other


@LOOPS
def test_sales_off_forces_nothing(run: Any) -> None:
    rec, replies, seen = run([(BUDGET, [], "Уточніть авто.")], sales=False)
    assert rec.ran == []
    assert replies[0].endswith("Уточніть авто.")
    assert not _has_tool_result(seen[0][0])


def test_text_path_keeps_no_query_with_sales_off() -> None:
    agent = LLMAgent(
        api_key="test-key",
        system_prompt="base",
        llm_router=create_autospec(LLMRouter, instance=True),
        tool_router=ToolRouter(),
        tools=_TOOLS,
        network_policy=_policy(False),
    )
    agent._llm_router.complete = AsyncMock(
        return_value=LLMResponse(text="Ок.", usage=Usage(1, 1), provider="test")
    )
    asyncio.run(agent.process_message(BUDGET, []))
    assert agent._tire_query == {}


# ── Pure parts ───────────────────────────────────────────────────────


class TestSearchArgs:
    def test_complete_summer(self) -> None:
        q = {"sizes": ["205/55 R16"], "season": "summer", "budget": {"amount": 3000}}
        assert search_args_from_query(q) == {
            "width": 205,
            "profile": 55,
            "diameter": 16,
            "season": "summer",
        }

    def test_winter_needs_the_studs(self) -> None:
        assert search_args_from_query({"sizes": ["235/55 R19"], "season": "winter"}) is None
        args = search_args_from_query(
            {"sizes": ["235/55 R19"], "season": "winter", "nail": "studless"}
        )
        assert args is not None and args["studded"] is False

    @pytest.mark.parametrize("season", ["summer", "all_season"])
    def test_studs_not_required_off_winter(self, season: str) -> None:
        assert search_args_from_query({"sizes": ["205/55 R16"], "season": season}) is not None

    @pytest.mark.parametrize(
        "query",
        [
            {},
            None,
            {"sizes": ["205/55 R16"]},
            {"season": "summer"},
            {"sizes": ["205/55 R16", "215/55 R16"], "season": "summer"},
            {"sizes": ["205/55 R16"], "season": "spring"},
        ],
    )
    def test_incomplete(self, query: Any) -> None:
        assert search_args_from_query(query) is None

    def test_any_season_searches_without_one(self) -> None:
        args = search_args_from_query({"sizes": ["205/55 R16"], "season": "any"})
        assert args == {"width": 205, "profile": 55, "diameter": 16}

    def test_filters(self) -> None:
        args = search_args_from_query(
            {
                "sizes": ["245/40 R18"],
                "rear_size": "275/35 R18",
                "season": "summer",
                "brands": ["Michelin"],
                "tech": ["runflat"],
            }
        )
        assert args == {
            "width": 245,
            "profile": 40,
            "diameter": 18,
            "season": "summer",
            "rear_width": 275,
            "rear_profile": 35,
            "rear_diameter": 18,
            "brand": "Michelin",
            "runflat": True,
        }

    def test_two_brands_search_without_brand(self) -> None:
        args = search_args_from_query(
            {"sizes": ["205/55 R16"], "season": "summer", "brands": ["Michelin", "Nokian"]}
        )
        assert args is not None and "brand" not in args


def _searched(args: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"role": "user", "content": "x"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "a", "name": "search_tires", "input": args}],
        },
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": "{}"}]},
    ]


class TestAlreadySearched:
    def test_same_args_are_covered_even_with_extras(self) -> None:
        hist = _searched({**_WINTER_ARGS, "studded": "true", "brand": "Nokian"})
        assert already_searched({**_WINTER_ARGS, "studded": True}, hist)

    def test_a_new_value_is_a_new_request(self) -> None:
        assert not already_searched({**_WINTER_ARGS, "studded": True}, _searched(_WINTER_ARGS))

    def test_only_the_last_search_counts(self) -> None:
        hist = _searched({**_WINTER_ARGS, "studded": True}) + _searched(_WINTER_ARGS)
        assert not already_searched({**_WINTER_ARGS, "studded": True}, hist)

    def test_no_search_yet(self) -> None:
        assert not already_searched(_WINTER_ARGS, [{"role": "user", "content": "x"}])


class TestTurnNamesTheTyres:
    @pytest.mark.parametrize("text", [STUDDED, BUDGET, "а є Michelin?", "а зимові є?"])
    def test_yes(self, text: str) -> None:
        assert turn_names_the_tyres(text)

    @pytest.mark.parametrize(
        "text",
        [
            "а скільки коштує доставка?",
            "",
            "запишіть на шиномонтаж, зимові 205/55 R16",
            "литі диски 205/55 R16 зимові",
        ],
    )
    def test_no(self, text: str) -> None:
        assert not turn_names_the_tyres(text)


class TestForcedTireSearch:
    _Q: ClassVar[dict[str, Any]] = {"sizes": ["205/55 R16"], "season": "summer"}

    def test_once_per_turn(self) -> None:
        gate = ForcedTireSearch(sales_enabled=True, tools=_TOOLS)
        hist = [{"role": "user", "content": BUDGET}]
        assert gate.plan(self._Q, BUDGET, hist) is not None
        assert gate.plan(self._Q, BUDGET, hist) is None

    def test_unarmed_without_sales_or_the_tool(self) -> None:
        hist = [{"role": "user", "content": BUDGET}]
        assert (
            ForcedTireSearch(sales_enabled=False, tools=_TOOLS).plan(self._Q, BUDGET, hist) is None
        )
        no_tool = [t for t in _TOOLS if t["name"] != "search_tires"]
        assert (
            ForcedTireSearch(sales_enabled=True, tools=no_tool).plan(self._Q, BUDGET, hist) is None
        )

    def test_a_non_stock_size_question_is_not_searched(self) -> None:
        # «225/45 R17 замість 205/55 R16?» — the specialist picks it
        query = {"sizes": ["225/45 R17"], "season": "summer"}
        swap = "літні, можна поставити 225/45 R17 замість 205/55 R16?"
        plain = "літні 225/45 R17"
        gate = ForcedTireSearch(sales_enabled=True, tools=_TOOLS)
        assert gate.plan(query, swap, [{"role": "user", "content": swap}]) is None
        control = ForcedTireSearch(sales_enabled=True, tools=_TOOLS)
        assert control.plan(query, plain, [{"role": "user", "content": plain}]) is not None


# ── The caveat and the goldset mock ─────────────────────────────────


class TestNoStuddedCaveatNeedsAStuddedSearch:
    _RELAXED: ClassVar[dict[str, Any]] = {
        "items": _ITEMS,
        "relaxed": ["studded"],
        "caveat_key": "no_studded_offer_friction",
    }

    def test_studded_true(self) -> None:
        assert tire_caveat_phrase(self._RELAXED, {"studded": True}) == NO_STUDDED_PHRASE

    @pytest.mark.parametrize("args", [None, {}, {"studded": False}, {"studded": "true"}])
    def test_not_studded(self, args: Any) -> None:
        assert tire_caveat_phrase(self._RELAXED, args) is None


class TestGoldsetMockByArgs:
    _MOCK: ClassVar[dict[str, Any]] = {
        "total": 1,
        "items": [{"id": "plain"}],
        "when_args": [{"args": {"studded": "^true$"}, "result": {"relaxed": ["studded"]}}],
    }

    def test_rule_matches_on_args(self) -> None:
        from scripts.run_goldset import mock_handler

        handler = mock_handler(self._MOCK)
        assert asyncio.run(handler(width=205, studded=True)) == {"relaxed": ["studded"]}
        assert asyncio.run(handler(width=205)) == {"total": 1, "items": [{"id": "plain"}]}
        assert asyncio.run(handler(width=205, studded=False))["items"] == [{"id": "plain"}]

    def test_constant_mock_unchanged(self) -> None:
        from scripts.run_goldset import mock_handler

        assert asyncio.run(mock_handler({"a": 1})(x=1)) == {"a": 1}

    @pytest.mark.parametrize("bad", [[], "x", [{"args": {}, "result": 1}], [{"args": {"a": "b"}}]])
    def test_bad_when_args_rejected(self, bad: Any) -> None:
        from scripts.run_goldset import CaseError, parse_case

        raw = {
            "id": "x_case",
            "sales_enabled": True,
            "mocks": {"search_tires": {"when_args": bad}},
            "turns": [{"user": "привіт"}],
        }
        with pytest.raises(CaseError, match="when_args"):
            parse_case(raw)
