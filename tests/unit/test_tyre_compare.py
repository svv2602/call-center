"""Two tyres of the call's last search compared by the code, from EU labels.

Probe 2026-09-29 (`camry_compare_taurus_bridgestone`): «таурус лучше
бриджестоуна?» got a general brand answer (Taurus was 4th, beyond the three
the model sees); «а чем Туранза 6 лучше Т005?» got «в базі знань немає
порівняння» or «новіша модель з можливими покращеннями». Under sales the code
says the label classes of the named tyres before the first round; with sales
off nothing changes.

Expected phrases are built from the items' own labels here, never pasted
whole — a fixture that spelled the answer would pass a renderer that copies
the wrong field.
"""

from __future__ import annotations

import asyncio
import copy
import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, create_autospec

import pytest

from src.agent.agent import LLMAgent
from src.agent.streaming_loop import StreamingAgentLoop
from src.agent.tool_result_compressor import compress_tool_result
from src.agent.tyre_compare import (
    MAX_COMPARED,
    NO_LABEL,
    TyreCompare,
    compare_phrase,
    compare_said_note,
    is_compare_question,
    named_items,
)
from src.core.call_session import CallSession
from src.core.pipeline import merge_tire_query
from src.llm.models import LLMResponse, ToolCall, Usage
from src.llm.router import LLMRouter
from src.main import _build_tool_router
from src.store_client.client import StoreClient
from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection
from tests.unit.mocks.mock_llm_router import MockLLMRouter
from tests.unit.mocks.mock_tts import MockTTSEngine
from tests.unit.test_vehicle_lookup_gate import (
    _TOOLS,
    CAMRY,
    _last_bot,
    _policy,
    _stream_events,
)

# The neighbours' result for Camry 215/55 R17, summer (wave 2-C README).
ITEMS: list[dict[str, Any]] = [
    {"id": "a1", "brand": "Firestone", "model": "Roadhawk 2", "size": "215/55 R17 98W",
     "season": "summer", "price": 5184, "in_stock": True,
     "eu_label": {"fuel": "B", "wet": "A", "noise_db": 71}},
    {"id": "a2", "brand": "Bridgestone", "model": "Turanza T005", "size": "215/55 R17 94V",
     "season": "summer", "price": 5766, "in_stock": True,
     "eu_label": {"fuel": "A", "wet": "A", "noise_db": 71}},
    {"id": "a3", "brand": "Bridgestone", "model": "Turanza 6", "size": "215/55 R17 98W",
     "season": "summer", "price": 6687, "in_stock": True,
     "eu_label": {"fuel": "C", "wet": "B", "noise_db": 71}},
    {"id": "a4", "brand": "Taurus", "model": "SUMMER 3", "size": "215/55 R17 98W",
     "season": "summer", "price": 3450, "in_stock": True,
     "eu_label": {"fuel": "B", "wet": "B", "noise_db": 71}},
    {"id": "a5", "brand": "Matador", "model": "Hectorra 5", "size": "215/55 R17 98Y",
     "season": "summer", "price": 4425, "in_stock": True,
     "eu_label": {"fuel": "C", "wet": "B", "noise_db": 72}},
]  # fmt: skip
BY_MODEL = {i["model"]: i for i in ITEMS}


def _name(item: dict[str, Any]) -> str:
    return f"{item['brand']} {item['model']}"


def _clause(item: dict[str, Any]) -> str:
    """The label clause of one tyre, from its own label."""
    lab = item["eu_label"]
    return (
        f"{_name(item)}: мокра дорога {lab['wet']}, економія палива {lab['fuel']}, "
        f"шум {lab['noise_db']} дБ"
    )


def _gate(items: list[dict[str, Any]] | None = None) -> TyreCompare:
    gate = TyreCompare(sales_enabled=True)
    gate.note_search({"total": len(items or ITEMS), "items": copy.deepcopy(items or ITEMS)})
    return gate


# ── The question ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "таурус лучше бриджестоуна?",
        "а чем Туранза 6 лучше Т005?",
        "що краще — файрстоун чи матадор",
        "Туранза 6 чи Т005?",
        "таурус или бриджстоун",
        "порівняйте туранзу і таурус",
        "сравните их",
        "чим відрізняється туранза від таурусу",
        "чем отличается туранза от тауруса",
        "яка різниця між ними",
        "какая разница",
        "таурус гірший за бріджстоун?",
        "таурус хуже?",
    ],
)
def test_compare_question(text: str) -> None:
    assert is_compare_question(text)


