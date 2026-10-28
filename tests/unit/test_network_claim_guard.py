"""Network claim guard: what a network cannot promise never reaches TTS.

Policies are built the way production builds them — `NetworkPolicy.from_tenant_config`
over the owner's tenant patches in `scripts/configure_tenants.py` — so a test
cannot pass on a hand-made policy production never produces.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Any, ClassVar

import pytest

from scripts.configure_tenants import PROKOLESO_CONFIG_PATCH, TVOYA_SHINA_CONFIG_PATCH
from src.agent.network_claim_guard import (
    ENV_FLAG,
    PASS,
    REPLACE,
    RULE_DELIVERY_SUM,
    RULE_EXTENDED_WARRANTY,
    RULE_FREE_DELIVERY,
    RULE_ORDER_CONFIRMED,
    RULE_SERVICE_OFFER,
    RULES,
    blocked_rules,
    check_sentence,
    guard_network_claims,
)
from src.agent.network_policy import SERVICE_LABELS, NetworkPolicy
from src.core.sentence_buffer import SentenceReady
from src.llm.models import StreamDone, TextDelta, ToolCallStart, Usage
from src.monitoring.metrics import network_claim_blocked_total

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from src.core.sentence_buffer import BufferEvent


def _policy(patch: dict[str, Any], *, sales: bool) -> NetworkPolicy:
    return NetworkPolicy.from_tenant_config({**patch, "sales_enabled": sales})


TS_ON = _policy(TVOYA_SHINA_CONFIG_PATCH, sales=True)
TS_OFF = _policy(TVOYA_SHINA_CONFIG_PATCH, sales=False)
PK_ON = _policy(PROKOLESO_CONFIG_PATCH, sales=True)
PK_OFF = _policy(PROKOLESO_CONFIG_PATCH, sales=False)
#: Production on 2026-09-28: neither tenant has `network_policy` in config.
PROD_TODAY = NetworkPolicy.from_tenant_config({})
UNKNOWN_DELIVERY = replace(PK_ON, delivery_mode="unknown")

ALL_POLICIES = {
    "ts_on": TS_ON,
    "ts_off": TS_OFF,
    "pk_on": PK_ON,
    "pk_off": PK_OFF,
    "prod_today": PROD_TODAY,
    "unknown_delivery": UNKNOWN_DELIVERY,
    "none": None,
}


def _rule(sentence: str, policy: NetworkPolicy | None) -> str | None:
    verdict = check_sentence(sentence, policy)
    return None if verdict.action == PASS else verdict.rule


# ── free_delivery ───────────────────────────────────────────────────────

FREE_DELIVERY_CLAIMS = [
    "Доставка безкоштовна.",
    "Доставка у нас абсолютно безкоштовна.",
    "Доставимо безкоштовно по всій Україні.",
    "Маємо безкоштовну доставку Новою поштою.",
    "Безкоштовна доставка діє на всі шини.",
    "Бесплатная доставка по всей Украине.",
    "Доставка у нас бесплатная.",
    "Оформим бесплатную доставку.",
]

FREE_DELIVERY_NEGATIONS = [
    "Доставка не безкоштовна, за тарифами перевізника.",
    "Безкоштовної доставки немає.",
    "Доставка у нас не бесплатная.",
    "Бесплатной доставки нет.",
]


class TestFreeDelivery:
    @pytest.mark.parametrize("sentence", FREE_DELIVERY_CLAIMS)
    def test_carrier_tariff_network_cannot_promise_free_delivery(self, sentence: str) -> None:
        assert _rule(sentence, PK_ON) == RULE_FREE_DELIVERY

    @pytest.mark.parametrize("sentence", FREE_DELIVERY_CLAIMS)
    def test_unknown_mode_is_default_deny(self, sentence: str) -> None:
        verdict = check_sentence(sentence, UNKNOWN_DELIVERY)
        assert verdict.rule == RULE_FREE_DELIVERY
        assert "менеджер" in (verdict.replacement or "")

    @pytest.mark.parametrize("sentence", FREE_DELIVERY_CLAIMS)
    def test_free_network_may_promise_it(self, sentence: str) -> None:
        assert _rule(sentence, TS_ON) is None

    @pytest.mark.parametrize("sentence", FREE_DELIVERY_NEGATIONS)
    def test_negation_is_a_correct_answer(self, sentence: str) -> None:
        assert _rule(sentence, PK_ON) is None

    def test_carrier_replacement_names_the_tariff_not_a_price(self) -> None:
        verdict = check_sentence("Доставка безкоштовна.", PK_ON)
        assert verdict.action == REPLACE
        assert "тариф" in (verdict.replacement or "")


# ── extended_warranty ───────────────────────────────────────────────────


class TestExtendedWarranty:
    @pytest.mark.parametrize(
        "sentence",
        [
            "На ці шини діє розширена гарантія.",
            "Є розширена гарантія на Bridgestone.",
            "Можемо оформити розширену гарантію.",
            "Действует расширенная гарантия на Bridgestone.",
        ],
    )
    def test_network_without_extended_warranty_blocks_every_promise(self, sentence: str) -> None:
        assert PK_ON.extended_warranty_brands == frozenset()
        assert _rule(sentence, PK_ON) == RULE_EXTENDED_WARRANTY

    def test_listed_brand_passes(self) -> None:
        assert _rule("Є розширена гарантія на Bridgestone.", TS_ON) is None

    @pytest.mark.parametrize(
        "sentence",
        ["На Michelin діє розширена гарантія.", "На ці шини діє розширена гарантія."],
    )
    def test_unlisted_or_unnamed_brand_is_blocked(self, sentence: str) -> None:
        assert _rule(sentence, TS_ON) == RULE_EXTENDED_WARRANTY

    @pytest.mark.parametrize(
        "sentence",
        ["Розширеної гарантії немає.", "Расширенной гарантии нет, только гарантия производителя."],
    )
    def test_negation_passes(self, sentence: str) -> None:
        assert _rule(sentence, PK_ON) is None


# ── service_offer ───────────────────────────────────────────────────────

SERVICE_MENTIONS: dict[str, list[str]] = {
    "fitting": [
        "Можу записати вас на шиномонтаж на завтра.",
        "Вартість шиномонтажу залежить від діаметра.",
        "Запишу вас на перевзування.",
        "Запишу вас на шиномонтаж у Києві.",
        "Стоимость шиномонтажа зависит от диаметра.",
    ],
    "storage": [
        "Шини можна залишити на зберігання до весни.",
        "Зберігання шин коштує недорого.",
        "Можем взять шины на хранение.",
    ],
}


class TestServiceOffer:
    def test_every_service_label_has_mentions_under_test(self) -> None:
        assert set(SERVICE_MENTIONS) == set(SERVICE_LABELS)

    @pytest.mark.parametrize("key", sorted(SERVICE_LABELS))
    def test_every_missing_service_is_blocked(self, key: str) -> None:
        others = frozenset(SERVICE_LABELS) - {key}
        policy = replace(PK_ON, services=others)
        for sentence in SERVICE_MENTIONS[key]:
            verdict = check_sentence(sentence, policy)
            assert verdict.rule == RULE_SERVICE_OFFER, sentence
            assert SERVICE_LABELS[key] in (verdict.replacement or ""), sentence

    @pytest.mark.parametrize("key", sorted(SERVICE_LABELS))
    def test_offered_service_passes(self, key: str) -> None:
        policy = replace(PK_ON, services=frozenset({key}))
        for sentence in SERVICE_MENTIONS[key]:
            assert _rule(sentence, policy) is None, sentence

    @pytest.mark.parametrize(
        "sentence",
        [
            "На жаль, не надаємо шиномонтаж.",
            "На жаль, наш магазин не надає послуги шиномонтажу.",
            "Шиномонтаж, на жаль, ми не надаємо.",
            "Зберігання шин у нас немає.",
            "Шиномонтаж мы не предоставляем.",
        ],
    )
    def test_refusal_passes(self, sentence: str) -> None:
        assert _rule(sentence, PK_ON) is None


class TestTvoyaShinaFittingCallIsUntouched:
    """Today's production: fitting calls in Твоя Шина, sales off, no policy in config."""

    SENTENCES = (
        "Записала вас на шиномонтаж на 10:00.",
        "Записую вас на шиномонтаж у Києві, 16 вересня о 10:00.",
        "Шини привозите свої з собою чи ті, що у нас на зберіганні?",
        "Зберігання шин у нас є.",
        "Вартість шиномонтажу залежить від діаметра.",
        "Запис на перевзування підтверджено.",
    )

    @pytest.mark.parametrize("name", ["ts_on", "ts_off", "prod_today", "none"])
    def test_fitting_phrases_reach_tts(self, name: str) -> None:
        policy = ALL_POLICIES[name]
        for sentence in self.SENTENCES:
            assert _rule(sentence, policy) is None, (name, sentence)

    def test_sales_off_enforces_no_policy_rule(self) -> None:
        # Sales off: `services` is "not configured", not "not provided".
        for sentence in [*FREE_DELIVERY_CLAIMS, "Є розширена гарантія на Michelin."]:
            assert _rule(sentence, PK_OFF) is None
            assert _rule(sentence, PROD_TODAY) is None


