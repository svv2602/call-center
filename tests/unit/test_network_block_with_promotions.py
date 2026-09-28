"""«Умови мережі» × live promotions: the standard line must not contradict a promotion.

Goldset №2: «чи є розширена гарантія на Мішлен?» in Твоя Шина, while «Сервісна
програма MICHELIN» runs → «тільки Bridgestone, для Michelin немає». The block
said «Розширена гарантія: тільки на шини Bridgestone.» and outvoted the
promotions block.

Invariants:
- ``promos=None`` / empty overrides → the block is byte-for-byte the incumbent
  one (snapshots below were rendered by ``7e4a0c4``);
- a live promotion's warranty brands, free-delivery brands and partner service
  reach their line, every one of them, pointing at the promotions block;
- a promotion only extends a line: a brand the policy already covers, a
  service the network offers, a free-delivery network — nothing changes;
- every line a promotion changed passes the network-claim guard with the same
  promotions (the block never teaches a sentence the guard would cut);
- both agents pass their overrides to the renderer (wiring, each loop alone).
"""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, create_autospec

import pytest

from scripts.configure_tenants import PROKOLESO_CONFIG_PATCH, TVOYA_SHINA_CONFIG_PATCH
from scripts.migrate_promotions import KNOWN_PROMOTIONS
from src.agent.network_claim_guard import PASS, check_sentence
from src.agent.network_policy import PROMO_TERMS_REF, NetworkPolicy, render_network_block
from src.agent.promotions import ActivePromotion, PromoOverrides, promo_overrides
from src.llm.models import LLMResponse, StreamDone, TextDelta, Usage
from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection
from tests.unit.mocks.mock_llm_router import MockLLMRouter
from tests.unit.mocks.mock_tts import MockTTSEngine

_BASE = "Базовий промпт для тесту."
_TODAY = date(2026, 10, 15)


def _policy(cfg: dict[str, Any]) -> NetworkPolicy:
    return NetworkPolicy.from_tenant_config({**cfg, "sales_enabled": True})


TS = _policy(TVOYA_SHINA_CONFIG_PATCH)
PK = _policy(PROKOLESO_CONFIG_PATCH)

# KNOWN_PROMOTIONS: the first six are Про Колесо's, the last two Твоя Шина's.
_PK_SPECS, _TS_SPECS = KNOWN_PROMOTIONS[:6], KNOWN_PROMOTIONS[6:]


def _known(specs: Any, *, brands: tuple[str, ...] = ()) -> PromoOverrides:
    return promo_overrides(
        [
            ActivePromotion(
                s.title_prefix, s.bot_text, _TODAY, overrides=s.overrides, mention_brands=brands
            )
            for s in specs
        ]
    )


PK_PROMOS = _known(_PK_SPECS)
TS_PROMOS = _known(_TS_SPECS)


def _lines(block: str | None) -> list[str]:
    assert block is not None
    return block.split("\n")


def _line(block: str | None, prefix: str) -> str:
    found = [ln for ln in _lines(block) if ln.startswith(prefix)]
    assert len(found) == 1, (prefix, block)
    return found[0]


def _warranty(block: str | None) -> str:
    found = [ln for ln in _lines(block) if "гаранті" in ln]
    assert len(found) == 1, block
    return found[0]


# ── No promotions: the incumbent block, byte for byte ───────────────────

_HEAD = (
    "\n## Умови мережі\n"
    "Називай клієнту тільки умови з цього списку. Чого тут немає — «це уточнить менеджер».\n"
    "- Замовлення: ти оформлюєш заявку, менеджер передзвонить і підтвердить її. "
    "Не кажи, що замовлення підтверджено.\n"
    "- Ціну називай за одну шину; можна замовити й окремі шини, не лише комплект.\n"
)
SNAPSHOTS: dict[str, tuple[dict[str, Any], str]] = {
    "tariff": (
        {
            "delivery_mode": "carrier_tariff",
            "delivery_carriers": ["Перевізник"],
            "services": ["storage"],
            "payment_methods": ["card"],
            "extended_warranty_brands": ["bridgestone"],
        },
        _HEAD + "- Доставка: за тарифами перевізника (Перевізник). Вартість доставки не називай.\n"
        "- Оплата: оплата карткою.\n"
        "- Послуги мережі: зберігання шин.\n"
        "- Не надаємо: шиномонтаж. Інші мережі не згадуй і туди не направляй.\n"
        "- Розширена гарантія: тільки на шини Bridgestone.\n"
        "- Підбір: пропонуй не більше 3 варіантів.",
    ),
    "free": (
        {"delivery_mode": "free", "services": ["fitting", "storage"]},
        _HEAD + "- Доставка: безкоштовна.\n"
        "- Оплата: способи не називай — це уточнить менеджер.\n"
        "- Послуги мережі: шиномонтаж, зберігання шин.\n"
        "- Розширеної гарантії мережа не надає.\n"
        "- Підбір: пропонуй не більше 3 варіантів.",
    ),
    "unknown": (
        {},
        _HEAD + "- Доставка: умови й вартість не називай — це уточнить менеджер.\n"
        "- Оплата: способи не називай — це уточнить менеджер.\n"
        "- Не надаємо: шиномонтаж, зберігання шин. Інші мережі не згадуй і туди не направляй.\n"
        "- Розширеної гарантії мережа не надає.\n"
        "- Підбір: пропонуй не більше 3 варіантів.",
    ),
}


