"""«Купити резину» with no car and no size: the code asks the first question.

Goldset №8 (ProKoleso): «там мені купити треба резину» / «нужны шины» got the
menu question «Підбір та замовлення шин чи є питання щодо товарів або
послуг?». Under sales the code's question is the whole reply and no LLM round
runs; with sales off nothing changes.

The loop tests go through the real ``main._build_tool_router`` on an
autospecced ``StoreClient`` (the harness of `test_vehicle_lookup_gate`). Each
asserts that the reply is the code's phrase AND that the model was never asked
— a neighbour answering with a car question would not pass both.
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any
from unittest.mock import AsyncMock, create_autospec

import pytest

from src.agent.agent import LLMAgent
from src.agent.streaming_loop import StreamingAgentLoop
from src.agent.vague_tyre_buy import (
    ASK_CAR_OR_SIZE,
    VagueBuyGate,
    names_a_car,
    says_wants_tyres,
    vague_tyre_buy,
)
from src.core.pipeline import merge_tire_query
from src.llm.models import LLMResponse, Usage
from src.llm.router import LLMRouter
from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection
from tests.unit.mocks.mock_llm_router import MockLLMRouter
from tests.unit.mocks.mock_tts import MockTTSEngine
from tests.unit.test_vehicle_lookup_gate import (
    _TOOLS,
    _policy,
    _router,
    _Snapshotting,
    _stream_events,
)

MENU = "Підкажіть, з чим можу допомогти: підбір та замовлення шин чи є питання щодо товарів або послуг?"


# ── The utterance ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "там мені купити треба резину",
        "там мне купить надо резину",
        "нужны шины",
        "потрібні шини",
        "хочу купити зимові шини",
        "підберіть мені гуму на літо",
        "подобрать резину",
        "треба чотири колеса купити",
        "Добрий день, шукаю покришки",
        "мне нужна резина на мою машину",
        "хочу купити шини для мого авто",
    ],
)
def test_wants_tyres(text: str) -> None:
    assert says_wants_tyres(text)
    assert not names_a_car(text)


@pytest.mark.parametrize(
    "text",
    [
        # fitting, storage, service — not a purchase
        "записатися на шиномонтаж",
        "треба перевзути шини",
        "мені треба поміняти резину",
        "треба відремонтувати колесо, пробив",
        "потрібне зберігання шин",
        "треба балансування коліс",
        # wheels — with and without a tyre noun next to them
        "потрібні литі диски",
        "треба купити шини і литі диски",
        "нужны колеса и диски",
        # a size or a diameter — the search gate's, not this one's
        "нужны шины 205/55 R16",
        "треба шини на шістнадцятий радіус",
        "купити резину R17",
        # negation, already bought
        "ні, шини не треба",
        "мені не потрібні шини",
        "я вже купив шини",
        # another topic in the same breath
        "хочу купити шини, скільки коштує доставка",
        "треба шини, можна оплатити частинами?",
        "я замовляв шини, коли відправите",
        "хочу шини, з'єднайте з оператором",
        "яка гарантія на шини, хочу купити",
        # the corpus (prod, 30 days): «хочу» + another verb is a question
        "скажи там мне звонила по поводу шин Я хотел уточнить их выписку",
        "хочемо узнати ціну резини",
        "мені треба дізнатися ціну на шини",
        # «хочу» wants only what follows it
        "хочу подякувати, шини відмінні, все їздить",
        # a network-facts topic with no word of the other-topic list
        "нужны шины, можно картой?",
        # a long utterance carries more than the want
        "добрий день мені треба купити резину але спочатку я розкажу вам довгу історію про свою поїздку сьогодні",
        # no tyre noun / no want
        "так",
        "ні",
        "дякую",
        "треба подумати",
        "шини",
        "Бріджстоун",
        "",
    ],
)
def test_default_deny(text: str) -> None:
    assert not says_wants_tyres(text)


def test_a_named_car_is_the_lookup_gates() -> None:
    assert names_a_car("нужны шины на камри")
    assert names_a_car("треба шини для Шкоди Октавії")
    # no «на / для» in front of the car — still the car's
    assert names_a_car("Тойота Камрі, шини потрібні")
    assert not names_a_car("хочу собі нову гуму")
    assert not names_a_car("нужны шины на машину")


def _pred(text: str = "нужны шины", **kw: Any) -> bool:
    args: dict[str, Any] = {
        "query": {},
        "history": [],
        "tools_called": set(),
        "last_bot_text": "",
        "in_fitting": False,
    }
    args.update(kw)
    return vague_tyre_buy(text, **args)


def test_predicate_conditions_one_by_one() -> None:
    assert _pred()
    assert _pred(query={"season": "winter", "brands": ["Michelin"]})
    assert not _pred("нужны шины на камри")
    assert not _pred(query={"sizes": ["205/55 R16"]})
    assert not _pred(query={"diameter": 16})
    assert not _pred(tools_called={"get_vehicle_tire_sizes"})
    assert not _pred(tools_called={"search_tires"})
    history = [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "x", "name": "search_tires", "input": {}}],
        }
    ]
    assert not _pred(history=history)
    assert not _pred(last_bot_text="Яке у вас авто?")
    assert not _pred(last_bot_text="Підкажіть розмір шин.")
    assert _pred(last_bot_text="Доброго дня! Як можу до вас звертатися?")
    assert not _pred(in_fitting=True)


def test_gate_once_per_call_and_only_under_sales() -> None:
    kw: dict[str, Any] = {
        "query": {},
        "history": [],
        "tools_called": set(),
        "last_bot_text": "",
        "in_fitting": False,
    }
    gate = VagueBuyGate(sales_enabled=True)
    assert gate.plan("нужны шины", **kw) == ASK_CAR_OR_SIZE
    assert gate.plan("нужны шины", **kw) is None
    assert VagueBuyGate(sales_enabled=False).plan("нужны шины", **kw) is None


def test_the_phrase_is_read_as_a_car_question_next_turn() -> None:
    # The answer «Тойота Камрі» must reach the forced car lookup.
    from src.agent.vehicle_lookup_gate import named_car_words

    assert ASK_CAR_OR_SIZE.endswith("?")
    assert named_car_words("Тойота Камрі", ASK_CAR_OR_SIZE)[0] == ["тойота", "камрі"]


# ── Both loops ────────────────────────────────────────────────────────


def _stream(
    turns: list[tuple[str, str]],
    *,
    sales: bool = True,
    active: set[str] | None = None,
    called: set[str] | None = None,
    history: list[dict[str, Any]] | None = None,
):
    router, session, _store, audited = _router(sales)
    history = copy.deepcopy(history or [])
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
    asked: list[int] = []
    for user_text, reply in turns:
        llm = _Snapshotting(_stream_events([], reply))
        loop._llm_router = llm
        progress = None
        if sales:
            session.tire_query = merge_tire_query(session.tire_query, user_text)
            progress = dict(session.tire_query) or None
        res = asyncio.run(
            loop.run_turn(
                user_text,
                history,
                tire_progress=progress,
                active_scenarios=active,
                tools_called=called,
            )
        )
        replies.append(res.spoken_text)
        asked.append(len(llm.seen))
    return audited, history, replies, asked


def _text(
    turns: list[tuple[str, str]],
    *,
    sales: bool = True,
    active: set[str] | None = None,
    called: set[str] | None = None,
    history: list[dict[str, Any]] | None = None,
):
    router, _session, _store, audited = _router(sales)
    history = copy.deepcopy(history or [])
    agent = LLMAgent(
        api_key="test-key",
        system_prompt="base",
        llm_router=create_autospec(LLMRouter, instance=True),
        tool_router=router,
        tools=_TOOLS,
        network_policy=_policy(sales),
    )
    replies: list[str] = []
    asked: list[int] = []
    for user_text, reply in turns:
        seen: list[Any] = []

        async def _complete(_task: Any, messages: Any, *_a: Any, _s=seen, _r=reply, **_k: Any):
            _s.append(copy.deepcopy(messages))
            return LLMResponse(text=_r, usage=Usage(1, 1), provider="test")

        agent._llm_router.complete = AsyncMock(side_effect=_complete)
        text, history = asyncio.run(
            agent.process_message(user_text, history, active_scenarios=active, tools_called=called)
        )
        replies.append(text)
        asked.append(len(seen))
    return audited, history, replies, asked


LOOPS = pytest.mark.parametrize("run", [_stream, _text], ids=["streaming", "text"])


@LOOPS
@pytest.mark.parametrize("utterance", ["там мені купити треба резину", "нужны шины"])
def test_goldset_utterances_get_the_codes_question(run: Any, utterance: str) -> None:
    audited, history, replies, asked = run([(utterance, MENU)])
    assert replies == [ASK_CAR_OR_SIZE]
    assert asked == [0]  # no LLM round: no menu, no second question
    assert audited == []
    last = history[-1]
    assert last["role"] == "assistant"
    assert last["content"] == [{"type": "text", "text": ASK_CAR_OR_SIZE}]


@LOOPS
def test_the_answer_goes_to_the_model_and_the_car_lookup(run: Any) -> None:
    audited, _, replies, asked = run([("нужны шины", MENU), ("Тойота Камрі", "Який діаметр?")])
    assert replies[0] == ASK_CAR_OR_SIZE
    assert asked == [0, 1]
    assert replies[1] == "Який діаметр?"
    # the code's question made «Тойота Камрі» a car answer for the lookup gate
    assert audited and audited[0][0] == "get_vehicle_tire_sizes"


@LOOPS
def test_once_per_call(run: Any) -> None:
    turns = [
        ("нужны шины", MENU),
        ("а ви працюєте сьогодні", "Так, працюємо."),
        ("нужны шины", MENU),
    ]
    _, _, replies, asked = run(turns)
    assert replies[0] == ASK_CAR_OR_SIZE
    assert asked == [0, 1, 1]
    assert replies[2] == MENU


@LOOPS
def test_sales_off_is_untouched(run: Any) -> None:
    _, _, replies, asked = run([("там мені купити треба резину", MENU)], sales=False)
    assert replies == [MENU]
    assert asked == [1]


@LOOPS
def test_a_car_named_is_left_to_the_lookup_gate(run: Any) -> None:
    audited, _, replies, asked = run([("нужны шины на камри", "Який діаметр?")])
    assert replies == ["Який діаметр?"]
    assert asked == [1]
    assert audited[0][0] == "get_vehicle_tire_sizes"


@LOOPS
def test_a_size_named_is_left_to_the_model(run: Any) -> None:
    _, _, replies, asked = run([("нужны шины 205/55 R16", "Літні чи зимові?")])
    assert replies == ["Літні чи зимові?"]
    assert asked == [1]


@LOOPS
def test_in_a_fitting_booking_the_model_answers(run: Any) -> None:
    _, _, replies, asked = run([("треба купити резину", "Добре.")], active={"fitting"})
    assert replies == ["Добре."]
    assert asked == [1]


@LOOPS
def test_a_network_fact_turn_is_the_facts(run: Any) -> None:
    # «доставка» is another topic: the gate stays out (default deny), and the
    # model is asked even when the facts module has nothing to say.
    _, _, _, asked = run([("нужны шины, и сколько стоит доставка?", "Доставка безкоштовна.")])
    assert asked == [1]


@LOOPS
def test_a_size_said_earlier_is_the_requests(run: Any) -> None:
    # turn 1 gives the size (no season — no forced search), the model answers
    # without a car or size word; turn 2 «нужны шины» — the request has sizes.
    audited, _, replies, asked = run([("205/55 R16", "Добре."), ("нужны шины", "Літні чи зимові?")])
    assert audited == []
    assert asked == [1, 1]
    assert replies[1] == "Літні чи зимові?"


@LOOPS
def test_the_bot_already_asked_for_the_car(run: Any) -> None:
    _, _, replies, asked = run([("добрий день", "Яке у вас авто?"), ("нужны шины", "Добре.")])
    assert asked == [1, 1]
    assert replies[1] == "Добре."


@LOOPS
def test_a_search_in_the_call_is_the_pick_under_way(run: Any) -> None:
    _, _, replies, asked = run([("нужны шины", "Добре.")], called={"search_tires"})
    assert asked == [1]
    assert replies == ["Добре."]


@LOOPS
def test_a_search_in_the_history_is_the_pick_under_way(run: Any) -> None:
    # A history handed over with a search in it (tools_called not passed).
    earlier = [
        {"role": "user", "content": "літні 205/55 R16"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "h1", "name": "search_tires", "input": {"width": 205}}
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "h1", "content": "{}"}],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "Є варіанти."}]},
    ]
    _, _, replies, asked = run([("нужны шины", "Добре.")], history=earlier)
    assert asked == [1]
    assert replies == ["Добре."]
