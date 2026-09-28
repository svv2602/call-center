"""Network facts said by the code (`src/agent/network_facts.py`), both loops.

Expected phrases are never written into a fixture: every policy is built by
``NetworkPolicy.from_tenant_config`` from the real ``configure_tenants``
patches, and the assertions check the phrase against that policy's own fields
(or against the goldset's regexes), so a wrong field or a composed condition
shows up as a failure.
"""

from __future__ import annotations

import asyncio
import copy
import datetime
import re
from typing import Any
from unittest.mock import AsyncMock, create_autospec

import pytest

from scripts.configure_tenants import PROKOLESO_CONFIG_PATCH, TVOYA_SHINA_CONFIG_PATCH
from src.agent import network_facts as nf
from src.agent.agent import LLMAgent, ToolRouter
from src.agent.network_policy import BANK_LABELS, NetworkPolicy
from src.agent.promotions import ActivePromotion, promo_overrides
from src.agent.streaming_loop import StreamingAgentLoop
from src.llm.models import LLMResponse, StreamDone, TextDelta, Usage
from src.llm.router import LLMRouter
from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection
from tests.unit.mocks.mock_llm_router import MockLLMRouter
from tests.unit.mocks.mock_tts import MockTTSEngine


def _cfg(patch: dict[str, Any], sales: bool = True, **policy: Any) -> dict[str, Any]:
    cfg = copy.deepcopy(patch)
    cfg["sales_enabled"] = sales
    for key, value in policy.items():
        if value is _DROP:
            cfg["network_policy"].pop(key, None)
        else:
            cfg["network_policy"][key] = value
    return cfg


_DROP = object()


def _ts(sales: bool = True, **policy: Any) -> NetworkPolicy:
    return NetworkPolicy.from_tenant_config(_cfg(TVOYA_SHINA_CONFIG_PATCH, sales, **policy))


def _pk(sales: bool = True, **policy: Any) -> NetworkPolicy:
    return NetworkPolicy.from_tenant_config(_cfg(PROKOLESO_CONFIG_PATCH, sales, **policy))


TS = _ts()
PK = _pk()

_MONEY = re.compile(r"\d+\s*(грн|гривень|гривні|гривен)")
_DAYS = re.compile(r"\d+\s*(днів|дні|дней|дня|місяц|месяц|рок|год)")


def _promo(title: str, bot_text: str, overrides: dict[str, Any], brands: tuple[str, ...]):
    return ActivePromotion(title, bot_text, datetime.date(2026, 12, 31), overrides, brands)


# The goldset promotions (tests/goldset/cases/promotions.yaml), as the tenant writes them.
DOUBLESTAR = promo_overrides(
    [
        _promo(
            "Безкоштовна доставка Doublestar та Rydanz",
            "Безкоштовна доставка «Новою Поштою» по Україні на всі шини Doublestar та Rydanz.",
            {"free_delivery": True},
            ("doublestar", "rydanz"),
        )
    ]
)
MICHELIN = promo_overrides(
    [
        _promo(
            "Сервісна програма MICHELIN",
            "Сервісна програма MICHELIN: при покупці в торговому центрі «Твоя Шина» …",
            {"extended_warranty_brands": ["michelin"]},
            ("michelin",),
        )
    ]
)

#: One utterance per topic — every member of the enum (goldset wording where it has one).
TOPIC_UTTERANCES: dict[str, list[str]] = {
    nf.DELIVERY_COST: [
        "скільки коштує доставка до Львова?",
        "сколько стоит доставка во Львов?",
        "доставка у вас безкоштовна?",
        "а доставку платную делаете?",
        "а скільки доставка до Києва?",  # «скільки» alone, no cost word
    ],
    nf.DELIVERY_ETA: ["як швидко доставите шини у Київ?", "сколько дней идет доставка?"],
    nf.TRACKING: [
        "а як я дізнаюсь де посилка?",
        "где моя посылка?",
        "де взяти номер ТТН?",
        "как отследить заказ?",
    ],
    nf.PICKUP: ["а можна забрати шини самому в Дніпрі?", "самовывоз есть?"],
    nf.INSTALLMENTS: ["а можно взять в рассрочку?", "чи можна оплатити частинами?"],
    nf.COD: ["наложенным платежом можно?", "чи є накладений платіж?"],
    nf.PAYMENT: ["як можна оплатити?", "как у вас оплата?"],
    nf.EXTENDED_WARRANTY: [
        "чи є розширена гарантія на шини Michelin?",
        "есть гарантия от порезов?",
    ],
    nf.WARRANTY: ["яка гарантія на шини?", "какая гарантия на резину?"],
    nf.RETURNS: ["чи можна повернути шини?", "а обмен возможен?"],
}