# ── order_confirmed ─────────────────────────────────────────────────────


class TestOrderConfirmed:
    @pytest.mark.parametrize(
        "sentence",
        [
            "Ваше замовлення підтверджено.",
            "Замовлення оформлено, чекайте на доставку.",
            "Я оформила ваше замовлення.",
            "Ваш заказ подтверждён.",
            "Заказ оформлен.",
        ],
    )
    @pytest.mark.parametrize("name", sorted(ALL_POLICIES))
    def test_always_blocked(self, sentence: str, name: str) -> None:
        assert _rule(sentence, ALL_POLICIES[name]) == RULE_ORDER_CONFIRMED

    @pytest.mark.parametrize(
        "sentence",
        [
            "Підтверджуєте замовлення?",
            "Для оформлення замовлення назвіть, будь ласка, ваше ім'я.",
            "Замовлення буде підтверджено менеджером.",
            "Замовлення ще не підтверджено.",
            "Ваш запис підтверджено.",
        ],
    )
    def test_not_a_claim_passes(self, sentence: str) -> None:
        assert _rule(sentence, PK_ON) is None


# ── Replacements ────────────────────────────────────────────────────────


class TestReplacements:
    CLAIMS: ClassVar[dict[str, str]] = {
        RULE_ORDER_CONFIRMED: "Ваше замовлення підтверджено.",
        RULE_SERVICE_OFFER: "Запишу вас на шиномонтаж і зберігання шин.",
        RULE_FREE_DELIVERY: "Доставка безкоштовна.",
        RULE_EXTENDED_WARRANTY: "Є розширена гарантія на Michelin.",
        RULE_DELIVERY_SUM: "Доставка коштуватиме 950 грн.",
    }

    def test_every_rule_is_exercised(self) -> None:
        assert set(self.CLAIMS) == set(RULES)

    @pytest.mark.parametrize("name", sorted(ALL_POLICIES))
    def test_replacement_is_itself_allowed(self, name: str) -> None:
        policy = ALL_POLICIES[name]
        for claim in self.CLAIMS.values():
            verdict = check_sentence(claim, policy)
            if verdict.action == PASS:
                continue
            assert verdict.action == REPLACE, claim
            assert verdict.replacement
            assert check_sentence(verdict.replacement, policy).action == PASS, verdict

    def test_blocked_rules_reads_a_whole_reply(self) -> None:
        reply = "Шини є. Доставка безкоштовна. Замовлення оформлено."
        assert blocked_rules(reply, PK_ON) == [RULE_FREE_DELIVERY, RULE_ORDER_CONFIRMED]
        assert blocked_rules(reply, TS_ON) == [RULE_ORDER_CONFIRMED]