@pytest.mark.parametrize(
    "text",
    [
        "беру Туранзу",
        "краще візьму Туранзу 6",
        "давайте Таурус, оформлюйте",
        "а є дешевше?",
        "а есть дешевле?",
        "таурус чи бріджстоун, скільки коштує",
        "яка ціна туранзи чи тауруса",
        "а чи є Туранза 6 і Таурус?",
        "чи можна Туранзу 6 або Таурус замовити",
        "підкажіть чи є таурус",
        "а чи Туранза 6 і Таурус у наявності",
        "туранза 6 і таурус чи є в наявності",
        "добре, дякую",
        "",
    ],
)
def test_not_a_compare_question(text: str) -> None:
    assert not is_compare_question(text)


# ── Which tyres ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "models"),
    [
        # a brand with two models → both; Taurus beyond the model's three
        ("таурус лучше бриджестоуна?", ["Turanza T005", "Turanza 6", "SUMMER 3"]),
        ("а чем Туранза 6 лучше Т005?", ["Turanza T005", "Turanza 6"]),
        ("туранза шість чи т нуль нуль п'ять", ["Turanza T005", "Turanza 6"]),
        ("Туранза 6 чи Т005?", ["Turanza T005", "Turanza 6"]),
        # a lettered version alone names its model, no family word needed
        ("таурус чи т005", ["Turanza T005", "SUMMER 3"]),
        # the version before the family word still picks only that version
        ("т005 туранза чи таурус", ["Turanza T005", "SUMMER 3"]),
        # case forms: «туранзу», «тауруса»
        ("туранзу шість чи тауруса", ["Turanza 6", "SUMMER 3"]),
        # a model of the brand named → only it, not the whole brand
        ("бріджстоун туранза шість чи таурус", ["Turanza 6", "SUMMER 3"]),
        # a family with no version → every model of it
        ("туранза чи таурус", ["Turanza T005", "Turanza 6", "SUMMER 3"]),
        ("що краще — файрстоун чи матадор", ["Roadhawk 2", "Hectorra 5"]),
        ("гекторра чи файрстоун", ["Roadhawk 2", "Hectorra 5"]),
        ("Bridgestone or Taurus", ["Turanza T005", "Turanza 6", "SUMMER 3"]),
        # not in the result
        ("мішлен чи нокіан", []),
        ("туранза п'ять", []),
        # a model word that is not a family name
        ("літня спорт", []),
    ],
)
def test_named_items(text: str, models: list[str]) -> None:
    assert [i["model"] for i in named_items(text, ITEMS)] == models


def test_a_version_not_in_the_result_picks_none_of_its_family() -> None:
    items = [BY_MODEL["Turanza 6"], BY_MODEL["SUMMER 3"]]
    said = "туранза т нуль нуль п'ять чи таурус"
    assert [i["model"] for i in named_items(said, items)] == ["SUMMER 3"]


def test_generic_model_word_is_no_family() -> None:
    items = [
        {"brand": "X", "model": "Sport 1"},
        {"brand": "Y", "model": "Winter Grip"},
    ]
    assert named_items("спорт чи вінтер", items) == []


# ── The phrase ────────────────────────────────────────────────────────


def test_phrase_is_the_labels_and_the_verdict() -> None:
    t005, t6 = BY_MODEL["Turanza T005"], BY_MODEL["Turanza 6"]
    phrase = _gate().plan("а чем Туранза 6 лучше Т005?")
    assert phrase is not None
    assert phrase.startswith(_clause(t005) + "; " + _clause(t6) + ".")
    # the verdict: lowest letter wins, equal dB is «однаковий»
    assert f"на мокрій дорозі краще {_name(t005)} ({t005['eu_label']['wet']})" in phrase
    assert f"економніша {_name(t005)} ({t005['eu_label']['fuel']})" in phrase
    assert f"шум однаковий — {t005['eu_label']['noise_db']} дБ" in phrase
    # nothing beyond the label
    for invented in ("новіш", "покращ", "сучасн", "якісн", "преміум"):
        assert invented not in phrase.lower()