class TestTopics:
    def test_every_topic_has_utterances(self) -> None:
        assert set(TOPIC_UTTERANCES) == set(nf.TOPICS)

    @pytest.mark.parametrize(
        ("topic", "text"), [(t, u) for t, us in TOPIC_UTTERANCES.items() for u in us]
    )
    def test_detected(self, topic: str, text: str) -> None:
        assert topic in nf.fact_topics(text)

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "диски 16 на Октавію",
            "потрібні зимові шини 205/55 R16",
            "я вам повернуся пізніше",
            "доставка",  # an answer to «доставка чи самовивіз?», not a question
            "самовивіз",
            "частинами",
            "яка гарантія на шиномонтаж?",
            "яка гарантія на литі диски?",
            "записатися на шиномонтаж",
            "ось номер ТТН",  # a statement, not a question
            "можна я повернуся до цього пізніше?",
            "чи можна повернути шини після монтажу?",
        ],
    )
    def test_nothing(self, text: str) -> None:
        assert nf.fact_topics(text) == []

    def test_warranty_and_returns_together(self) -> None:
        assert nf.fact_topics("яка гарантія на шини і чи можна їх повернути?") == [
            nf.WARRANTY,
            nf.RETURNS,
        ]

    def test_how_many_days_is_the_eta_not_the_cost(self) -> None:
        assert nf.fact_topics("скільки днів йде доставка?") == [nf.DELIVERY_ETA]

    def test_a_specific_way_replaces_the_general_payment_question(self) -> None:
        assert nf.fact_topics("можна оплатити частинами?") == [nf.INSTALLMENTS]


