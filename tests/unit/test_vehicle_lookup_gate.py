"""The car's factory sizes looked up by the code; studs asked only for winter (wave 1-A).

Probe 2026-09-29 (`camry_compare_taurus_bridgestone`, ProKoleso 0/2): the
model once never called `get_vehicle_tire_sizes`, once had the sizes but on
«летние» asked «Шиповані чи без шипів?» and searched nothing.

The loop tests run through the real ``main._build_tool_router`` (the
``vehicle_text`` handler, the season guard of ``search_tires``) on an
autospecced ``StoreClient`` — a bare mock would make a path that does not
exist look green. The store answers by its arguments.
"""

from __future__ import annotations

import asyncio
import copy
import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, create_autospec

import pytest

from scripts.configure_tenants import TVOYA_SHINA_CONFIG_PATCH
from src.agent.agent import LLMAgent
from src.agent.network_policy import NetworkPolicy
from src.agent.streaming_loop import StreamingAgentLoop
from src.agent.vehicle_lookup_gate import (
    LOOKUP_TOOL,
    VehicleLookupGate,
    asks_about_studs,
    drop_stud_questions,
    drop_stud_questions_text,
    factory_size,
    named_car_words,
    said_diameter,
)
from src.core.call_session import CallSession
from src.core.pipeline import merge_tire_query
from src.core.sentence_buffer import SentenceReady
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
from src.main import _build_tool_router
from src.store_client.client import StoreClient
from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection
from tests.unit.mocks.mock_llm_router import MockLLMRouter
from tests.unit.mocks.mock_tts import MockTTSEngine

CAMRY_SIZES = ["205/65 R16", "215/55 R17", "235/45 R18", "235/40 R19"]
CAMRY = {
    "found": True,
    "brand": "Toyota",
    "model": "Camry",
    "years": [2021, 2020, 2019, 2018],
    "stock_sizes": CAMRY_SIZES,
}
STUD_Q = "Шиповані чи без шипів?"
_TOOL_NAMES = ("search_tires", "get_vehicle_tire_sizes", "search_disks")
_TOOLS = [{"name": n, "description": "", "input_schema": {"type": "object"}} for n in _TOOL_NAMES]
_TOOLS_NO_LOOKUP = [t for t in _TOOLS if t["name"] != LOOKUP_TOOL]


def _policy(sales: bool) -> NetworkPolicy:
    return NetworkPolicy.from_tenant_config({**TVOYA_SHINA_CONFIG_PATCH, "sales_enabled": sales})


# ── Pure parts ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "words"),
    [
        ("шины на камри", ["камри"]),
        ("шины R17 на Toyota Camry", ["toyota", "camry"]),
        ("у мене тойота камрі, які шини?", ["тойота", "камрі"]),
        ("шини для Шкоди Октавії", ["шкоди", "октавії"]),
        # a filler word, a season, a tyre brand, a count — no car
        ("шиповані, бренд будь-який, без побажань", []),
        ("шини на зиму", []),
        ("хочу купити гуму на літо", []),
        ("мені треба чотири шини", []),
        ("чи є шини на замовлення?", []),
        ("шипованные шины на Nokian", []),
        ("Тойота Камрі, шини потрібні", []),  # no marker, bot asked nothing
    ],
)
def test_named_car_words(text: str, words: list[str]) -> None:
    assert named_car_words(text)[0] == words


def test_answer_to_the_car_question_is_all_car() -> None:
    assert named_car_words("камрі 2018", "Добре. Яке у вас авто?") == (["камрі"], 2018)
    assert named_car_words("камрі 2018", "Добре. Яке у вас авто.")[0] == []


def test_said_diameter() -> None:
    assert said_diameter("шины R17 на Toyota Camry") == 17
    assert said_diameter("сімнадцятий радіус") == 17
    assert said_diameter("шины 215/55 R17") is None  # a full size is the caller's own
    assert said_diameter("летние") is None


def test_factory_size() -> None:
    assert factory_size(CAMRY_SIZES, 17) == "215/55 R17"
    assert factory_size(CAMRY_SIZES, None) is None  # four sizes — the caller chooses
    assert factory_size(["205/55 R16"], None) == "205/55 R16"
    assert factory_size(["225/45 R17", "215/50 R17"], 17) is None
    assert factory_size(CAMRY_SIZES, 15) is None