# ── The stream filter ───────────────────────────────────────────────────


async def _emit(events: list[BufferEvent]) -> AsyncIterator[BufferEvent]:
    for event in events:
        yield event


async def _run(events: list[BufferEvent], policy: NetworkPolicy | None) -> list[BufferEvent]:
    return [e async for e in guard_network_claims(_emit(events), policy, "call-x")]


def _spoken(events: list[BufferEvent]) -> list[str]:
    return [e.text for e in events if isinstance(e, SentenceReady)]


def _counter(rule: str) -> float:
    return network_claim_blocked_total.labels(rule=rule)._value.get()


class TestFilter:
    @pytest.mark.asyncio
    async def test_claim_is_replaced_and_counted(self, caplog: pytest.LogCaptureFixture) -> None:
        before = _counter(RULE_FREE_DELIVERY)
        with caplog.at_level(logging.WARNING, logger="src.agent.network_claim_guard"):
            out = await _run([SentenceReady("Доставка безкоштовна.")], PK_ON)
        assert _spoken(out) == ["Доставка — за тарифами перевізника."]
        assert _counter(RULE_FREE_DELIVERY) == before + 1
        assert any(
            "network_claim_blocked" in r.message
            and "call-x" in r.message
            and RULE_FREE_DELIVERY in r.message
            for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_fragments_are_judged_as_one_sentence(self) -> None:
        events = [SentenceReady("Шиномонтаж,"), SentenceReady("на жаль, ми не надаємо.")]
        out = await _run(events, PK_ON)
        assert _spoken(out) == ["Шиномонтаж,", "на жаль, ми не надаємо."]

    @pytest.mark.asyncio
    async def test_other_sentences_and_events_pass_in_order(self) -> None:
        tool_start = ToolCallStart(id="t1", name="get_pickup_points")
        events: list[Any] = [
            SentenceReady("Шини є в наявності."),
            SentenceReady("Доставимо безкоштовно"),
            tool_start,
            SentenceReady("Ось адреси."),
        ]
        out = await _run(events, PK_ON)
        assert out[0] == events[0]
        assert out[1] == SentenceReady("Доставка — за тарифами перевізника.")
        assert out[2] is tool_start
        assert out[3] == events[3]

    @pytest.mark.asyncio
    async def test_a_turn_of_claims_is_never_silent(self) -> None:
        events = [
            SentenceReady("Доставка безкоштовна."),
            SentenceReady("Доставка у нас бесплатная."),
        ]
        out = await _run(events, PK_ON)
        assert _spoken(out) == ["Доставка — за тарифами перевізника."]

    @pytest.mark.asyncio
    async def test_env_flag_off_passes_everything(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_FLAG, "false")
        out = await _run([SentenceReady("Доставка безкоштовна.")], PK_ON)
        assert _spoken(out) == ["Доставка безкоштовна."]

    @pytest.mark.asyncio
    async def test_env_flag_default_is_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ENV_FLAG, raising=False)
        out = await _run([SentenceReady("Доставка безкоштовна.")], PK_ON)
        assert _spoken(out) != ["Доставка безкоштовна."]


