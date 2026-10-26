"""The model's «найдешевша / новинка / переможець тестів» is judged against the result.

A port of the tshina web consultant's answer guards (`PriceClaimGuard`,
`stripNoveltyClaim`, `stripUnbackedClaims`, bef4f5d66, 2026-10-26). The shown
tyres are in the live shape of `StoreClient._tire_item` (``id, brand, model,
size, season, price, in_stock``); the prices are tshina's Tiguan R18 dialog,
where «CrossWind … самая доступная» was said at 4 314 ₴ against 3 606 ₴.

Every refusal is checked by *which* rule fired (the metric label): the network
claim guard and the wheel-fit guard sit next to this one in the chain.
"""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Any
from unittest.mock import create_autospec

import pytest

from scripts.configure_tenants import TVOYA_SHINA_CONFIG_PATCH
from src.agent import tire_claim_guard as guard
from src.agent.agent import LLMAgent, ToolRouter
from src.agent.network_policy import NetworkPolicy
from src.agent.streaming_loop import StreamingAgentLoop
from src.agent.tire_claim_guard import (
    REPLACEMENTS,
    RULE_CHEAPEST,
    RULE_NOVELTY,
    RULE_UNBACKED_RATING,
    RULES,
    TireClaimState,
    guard_tire_claims,
    guard_tire_claims_text,
)
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
from src.monitoring.metrics import tire_claim_refused_total
from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection
from tests.unit.mocks.mock_llm_router import MockLLMRouter
from tests.unit.mocks.mock_tts import MockTTSEngine


def _item(id_: str, brand: str, model: str, price: int, **extra: Any) -> dict[str, Any]:
    """One `StoreClient._tire_item`."""
    return {
        "id": id_,
        "brand": brand,
        "model": model,
        "size": "235/55 R18",
        "season": "winter",
        "price": price,
        "in_stock": True,
        **extra,
    }


ITEMS = [
    _item("1", "Bridgestone", "Blizzak 6", 8625),
    _item("2", "Firestone", "WinterHawk 4", 6630),
    _item("3", "CrossWind", "Grip Peak Winter", 4314),
    _item("4", "Rydanz", "NORDICA NR01", 3882),
    _item("5", "Doublestar", "WINTERKING DW02", 3606),
]
RESULT = {"total": len(ITEMS), "items": ITEMS}

ASKED = "зимові шини 235/55 R18 на Тігуан"
CHEAP_LIE = "CrossWind Grip Peak Winter — найдешевша серед показаних."
OFFER = "Є Bridgestone Blizzak 6 за 8625 гривень."


def _state(result: Any = RESULT, *, sales: bool = True) -> TireClaimState:
    state = TireClaimState(sales_enabled=sales)
    state.note("search_tires", copy.deepcopy(result))
    return state


def _count(rule: str) -> float:
    return tire_claim_refused_total.labels(rule=rule)._value.get()


def _judge(text: str, user_text: str = ASKED, state: TireClaimState | None = None) -> str:
    return guard_tire_claims_text(text, state or _state(), user_text)


def _fired(text: str, user_text: str = ASKED, state: TireClaimState | None = None) -> set[str]:
    """Which rules refused ``text`` (by the metric, not by the wording)."""
    before = {rule: _count(rule) for rule, _ in RULES}
    _judge(text, user_text, state)
    return {rule for rule, _ in RULES if _count(rule) > before[rule]}


@pytest.fixture(autouse=True)
def _guard_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(guard.ENV_FLAG, raising=False)


# ── cheapest ─────────────────────────────────────────────────────────────