def _gate(sales: bool = True) -> VehicleLookupGate:
    return VehicleLookupGate(sales_enabled=sales)


def test_plan_arms_only_under_sales_with_the_tool() -> None:
    assert _gate(False).plan("шины на камри", "", _TOOLS) is None
    assert _gate().plan("шины на камри", "", _TOOLS_NO_LOOKUP) is None
    assert _gate().plan("шины на камри", "", _TOOLS) == {"vehicle_text": "шины на камри"}


@pytest.mark.parametrize(
    "text",
    [
        "запишіть на шиномонтаж на камрі",  # fitting request
        "диски на камри r17",  # wheels
        "шиповані, бренд будь-який, без побажань",  # no car
        "а доставка на камрі є?",  # not about tyres
        "",
    ],
)
def test_plan_declines(text: str) -> None:
    assert _gate().plan(text, "", _TOOLS) is None


def test_plan_does_not_repeat_the_same_car_words() -> None:
    gate = _gate()
    assert gate.plan("шины на камри", "", _TOOLS) is not None
    assert gate.plan("а шины на камри летние?", "", _TOOLS) is None
    assert gate.plan("шины на камри 2018", "", _TOOLS) is not None  # a year is a new car


def test_settle_default_deny_and_once_per_car() -> None:
    gate = _gate()
    gate.plan("шины на камри", "", _TOOLS)
    assert gate.settle({"found": False, "vehicle_resolved": False}) is None
    assert gate.settle({"found": False, "brand": "Toyota", "message": "…"}) is None
    assert gate.settle("boom") is None
    assert gate.settle({**CAMRY, "found": False}) is None  # the result's own verdict wins
    assert gate.settle({**CAMRY, "model": ""}) is None  # no half a car in the history
    assert gate.settle(copy.deepcopy(CAMRY)) == {"brand": "Toyota", "model": "Camry"}
    assert gate.settle(copy.deepcopy(CAMRY)) is None  # already in the history


def test_settle_carries_the_year_said() -> None:
    gate = _gate()
    gate.plan("шини на камрі 2018", "", _TOOLS)
    assert gate.settle(copy.deepcopy(CAMRY)) == {"brand": "Toyota", "model": "Camry", "year": 2018}


def test_model_lookup_counts_as_the_car_looked_up() -> None:
    gate = _gate()
    gate.note_model_call({"brand": "Toyota", "model": "Camry"}, copy.deepcopy(CAMRY))
    gate.plan("шины на камри", "", _TOOLS)
    assert gate.settle(copy.deepcopy(CAMRY)) is None
    gate.note_turn("R17", {})
    assert gate.apply({"season": "summer"}) == {"season": "summer", "sizes": ["215/55 R17"]}


def test_apply_never_over_the_callers_size() -> None:
    gate = _gate()
    gate.note_model_call({}, copy.deepcopy(CAMRY))
    gate.note_turn("на R17", {})
    assert gate.apply({"diameter": 17}) == {"sizes": ["215/55 R17"]}
    assert gate.apply({"sizes": ["225/45 R17"]}) == {"sizes": ["225/45 R17"]}
    # its own size follows a new diameter
    gate.note_turn("а на R18?", {})
    assert gate.apply({"sizes": ["215/55 R17"]}) == {"sizes": ["235/45 R18"]}


def test_apply_skips_a_staggered_car_and_sales_off() -> None:
    gate = _gate()
    gate.note_model_call(
        {}, {**CAMRY, "stock_sizes": ["245/40 R18"], "staggered_pairs": [["a", "b"]]}
    )
    assert gate.apply({"season": "summer"}) == {"season": "summer"}
    off = _gate(False)
    off.note_model_call({}, {**CAMRY, "stock_sizes": ["205/55 R16"]})
    assert off.apply({"season": "summer"}) == {"season": "summer"}


# ── Studs only in winter ──────────────────────────────────────────────


def test_asks_about_studs() -> None:
    assert asks_about_studs(STUD_Q)
    assert asks_about_studs("Вам на липучці?")
    assert not asks_about_studs("Ці шини без шипів.")
    assert not asks_about_studs("Який сезон вам потрібен?")


@pytest.mark.parametrize("season", ["summer", "all_season", "any"])
def test_text_drops_only_the_stud_question(season: str) -> None:
    text = f"Для Camry штатний 215/55 R17. {STUD_Q} Є Taurus за 3450."
    out = drop_stud_questions_text(text, {"season": season})
    assert out == "Для Camry штатний 215/55 R17. Є Taurus за 3450."