# ── Wiring: the real `run_turn` ─────────────────────────────────────────


class _RecordingTTS:
    """Records what it was asked to say — the observation is the text itself."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    async def initialize(self) -> None:
        return None

    async def synthesize(self, text: str) -> bytes:
        self.texts.append(text)
        return b"\x00" * 640

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        self.texts.append(text)
        yield b"\x00" * 640


async def _turn(text: str, policy: NetworkPolicy | None) -> list[str]:
    from src.agent.agent import ToolRouter
    from src.agent.streaming_loop import StreamingAgentLoop
    from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection
    from tests.unit.mocks.mock_llm_router import MockLLMRouter

    router = MockLLMRouter(
        [[TextDelta(text=text), StreamDone(stop_reason="end_turn", usage=Usage(1, 1))]]
    )
    tts = _RecordingTTS()
    loop = StreamingAgentLoop(
        llm_router=router,
        tool_router=ToolRouter(),
        tts=tts,
        conn=MockAudioSocketConnection(),
        barge_in_event=asyncio.Event(),
        system_prompt="Test system prompt",
        network_policy=policy,
    )
    await loop.run_turn("Скільки коштує доставка?", [])
    return tts.texts


class TestWiredIntoTheTurn:
    """A corpus test on the predicate does not cover the call site."""

    @pytest.mark.asyncio
    async def test_claim_never_reaches_tts(self) -> None:
        texts = await _turn("Доставка безкоштовна. Шини будуть завтра.", PK_ON)
        assert not any("безкоштовн" in t for t in texts)
        assert "Доставка — за тарифами перевізника." in texts

    @pytest.mark.asyncio
    async def test_order_confirmation_blocked_without_policy(self) -> None:
        texts = await _turn("Ваше замовлення підтверджено.", None)
        assert not any("підтверджено" in t for t in texts)

    @pytest.mark.asyncio
    async def test_tvoya_shina_fitting_phrase_reaches_tts(self) -> None:
        texts = await _turn("Записала вас на шиномонтаж на 10:00.", PROD_TODAY)
        assert any("шиномонтаж" in t for t in texts)


# ── delivery_sum: a delivery sum no quote of this call backs ────────────
# Wave 3-E of np-delivery-quote-2026-10-27 (R04, R13, R17, R18).

_QUOTE_KEY_ENV = "NP_TEST_GUARD_QUOTE_KEY"
#: `delivery_quote.tariff_phrase` for Про Колесо (carrier «Нова Пошта»).
PK_TARIFF = "Доставка — за тарифами перевізника Нова Пошта."
QUOTED = "Доставка Новою поштою — орієнтовно 810 грн."
COMPOSED = "Доставка до Львова коштуватиме 950 грн."


def _pk_quote(*, sales: bool = True) -> NetworkPolicy:
    raw = {
        **PROKOLESO_CONFIG_PATCH["network_policy"],
        "delivery_quote": "novaposhta",
        "delivery_quote_key_env": _QUOTE_KEY_ENV,
    }
    return NetworkPolicy.from_tenant_config(
        {**PROKOLESO_CONFIG_PATCH, "network_policy": raw, "sales_enabled": sales}
    )


PK_QUOTE = _pk_quote()


@pytest.fixture
def np_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_QUOTE_KEY_ENV, "k" * 32)


def _sum_rule(sentence: str, policy: NetworkPolicy | None, quotes: Any = ()) -> str | None:
    verdict = check_sentence(sentence, policy, None, quotes)
    return None if verdict.action == PASS else verdict.rule


@pytest.mark.usefixtures("np_key")
class TestDeliverySum:
    @pytest.mark.parametrize(
        "sentence",
        [
            COMPOSED,
            "Доставка обойдется в 900 гривен.",
            "Доставка до Львова — дев'ятсот п'ятдесят гривень.",
            "Відправка Новою поштою буде близько 700 грн.",
            "Пересилка коштуватиме 1 200 грн.",
            "Кур'єром до дверей — ще 90 грн.",
            "Sportrak SP709 — 3118 грн, доставка Новою поштою — орієнтовно 950 грн.",
        ],
    )
    def test_unquoted_sum_is_replaced_by_the_tariff(self, sentence: str) -> None:
        verdict = check_sentence(sentence, PK_QUOTE, None, [810])
        assert verdict.action == REPLACE
        assert verdict.rule == RULE_DELIVERY_SUM
        assert verdict.replacement == PK_TARIFF

    def test_every_sum_is_refused_before_a_quote(self) -> None:
        assert _sum_rule(QUOTED, PK_QUOTE, []) == RULE_DELIVERY_SUM

    @pytest.mark.parametrize(
        ("sentence", "quotes"),
        [
            (QUOTED, [810]),
            ("Доставка Новою поштою — орієнтовно вісімсот десять гривень.", [810]),
            (
                "Доставка Новою поштою — орієнтовно 810 грн; кур'єром до дверей — ще 60 грн.",
                [810, 60],
            ),
            ("Sportrak SP709 — 3118 грн, доставка Новою поштою — орієнтовно 810 грн.", [810]),
        ],
    )
    def test_quoted_sum_passes(self, sentence: str, quotes: list[int]) -> None:
        assert _sum_rule(sentence, PK_QUOTE, quotes) is None

    def test_a_second_unquoted_sum_in_the_sentence_is_caught(self) -> None:
        sentence = "Доставка Новою поштою — орієнтовно 810 грн; кур'єром до дверей — ще 60 грн."
        assert _sum_rule(sentence, PK_QUOTE, [810]) == RULE_DELIVERY_SUM

    @pytest.mark.parametrize(
        "sentence",
        [
            "Шина коштує 2400 грн.",
            "Sportrak SP709 — 3118 грн, а доставка — за тарифами перевізника.",
            "При накладеному платежі — ще додатково 2% від суми замовлення.",
            "При накладеному платежі — ще додатково 2 % від суми замовлення + 20 грн.",
            "Оформлю доставку Sportrak по 3118 грн за шину.",
            "За 3118 грн доставимо завтра.",
            # the policy's own cash-on-delivery fee line (`cod_fee_text`)
            "Відправка накладеним платежем — комісія 2 % + 20 грн.",
            "Доставка — зазвичай 1–3 дні.",
            PK_TARIFF,
        ],
    )
    def test_not_a_delivery_sum_passes(self, sentence: str) -> None:
        assert _sum_rule(sentence, PK_QUOTE, []) is None

    @pytest.mark.parametrize(
        "policy",
        [PK_ON, PK_OFF, TS_ON, TS_OFF, PROD_TODAY, None, _pk_quote(sales=False)],
        ids=["pk_no_flag", "pk_off", "ts_on", "ts_off", "prod_today", "none", "pk_flag_sales_off"],
    )
    def test_no_quote_no_rule(self, policy: NetworkPolicy | None) -> None:
        assert _sum_rule(COMPOSED, policy, []) is None

    def test_flag_without_its_key_is_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_QUOTE_KEY_ENV)
        assert _sum_rule(COMPOSED, PK_QUOTE, []) is None

    def test_a_caller_without_quotes_is_unchanged(self) -> None:
        """`guard_text` and `blocked_rules` pass no quotes: the rule stays out."""
        assert check_sentence(COMPOSED, PK_QUOTE).action == PASS
        assert blocked_rules(COMPOSED, PK_QUOTE) == []

    def test_replacement_passes_itself(self) -> None:
        replacement = check_sentence(COMPOSED, PK_QUOTE, None, []).replacement
        assert replacement is not None
        assert check_sentence(replacement, PK_QUOTE, None, []).action == PASS

    @pytest.mark.asyncio
    async def test_filter_reads_the_quotes_live(self) -> None:
        quotes: list[int] = []

        async def _two() -> AsyncIterator[BufferEvent]:
            yield SentenceReady(QUOTED)
            quotes.append(810)  # `get_delivery_cost` answered between the sentences
            yield SentenceReady(QUOTED)

        before = _counter(RULE_DELIVERY_SUM)
        out = [
            e
            async for e in guard_network_claims(_two(), PK_QUOTE, "call-x", delivery_quotes=quotes)
        ]
        assert _spoken(out) == [PK_TARIFF, QUOTED]
        assert _counter(RULE_DELIVERY_SUM) == before + 1


# ── delivery_sum wired into the real `run_turn` ─────────────────────────

_NETWORK_RULES = (*RULES, "promo_denied")


def _network_counts() -> dict[str, float]:
    return {rule: _counter(rule) for rule in _NETWORK_RULES}


async def _quote_turn(
    text: str, policy: NetworkPolicy | None, quotes: list[int], user_text: str
) -> list[str]:
    from src.agent.agent import ToolRouter
    from src.agent.streaming_loop import StreamingAgentLoop
    from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection
    from tests.unit.mocks.mock_llm_router import MockLLMRouter

    router = MockLLMRouter(
        [[TextDelta(text=text), StreamDone(stop_reason="end_turn", usage=Usage(1, 1))]]
    )
    tts = _RecordingTTS()
    loop = StreamingAgentLoop(
        llm_router=router,
        tool_router=ToolRouter(),
        tts=tts,
        conn=MockAudioSocketConnection(),
        barge_in_event=asyncio.Event(),
        system_prompt="Test system prompt",
        network_policy=policy,
        delivery_quotes=quotes,
    )
    await loop.run_turn(user_text, [])
    # Thinking fillers («Хвилинку.») are spoken too; only delivery talk is judged here.
    return [t for t in tts.texts if "оставк" in t]


ASK_COST = "Скільки коштує доставка до Львова?"


@pytest.mark.usefixtures("np_key")
class TestDeliverySumWiredIntoTheTurn:
    """Which refusal fired is read from the metric label, not from the wording."""

    @pytest.mark.asyncio
    async def test_composed_sum_never_reaches_tts(self) -> None:
        before = _network_counts()
        texts = await _quote_turn(COMPOSED, PK_QUOTE, [810], ASK_COST)
        assert not any("950" in t for t in texts)
        assert texts == [PK_TARIFF]
        after = _network_counts()
        assert {r: after[r] - before[r] for r in _NETWORK_RULES} == {
            r: (1 if r == RULE_DELIVERY_SUM else 0) for r in _NETWORK_RULES
        }

    @pytest.mark.asyncio
    async def test_the_calls_quote_reaches_tts(self) -> None:
        """The loop passes the call's own list: a quoted sum is spoken as is."""
        before = _network_counts()
        texts = await _quote_turn(QUOTED, PK_QUOTE, [810], ASK_COST)
        # Quote on: the code says no tariff line first (`network_facts`).
        assert texts == [QUOTED]
        assert _network_counts() == before

    @pytest.mark.asyncio
    async def test_without_the_flag_the_turn_is_as_before(self) -> None:
        before = _network_counts()
        texts = await _quote_turn(COMPOSED, PK_ON, [], ASK_COST)
        assert texts == [PK_TARIFF, COMPOSED]
        assert _network_counts() == before

    @pytest.mark.asyncio
    async def test_tvoya_shina_is_untouched(self) -> None:
        before = _network_counts()
        texts = await _quote_turn(COMPOSED, TS_ON, [], ASK_COST)
        assert COMPOSED in texts
        assert _network_counts() == before