class TestPhrases:
    """Each phrase is the policy's own field; no field → no phrase."""

    def test_delivery_cost_ts_free(self) -> None:
        (phrase,) = nf.turn_facts("сколько стоит доставка во Львов?", TS)
        assert "безкоштовн" in phrase
        assert TS.delivery_carriers[0] in phrase

    def test_delivery_cost_pk_carrier_tariff_never_a_sum(self) -> None:
        (phrase,) = nf.turn_facts("сколько стоит доставка во Львов?", PK)
        assert re.search(r"тариф\w*\s+перевізник", phrase)
        assert "безкоштовн" not in phrase
        assert not _MONEY.search(phrase)
        assert not re.search(r"\d", phrase)

    def test_delivery_unknown_mode_says_nothing(self) -> None:
        pol = _pk(delivery_mode="teleport")
        assert pol.delivery_mode == "unknown"
        assert nf.turn_facts("скільки коштує доставка?", pol) == []

    def test_eta(self) -> None:
        (phrase,) = nf.turn_facts("як швидко доставите шини у Київ?", TS)
        assert TS.delivery_eta_text and TS.delivery_eta_text in phrase
        assert re.search(r"(1|один|одного)\s*(-|–|—|до)\s*(3|три|трьох)", phrase)

    def test_tracking(self) -> None:
        (phrase,) = nf.turn_facts("а як я дізнаюсь де посилка?", PK)
        assert PK.tracking_text and PK.tracking_text[1:] in phrase
        assert "ТТН" in phrase

    def test_pickup(self) -> None:
        assert PK.pickup_available
        assert nf.turn_facts("а можна забрати шини самому в Дніпрі?", PK) == ["Самовивіз є."]

    @pytest.mark.parametrize("pol", [TS, PK], ids=["ts", "pk"])
    def test_installments_banks(self, pol: NetworkPolicy) -> None:
        (phrase,) = nf.turn_facts("а можно взять в рассрочку?", pol)
        assert re.search("частинами|розстрочк|рассрочк|частями", phrase)
        assert re.search("моно|monobank|приват|Приват", phrase)
        for bank in pol.installment_banks:
            assert BANK_LABELS[bank] in phrase

    def test_cod_fee(self) -> None:
        (phrase,) = nf.turn_facts("чи є накладений платіж?", TS)
        assert TS.cod_fee_text and TS.cod_fee_text in phrase

    def test_payment_lists_every_method(self) -> None:
        (phrase,) = nf.turn_facts("як можна оплатити?", PK)
        for word in ("накладений", "карткою", "передоплата", "частинами"):
            assert word in phrase

    def test_extended_warranty_ts_own_brand(self) -> None:
        (phrase,) = nf.turn_facts("чи є розширена гарантія на шини Michelin?", TS)
        assert "Bridgestone" in phrase
        assert not re.search(
            r"(є|діє)\s+(?:\w+\s+){0,2}розширен\w*\s+гаранті\w*\s+(?:\w+\s+){0,2}Michelin", phrase
        )

    def test_extended_warranty_pk_none(self) -> None:
        (phrase,) = nf.turn_facts("чи є розширена гарантія на шини Michelin?", PK)
        assert "Bridgestone" not in phrase
        assert "не надає" in phrase

    @pytest.mark.parametrize("pol", [TS, PK], ids=["ts", "pk"])
    def test_warranty_and_returns_same_in_both(self, pol: NetworkPolicy) -> None:
        phrases = nf.turn_facts("яка гарантія на шини і чи можна їх повернути?", pol)
        assert len(phrases) == 2
        joined = " ".join(phrases)
        assert "гаранті" in joined
        assert re.search("поверн|обмін", joined)
        assert not _DAYS.search(joined)
        assert pol.warranty_text and pol.warranty_text[1:] in phrases[0]
        assert pol.returns_text and pol.returns_text[1:] in phrases[1]

    @pytest.mark.parametrize(
        ("topic", "field", "value"),
        [
            (nf.DELIVERY_COST, "delivery_mode", _DROP),
            (nf.DELIVERY_ETA, "delivery_eta_text", _DROP),
            (nf.TRACKING, "tracking_text", _DROP),
            (nf.PICKUP, "pickup_available", False),
            (nf.INSTALLMENTS, "payment_methods", ["cod", "card"]),
            (nf.COD, "payment_methods", ["card", "installments"]),
            (nf.PAYMENT, "payment_methods", []),
            (nf.WARRANTY, "warranty_text", _DROP),
            (nf.RETURNS, "returns_text", _DROP),
        ],
    )
    def test_topic_without_data_says_nothing(self, topic: str, field: str, value: Any) -> None:
        text = TOPIC_UTTERANCES[topic][0]
        assert nf.turn_facts(text, TS)  # the field is what makes the phrase
        assert nf.turn_facts(text, _ts(**{field: value})) == []

    def test_extended_warranty_unconfigured_says_nothing(self) -> None:
        pol = NetworkPolicy.from_tenant_config({"sales_enabled": True})
        assert nf.turn_facts("чи є розширена гарантія на шини Michelin?", pol) == []

    @pytest.mark.parametrize("topic", nf.TOPICS)
    def test_sales_off_says_nothing(self, topic: str) -> None:
        text = TOPIC_UTTERANCES[topic][0]
        assert nf.turn_facts(text, _ts()) or nf.turn_facts(text, _pk())
        assert nf.turn_facts(text, _ts(sales=False)) == []
        assert nf.turn_facts(text, _pk(sales=False)) == []
        assert nf.turn_facts(text, None) == []
        assert nf.fact_sentences(nf.fact_topics(text), _ts(sales=False)) == []

    def test_at_most_two_phrases(self) -> None:
        text = "скільки коштує доставка, як швидко, і чи можна частинами?"
        assert len(nf.fact_topics(text)) == 3
        phrases = nf.turn_facts(text, TS)
        assert len(phrases) == nf.MAX_FACTS_PER_TURN == 2
        assert phrases == nf.fact_sentences(nf.fact_topics(text), TS)[:2]

    def test_one_phrase_per_topic(self) -> None:
        topics = [nf.DELIVERY_COST, nf.DELIVERY_COST]
        assert len(nf.fact_sentences(topics, TS)) == 1