@pytest.mark.parametrize("query", [{"season": "winter"}, {}, None])
def test_text_keeps_the_stud_question_in_winter_or_unknown(query: Any) -> None:
    text = f"Для Camry штатний 215/55 R17. {STUD_Q}"
    assert drop_stud_questions_text(text, query) == text


async def _collect(stream: Any) -> list[str]:
    return [e.text async for e in stream]


def _events(*parts: str):
    async def gen():
        for p in parts:
            yield SentenceReady(text=p)

    return gen()


def test_stream_drops_a_fragmented_stud_question() -> None:
    out = asyncio.run(
        _collect(
            drop_stud_questions(
                _events("Добре.", "Вам шиповані", "чи без шипів?", "Є Taurus."),
                {"season": "summer"},
            )
        )
    )
    assert out == ["Добре.", "Є Taurus."]
    kept = asyncio.run(
        _collect(
            drop_stud_questions(_events("Вам шиповані", "чи без шипів?"), {"season": "winter"})
        )
    )
    assert kept == ["Вам шиповані", "чи без шипів?"]


# ── The handler: vehicle_text ─────────────────────────────────────────


def _store() -> Any:
    store = create_autospec(StoreClient, instance=True)

    async def _resolve(text: str) -> dict[str, Any] | None:
        low = text.lower()
        if "камри" in low or "камрі" in low or "camry" in low:
            return {"brand": "Toyota", "model": "Camry"}
        if "тойот" in low:
            return {"brand": "Toyota"}  # brand only — no model
        return None

    async def _sizes(brand: str = "", model: str = "", year: int = 0, **_: Any) -> dict[str, Any]:
        if (brand, model) == ("Toyota", "Camry"):
            return copy.deepcopy(CAMRY)
        return {"found": False, "message": "not found"}

    async def _search(network: str = "", **params: Any) -> dict[str, Any]:
        return {
            "total": 1,
            "items": [
                {
                    "id": "a4",
                    "brand": "Taurus",
                    "model": "SUMMER 3",
                    "size": "215/55 R17 98W",
                    "season": "summer",
                    "price": 3450,
                    "in_stock": True,
                }
            ],
        }

    store.resolve_vehicle_text.side_effect = _resolve
    store.get_vehicle_tire_sizes.side_effect = _sizes
    store.search_tires.side_effect = _search
    return store


def _router(sales: bool = True):
    session = CallSession(uuid.uuid4())
    store = _store()
    router = _build_tool_router(session, store_client=store, network_policy=_policy(sales))
    audited: list[tuple[str, dict[str, Any]]] = []

    async def _hook(name: str, args: dict[str, Any], *_: Any) -> None:
        audited.append((name, dict(args)))

    router.set_execute_hook(_hook)
    return router, session, store, audited


def test_handler_resolves_vehicle_text_under_sales() -> None:
    router, _, store, _ = _router()
    res = asyncio.run(router.execute(LOOKUP_TOOL, {"vehicle_text": "шины на камри"}))
    assert res["found"] is True
    store.resolve_vehicle_text.assert_awaited_once_with("шины на камри")
    store.get_vehicle_tire_sizes.assert_awaited_once_with(brand="Toyota", model="Camry")


def test_handler_needs_brand_and_model() -> None:
    router, _, store, _ = _router()
    res = asyncio.run(router.execute(LOOKUP_TOOL, {"vehicle_text": "шини на тойоту"}))
    assert res == {"found": False, "vehicle_resolved": False}
    store.get_vehicle_tire_sizes.assert_not_awaited()


def test_handler_model_args_win_and_sales_off_never_resolves() -> None:
    router, _, store, _ = _router()
    asyncio.run(router.execute(LOOKUP_TOOL, {"brand": "Toyota", "model": "Camry"}))
    asyncio.run(
        router.execute(
            LOOKUP_TOOL, {"brand": "Toyota", "model": "Camry", "vehicle_text": "шини на тойоту"}
        )
    )
    store.resolve_vehicle_text.assert_not_awaited()
    off, _, off_store, _ = _router(sales=False)
    asyncio.run(off.execute(LOOKUP_TOOL, {"vehicle_text": "шины на камри"}))
    off_store.resolve_vehicle_text.assert_not_awaited()
    off_store.get_vehicle_tire_sizes.assert_awaited_once_with()