# ── The tyre-price guard sits earlier in the chain ──────────────────────

SPORTRAK = {
    "id": "sp709",
    "brand": "Sportrak",
    "model": "Snowtrek SP709",
    "size": "205/55 R16",
    "season": "winter",
    "price": 3118,
    "in_stock": True,
}
TYRE_AND_DELIVERY = (
    "Sportrak Snowtrek SP709 — 3118 грн, доставка Новою поштою — орієнтовно 810 грн."
)


async def _tyre_turn(reply: str, quotes: list[int]) -> list[str]:
    import json

    from src.agent.agent import ToolRouter
    from src.agent.streaming_loop import StreamingAgentLoop
    from src.llm.models import ToolCallDelta, ToolCallEnd
    from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection
    from tests.unit.mocks.mock_llm_router import MockLLMRouter

    tool_router = ToolRouter()

    async def _search(**_: Any) -> dict[str, Any]:
        return {"total": 1, "items": [dict(SPORTRAK)]}

    tool_router.register("search_tires", _search)
    args = {"width": 205, "profile": 55, "diameter": 16, "season": "winter"}
    rounds = [
        [
            ToolCallStart(id="s0", name="search_tires"),
            ToolCallDelta(id="s0", arguments_chunk=json.dumps(args)),
            ToolCallEnd(id="s0"),
            StreamDone(stop_reason="tool_use", usage=Usage(1, 1)),
        ],
        [TextDelta(text=reply), StreamDone(stop_reason="end_turn", usage=Usage(1, 1))],
    ]
    tts = _RecordingTTS()
    loop = StreamingAgentLoop(
        llm_router=MockLLMRouter(rounds),
        tool_router=tool_router,
        tts=tts,
        conn=MockAudioSocketConnection(),
        barge_in_event=asyncio.Event(),
        tools=[{"name": "search_tires", "description": "", "input_schema": {"type": "object"}}],
        system_prompt="Test system prompt",
        network_policy=PK_QUOTE,
        delivery_quotes=quotes,
    )
    await loop.run_turn("зимові шини 205/55 R16, скільки з доставкою до Львова?", [])
    return tts.texts