class TestCheapest:
    @pytest.mark.parametrize(
        "sentence",
        [
            CHEAP_LIE,
            "Rydanz NORDICA NR01 — самая дешёвая из показанных.",
            "Bridgestone Blizzak 6 — найдоступніший варіант.",
            "Раджу Firestone WinterHawk 4, бо вона найбюджетніша.",
            "У CrossWind Grip Peak Winter мінімальна ціна.",
        ],
    )
    def test_false_claim_about_a_shown_tyre_is_replaced(self, sentence: str) -> None:
        assert _fired(sentence) == {RULE_CHEAPEST}
        assert _judge(sentence) == REPLACEMENTS[RULE_CHEAPEST]

    def test_she_refers_to_the_tyre_named_before(self) -> None:
        text = "Раджу CrossWind Grip Peak Winter. Вона також найдоступніша за ціною."
        assert _judge(text) == f"Раджу CrossWind Grip Peak Winter. {REPLACEMENTS[RULE_CHEAPEST]}"

    @pytest.mark.parametrize(
        "sentence",
        [
            "Doublestar WINTERKING DW02 — найдешевший варіант.",
            "Самая доступная — Doublestar WINTERKING DW02.",
        ],
    )
    def test_true_claim_passes(self, sentence: str) -> None:
        assert _judge(sentence) == sentence

    def test_equal_lowest_price_is_true(self) -> None:
        items = [*ITEMS, _item("6", "Kormoran", "Snow", 3606)]
        sentence = "Kormoran Snow — найдешевша серед показаних."
        assert _judge(sentence, state=_state({"items": items})) == sentence

    @pytest.mark.parametrize(
        "sentence",
        [
            "Найдешевша модель — у кінці списку.",
            "CrossWind Grip Peak Winter і Rydanz NORDICA NR01 — найдешевші.",
            "CrossWind Grip Peak Winter дешевша, ніж Bridgestone Blizzak 6.",
            "Bridgestone Blizzak 6 — самая доступная среди премиальных.",
            "Вона найдешевша.",
            "Michelin X-Ice Snow — найдешевша.",
        ],
    )
    def test_ambiguous_passes(self, sentence: str) -> None:
        assert _fired(sentence) == set()
        assert _judge(sentence) == sentence

    @pytest.mark.parametrize(
        "sentence",
        [
            "CrossWind Grip Peak Winter не найдешевша, але тиха.",
            "CrossWind Grip Peak Winter — найдешевша?",
            "Якщо потрібна найдешевша, CrossWind Grip Peak Winter не підійде.",
        ],
    )
    def test_negation_and_question_pass(self, sentence: str) -> None:
        assert _judge(sentence) == sentence

    def test_named_tyre_without_a_price_passes(self) -> None:
        # A catalogue row priced 0 (on request): nothing to compare it by.
        items = [*ITEMS, _item("6", "Kormoran", "Snow", 0)]
        sentence = "Kormoran Snow — найдешевша серед показаних."
        assert _judge(sentence, state=_state({"items": items})) == sentence

    def test_one_shown_price_is_nothing_to_compare(self) -> None:
        state = _state({"items": [ITEMS[2]]})
        assert _judge(CHEAP_LIE, state=state) == CHEAP_LIE


# ── novelty ──────────────────────────────────────────────────────────────


class TestNovelty:
    @pytest.mark.parametrize(
        "sentence",
        [
            "Firestone WinterHawk 4 — новинка сезону.",
            "Це нова модель на ринку.",
            "Ця модель щойно з'явилась.",
            "Эта шина только появилась на рынке.",
        ],
    )
    def test_unasked_novelty_is_replaced(self, sentence: str) -> None:
        assert _fired(sentence) == {RULE_NOVELTY}
        assert _judge(sentence) == REPLACEMENTS[RULE_NOVELTY]

    def test_novelty_as_a_reason_loses_only_its_clause(self) -> None:
        text = "У Firestone WinterHawk 4 відгуків немає, бо вона щойно з'явилась на ринку."
        assert _judge(text) == "У Firestone WinterHawk 4 відгуків немає."

    @pytest.mark.parametrize("user_text", ["є щось нове?", "покажіть новинки", "свіжі шини є?"])
    def test_caller_asked_about_new_tyres(self, user_text: str) -> None:
        sentence = "Firestone WinterHawk 4 — новинка сезону."
        assert _judge(sentence, user_text) == sentence

    @pytest.mark.parametrize("sentence", ["Це не новинка.", "Чи є новіші моделі?"])
    def test_negation_and_question_pass(self, sentence: str) -> None:
        assert _judge(sentence) == sentence

    def test_knowledge_base_turn_is_silent(self) -> None:
        state = _state()
        state.note("search_knowledge_base", {"articles": []})
        sentence = "Firestone WinterHawk 4 — новинка сезону."
        assert _judge(sentence, state=state) == sentence
        state.begin_turn()
        assert _judge(sentence, state=state) == REPLACEMENTS[RULE_NOVELTY]


# ── unbacked rating ──────────────────────────────────────────────────────