class TestWithoutPromotions:
    @pytest.mark.parametrize("name", sorted(SNAPSHOTS))
    @pytest.mark.parametrize("promos", [None, PromoOverrides()], ids=["none", "empty"])
    def test_block_is_incumbent(self, name: str, promos: PromoOverrides | None) -> None:
        cfg, expected = SNAPSHOTS[name]
        policy = _policy({"network_policy": cfg})
        assert render_network_block(policy, promos) == expected

    @pytest.mark.parametrize("policy", [TS, PK], ids=["ts", "pk"])
    def test_real_networks_default_arg(self, policy: NetworkPolicy) -> None:
        assert render_network_block(policy, None) == render_network_block(policy)
        assert render_network_block(policy, PromoOverrides()) == render_network_block(policy)

    @pytest.mark.parametrize("promos", [TS_PROMOS, PK_PROMOS], ids=["ts", "pk"])
    def test_sales_off_stays_none(self, promos: PromoOverrides) -> None:
        assert render_network_block(None, promos) is None
        off = NetworkPolicy.from_tenant_config(TVOYA_SHINA_CONFIG_PATCH)
        assert off.sales_enabled is False
        assert render_network_block(off, promos) is None


# ── Warranty ────────────────────────────────────────────────────────────


class TestWarranty:
    def test_goldset_ts_michelin_joins_bridgestone(self) -> None:
        """The goldset case: the promotion's brand is on the warranty line."""
        line = _warranty(render_network_block(TS, TS_PROMOS))
        assert TS_PROMOS.extended_warranty_brands
        for brand in TS.extended_warranty_brands | TS_PROMOS.extended_warranty_brands:
            assert line.count(brand.capitalize()) == 1, line
        assert "тільки" not in line
        assert PROMO_TERMS_REF in line

    def test_network_without_warranty_names_promotion_brands(self) -> None:
        assert not PK.extended_warranty_brands
        line = _warranty(render_network_block(PK, PK_PROMOS))
        assert PK_PROMOS.extended_warranty_brands
        for brand in PK_PROMOS.extended_warranty_brands:
            assert brand.capitalize() in line
        assert PROMO_TERMS_REF in line

    def test_every_promotion_brand_listed(self) -> None:
        promos = PromoOverrides(extended_warranty_brands=frozenset({"michelin", "matador"}))
        line = _warranty(render_network_block(TS, promos))
        assert "Michelin" in line and "Matador" in line and "Bridgestone" in line

    def test_brand_already_in_policy_is_not_repeated(self) -> None:
        same = PromoOverrides(
            extended_warranty_brands=frozenset(b.upper() for b in TS.extended_warranty_brands)
        )
        assert _warranty(render_network_block(TS, same)) == _warranty(render_network_block(TS))

    def test_promotion_brand_does_not_leak_to_other_lines(self) -> None:
        promos = PromoOverrides(extended_warranty_brands=frozenset({"michelin"}))
        block = render_network_block(TS, promos)
        assert [ln for ln in _lines(block) if "Michelin" in ln] == [_warranty(block)]


# ── Free delivery by brand (carrier-tariff network) ─────────────────────


class TestDelivery:
    def test_brand_scopes_listed(self) -> None:
        promos = PromoOverrides(
            free_delivery=True,
            free_delivery_brand_scopes=(frozenset({"michelin"}), frozenset({"goodyear"})),
        )
        line = _line(render_network_block(PK, promos), "- Доставка:")
        assert "за тарифами перевізника" in line
        assert "Michelin" in line and "Goodyear" in line
        assert PROMO_TERMS_REF in line
        assert line.endswith("Вартість доставки не називай.")

    def test_every_brand_scope_names_no_brand(self) -> None:
        line = _line(render_network_block(PK, PK_PROMOS), "- Доставка:")
        assert any(not scope for scope in PK_PROMOS.free_delivery_brand_scopes)
        assert "за акцією безкоштовна (" in line
        assert "на шини" not in line

    @pytest.mark.parametrize("cfg", ["free", "unknown"])
    def test_other_delivery_modes_unchanged(self, cfg: str) -> None:
        policy = _policy({"network_policy": SNAPSHOTS[cfg][0]})
        promos = PromoOverrides(
            free_delivery=True, free_delivery_brand_scopes=(frozenset({"michelin"}),)
        )
        assert _line(render_network_block(policy, promos), "- Доставка:") == _line(
            render_network_block(policy), "- Доставка:"
        )