def test_verdict_ties_and_quieter() -> None:
    fire, mat = BY_MODEL["Roadhawk 2"], BY_MODEL["Hectorra 5"]
    phrase = compare_phrase([fire, mat])
    assert f"тихіша {_name(fire)} ({fire['eu_label']['noise_db']} дБ)" in phrase
    t6, taurus = BY_MODEL["Turanza 6"], BY_MODEL["SUMMER 3"]
    tie = compare_phrase([t6, taurus])
    assert f"на мокрій дорозі однаково — {t6['eu_label']['wet']}" in tie
    assert f"економніша {_name(taurus)} ({taurus['eu_label']['fuel']})" in tie
    two = compare_phrase([BY_MODEL["Turanza T005"], fire, t6])
    assert f"на мокрій дорозі краще {_name(BY_MODEL['Turanza T005'])} і {_name(fire)}" in two


def test_no_label_says_so_and_is_out_of_the_verdict() -> None:
    bare = {k: v for k, v in BY_MODEL["SUMMER 3"].items() if k != "eu_label"}
    t6 = BY_MODEL["Turanza 6"]
    phrase = compare_phrase([t6, bare])
    assert f"по {_name(bare)} {NO_LABEL}" in phrase
    assert _clause(t6) in phrase
    # one label only → no verdict to draw
    assert "За етикеткою" not in phrase
    # both without a label
    none = compare_phrase([bare, {"brand": "Matador", "model": "Hectorra 5"}])
    assert none.count(NO_LABEL) == 2
    assert none[0].isupper()


def test_partial_label_says_only_what_it_has() -> None:
    a = {"brand": "A", "model": "One", "eu_label": {"wet": "B"}}
    b = {"brand": "B", "model": "Two", "eu_label": {"wet": "A", "noise_db": 70}}
    phrase = compare_phrase([a, b])
    assert "A One: мокра дорога B;" in phrase
    assert "економія палива" not in phrase.split("За етикеткою")[0].split(";")[0]
    assert "тихіша" not in phrase and "шум однаковий" not in phrase


# ── The gate ──────────────────────────────────────────────────────────


def test_gate_default_deny_and_sales_off() -> None:
    gate = _gate()
    assert gate.plan("беру Туранзу 6") is None
    assert gate.plan("а є дешевше?") is None
    assert gate.plan("мішлен чи таурус") is None  # one of the two is not in the result
    assert gate.plan("туранза 6 краща?") is None  # one tyre is no comparison
    # two tyres named, but a choice / a price question — the model's
    assert gate.plan("беру Туранзу 6, а не Таурус") is None
    assert gate.plan("скільки коштує таурус і туранза 6") is None
    assert gate.plan("туранза 6 і таурус") is None  # no question at all
    off = TyreCompare(sales_enabled=False)
    off.note_search({"items": copy.deepcopy(ITEMS)})
    assert off.last_items == []
    assert off.plan("таурус лучше бриджестоуна?") is None
    assert TyreCompare(sales_enabled=True).plan("таурус лучше бриджестоуна?") is None


def test_gate_keeps_the_last_result_only() -> None:
    gate = _gate()
    gate.note_search({"error": True, "reason": "x"})  # a refusal is not a result
    gate.note_search("Сервіс тимчасово не відповідає")
    gate.note_search({"error": "timeout"})
    assert len(gate.last_items) == len(ITEMS)
    gate.note_search({"total": 0, "items": []})  # a new search found nothing
    assert gate.plan("таурус лучше бриджестоуна?") is None
    gate.note_search({"total": 1, "items": [BY_MODEL["SUMMER 3"]]})
    assert gate.plan("таурус лучше бриджестоуна?") is None


def test_gate_keeps_every_item_not_only_three() -> None:
    gate = _gate()
    assert [i["model"] for i in gate.last_items] == [i["model"] for i in ITEMS]
    assert gate.last_items[3]["eu_label"] == ITEMS[3]["eu_label"]


def test_one_model_in_two_sizes_is_said_once() -> None:
    twin = {**BY_MODEL["Turanza 6"], "id": "a3b", "size": "215/60 R17"}
    gate = _gate([BY_MODEL["Turanza 6"], twin, BY_MODEL["SUMMER 3"]])
    phrase = gate.plan("туранза 6 чи таурус")
    assert phrase is not None
    assert phrase.count(_name(BY_MODEL["Turanza 6"]) + ":") == 1


def test_gate_caps_the_list() -> None:
    many = [
        {"brand": "Taurus", "model": f"Model {n}", "eu_label": {"wet": "B"}}
        for n in range(MAX_COMPARED + 1)
    ]
    gate = _gate([*many, BY_MODEL["Turanza 6"]])
    assert gate.plan("таурус чи туранза") is None
    gate = _gate([*many[: MAX_COMPARED - 1], BY_MODEL["Turanza 6"]])
    assert gate.plan("таурус чи туранза") is not None