# ── Both loops: the probe dialogue ────────────────────────────────────

Round = list[tuple[str, dict[str, Any]]]


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


class _Snapshotting(MockLLMRouter):
    def __init__(self, responses: list[Any]) -> None:
        super().__init__(responses)
        self.seen: list[list[dict[str, Any]]] = []

    async def complete_stream(self, task: Any, messages: Any, **kwargs: Any):  # type: ignore[override]
        self.seen.append(copy.deepcopy(messages))
        async for e in super().complete_stream(task, messages, **kwargs):
            yield e


def _last_bot(history: list[dict[str, Any]]) -> str:
    for m in reversed(history):
        if m["role"] != "assistant":
            continue
        if isinstance(m["content"], str):
            return m["content"]
        for b in m["content"]:
            if b.get("type") == "text":
                return b["text"]
    return ""


def _stream(turns: list[tuple[str, list[Round], str]], *, sales: bool = True):
    """One StreamingAgentLoop per call, ``tire_progress`` as the pipeline builds it."""
    router, session, _store_mock, audited = _router(sales)
    history: list[dict[str, Any]] = []
    loop = StreamingAgentLoop(
        llm_router=MockLLMRouter([]),
        tool_router=router,
        tts=MockTTSEngine(),
        conn=MockAudioSocketConnection(),
        barge_in_event=asyncio.Event(),
        tools=_TOOLS,
        system_prompt="Test system prompt",
        network_policy=_policy(sales),
    )
    replies: list[str] = []
    seen: list[list[list[dict[str, Any]]]] = []
    for user_text, rounds, reply in turns:
        llm = _Snapshotting(_stream_events(rounds, reply))
        loop._llm_router = llm
        progress = None
        if sales:
            session.tire_query = merge_tire_query(
                session.tire_query,
                user_text,
                last_bot_text=_last_bot(history),
                consult_started=any(n == LOOKUP_TOOL for n, _ in audited),
            )
            progress = dict(session.tire_query) or None
        res = asyncio.run(loop.run_turn(user_text, history, tire_progress=progress))
        replies.append(res.spoken_text)
        seen.append(llm.seen)
    return audited, history, replies, seen


def _text(turns: list[tuple[str, list[Round], str]], *, sales: bool = True):
    """One LLMAgent per call (the goldset); the season guard reads the agent's request."""
    router, session, _store_mock, audited = _router(sales)
    history: list[dict[str, Any]] = []
    agent = LLMAgent(
        api_key="test-key",
        system_prompt="base",
        llm_router=create_autospec(LLMRouter, instance=True),
        tool_router=router,
        tools=_TOOLS,
        network_policy=_policy(sales),
    )
    replies: list[str] = []
    seen: list[list[list[dict[str, Any]]]] = []
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
        # The live pipeline keeps the season in the session for the guard.
        session.tire_query = dict(agent._tire_query)
        text, history = asyncio.run(agent.process_message(user_text, history))
        session.tire_query = dict(agent._tire_query)
        replies.append(text)
        seen.append(turn_seen)
    return audited, history, replies, seen


LOOPS = pytest.mark.parametrize("run", [_stream, _text], ids=["streaming", "text"])

CAMRY_DIALOGUE = [
    ("шины на камри", [], "Для Toyota Camry штатні розміри від R16 до R19. Який діаметр?"),
    ("шины R17 на Toyota Camry", [], "Штатний 215/55 R17. Літні чи зимові?"),
    ("летние", [], f"Є Taurus SUMMER 3 за 3450 гривень. {STUD_Q}"),
]


def _lookup_pairs(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        b["input"]
        for m in history
        if m["role"] == "assistant" and isinstance(m["content"], list)
        for b in m["content"]
        if b.get("type") == "tool_use" and b.get("name") == LOOKUP_TOOL
    ]


