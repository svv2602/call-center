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
    nf.SERVICE: [
        "а шиномонтаж у вас є?",  # goldset services_asked_in_both
        "мені треба записати на шиномонтаж дві машини",  # R07.3
        "купити шини і щоб одразу поставили — запишете на шиномонтаж?",  # R03 / R07.3
        "записатися на шиномонтаж",
        "можно записаться на шиномонтаже?",
        "переобуть машину можно?",
        "треба перевзути авто",
        "шини з монтажем можна?",
        "хочу купить шины и сразу поставить у вас",
        "а зберігання шин у вас є?",
        "хранение шин есть?",
    ],
    nf.NON_STOCK_SIZE: [
        "можна поставити 225/45 R17 замість 205/55 R16?",
        "можно поставить шины шире на Октавию?",
        # R07.4: one parameter, two values (operators' calls)
        "скажите, если у нас 55 профиль стоит, а я поставлю 60, сильно разница будет?",
        "якщо 55, а 60, можливо, взяти мені?",
    ],
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

    def test_pickup_offered_is_left_to_get_pickup_points(self) -> None:
        # goldset №7: «Самовивіз є.» first → the model asked for a district
        # instead of calling get_pickup_points
        assert PK.pickup_available
        assert nf.turn_facts("а можна забрати шини самому в Дніпрі?", PK) == []

    def test_no_pickup_is_said(self) -> None:
        phrases = nf.turn_facts("а можна забрати шини самому?", _ts(pickup_available=False))
        assert phrases == ["Самовивозу немає, лише доставка."]

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
        # pickup is spoken only when the network has none
        no_pickup = _ts(pickup_available=False)
        assert (
            nf.turn_facts(text, _ts())
            or nf.turn_facts(text, _pk())
            or (nf.turn_facts(text, no_pickup))
        )
        assert nf.turn_facts(text, _ts(sales=False, pickup_available=False)) == []
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
    reply, systems, _ = run("розкажіть про шини", TS)
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


class TestNonStockSize:
    """Goldset `non_stock_size_not_offered` [ТШ] red in 2 of 3 runs: the model
    offered to check the size itself. Owner: «нештатний розмір підбирає
    спеціаліст»."""

    @pytest.mark.parametrize(
        "text",
        [
            "можна поставити 225/45 R17 замість 205/55 R16?",
            "можна двісті двадцять п'ять сорок п'ять сімнадцять замість двісті п'ять "
            "п'ятдесят п'ять шістнадцять?",
            "можно 215 вместо 205?",
            "а можна поставити більший радіус?",
            "а можна інший діаметр поставити, сімнадцятий?",
            "шини ширші за заводські можна?",
            # R07.4 «55 → 60»: one parameter said with two values
            "скажите, если у нас 55 профиль стоит, а я поставлю 60, сильно разница будет?",
            "скажіть, якщо у нас 55 профіль стоїть, а я поставлю 60, сильно різниця буде?",
            "якщо 55, а 60, можливо, взяти мені?",  # call_217, about 225/55 R17
            "стоїть 16-й радіус, можна 17-й поставити?",
            "а если поставить 65 профиль вместо 55?",
            "а можна замість 55 профілю поставити 60?",
            "у мене 16 радіусом стоять, а 17 можна взяти?",
            "205/55 R16 стоїть, а радіус 17 можна поставити?",  # the old value in the size
        ],
    )
    def test_size_change_questions(self, text: str) -> None:
        assert nf.is_non_stock_size_question(text)
        assert nf.turn_facts(text, TS)[0] == nf.NON_STOCK_SIZE_PHRASE
        assert nf.turn_facts(text, PK)[0] == nf.NON_STOCK_SIZE_PHRASE

    @pytest.mark.parametrize(
        "text",
        [
            "потрібні шини 205/55 R16 літні",
            "є шини 205/55 R16 замість тих, що були?",
            "чи можна замість Мішлен взяти Бріджстоун?",
            "можна замість двох шин взяти чотири?",
            "чи можна доставку замість самовивозу?",
            "225/45 R17 замість 205/55 R16",  # no question
            # True here would also stop the forced tyre search (tire_search_gate)
            "205 на 55 чи на 65?",  # call_189: the caller is unsure of their size
            "профіль 55 чи 65, не пам'ятаю?",
            "профиль 55 или 65, не помню?",
            "індекс 91 чи 94",
            "індекс 91 чи 94 краще взяти?",
            "ціна 55 гривень",
            "55-й профіль",
            "55-й профіль можна поставити?",
            "можна поставити 225/55 R17, профіль підходить?",  # 55 and 17: not two profiles
            "радіус 16, можна поставити 4 шини?",
            "225/55 R17 профіль, а можна взяти 2 шини?",
            "якщо 55, а 55 взяти?",
            "якщо 91, а 94 взяти?",  # load indices, not profiles
            "профіль 55, а індекс навантаження 91 можна поставити?",
            "радиус 16, а нагрузка 94 подойдет, можно поставить?",
            "о 16, а 17 можна поставити?",  # hours, no parameter
        ],
    )
    def test_not_a_size_change(self, text: str) -> None:
        assert not nf.is_non_stock_size_question(text)
        assert nf.NON_STOCK_SIZE not in nf.fact_topics(text)

    def test_one_parameter_swap_stops_the_forced_search(self) -> None:
        # the call site: ForcedTireSearch.plan asks the predicate before searching
        from src.agent.tire_search_gate import ForcedTireSearch

        tools = [
            {"name": n, "description": "", "input_schema": {"type": "object"}}
            for n in ("search_tires", "check_availability")
        ]
        query = {"sizes": ["225/55 R17"], "season": "summer"}
        swap = "літні, скажите, если у нас 55 профиль стоит, а я поставлю 60, разница будет?"
        plain = "літні 225/55 R17"
        assert nf.NON_STOCK_SIZE in nf.fact_topics(swap)
        gate = ForcedTireSearch(sales_enabled=True, tools=tools)
        assert gate.plan(query, swap, [{"role": "user", "content": swap}]) is None
        control = ForcedTireSearch(sales_enabled=True, tools=tools)
        assert control.plan(query, plain, [{"role": "user", "content": plain}]) is not None