def test_note_tells_the_model_it_was_said() -> None:
    phrase = compare_phrase([BY_MODEL["Turanza 6"], BY_MODEL["SUMMER 3"]])
    note = compare_said_note(phrase)
    assert f"«{phrase}»" in note
    assert "не додавай характеристик" in note
    assert compare_said_note(None) == ""


# ── The compressor ────────────────────────────────────────────────────


def test_compressor_sales_on_passes_the_label() -> None:
    out = json.loads(
        compress_tool_result("search_tires", {"total": 5, "items": ITEMS}, sales_enabled=True)
    )
    assert [i["eu_label"] for i in out["items"]] == [i["eu_label"] for i in ITEMS[:3]]


def test_compressor_sales_off_is_byte_identical() -> None:
    bare = [{k: v for k, v in i.items() if k != "eu_label"} for i in ITEMS]
    with_label = compress_tool_result("search_tires", {"total": 5, "items": ITEMS})
    without = compress_tool_result("search_tires", {"total": 5, "items": bare})
    assert with_label == without
    assert "eu_label" not in with_label


# ── Both loops ────────────────────────────────────────────────────────


def _router(sales: bool):
    session = CallSession(uuid.uuid4())
    store = create_autospec(StoreClient, instance=True)

    async def _resolve(text: str) -> dict[str, Any] | None:
        low = text.lower()
        if "камри" in low or "камрі" in low or "camry" in low:
            return {"brand": "Toyota", "model": "Camry"}
        return None

    async def _sizes(brand: str = "", model: str = "", year: int = 0, **_: Any) -> dict[str, Any]:
        return copy.deepcopy(CAMRY)

    async def _search(network: str = "", **params: Any) -> dict[str, Any]:
        return {"total": len(ITEMS), "items": copy.deepcopy(ITEMS)}

    store.resolve_vehicle_text.side_effect = _resolve
    store.get_vehicle_tire_sizes.side_effect = _sizes
    store.search_tires.side_effect = _search
    router = _build_tool_router(session, store_client=store, network_policy=_policy(sales))
    audited: list[tuple[str, dict[str, Any]]] = []

    async def _hook(name: str, args: dict[str, Any], *_: Any) -> None:
        audited.append((name, dict(args)))

    router.set_execute_hook(_hook)
    return router, session, audited


class _Seeing(MockLLMRouter):
    def __init__(self, responses: list[Any]) -> None:
        super().__init__(responses)
        self.systems: list[str] = []

    async def complete_stream(self, task: Any, messages: Any, **kwargs: Any):  # type: ignore[override]
        self.systems.append(kwargs.get("system") or "")
        async for e in super().complete_stream(task, messages, **kwargs):
            yield e


Round = list[tuple[str, dict[str, Any]]]


def _stream(turns: list[tuple[str, list[Round], str]], *, sales: bool = True):
    router, session, audited = _router(sales)
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
    systems: list[list[str]] = []
    for user_text, rounds, reply in turns:
        llm = _Seeing(_stream_events(rounds, reply))
        loop._llm_router = llm
        progress = None
        if sales:
            session.tire_query = merge_tire_query(
                session.tire_query,
                user_text,
                last_bot_text=_last_bot(history),
                consult_started=any(n == "get_vehicle_tire_sizes" for n, _ in audited),
            )
            progress = dict(session.tire_query) or None
        res = asyncio.run(loop.run_turn(user_text, history, tire_progress=progress))
        replies.append(res.spoken_text)
        systems.append(llm.systems)
    return audited, replies, systems


def _text(turns: list[tuple[str, list[Round], str]], *, sales: bool = True):
    router, session, audited = _router(sales)
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
    systems: list[list[str]] = []
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
        turn_systems: list[str] = []

        async def _complete(
            _task: Any, _messages: Any, *_a: Any, _r=responses, _s=turn_systems, **k: Any
        ):
            _s.append(k.get("system") or "")
            return _r.pop(0)

        agent._llm_router.complete = AsyncMock(side_effect=_complete)
        # The live pipeline merges the caller's words into the session before
        # the turn; the season guard of the search handler reads it.
        session.tire_query = merge_tire_query(
            dict(agent._tire_query), user_text, last_bot_text=_last_bot(history)
        )
        text, history = asyncio.run(agent.process_message(user_text, history))
        session.tire_query = dict(agent._tire_query)
        replies.append(text)
        systems.append(turn_systems)
    return audited, replies, systems


