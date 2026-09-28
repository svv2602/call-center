"""Claim guard coverage (wave 4-P): a written policy, and the second road to TTS.

1. ``NetworkPolicy.configured`` — True only when ``tenants.config`` carries a
   ``network_policy`` dict. Only then an empty ``services`` means "not
   provided", so ``service_offer`` runs even with sales off (Про Колесо offered
   fitting in 9 of 26 bot turns on 2026-09-01..15). Unwritten policy → the
   guard behaves as before this wave.
2. ``_request_summary_fallback`` — LLM text synthesised straight into TTS; it
   now passes the same sentence verdicts (replace, never silence).

Policies are built the way production builds them — ``from_tenant_config`` over
the owner's patches in ``scripts/configure_tenants.py``.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from scripts.configure_tenants import PROKOLESO_CONFIG_PATCH, TVOYA_SHINA_CONFIG_PATCH
from src.agent.network_claim_guard import (
    ENV_FLAG,
    PASS,
    RULE_EXTENDED_WARRANTY,
    RULE_FREE_DELIVERY,
    RULE_SERVICE_OFFER,
    check_sentence,
    guard_text,
)
from src.agent.network_policy import NetworkPolicy
from src.llm.models import LLMResponse, Usage


def _policy(patch: dict[str, Any], *, sales: bool) -> NetworkPolicy:
    return NetworkPolicy.from_tenant_config({**patch, "sales_enabled": sales})


TS_OFF = _policy(TVOYA_SHINA_CONFIG_PATCH, sales=False)
PK_OFF = _policy(PROKOLESO_CONFIG_PATCH, sales=False)
PK_ON = _policy(PROKOLESO_CONFIG_PATCH, sales=True)
#: Production on 2026-09-28: neither tenant has `network_policy` in config.
PROD_TODAY = NetworkPolicy.from_tenant_config({})
UNWRITTEN_SALES_ON = NetworkPolicy.from_tenant_config({"sales_enabled": True})

#: Shapes seen in Про Колесо calls `1db8e83e` / `a202b55b`.
PK_FITTING_OFFERS = (
    "Записую вас на шиномонтаж у Києві, 16 вересня о 10:00.",
    "У якому місті вам зручніше записатися на шиномонтаж?",
    "Шини привозите свої з собою чи ті, що у нас на зберіганні?",
)

TS_FITTING_PHRASES = (
    "Записала вас на шиномонтаж на 10:00.",
    "Записую вас на шиномонтаж у Києві, 16 вересня о 10:00.",
    "Шини привозите свої з собою чи ті, що у нас на зберіганні?",
    "Вартість шиномонтажу залежить від діаметра.",
)


def _rule(sentence: str, policy: NetworkPolicy | None) -> str | None:
    verdict = check_sentence(sentence, policy)
    return None if verdict.action == PASS else verdict.rule


# ── NetworkPolicy.configured ────────────────────────────────────────────


class TestConfigured:
    @pytest.mark.parametrize(
        "cfg",
        [
            None,
            {},
            [],
            "garbage",
            {"sales_enabled": True},
            {"network_policy": None},
            {"network_policy": "free delivery"},
            {"network_policy": ["fitting"]},
        ],
    )
    def test_unwritten_or_garbage_is_not_configured(self, cfg: Any) -> None:
        assert NetworkPolicy.from_tenant_config(cfg).configured is False

    @pytest.mark.parametrize("patch", [TVOYA_SHINA_CONFIG_PATCH, PROKOLESO_CONFIG_PATCH])
    @pytest.mark.parametrize("sales", [True, False])
    def test_written_policy_is_configured(self, patch: dict[str, Any], sales: bool) -> None:
        assert _policy(patch, sales=sales).configured is True

    def test_default_is_not_configured(self) -> None:
        assert NetworkPolicy().configured is False


# ── service_offer follows `configured`, not `sales_enabled` ─────────────


class TestServiceOfferWithSalesOff:
    @pytest.mark.parametrize("sentence", PK_FITTING_OFFERS)
    def test_prokoleso_without_sales_catches_a_fitting_offer(self, sentence: str) -> None:
        verdict = check_sentence(sentence, PK_OFF)
        assert verdict.rule == RULE_SERVICE_OFFER
        assert verdict.replacement
        assert check_sentence(verdict.replacement, PK_OFF).action == PASS

    @pytest.mark.parametrize("sentence", TS_FITTING_PHRASES)
    @pytest.mark.parametrize("name", ["ts_off", "prod_today", "none"])
    def test_tvoya_shina_fitting_is_not_cut(self, sentence: str, name: str) -> None:
        policy = {"ts_off": TS_OFF, "prod_today": PROD_TODAY, "none": None}[name]
        assert _rule(sentence, policy) is None

    @pytest.mark.parametrize("sentence", PK_FITTING_OFFERS)
    def test_unconfigured_policy_does_not_cut_fitting(self, sentence: str) -> None:
        # Tvoya Shina fitting calls today: config has no `network_policy`.
        assert _rule(sentence, PROD_TODAY) is None

    @pytest.mark.parametrize("sentence", PK_FITTING_OFFERS)
    def test_unwritten_policy_with_sales_on_keeps_the_old_gate(self, sentence: str) -> None:
        # Invariant: without a written policy the guard behaves as before the
        # wave, and before it `sales_enabled` alone gated `service_offer`.
        assert _rule(sentence, UNWRITTEN_SALES_ON) == RULE_SERVICE_OFFER

    @pytest.mark.parametrize(
        ("sentence", "rule"),
        [
            ("Доставка безкоштовна.", RULE_FREE_DELIVERY),
            ("Є розширена гарантія на Michelin.", RULE_EXTENDED_WARRANTY),
        ],
    )
    def test_sales_rules_stay_behind_sales_enabled(self, sentence: str, rule: str) -> None:
        assert _rule(sentence, PK_OFF) is None
        assert _rule(sentence, PK_ON) == rule

    def test_refusal_passes_with_sales_off(self) -> None:
        assert _rule("На жаль, наш магазин не надає послуги шиномонтажу.", PK_OFF) is None


# ── guard_text ──────────────────────────────────────────────────────────


class TestGuardText:
    def test_claim_replaced_rest_kept(self) -> None:
        out = guard_text("Знайшла дві точки. Записую вас на шиномонтаж о 10:00.", PK_OFF)
        assert out.startswith("Знайшла дві точки.")
        assert "Записую" not in out
        assert "не надає" in out

    def test_clean_text_is_unchanged(self) -> None:
        text = "Записую вас на шиномонтаж о 10:00.  Все вірно?"
        assert guard_text(text, TS_OFF) == text

    def test_only_claims_never_empty(self) -> None:
        out = guard_text(
            "Записую вас на шиномонтаж. Шиномонтаж о 10:00. Приїжджайте на шиномонтаж.",
            PK_OFF,
        )
        assert out == "На жаль, шиномонтаж наша мережа не надає."

    def test_output_is_final(self) -> None:
        once = guard_text("Ваше замовлення підтверджено. Записую на шиномонтаж.", PK_OFF)
        assert guard_text(once, PK_OFF) == once

    def test_env_flag_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_FLAG, "0")
        text = "Записую вас на шиномонтаж."
        assert guard_text(text, PK_OFF) == text


# ── Wiring: `_request_summary_fallback` ─────────────────────────────────


def _router_answering(text: str) -> Any:
    router = MagicMock(spec=["complete", "_resolve_chain"])
    router.complete = AsyncMock(
        return_value=LLMResponse(
            text=text, tool_calls=[], stop_reason="end_turn", usage=Usage(1, 1)
        )
    )
    return router


def _loop(router: Any, policy: NetworkPolicy | None) -> Any:
    from src.agent.agent import ToolRouter
    from src.agent.streaming_loop import StreamingAgentLoop
    from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection
    from tests.unit.mocks.mock_tts import MockTTSEngine

    return StreamingAgentLoop(
        llm_router=router,
        tool_router=ToolRouter(),
        tts=MockTTSEngine(),
        conn=MockAudioSocketConnection(),
        barge_in_event=asyncio.Event(),
        system_prompt="Test system prompt",
        network_policy=policy,
    )


class TestSummaryFallbackIsGuarded:
    @pytest.mark.asyncio
    async def test_fitting_offer_in_prokoleso_summary_is_replaced(self) -> None:
        loop = _loop(_router_answering("Записую вас на шиномонтаж у Києві о 10:00."), PK_OFF)
        summary = await loop._request_summary_fallback("system", [])
        assert "Записую" not in summary
        assert summary == "На жаль, шиномонтаж наша мережа не надає."

    @pytest.mark.asyncio
    async def test_order_confirmation_replaced_without_policy(self) -> None:
        loop = _loop(_router_answering("Знайшла шини. Ваше замовлення підтверджено."), None)
        summary = await loop._request_summary_fallback("system", [])
        assert "підтверджено" not in summary
        assert summary.startswith("Знайшла шини.")
        assert "менеджер" in summary

    @pytest.mark.asyncio
    async def test_tvoya_shina_summary_is_spoken_unchanged(self) -> None:
        text = "Записую вас на шиномонтаж у Києві о 10:00."
        loop = _loop(_router_answering(text), PROD_TODAY)
        assert await loop._request_summary_fallback("system", []) == text