class TestUnbackedRating:
    @pytest.mark.parametrize(
        "sentence",
        [
            "Bridgestone Blizzak 6 — переможець тестів ADAC.",
            "Вона найкраща за тестами.",
            "Це лідер рейтингу.",
            "Ця шина — номер один серед зимових.",
            "Рейтинг цієї шини 4.7 з 5.",
            "У всіх моделей високий рейтинг.",
        ],
    )
    def test_claim_without_data_is_replaced(self, sentence: str) -> None:
        assert _fired(sentence) == {RULE_UNBACKED_RATING}
        assert _judge(sentence) == REPLACEMENTS[RULE_UNBACKED_RATING]

    def test_tests_on_a_shown_tyre_back_the_claim(self) -> None:
        items = [
            *ITEMS[:-1],
            _item("5", "Doublestar", "WINTERKING DW02", 3606, tests=[{"place": 1}]),
        ]
        sentence = "Bridgestone Blizzak 6 — переможець тестів ADAC."
        assert _judge(sentence, state=_state({"items": items})) == sentence

    def test_shown_rating_backs_the_number(self) -> None:
        items = [*ITEMS[:-1], _item("5", "Doublestar", "WINTERKING DW02", 3606, rating=4.7)]
        sentence = "Рейтинг цієї шини 4.7 з 5."
        assert _judge(sentence, state=_state({"items": items})) == sentence

    def test_knowledge_base_turn_is_silent(self) -> None:
        # «Один із лідерів тестів ADAC» is in the brand articles: backed.
        state = _state()
        state.note("search_knowledge_base", {"articles": []})
        sentence = "Continental — один із лідерів тестів ADAC."
        assert _judge(sentence, state=state) == sentence

    @pytest.mark.parametrize("sentence", ["Вона не переможець тестів.", "Чи є дані тестів?"])
    def test_negation_and_question_pass(self, sentence: str) -> None:
        assert _judge(sentence) == sentence

    @pytest.mark.parametrize(
        "sentence",
        [
            "Варіант номер один — Bridgestone Blizzak 6 за 4200 гривень.",
            "Номер один у списку — Firestone.",
            "Ось топ-3 варіанти у вашому розмірі.",
            "ADAC щороку тестує зимові шини.",
        ],
    )
    def test_offer_listing_and_bare_source_pass(self, sentence: str) -> None:
        # Replacing an offer sentence would lose the tyre and its price.
        assert _judge(sentence) == sentence

    @pytest.mark.parametrize(
        "sentence", ["Це номер один у рейтингу продажів.", "Вона в топ-3 серед зимових."]
    )
    def test_ranking_number_is_replaced(self, sentence: str) -> None:
        assert _fired(sentence) == {RULE_UNBACKED_RATING}


# ── the filter: replacement, once, switch, sales scope ───────────────────


def test_replacement_said_once_per_reply() -> None:
    text = "Firestone — новинка. Bridgestone — новинка теж. Обидві тихі."
    assert _judge(text) == f"{REPLACEMENTS[RULE_NOVELTY]} Обидві тихі."


def test_untouched_reply_is_returned_as_is() -> None:
    assert _judge(OFFER) == OFFER