LOOPS = pytest.mark.parametrize("run", [_stream, _text], ids=["streaming", "text"])

SEARCH_ARGS = {"width": 215, "profile": 55, "diameter": 17, "season": "summer"}
MODEL_REPLY = "Добре."

#: The model searches itself (the model's tool call site): the request is not
#: complete in the caller's words, so the code's search gate stays out.
MODEL_SEARCH = [
    ("літні шини, шукайте", [[("search_tires", SEARCH_ARGS)]], "Є Firestone, Bridgestone, Taurus."),
]
#: The code searches (the forced search site): the probe dialogue.
FORCED_SEARCH = [
    ("шины на камри", [], "Який діаметр?"),
    ("шины R17 на Toyota Camry", [], "Літні чи зимові?"),
    ("летние", [], "Є Firestone, Bridgestone, Taurus."),
]


def _expected(*models: str) -> str:
    return compare_phrase([BY_MODEL[m] for m in models])


@LOOPS
@pytest.mark.parametrize("dialogue", [MODEL_SEARCH, FORCED_SEARCH], ids=["model", "forced"])
def test_taurus_vs_bridgestone(run: Any, dialogue: list[Any]) -> None:
    audited, replies, systems = run([*dialogue, ("таурус лучше бриджестоуна?", [], MODEL_REPLY)])
    assert [n for n, _ in audited].count("search_tires") == 1
    phrase = _expected("Turanza T005", "Turanza 6", "SUMMER 3")
    # the code's phrase first, the model goes on after it
    assert replies[-1] == f"{phrase} {MODEL_REPLY}"
    for item in (BY_MODEL["Turanza T005"], BY_MODEL["Turanza 6"], BY_MODEL["SUMMER 3"]):
        assert _clause(item) in replies[-1]
    # the model is told, on its first round of the turn
    assert systems[-1][0].endswith(compare_said_note(phrase))
    # the turns before carry no note
    assert all("порівняння шин" not in s for turn in systems[:-1] for s in turn)


@LOOPS
@pytest.mark.parametrize("dialogue", [MODEL_SEARCH, FORCED_SEARCH], ids=["model", "forced"])
def test_turanza_6_vs_t005(run: Any, dialogue: list[Any]) -> None:
    _, replies, systems = run([*dialogue, ("а чем Туранза 6 лучше Т005?", [], MODEL_REPLY)])
    phrase = _expected("Turanza T005", "Turanza 6")
    assert replies[-1] == f"{phrase} {MODEL_REPLY}"
    assert compare_said_note(phrase) in systems[-1][0]


@LOOPS
@pytest.mark.parametrize(
    "utterance",
    [
        "беру Туранзу 6",
        "беру Туранзу 6, а не Таурус",
        "а є дешевше?",
        "мішлен чи таурус",
        "туранза 6 краща?",
    ],
)
def test_not_a_comparison_is_the_models(run: Any, utterance: str) -> None:
    _, replies, systems = run([*MODEL_SEARCH, (utterance, [], MODEL_REPLY)])
    assert replies[-1] == MODEL_REPLY
    assert all("порівняння шин" not in s for s in systems[-1])


@LOOPS
def test_no_search_in_the_call_says_nothing(run: Any) -> None:
    _, replies, _ = run([("таурус лучше бриджестоуна?", [], MODEL_REPLY)])
    assert replies == [MODEL_REPLY]


@LOOPS
def test_one_phrase_per_turn_said_again_only_when_asked(run: Any) -> None:
    turns = [
        *MODEL_SEARCH,
        ("таурус лучше бриджестоуна?", [], MODEL_REPLY),
        ("зрозуміло", [], MODEL_REPLY),
        ("а таурус лучше бриджестоуна?", [], MODEL_REPLY),
    ]
    _, replies, _ = run(turns)
    phrase = _expected("Turanza T005", "Turanza 6", "SUMMER 3")
    assert replies[-3].count(phrase) == 1
    assert replies[-2] == MODEL_REPLY
    assert replies[-1] == f"{phrase} {MODEL_REPLY}"


@LOOPS
def test_sales_off_is_untouched(run: Any) -> None:
    turns = [*MODEL_SEARCH, ("таурус лучше бриджестоуна?", [], MODEL_REPLY)]
    _, replies, systems = run(turns, sales=False)
    assert replies[-1] == MODEL_REPLY
    assert all("порівняння шин" not in s for turn in systems for s in turn)