@pytest.mark.usefixtures("np_key")
class TestTyrePriceGuardNeighbour:
    @pytest.mark.asyncio
    async def test_tyre_and_delivery_sum_in_one_sentence_survive(self) -> None:
        from src.agent.tire_claim_guard import RULE_PRICE
        from src.monitoring.metrics import tire_claim_refused_total

        before = tire_claim_refused_total.labels(rule=RULE_PRICE)._value.get()
        net_before = _network_counts()
        texts = await _tyre_turn(TYRE_AND_DELIVERY, [810])
        assert TYRE_AND_DELIVERY in " ".join(texts)
        assert tire_claim_refused_total.labels(rule=RULE_PRICE)._value.get() == before
        assert _network_counts() == net_before

    @pytest.mark.asyncio
    async def test_an_unquoted_delivery_sum_next_to_a_tyre_never_reaches_tts(self) -> None:
        """The tyre guard leaves a delivery sum alone (wave 4-G); a sum no quote
        backs is still `delivery_sum`'s to replace, a tyre named or not."""
        before = _counter(RULE_DELIVERY_SUM)
        reply = TYRE_AND_DELIVERY.replace("810", "950")
        assert reply != TYRE_AND_DELIVERY
        joined = " ".join(await _tyre_turn(reply, [810]))
        assert "950" not in joined
        assert PK_TARIFF in joined
        assert _counter(RULE_DELIVERY_SUM) == before + 1

    @pytest.mark.asyncio
    async def test_separate_sentences_survive_both_guards(self) -> None:
        reply = "Sportrak Snowtrek SP709 — 3118 грн. Доставка Новою поштою — орієнтовно 810 грн."
        texts = await _tyre_turn(reply, [810])
        joined = " ".join(texts)
        assert "3118 грн" in joined
        assert QUOTED in texts
