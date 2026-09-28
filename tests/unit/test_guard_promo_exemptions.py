"""Network claim guard × live promotions: a promise a promotion covers is not false.

Promotions are built the way production builds them: rows → ``load_active_promotions``
(live today, this network only) → ``promo_overrides``; the 8 known promotions come
from ``scripts/migrate_promotions.KNOWN_PROMOTIONS``. Policies come from the
owner's tenant patches in ``scripts/configure_tenants.py``.

Invariants:
- an exemption only clears a rule, never adds one, and ``promos=None`` / empty
  overrides are exactly the verdicts without promotions;
- only a live promotion of *this* network exempts (expired / another network's
  promotion never reaches the overrides);
- ``extended_warranty`` is cleared only for a brand of the promotion;
- ``service_offer`` is cleared only for the partner's service in a sentence that
  names the partner network;
- ``order_confirmed`` is never cleared;
- the overrides reach every call site: the streamed chain, the summary fallback,
  the text path of ``LLMAgent`` and both constructors in ``main.py``.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import pathlib
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, create_autospec

import pytest

from scripts.configure_tenants import PROKOLESO_CONFIG_PATCH, TVOYA_SHINA_CONFIG_PATCH
from scripts.migrate_promotions import KNOWN_PROMOTIONS
from src.agent import promotions
from src.agent.network_claim_guard import (
    PASS,
    RULE_EXTENDED_WARRANTY,
    RULE_FREE_DELIVERY,
    RULE_ORDER_CONFIRMED,
    RULE_SERVICE_OFFER,
    blocked_rules,
    check_sentence,
    guard_text,
)
from src.agent.network_policy import NetworkPolicy
from src.agent.promotions import (
    ActivePromotion,
    PromoOverrides,
    load_active_promotions,
    promo_overrides,
)
from src.llm.models import LLMResponse, StreamDone, TextDelta, Usage
from src.llm.router import LLMRouter
from src.monitoring.metrics import network_claim_blocked_total

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_REPO = pathlib.Path(__file__).resolve().parents[2]
_PK_TENANT = "11111111-1111-1111-1111-111111111111"
_TS_TENANT = "22222222-2222-2222-2222-222222222222"
_TODAY = date(2026, 10, 15)


def _policy(patch: dict[str, Any], *, sales: bool) -> NetworkPolicy:
    return NetworkPolicy.from_tenant_config({**patch, "sales_enabled": sales})


PK_ON = _policy(PROKOLESO_CONFIG_PATCH, sales=True)
PK_OFF = _policy(PROKOLESO_CONFIG_PATCH, sales=False)
TS_ON = _policy(TVOYA_SHINA_CONFIG_PATCH, sales=True)
TS_OFF = _policy(TVOYA_SHINA_CONFIG_PATCH, sales=False)
PROD_TODAY = NetworkPolicy.from_tenant_config({})

# KNOWN_PROMOTIONS: the first six are Про Колесо's, the last two Твоя Шина's.
_PK_SPECS, _TS_SPECS = KNOWN_PROMOTIONS[:6], KNOWN_PROMOTIONS[6:]


def _known(specs: Any) -> PromoOverrides:
    return promo_overrides(
        [ActivePromotion(s.title_prefix, s.bot_text, _TODAY, overrides=s.overrides) for s in specs]
    )


PK_PROMOS = _known(_PK_SPECS)
TS_PROMOS = _known(_TS_SPECS)
EMPTY = PromoOverrides()


def _rule(sentence: str, policy: NetworkPolicy | None, promos: PromoOverrides | None) -> str | None:
    verdict = check_sentence(sentence, policy, promos)
    return None if verdict.action == PASS else verdict.rule


class TestKnownPromotions:
    """The fixture is what the migration writes, not a hand-made shape."""

    def test_split_by_network(self) -> None:
        assert _TS_SPECS[0].title_prefix.startswith("Акція «Доставимо")
        assert len(_TS_SPECS) == 2

    def test_pk_overrides(self) -> None:
        assert PK_PROMOS.free_delivery is True
        assert PK_PROMOS.extended_warranty_brands == frozenset({"matador"})
        assert {e["service"] for e in PK_PROMOS.partner_services} == {"fitting"}

    def test_ts_overrides(self) -> None:
        assert TS_PROMOS.free_delivery is True
        assert TS_PROMOS.extended_warranty_brands == frozenset({"michelin"})
        assert TS_PROMOS.partner_services == ()


# ── Default-deny: no promotions — the old verdicts, exactly ─────────────

SENTENCES = [
    "Доставка безкоштовна.",
    "Доставка у нас бесплатная.",
    "Розширена гарантія на шини Matador.",
    "Розширена гарантія на Мішлен.",
    "Розширена гарантія на шини Бріджстоун.",
    "Розширена гарантія діє на всі шини.",
    "Записую вас на шиномонтаж у Києві о 10:00.",
    "Знижка 25% на шиномонтаж у сервісних центрах «Твоя Шина».",
    "Шини можна залишити на зберігання у Твоїй Шині.",
    "Ваше замовлення підтверджено.",
    "Замовлення оформлено у Твоя Шина з безкоштовною доставкою.",
    "На жаль, шиномонтаж ми не надаємо.",
    "Доставка не безкоштовна.",
    "Є шини Matador 205/55 R16.",
]
POLICIES = {
    "pk_on": PK_ON,
    "pk_off": PK_OFF,
    "ts_on": TS_ON,
    "ts_off": TS_OFF,
    "prod_today": PROD_TODAY,
    "none": None,
}


class TestDefaultDeny:
    @pytest.mark.parametrize("policy", POLICIES.values(), ids=POLICIES.keys())
    @pytest.mark.parametrize("sentence", SENTENCES)
    def test_no_promotions_is_the_old_verdict(
        self, sentence: str, policy: NetworkPolicy | None
    ) -> None:
        old = check_sentence(sentence, policy)
        assert check_sentence(sentence, policy, None) == old
        assert check_sentence(sentence, policy, EMPTY) == old
        assert old.promo_exempt == ()

    @pytest.mark.parametrize("promos", [PK_PROMOS, TS_PROMOS], ids=["pk", "ts"])
    @pytest.mark.parametrize("policy", POLICIES.values(), ids=POLICIES.keys())
    @pytest.mark.parametrize("sentence", SENTENCES)
    def test_promotions_never_add_a_block(
        self, sentence: str, policy: NetworkPolicy | None, promos: PromoOverrides
    ) -> None:
        if check_sentence(sentence, policy).action == PASS:
            assert check_sentence(sentence, policy, promos).action == PASS

    @pytest.mark.parametrize("promos", [PK_PROMOS, TS_PROMOS], ids=["pk", "ts"])
    def test_order_confirmed_is_never_exempt(self, promos: PromoOverrides) -> None:
        for policy in (PK_ON, TS_ON, None):
            assert _rule("Ваше замовлення підтверджено.", policy, promos) == RULE_ORDER_CONFIRMED


# ── free_delivery ───────────────────────────────────────────────────────


class TestFreeDelivery:
    @pytest.mark.parametrize(
        "sentence",
        ["Доставка безкоштовна.", "Доставимо безкоштовно по всій Україні.", "Доставка бесплатная."],
    )
    def test_live_promotion_clears_it(self, sentence: str) -> None:
        assert _rule(sentence, PK_ON, None) == RULE_FREE_DELIVERY
        verdict = check_sentence(sentence, PK_ON, PK_PROMOS)
        assert verdict.action == PASS
        assert verdict.promo_exempt == (RULE_FREE_DELIVERY,)

    def test_promotion_without_free_delivery_does_not(self) -> None:
        warranty_only = PromoOverrides(extended_warranty_brands=frozenset({"matador"}))
        assert _rule("Доставка безкоштовна.", PK_ON, warranty_only) == RULE_FREE_DELIVERY

    def test_other_rule_in_the_sentence_still_blocks(self) -> None:
        assert (
            _rule("Доставка безкоштовна, замовлення підтверджено.", PK_ON, PK_PROMOS)
            == RULE_ORDER_CONFIRMED
        )


# ── extended_warranty ───────────────────────────────────────────────────


class TestExtendedWarranty:
    @pytest.mark.parametrize(
        "sentence",
        [
            "Розширена гарантія на шини Matador.",
            "Розширена гарантія на шини Матадор.",
            "На Матадор діє розширена гарантія.",
        ],
    )
    def test_promotion_brand_is_cleared(self, sentence: str) -> None:
        assert _rule(sentence, PK_ON, None) == RULE_EXTENDED_WARRANTY
        verdict = check_sentence(sentence, PK_ON, PK_PROMOS)
        assert verdict.action == PASS
        assert verdict.promo_exempt == (RULE_EXTENDED_WARRANTY,)

    @pytest.mark.parametrize(
        "sentence", ["Розширена гарантія на Мішлен.", "Розширена гарантія на шини Michelin."]
    )
    def test_michelin_is_not_covered_by_matador(self, sentence: str) -> None:
        assert _rule(sentence, PK_ON, PK_PROMOS) == RULE_EXTENDED_WARRANTY

    @pytest.mark.parametrize(
        "sentence", ["Розширена гарантія на Мішлен.", "Розширена гарантія на шини Michelin."]
    )
    def test_michelin_programme_covers_michelin_in_tvoya_shina(self, sentence: str) -> None:
        assert _rule(sentence, TS_ON, None) == RULE_EXTENDED_WARRANTY
        assert _rule(sentence, TS_ON, TS_PROMOS) is None

    def test_brandless_promise_still_blocked(self) -> None:
        assert (
            _rule("Розширена гарантія діє на всі шини.", PK_ON, PK_PROMOS) == RULE_EXTENDED_WARRANTY
        )


# ── service_offer: partner service, partner named ───────────────────────


class TestPartnerService:
    @pytest.mark.parametrize(
        "sentence",
        [
            "Знижка 25% на шиномонтаж у сервісних центрах «Твоя Шина».",
            "Шиномонтаж можна зробити в Твоїй Шині зі знижкою.",
        ],
    )
    def test_partner_named_is_cleared(self, sentence: str) -> None:
        assert _rule(sentence, PK_ON, None) == RULE_SERVICE_OFFER
        verdict = check_sentence(sentence, PK_ON, PK_PROMOS)
        assert verdict.action == PASS
        assert verdict.promo_exempt == (RULE_SERVICE_OFFER,)

    @pytest.mark.parametrize(
        "sentence",
        [
            # the 9 production hits have this shape: the network's own offer
            "Записую вас на шиномонтаж у Києві о 10:00.",
            "Можемо зробити шиномонтаж завтра.",
        ],
    )
    def test_own_offer_without_partner_still_blocked(self, sentence: str) -> None:
        assert _rule(sentence, PK_ON, PK_PROMOS) == RULE_SERVICE_OFFER

    def test_storage_is_not_the_partner_service(self) -> None:
        verdict = check_sentence(
            "Шини можна залишити на зберігання у Твоїй Шині.", PK_ON, PK_PROMOS
        )
        assert verdict.rule == RULE_SERVICE_OFFER

    def test_only_the_uncovered_service_is_refused(self) -> None:
        verdict = check_sentence("У Твоя Шина є шиномонтаж і зберігання шин.", PK_ON, PK_PROMOS)
        assert verdict.rule == RULE_SERVICE_OFFER
        assert verdict.replacement is not None
        assert "зберігання" in verdict.replacement
        assert "шиномонтаж" not in verdict.replacement

    def test_other_partner_name_does_not_clear(self) -> None:
        other = PromoOverrides(
            partner_services=({"service": "fitting", "network_label": "Про Колесо"},)
        )
        assert _rule("Знижка на шиномонтаж у Твоя Шина.", PK_ON, other) == RULE_SERVICE_OFFER

    def test_partner_without_label_does_not_clear(self) -> None:
        bare = PromoOverrides(partner_services=({"service": "fitting"},))
        assert _rule("Знижка на шиномонтаж у Твоя Шина.", PK_ON, bare) == RULE_SERVICE_OFFER


# ── Only live promotions of this network reach the overrides ─────────────


class _Row:
    def __init__(self, data: dict[str, Any]) -> None:
        self._mapping = data


class _Conn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def __aenter__(self) -> _Conn:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def execute(self, query: Any, params: dict[str, Any] | None = None) -> list[_Row]:
        assert "FROM promotions" in str(query)
        # The fake returns every row: the reader must not trust the SQL filter.
        return [_Row(r) for r in self._rows]


class _Engine:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def begin(self) -> _Conn:
        return _Conn(self.rows)


def _row(
    spec_title: str,
    *,
    tenant: str,
    start: date = _TODAY - timedelta(days=10),
    end: date = _TODAY + timedelta(days=10),
) -> dict[str, Any]:
    spec = next(s for s in KNOWN_PROMOTIONS if s.title_prefix == spec_title)
    return {
        "tenant_id": tenant,
        "title": spec.title_prefix,
        "bot_text": spec.bot_text,
        "valid_from": start,
        "valid_to": end,
        "overrides": dict(spec.overrides),
        "mention_brands": list(spec.mention_brands),
        "active": True,
    }


@pytest.fixture(autouse=True)
def _clear_promo_cache() -> Any:
    promotions._cache.clear()
    promotions._cache_ts = 0.0
    yield
    promotions._cache.clear()


def _live_overrides(rows: list[dict[str, Any]], tenant: str) -> PromoOverrides:
    loaded = asyncio.run(load_active_promotions(_Engine(rows), tenant, today=_TODAY))  # type: ignore[arg-type]
    return promo_overrides(loaded)


_PK_FREE = _PK_SPECS[0].title_prefix
_TS_FREE = _TS_SPECS[0].title_prefix
# Про Колесо's free delivery covers Doublestar and Rydanz only (wave H): a
# brandless promise stays refused, so these tests name a covered brand.
_PK_FREE_CLAIM = "Доставка шин Doublestar безкоштовна."


class TestOnlyLivePromotions:
    def test_live_promotion_clears(self) -> None:
        promos = _live_overrides([_row(_PK_FREE, tenant=_PK_TENANT)], _PK_TENANT)
        assert _rule(_PK_FREE_CLAIM, PK_ON, promos) is None

    def test_expired_promotion_does_not_clear(self) -> None:
        expired = _row(
            _PK_FREE,
            tenant=_PK_TENANT,
            start=_TODAY - timedelta(days=30),
            end=_TODAY - timedelta(days=1),
        )
        promos = _live_overrides([expired], _PK_TENANT)
        assert _rule(_PK_FREE_CLAIM, PK_ON, promos) == RULE_FREE_DELIVERY

    def test_not_started_promotion_does_not_clear(self) -> None:
        future = _row(_PK_FREE, tenant=_PK_TENANT, start=_TODAY + timedelta(days=1))
        promos = _live_overrides([future], _PK_TENANT)
        assert _rule(_PK_FREE_CLAIM, PK_ON, promos) == RULE_FREE_DELIVERY

    def test_tvoya_shina_promotion_does_not_clear_in_prokoleso(self) -> None:
        promos = _live_overrides([_row(_TS_FREE, tenant=_TS_TENANT)], _PK_TENANT)
        assert _rule("Доставка безкоштовна.", PK_ON, promos) == RULE_FREE_DELIVERY

    def test_michelin_programme_of_tvoya_shina_does_not_reach_prokoleso(self) -> None:
        rows = [_row(_TS_SPECS[1].title_prefix, tenant=_TS_TENANT)]
        promos = _live_overrides(rows, _PK_TENANT)
        assert _rule("Розширена гарантія на Мішлен.", PK_ON, promos) == RULE_EXTENDED_WARRANTY


# ── Observability: exemption is logged, never counted as a block ────────


class TestReporting:
    def test_exemption_logged_not_counted(self, caplog: pytest.LogCaptureFixture) -> None:
        before = network_claim_blocked_total.labels(rule=RULE_FREE_DELIVERY)._value.get()
        with caplog.at_level(logging.INFO, logger="src.agent.network_claim_guard"):
            out = guard_text("Доставка безкоштовна.", PK_ON, "call-1", promos=PK_PROMOS)
        assert out == "Доставка безкоштовна."
        after = network_claim_blocked_total.labels(rule=RULE_FREE_DELIVERY)._value.get()
        assert after == before
        assert any(
            "network_claim_promo_exempt" in r.message and "rule=free_delivery" in r.message
            for r in caplog.records
        )

    def test_blocked_rules_takes_promos(self) -> None:
        text = "Доставка безкоштовна. Ваше замовлення підтверджено."
        assert blocked_rules(text, PK_ON) == [RULE_FREE_DELIVERY, RULE_ORDER_CONFIRMED]
        assert blocked_rules(text, PK_ON, PK_PROMOS) == [RULE_ORDER_CONFIRMED]


# ── Wiring: the streamed chain and the summary fallback ─────────────────


class _RecordingTTS:
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


def _loop(router: Any, tts: Any, promos: PromoOverrides | None) -> Any:
    from src.agent.agent import ToolRouter
    from src.agent.streaming_loop import StreamingAgentLoop
    from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection

    return StreamingAgentLoop(
        llm_router=router,
        tool_router=ToolRouter(),
        tts=tts,
        conn=MockAudioSocketConnection(),
        barge_in_event=asyncio.Event(),
        system_prompt="Test system prompt",
        network_policy=PK_ON,
        promo_overrides=promos,
    )


async def _turn(text: str, promos: PromoOverrides | None) -> list[str]:
    from tests.unit.mocks.mock_llm_router import MockLLMRouter

    router = MockLLMRouter(
        [[TextDelta(text=text), StreamDone(stop_reason="end_turn", usage=Usage(1, 1))]]
    )
    tts = _RecordingTTS()
    await _loop(router, tts, promos).run_turn("Скільки коштує доставка?", [])
    return tts.texts


class TestStreamWiring:
    @pytest.mark.asyncio
    async def test_promotion_reaches_the_stream_filter(self) -> None:
        texts = await _turn("Доставка безкоштовна. Шини будуть завтра.", PK_PROMOS)
        assert "Доставка безкоштовна." in texts

    @pytest.mark.asyncio
    async def test_without_promotion_still_replaced(self) -> None:
        texts = await _turn("Доставка безкоштовна. Шини будуть завтра.", None)
        assert not any("безкоштовн" in t for t in texts)


def _router_answering(text: str) -> Any:
    router = MagicMock(spec=["complete", "_resolve_chain"])
    router.complete = AsyncMock(
        return_value=LLMResponse(
            text=text, tool_calls=[], stop_reason="end_turn", usage=Usage(1, 1)
        )
    )
    return router


class TestSummaryFallbackWiring:
    @pytest.mark.asyncio
    async def test_promotion_reaches_the_summary_fallback(self) -> None:
        loop = _loop(_router_answering("Доставка безкоштовна."), _RecordingTTS(), PK_PROMOS)
        assert await loop._request_summary_fallback("system", []) == "Доставка безкоштовна."

    @pytest.mark.asyncio
    async def test_without_promotion_replaced(self) -> None:
        loop = _loop(_router_answering("Доставка безкоштовна."), _RecordingTTS(), None)
        assert await loop._request_summary_fallback("system", []) == (
            "Доставка — за тарифами перевізника."
        )


# ── Wiring: LLMAgent text path (sandbox, goldset) ───────────────────────


def _agent_reply(text: str, policy: NetworkPolicy, promos: PromoOverrides | None) -> str:
    from src.agent.agent import LLMAgent, ToolRouter

    llm_router = create_autospec(LLMRouter, instance=True)
    llm_router.complete = AsyncMock(
        return_value=LLMResponse(text=text, usage=Usage(10, 5), provider="test")
    )
    agent = LLMAgent(
        api_key="test-key",
        system_prompt="base",
        llm_router=llm_router,
        tool_router=ToolRouter(),
        tools=[],
        network_policy=policy,
        promo_overrides=promos,
    )
    reply, _ = asyncio.run(agent.process_message("Скільки коштує доставка?", []))
    return reply


class TestTextPathWiring:
    def test_claim_is_guarded_on_the_text_path(self) -> None:
        reply = _agent_reply("Доставка безкоштовна. Шини будуть завтра.", PK_ON, None)
        assert "безкоштовн" not in reply
        assert reply.startswith("Доставка — за тарифами перевізника.")
        assert "Шини будуть завтра." in reply

    def test_promotion_reaches_the_text_path(self) -> None:
        text = "Доставка безкоштовна. Шини будуть завтра."
        assert _agent_reply(text, PK_ON, PK_PROMOS) == text

    def test_michelin_not_cleared_by_matador_on_the_text_path(self) -> None:
        reply = _agent_reply("Розширена гарантія на Мішлен.", PK_ON, PK_PROMOS)
        assert "Мішлен" not in reply

    @pytest.mark.parametrize("policy", [PK_OFF, TS_OFF, PROD_TODAY], ids=["pk", "ts", "prod"])
    def test_sales_off_text_path_is_unchanged(self, policy: NetworkPolicy) -> None:
        # Sales off: the text path does not judge — as on ce047fb.
        text = "Записую вас на шиномонтаж. Ваше замовлення підтверджено."
        assert _agent_reply(text, policy, None) == text


# ── Wiring: main.py (handle_call is not unit-runnable — AST) ─────────────


def _main_tree() -> ast.Module:
    return ast.parse((_REPO / "src/main.py").read_text(encoding="utf-8"))


def _kw(call: ast.Call, name: str) -> ast.expr | None:
    return next((k.value for k in call.keywords if k.arg == name), None)


class TestMainWiring:
    @pytest.mark.parametrize("ctor", ["LLMAgent", "StreamingAgentLoop"])
    def test_both_agents_get_the_overrides(self, ctor: str) -> None:
        calls = [
            n
            for n in ast.walk(_main_tree())
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == ctor
        ]
        assert calls
        for call in calls:
            value = _kw(call, "promo_overrides")
            assert isinstance(value, ast.Name) and value.id == "claim_overrides", ctor

    def test_overrides_built_from_the_live_list_only_with_sales(self) -> None:
        assigns = [
            n
            for n in ast.walk(_main_tree())
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "claim_overrides" for t in n.targets)
        ]
        assert len(assigns) == 1
        value = assigns[0].value
        assert isinstance(value, ast.IfExp)
        test = value.test
        assert isinstance(test, ast.Attribute) and test.attr == "sales_enabled"
        assert isinstance(test.value, ast.Name) and test.value.id == "network_policy"
        body = value.body
        assert isinstance(body, ast.Call) and isinstance(body.func, ast.Name)
        assert body.func.id == "promo_overrides"
        # `promos` is what `_load_promotions()` returned — no second query.
        assert isinstance(body.args[0], ast.Name) and body.args[0].id == "promos"
        assert isinstance(value.orelse, ast.Constant) and value.orelse.value is None


# ── Wiring: sandbox (same live list as the prompt block) ────────────────


def _sandbox_kwargs(
    monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]], sales: bool
) -> dict[str, Any]:
    from src.sandbox import agent_runner

    captured: dict[str, Any] = {}

    class _PM:
        def __init__(self, _engine: Any) -> None:
            pass

        async def get_active_prompt(self) -> dict[str, Any]:
            return {"id": None}

    async def _empty(*_a: Any, **_k: Any) -> list[Any]:
        return []

    def _agent(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return captured

    monkeypatch.setattr(agent_runner, "PromptManager", _PM)
    monkeypatch.setattr(agent_runner, "get_tools_with_overrides", _empty)
    monkeypatch.setattr(agent_runner, "get_few_shot_examples", _empty)
    monkeypatch.setattr(agent_runner, "get_safety_rules_for_prompt", _empty)
    monkeypatch.setattr(agent_runner, "fetch_tenant_promotions", _empty)
    monkeypatch.setattr(agent_runner, "LLMAgent", _agent)
    monkeypatch.setattr(promotions, "kyiv_today", lambda: _TODAY)
    asyncio.run(
        agent_runner.create_sandbox_agent(
            _Engine(rows),  # type: ignore[arg-type]
            tenant={"config": {**PROKOLESO_CONFIG_PATCH, "sales_enabled": sales}},
            tenant_id=_PK_TENANT,
        )
    )
    return captured


class TestSandboxWiring:
    def test_sales_on_passes_live_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [
            _row(_PK_FREE, tenant=_PK_TENANT),
            _row(_PK_SPECS[3].title_prefix, tenant=_PK_TENANT, end=_TODAY - timedelta(days=1)),
        ]
        kwargs = _sandbox_kwargs(monkeypatch, rows, sales=True)
        promos = kwargs["promo_overrides"]
        assert isinstance(promos, PromoOverrides)
        assert promos.free_delivery is True
        assert promos.extended_warranty_brands == frozenset()  # Matador row expired

    def test_sales_off_passes_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        kwargs = _sandbox_kwargs(monkeypatch, [_row(_PK_FREE, tenant=_PK_TENANT)], sales=False)
        assert kwargs["promo_overrides"] is None