# ── Partner service ─────────────────────────────────────────────────────


class TestPartnerService:
    def test_partner_named_on_not_provided_line(self) -> None:
        line = _line(render_network_block(PK, PK_PROMOS), "- Не надаємо:")
        labels = [e["network_label"] for e in PK_PROMOS.partner_services]
        assert labels
        for label in labels:
            assert f"«{label}»" in line
        assert PROMO_TERMS_REF in line

    @pytest.mark.parametrize(
        "entry",
        [
            {"service": "fitting"},  # partner not named → guard would cut it anyway
            {"service": "шиномонтаж", "network_label": "Партнер"},  # not an enum key
        ],
    )
    def test_unusable_partner_entry_ignored(self, entry: dict[str, str]) -> None:
        promos = PromoOverrides(partner_services=(entry,))
        assert render_network_block(PK, promos) == render_network_block(PK)

    def test_offered_service_not_added(self) -> None:
        """The «Не надаємо» line exists (storage), but fitting is the network's own."""
        policy = _policy({"network_policy": {"services": ["fitting"]}})
        promos = PromoOverrides(
            partner_services=({"service": "fitting", "network_label": "Партнер"},)
        )
        assert _line(render_network_block(policy), "- Не надаємо:")
        assert render_network_block(policy, promos) == render_network_block(policy)


# ── The block never teaches a sentence the guard cuts ───────────────────

_BRAND_DELIVERY = dataclasses.replace(
    PK_PROMOS, free_delivery_brand_scopes=(frozenset({"michelin", "goodyear"}),)
)


@pytest.mark.parametrize(
    ("policy", "promos"),
    [(TS, TS_PROMOS), (PK, PK_PROMOS), (PK, _BRAND_DELIVERY)],
    ids=["ts", "pk", "pk-brand-delivery"],
)
def test_promotion_lines_pass_guard(policy: NetworkPolicy, promos: PromoOverrides) -> None:
    before = set(_lines(render_network_block(policy)))
    changed = [ln for ln in _lines(render_network_block(policy, promos)) if ln not in before]
    assert changed
    for line in changed:
        verdict = check_sentence(line.removeprefix("- "), policy, promos)
        assert verdict.action == PASS, (line, verdict)


# ── Wiring: both agents render with their overrides ─────────────────────


def _llm_agent_system(policy: NetworkPolicy, promos: PromoOverrides) -> str:
    from src.agent.agent import LLMAgent
    from src.llm.router import LLMRouter

    llm_router = create_autospec(LLMRouter, instance=True)
    llm_router.complete = AsyncMock(
        return_value=LLMResponse(text="Відповідь", usage=Usage(10, 5), provider="test")
    )
    agent = LLMAgent(
        api_key="test-key",
        system_prompt=_BASE,
        llm_router=llm_router,
        network_policy=policy,
        promo_overrides=promos,
    )
    asyncio.run(agent.process_message("Привіт", []))
    return llm_router.complete.call_args.kwargs["system"]


class _RecordingRouter(MockLLMRouter):
    def __init__(self, responses: Any) -> None:
        super().__init__(responses)
        self.systems: list[str | None] = []

    async def complete_stream(self, task: Any, messages: Any, **kwargs: Any):  # type: ignore[override]
        self.systems.append(kwargs.get("system"))
        async for event in super().complete_stream(task, messages, **kwargs):
            yield event


def _streaming_loop_system(policy: NetworkPolicy, promos: PromoOverrides) -> str:
    from src.agent.agent import ToolRouter
    from src.agent.streaming_loop import StreamingAgentLoop

    router = _RecordingRouter(
        [[TextDelta(text="Добрий день."), StreamDone(stop_reason="end_turn", usage=Usage(1, 1))]]
    )

    async def run() -> None:
        loop = StreamingAgentLoop(
            llm_router=router,  # type: ignore[arg-type]
            tool_router=ToolRouter(),
            tts=MockTTSEngine(),  # type: ignore[arg-type]
            conn=MockAudioSocketConnection(),  # type: ignore[arg-type]
            barge_in_event=asyncio.Event(),
            system_prompt=_BASE,
            network_policy=policy,
            promo_overrides=promos,
        )
        await loop.run_turn("Привіт", [])

    asyncio.run(run())
    assert router.systems and router.systems[0] is not None
    return router.systems[0]


@pytest.mark.parametrize("build", [_llm_agent_system, _streaming_loop_system])
def test_agents_pass_overrides(build: Any) -> None:
    system = build(TS, TS_PROMOS)
    block = render_network_block(TS, TS_PROMOS)
    assert block is not None and block != render_network_block(TS)
    assert block in system
    assert _warranty(block) in system