class TestPromotions:
    def test_doublestar_named_gets_the_promotion(self) -> None:
        (phrase,) = nf.turn_facts("скільки коштує доставка шин Doublestar?", PK, DOUBLESTAR)
        assert phrase == DOUBLESTAR.grants[0].bot_text

    def test_rydanz_spelled_gets_the_promotion(self) -> None:
        (phrase,) = nf.turn_facts("скільки коштує доставка шин rydanz?", PK, DOUBLESTAR)
        assert phrase == DOUBLESTAR.grants[0].bot_text

    def test_brand_from_the_tyre_request(self) -> None:
        (phrase,) = nf.turn_facts(
            "скільки коштує доставка?", PK, DOUBLESTAR, {"brands": ["doublestar"]}
        )
        assert phrase == DOUBLESTAR.grants[0].bot_text

    @pytest.mark.parametrize(
        "text", ["скільки коштує доставка шин Michelin?", "чи є у вас безкоштовна доставка?"]
    )
    def test_other_brand_gets_the_standard_terms(self, text: str) -> None:
        (phrase,) = nf.turn_facts(text, PK, DOUBLESTAR)
        assert re.search(r"тариф\w*\s+перевізник", phrase)
        assert "безкоштовн" not in phrase

    def test_all_brand_free_delivery_promotion(self) -> None:
        promos = promo_overrides(
            [
                _promo(
                    "Все",
                    "Безкоштовна доставка на всі шини до кінця року.",
                    {"free_delivery": True},
                    (),
                )
            ]
        )
        assert nf.turn_facts("скільки коштує доставка?", PK, promos) == [promos.grants[0].bot_text]

    def test_michelin_warranty_promotion(self) -> None:
        (phrase,) = nf.turn_facts("чи є розширена гарантія на Мішлен?", TS, MICHELIN)
        assert phrase == MICHELIN.grants[0].bot_text

    def test_ts_other_brand_keeps_own_extended_warranty(self) -> None:
        (phrase,) = nf.turn_facts("чи є розширена гарантія на Continental?", TS, MICHELIN)
        assert "Bridgestone" in phrase

    def test_a_warranty_promotion_is_not_a_delivery_one(self) -> None:
        (phrase,) = nf.turn_facts("скільки коштує доставка шин Michelin?", PK, MICHELIN)
        assert re.search(r"тариф\w*\s+перевізник", phrase)

    def test_one_promotion_for_two_topics_is_said_once(self) -> None:
        both = promo_overrides(
            [
                _promo(
                    "Michelin",
                    "На шини Michelin — безкоштовна доставка і розширена гарантія.",
                    {"free_delivery": True, "extended_warranty_brands": ["michelin"]},
                    ("michelin",),
                )
            ]
        )
        text = "скільки коштує доставка Michelin і чи є розширена гарантія?"
        assert nf.fact_topics(text) == [nf.DELIVERY_COST, nf.EXTENDED_WARRANTY]
        assert nf.turn_facts(text, PK, both) == [both.grants[0].bot_text]

    def test_pk_warranty_promotion_for_another_brand_says_nothing(self) -> None:
        assert nf.turn_facts("чи є розширена гарантія на Continental?", PK, MICHELIN) == []


def test_already_said_note_quotes_every_phrase() -> None:
    phrases = nf.turn_facts("яка гарантія на шини і чи можна їх повернути?", TS)
    note = nf.already_said_note(phrases)
    for p in phrases:
        assert f"«{p}»" in note
    assert "Не повторюй" in note
    assert nf.already_said_note([]) == ""


# ── Both loops ────────────────────────────────────────────────────────

_TOOLS = [
    {"name": n, "description": "", "input_schema": {"type": "object"}}
    for n in ("search_tires", "search_disks", "search_knowledge_base")
]
LLM_REPLY = "У якому місті вас цікавить доставка?"


def _router() -> tuple[ToolRouter, list[str]]:
    router = ToolRouter()
    ran: list[str] = []

    async def _any(**_: Any) -> dict[str, Any]:
        ran.append("x")
        return {"items": []}

    for n in ("search_tires", "search_disks", "search_knowledge_base"):
        router.register(n, _any)
    return router, ran


class _TTS(MockTTSEngine):
    def __init__(self) -> None:
        super().__init__()
        self.texts: list[str] = []

    async def synthesize(self, text: str) -> bytes:
        self.texts.append(text)
        return await super().synthesize(text)


class _Seeing(MockLLMRouter):
    """Records the system prompt of each round and what was spoken before it."""

    def __init__(self, responses: list[Any], tts: _TTS) -> None:
        super().__init__(responses)
        self.systems: list[str] = []
        self.spoken_before: list[list[str]] = []
        self._tts = tts

    async def complete_stream(self, task: Any, messages: Any, **kwargs: Any):  # type: ignore[override]
        self.systems.append(kwargs.get("system", ""))
        self.spoken_before.append(list(self._tts.texts))
        async for e in super().complete_stream(task, messages, **kwargs):
            yield e


def _stream(text: str, policy: NetworkPolicy, promos: Any = None, query: Any = None):
    router, _ = _router()
    tts = _TTS()
    llm = _Seeing(
        [[TextDelta(text=LLM_REPLY), StreamDone(stop_reason="end_turn", usage=Usage(1, 1))]], tts
    )
    loop = StreamingAgentLoop(
        llm_router=llm,
        tool_router=router,
        tts=tts,
        conn=MockAudioSocketConnection(),
        barge_in_event=asyncio.Event(),
        tools=_TOOLS,
        system_prompt="Test system prompt",
        network_policy=policy,
        promo_overrides=promos,
    )
    res = asyncio.run(loop.run_turn(text, [], tire_progress=query))
    return res.spoken_text, llm.systems, llm.spoken_before