@pytest.mark.parametrize("value", ["0", "false", "no", "off", " OFF "])
def test_switch_off_leaves_the_text(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(guard.ENV_FLAG, value)
    assert _judge(CHEAP_LIE) == CHEAP_LIE
    assert asyncio.run(_collect([SentenceReady(CHEAP_LIE)], _state())) == [CHEAP_LIE]


def test_switch_on_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(guard.ENV_FLAG, "1")
    assert _judge(CHEAP_LIE) == REPLACEMENTS[RULE_CHEAPEST]


def test_sales_off_state_notes_nothing() -> None:
    state = _state(sales=False)
    assert state.items == []
    assert _judge(CHEAP_LIE, state=state) == CHEAP_LIE


def test_refusal_leaves_the_last_result() -> None:
    state = _state()
    state.note("search_tires", {"error": True, "reason": "no_size"})
    assert len(state.items) == len(ITEMS)


def test_metric_counts_the_rule() -> None:
    before = _count(RULE_CHEAPEST)
    _judge(CHEAP_LIE)
    assert _count(RULE_CHEAPEST) == before + 1


def test_log_names_call_and_rule(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO", logger=guard.__name__):
        guard_tire_claims_text(CHEAP_LIE, _state(), ASKED, call_id="call-7")
    assert "call=call-7" in caplog.text
    assert "rule=cheapest" in caplog.text


# ── the stream filter ────────────────────────────────────────────────────


async def _collect(events: list[Any], state: TireClaimState, user_text: str = ASKED) -> list[str]:
    async def _gen():
        for e in events:
            yield e

    return [e.text async for e in guard_tire_claims(_gen(), state, user_text) if hasattr(e, "text")]


class TestStream:
    def test_replaces_a_claim_sentence(self) -> None:
        out = asyncio.run(_collect([SentenceReady(CHEAP_LIE), SentenceReady(OFFER)], _state()))
        assert out == [REPLACEMENTS[RULE_CHEAPEST], OFFER]

    def test_fragments_judged_as_one_sentence(self) -> None:
        # «найдешевша.» alone names no tyre; held with its head it does.
        events = [
            SentenceReady("CrossWind Grip Peak Winter, якщо чесно,"),
            SentenceReady("найдешевша."),
        ]
        assert asyncio.run(_collect(events, _state())) == [REPLACEMENTS[RULE_CHEAPEST]]

    def test_she_across_sentences(self) -> None:
        events = [
            SentenceReady("Раджу CrossWind Grip Peak Winter."),
            SentenceReady("Вона найдешевша."),
        ]
        assert asyncio.run(_collect(events, _state())) == [
            "Раджу CrossWind Grip Peak Winter.",
            REPLACEMENTS[RULE_CHEAPEST],
        ]

    def test_once_per_stream(self) -> None:
        events = [
            SentenceReady("Firestone — новинка."),
            SentenceReady("Bridgestone — теж новинка."),
        ]
        assert asyncio.run(_collect(events, _state())) == [REPLACEMENTS[RULE_NOVELTY]]


# ── wiring: both loops ───────────────────────────────────────────────────

_TOOL_NAMES = ("search_tires", "search_disks", "get_vehicle_tire_sizes", "search_knowledge_base")
_TOOLS = [{"name": n, "description": "", "input_schema": {"type": "object"}} for n in _TOOL_NAMES]
_SEARCH = ("search_tires", {"width": 235, "profile": 55, "diameter": 18, "season": "winter"})


def _policy(sales: bool) -> NetworkPolicy:
    return NetworkPolicy.from_tenant_config({**TVOYA_SHINA_CONFIG_PATCH, "sales_enabled": sales})


def _router() -> ToolRouter:
    router = ToolRouter()
    for name in _TOOL_NAMES:

        async def _run(_name: str = name, **_: Any) -> dict[str, Any]:
            return copy.deepcopy(RESULT) if _name == "search_tires" else {"items": []}

        router.register(name, _run)
    return router


def _stream(reply: str, *, sales: bool) -> str:
    name, args = _SEARCH
    rounds = [
        [
            ToolCallStart(id="s0", name=name),
            ToolCallDelta(id="s0", arguments_chunk=json.dumps(args)),
            ToolCallEnd(id="s0"),
            StreamDone(stop_reason="tool_use", usage=Usage(1, 1)),
        ],
        [TextDelta(text=reply), StreamDone(stop_reason="end_turn", usage=Usage(1, 1))],
    ]
    loop = StreamingAgentLoop(
        llm_router=MockLLMRouter(rounds),
        tool_router=_router(),
        tts=MockTTSEngine(),
        conn=MockAudioSocketConnection(),
        barge_in_event=asyncio.Event(),
        tools=_TOOLS,
        system_prompt="Test system prompt",
        network_policy=_policy(sales),
    )
    return asyncio.run(loop.run_turn(ASKED, [])).spoken_text


def _text(reply: str, *, sales: bool) -> str:
    name, args = _SEARCH
    llm_router = create_autospec(LLMRouter, instance=True)
    llm_router.complete.side_effect = [
        LLMResponse(
            text="",
            tool_calls=[ToolCall(id="t0", name=name, arguments=args)],
            stop_reason="tool_use",
            usage=Usage(1, 1),
            provider="test",
        ),
        LLMResponse(text=reply, usage=Usage(1, 1), provider="test"),
    ]
    agent = LLMAgent(
        api_key="test-key",
        system_prompt="base",
        llm_router=llm_router,
        tool_router=_router(),
        tools=_TOOLS,
        network_policy=_policy(sales),
    )
    text, _ = asyncio.run(agent.process_message(ASKED, []))
    return text


LOOPS = pytest.mark.parametrize("run", [_stream, _text], ids=["streaming", "text"])


@LOOPS
def test_loop_replaces_the_claim_under_sales(run: Any) -> None:
    before = _count(RULE_CHEAPEST)
    said = run(f"{CHEAP_LIE} {OFFER}", sales=True)
    assert CHEAP_LIE not in said
    assert REPLACEMENTS[RULE_CHEAPEST] in said
    assert OFFER in said
    assert _count(RULE_CHEAPEST) == before + 1


@LOOPS
def test_loop_without_sales_is_unchanged(run: Any) -> None:
    before = {rule: _count(rule) for rule, _ in RULES}
    said = run(f"{CHEAP_LIE} {OFFER}", sales=False)
    assert CHEAP_LIE in said
    assert REPLACEMENTS[RULE_CHEAPEST] not in said
    assert {rule: _count(rule) for rule, _ in RULES} == before


# ── wiring: the code's own search, and the per-turn reset ────────────────

#: Complete in the caller's words: the code searches before round 1.
FORCED_ASK = "літні шини 235/55 R18"
NOVELTY = "Firestone WinterHawk 4 — новинка сезону."


def _loop(rounds_per_turn: list[list[list[Any]]], turns: list[str], *, progress: bool) -> list[str]:
    loop = StreamingAgentLoop(
        llm_router=MockLLMRouter([]),
        tool_router=_router(),
        tts=MockTTSEngine(),
        conn=MockAudioSocketConnection(),
        barge_in_event=asyncio.Event(),
        tools=_TOOLS,
        system_prompt="Test system prompt",
        network_policy=_policy(True),
    )
    said: list[str] = []
    for user_text, rounds in zip(turns, rounds_per_turn, strict=True):
        loop._llm_router = MockLLMRouter(rounds)
        query = merge_tire_query({}, user_text, consult_started=True) if progress else None
        res = asyncio.run(loop.run_turn(user_text, [], tire_progress=query or None))
        said.append(res.spoken_text)
    return said


def _agent(responses: list[LLMResponse]) -> LLMAgent:
    llm_router = create_autospec(LLMRouter, instance=True)
    llm_router.complete.side_effect = responses
    return LLMAgent(
        api_key="test-key",
        system_prompt="base",
        llm_router=llm_router,
        tool_router=_router(),
        tools=_TOOLS,
        network_policy=_policy(True),
    )


def _end(text: str) -> list[Any]:
    return [TextDelta(text=text), StreamDone(stop_reason="end_turn", usage=Usage(1, 1))]


def _reply(text: str) -> LLMResponse:
    return LLMResponse(text=text, usage=Usage(1, 1), provider="test")


def test_stream_judges_against_the_forced_search() -> None:
    (said,) = _loop([[_end(f"{CHEAP_LIE} {OFFER}")]], [FORCED_ASK], progress=True)
    assert CHEAP_LIE not in said
    assert REPLACEMENTS[RULE_CHEAPEST] in said


def test_text_judges_against_the_forced_search() -> None:
    agent = _agent([_reply(f"{CHEAP_LIE} {OFFER}")])
    said, _ = asyncio.run(agent.process_message(FORCED_ASK, []))
    assert CHEAP_LIE not in said
    assert REPLACEMENTS[RULE_CHEAPEST] in said


_KB_ROUND = [
    ToolCallStart(id="k0", name="search_knowledge_base"),
    ToolCallDelta(id="k0", arguments_chunk=json.dumps({"query": "Firestone"})),
    ToolCallEnd(id="k0"),
    StreamDone(stop_reason="tool_use", usage=Usage(1, 1)),
]


def test_stream_knowledge_base_silence_lasts_one_turn() -> None:
    said = _loop(
        [[_KB_ROUND, _end(NOVELTY)], [_end(NOVELTY)]],
        ["розкажіть про Firestone", "а ще що скажете?"],
        progress=False,
    )
    assert said[0] == NOVELTY
    assert said[1] == REPLACEMENTS[RULE_NOVELTY]


def test_text_knowledge_base_silence_lasts_one_turn() -> None:
    kb_call = ToolCall(id="k0", name="search_knowledge_base", arguments={"query": "Firestone"})
    agent = _agent(
        [
            LLMResponse(
                text="",
                tool_calls=[kb_call],
                stop_reason="tool_use",
                usage=Usage(1, 1),
                provider="test",
            ),
            _reply(NOVELTY),
            _reply(NOVELTY),
        ]
    )
    first, history = asyncio.run(agent.process_message("розкажіть про Firestone", []))
    second, _ = asyncio.run(agent.process_message("а ще що скажете?", history))
    assert first == NOVELTY
    assert second == REPLACEMENTS[RULE_NOVELTY]