@LOOPS
def test_camry_dialogue(run: Any) -> None:
    audited, history, replies, seen = run(CAMRY_DIALOGUE)
    names = [n for n, _ in audited]
    # turn 1: the code looked the car up, audited with the caller's words
    assert audited[0] == (LOOKUP_TOOL, {"vehicle_text": "шины на камри"})
    # the model saw the sizes before its first round of turn 1
    first = json.dumps(seen[0][0], ensure_ascii=False)
    assert "215/55 R17" in first
    # one pair in the history, with the car the catalogue read — no vehicle_text
    assert _lookup_pairs(history) == [{"brand": "Toyota", "model": "Camry"}]
    assert "vehicle_text" not in json.dumps(history, ensure_ascii=False)
    # turn 2: other words, same car — looked up, no second pair
    assert names[:2] == [LOOKUP_TOOL, LOOKUP_TOOL]
    # turn 3: «летние» — the code searched the factory R17 size, summer
    search = [a for n, a in audited if n == "search_tires"]
    assert len(search) == 1
    assert (search[0]["width"], search[0]["profile"], search[0]["diameter"]) == (215, 55, 17)
    assert search[0]["season"] == "summer"
    assert names.count(LOOKUP_TOOL) == 2
    # the stud question of the summer turn is gone, the offer stays
    assert "шип" not in replies[2].lower()
    assert "Taurus" in replies[2]


@LOOPS
def test_one_utterance_car_diameter_season_searches_in_the_same_turn(run: Any) -> None:
    audited, _, replies, _ = run([("літні шини R17 на камрі", [], f"Є Taurus. {STUD_Q}")])
    assert [n for n, _ in audited] == [LOOKUP_TOOL, "search_tires"]
    assert audited[1][1]["diameter"] == 17 and audited[1][1]["width"] == 215
    assert "шип" not in replies[0].lower()


@LOOPS
def test_unresolved_car_leaves_no_trace_in_history(run: Any) -> None:
    audited, history, _, seen = run([("шини на тойоту", [], "Яка модель Тойоти?")])
    assert audited == [(LOOKUP_TOOL, {"vehicle_text": "шини на тойоту"})]
    assert _lookup_pairs(history) == []
    assert not any(
        isinstance(m.get("content"), list)
        and any(b.get("type") == "tool_result" for b in m["content"])
        for m in seen[0][0]
    )


@LOOPS
def test_model_lookup_feeds_the_request(run: Any) -> None:
    turns = [
        (
            "Тойота Камрі, шини потрібні",
            [[(LOOKUP_TOOL, {"brand": "Toyota", "model": "Camry"})]],
            "Який діаметр?",
        ),
        ("R17, літні", [], "Ось варіанти."),
    ]
    audited, _, _, _ = run(turns)
    assert [n for n, _ in audited] == [LOOKUP_TOOL, "search_tires"]
    assert audited[0][1] == {"brand": "Toyota", "model": "Camry"}  # the model's own
    assert (audited[1][1]["width"], audited[1][1]["diameter"]) == (215, 17)


@LOOPS
def test_sales_off_is_untouched(run: Any) -> None:
    audited, history, replies, _ = run(
        [("шины на камри", [], "Який рік?"), ("летние", [], f"Добре. {STUD_Q}")], sales=False
    )
    assert audited == []
    assert _lookup_pairs(history) == []
    assert STUD_Q in replies[1]


@LOOPS
def test_season_before_diameter_is_kept_after_a_lookup(run: Any) -> None:
    # Four factory sizes: nothing to write until the diameter. The season said
    # in between is kept because the lookup started the consultation.
    turns = [
        ("шины на камри", [], "Який діаметр?"),
        ("летние", [], "Який діаметр?"),
        ("R17", [], "Ось варіанти."),
    ]
    audited, _, _, _ = run(turns)
    search = [a for n, a in audited if n == "search_tires"]
    assert len(search) == 1
    assert (search[0]["diameter"], search[0]["season"]) == (17, "summer")


def test_a_wheel_diameter_is_not_the_tyre_request() -> None:
    gate = _gate()
    gate.note_model_call({}, copy.deepcopy(CAMRY))
    gate.note_turn("шини R17", {})
    gate.note_turn("а диски R18 є?", {})
    assert gate.apply({}) == {"sizes": ["215/55 R17"]}


@LOOPS
def test_the_model_repeating_the_forced_lookup_is_not_run_again(run: Any) -> None:
    turns = [
        (
            "шины на камри",
            [[(LOOKUP_TOOL, {"brand": "Toyota", "model": "Camry"})]],
            "Який діаметр?",
        )
    ]
    audited, _, _, _ = run(turns)
    assert [n for n, _ in audited] == [LOOKUP_TOOL]