# ── A service the network lacks (R03, R07.3) ──────────────────────────

_REFUSAL = re.compile(r"не надаєм|не надаем|не пропонуєм")  # goldset services_asked_in_both


def _expected_refusal(*keys: str) -> str:
    from src.agent.prompts import _SERVICE_NOT_PROVIDED_PHRASE

    return (
        f"{nf.SERVICE_REFUSAL_PREFIX} {' і '.join(_SERVICE_NOT_PROVIDED_PHRASE[k] for k in keys)}."
    )


class TestServiceRefusal:
    @pytest.mark.parametrize("text", TOPIC_UTTERANCES[nf.SERVICE])
    def test_pk_refuses_by_code(self, text: str) -> None:
        (phrase,) = nf.turn_facts(text, PK)
        assert _REFUSAL.search(phrase)
        assert phrase in (_expected_refusal("fitting"), _expected_refusal("storage"))
        # no other network, no transfer, no city question
        assert not re.search(r"Твоя|Шина|оператор|місто|місті", phrase)

    @pytest.mark.parametrize("text", TOPIC_UTTERANCES[nf.SERVICE])
    def test_ts_offers_it_says_nothing(self, text: str) -> None:
        assert nf.SERVICE in nf.fact_topics(text)
        assert nf.turn_facts(text, TS) == []

    def test_the_multi_car_request(self) -> None:
        text = "мені треба записати на шиномонтаж дві машини"
        assert nf.asked_services(text) == ["fitting"]
        assert nf.turn_facts(text, PK) == [_expected_refusal("fitting")]

    def test_order_and_fitting_in_one_call(self) -> None:
        text = "хочу замовити шини і щоб одразу поставили"
        assert nf.asked_services(text) == ["fitting"]
        assert nf.turn_facts(text, PK) == [_expected_refusal("fitting")]

    def test_both_services_in_one_phrase(self) -> None:
        text = "а шиномонтаж і зберігання шин у вас є?"
        assert nf.asked_services(text) == ["fitting", "storage"]
        assert nf.turn_facts(text, PK) == [_expected_refusal("fitting", "storage")]

    def test_ts_without_storage_refuses_only_storage(self) -> None:
        pol = _ts(services=["fitting"])
        text = "а шиномонтаж і зберігання шин у вас є?"
        assert nf.turn_facts(text, pol) == [_expected_refusal("storage")]

    @pytest.mark.parametrize(
        "text",
        [
            "шини для шиномонтажу на СТО",  # the caller is a fitting shop: a sale
            "я з шиномонтажу, потрібні шини оптом",
            "а гарантія після шиномонтажу?",
            "яка гарантія на шиномонтаж?",
            "пошкодження під час монтажу",
            "доставка на шиномонтаж до знайомого",
            "монтаж не потрібен",
            "шиномонтаж не треба, тільки шини",
            "як правильно зберігати шини?",
            "термін зберігання шин який?",
            "мені поставили не ті шини",
            "можна поставити 225/45 R17 замість 205/55 R16?",
            "одразу поставлю сам",
            "потрібні зимові шини 205/55 R16",
        ],
    )
    def test_not_a_service_request(self, text: str) -> None:
        assert nf.asked_services(text) == []
        assert not any(_REFUSAL.search(p) for p in nf.turn_facts(text, PK))

    def test_every_service_label_is_refused(self) -> None:
        from src.agent.network_policy import SERVICE_LABELS

        for key, label in SERVICE_LABELS.items():
            assert nf.turn_facts(f"а {label} у вас є?", PK) == [_expected_refusal(key)]

    def test_a_new_service_key_is_refused_from_its_label(self, monkeypatch: Any) -> None:
        # default-deny: a key with no word forms here still gets a pattern
        from src.agent.network_policy import SERVICE_LABELS

        monkeypatch.setitem(SERVICE_LABELS, "alignment", "розвал-сходження")
        (phrase,) = nf.turn_facts("а розвал-сходження у вас робите?", PK)
        assert phrase == f"{nf.SERVICE_REFUSAL_PREFIX} розвал-сходження."

    def test_unconfigured_policy_says_nothing(self) -> None:
        pol = NetworkPolicy.from_tenant_config({"sales_enabled": True})
        assert nf.SERVICE in nf.fact_topics("а шиномонтаж у вас є?")
        assert nf.turn_facts("а шиномонтаж у вас є?", pol) == []

    def test_sales_off_says_nothing(self) -> None:
        assert nf.turn_facts("а шиномонтаж у вас є?", _pk(sales=False)) == []

    def test_a_partner_promotion_leaves_it_to_the_model(self) -> None:
        from src.agent.promotions import PromoOverrides

        promos = PromoOverrides(
            partner_services=({"service": "fitting", "network_label": "Партнер"},)
        )
        assert nf.turn_facts("а шиномонтаж у вас є?", PK, promos) == []
        # an unnamed partner clears nothing
        bare = PromoOverrides(partner_services=({"service": "fitting"},))
        assert nf.turn_facts("а шиномонтаж у вас є?", PK, bare) == [_expected_refusal("fitting")]

    def test_the_note_keeps_the_sale_going(self) -> None:
        phrases = nf.turn_facts("купити шини і щоб одразу поставили?", PK)
        note = nf.already_said_note(phrases)
        assert f"«{_expected_refusal('fitting')}»" in note
        assert "НЕ питай місто" in note
        assert "продовжуй підбір і замовлення шин" in note
        other = nf.already_said_note(nf.turn_facts("сколько стоит доставка во Львов?", PK))
        assert "НЕ питай місто" not in other


@LOOPS
def test_pk_refusal_is_said_first_and_the_sale_goes_on(run: Any) -> None:
    text = "купити шини і щоб одразу поставили — запишете на шиномонтаж?"
    phrase = _expected_refusal("fitting")
    reply, systems, spoken_before = run(text, PK)
    assert reply.startswith(phrase)
    assert LLM_REPLY in reply  # the model's turn is not cut: the tyre sale goes on
    assert nf.already_said_note([phrase]) in systems[0]
    if spoken_before is not None:
        assert spoken_before[0][:1] == [phrase]


@LOOPS
def test_ts_fitting_request_is_left_to_the_model(run: Any) -> None:
    reply, systems, _ = run("мені треба записати на шиномонтаж дві машини", TS)
    assert not _REFUSAL.search(reply)
    assert "Вже сказано клієнту" not in systems[0]