def _text(text: str, policy: NetworkPolicy, promos: Any = None, query: Any = None):
    router, _ = _router()
    agent = LLMAgent(
        api_key="test-key",
        system_prompt="base",
        llm_router=create_autospec(LLMRouter, instance=True),
        tool_router=router,
        tools=_TOOLS,
        network_policy=policy,
        promo_overrides=promos,
    )
    agent._tire_query = dict(query or {})  # an earlier part of the call
    systems: list[str] = []

    async def _complete(_task: Any, _messages: Any, *_a: Any, **kw: Any) -> LLMResponse:
        systems.append(kw.get("system", ""))
        return LLMResponse(text=LLM_REPLY, usage=Usage(1, 1), provider="test")

    agent._llm_router.complete = AsyncMock(side_effect=_complete)
    reply, _ = asyncio.run(agent.process_message(text, []))
    return reply, systems, None


LOOPS = pytest.mark.parametrize("run", [_stream, _text], ids=["streaming", "text"])


@LOOPS
@pytest.mark.parametrize("pol", [TS, PK], ids=["ts", "pk"])
def test_the_fact_comes_first_and_the_model_knows(run: Any, pol: NetworkPolicy) -> None:
    text = "сколько стоит доставка во Львов?"
    (phrase,) = nf.turn_facts(text, pol)
    reply, systems, spoken_before = run(text, pol)
    assert reply.startswith(phrase)
    assert LLM_REPLY in reply
    # the model read the «already said» note in the round it answered
    assert nf.already_said_note([phrase]) in systems[0]
    if spoken_before is not None:  # voice: spoken before the stream began
        assert spoken_before[0][:1] == [phrase]


@LOOPS
def test_the_promotion_phrase_is_said(run: Any) -> None:
    reply, _, _ = run("скільки коштує доставка шин Doublestar?", PK, DOUBLESTAR)
    assert reply.startswith(DOUBLESTAR.grants[0].bot_text)


@LOOPS
def test_the_guard_does_not_judge_the_code_phrase(run: Any, monkeypatch: Any) -> None:
    # A guard that replaces every sentence it is shown: the model's reply
    # goes, the code's phrase stays — the guard never saw it.
    from src.agent import network_claim_guard as guard

    real = guard.check_sentence

    def _cut_all(sentence: str, policy: Any, promos: Any = None) -> guard.Verdict:
        real(sentence, policy, promos)  # same signature as the real one
        return guard.Verdict(guard.REPLACE, rule="test", replacement="ЗАМІНА.")

    monkeypatch.setattr(guard, "check_sentence", _cut_all)
    text = "сколько стоит доставка во Львов?"
    (phrase,) = nf.turn_facts(text, PK)
    reply, _, _ = run(text, PK)
    assert reply.startswith(phrase)
    assert LLM_REPLY not in reply
    assert "ЗАМІНА." in reply


@LOOPS
def test_the_brand_of_the_tyre_request_picks_the_promotion(run: Any) -> None:
    reply, _, _ = run("скільки коштує доставка?", PK, DOUBLESTAR, {"brands": ["doublestar"]})
    assert reply.startswith(DOUBLESTAR.grants[0].bot_text)


@LOOPS
def test_sales_off_nothing_said_nothing_noted(run: Any) -> None:
    reply, systems, _ = run("сколько стоит доставка во Львов?", _ts(sales=False))
    assert reply == LLM_REPLY
    assert "Вже сказано клієнту" not in systems[0]


@LOOPS
def test_no_topic_nothing_said(run: Any) -> None:
    reply, systems, _ = run("потрібні шини", TS)
    assert reply == LLM_REPLY
    assert "Вже сказано клієнту" not in systems[0]


class TestPolicyTexts:
    """``warranty_text`` / ``returns_text`` / ``tracking_text``: soft parsing, default-deny."""

    @pytest.mark.parametrize("field", ["warranty_text", "returns_text", "tracking_text"])
    def test_written_for_both_networks(self, field: str) -> None:
        assert getattr(TS, field)
        assert getattr(TS, field) == getattr(PK, field)

    @pytest.mark.parametrize("field", ["warranty_text", "returns_text", "tracking_text"])
    @pytest.mark.parametrize("bad", [123, ["x"], "   ", None])
    def test_garbage_is_not_written(self, field: str, bad: Any) -> None:
        assert getattr(_ts(**{field: bad}), field) is None

    def test_tracking_line_in_the_block(self) -> None:
        from src.agent.network_policy import render_network_block

        block = render_network_block(TS) or ""
        assert f"- Відстеження посилки: {TS.tracking_text}." in block
        assert "Відстеження" not in (render_network_block(_ts(tracking_text=_DROP)) or "")
