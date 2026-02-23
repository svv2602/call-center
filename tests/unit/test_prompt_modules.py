"""Tests for modular prompt assembly and stage-aware injection.

Verifies that assemble_prompt() includes the right modules per scenario,
compute_order_stage() correctly determines the order stage, and
build_system_prompt_with_context() injects the right dynamic context.
"""

from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import patch

import pytest

from src.agent.prompts import (
    PRONUNCIATION_RULES,
    SYSTEM_PROMPT,
    _MOD_CONSULTATION,
    _MOD_CORE,
    _MOD_FITTING,
    _MOD_OBJECTIONS,
    _MOD_ORDER_FLOW,
    _MOD_ORDER_STATUS,
    _MOD_TIRE_SEARCH,
    _STAGE_OFFER_FITTING,
    _STAGE_ORDER_CONFIRMATION,
    assemble_prompt,
    build_system_prompt_with_context,
    compute_order_stage,
)


# ---------------------------------------------------------------------------
# TestAssemblePrompt
# ---------------------------------------------------------------------------


class TestAssemblePrompt:
    """Test assemble_prompt() selects correct modules per scenario."""

    def test_full_prompt_contains_all_modules(self) -> None:
        """scenario=None includes all scenario modules."""
        prompt = assemble_prompt(scenario=None)
        # Core always present
        assert "Ти — голосовий асистент" in prompt
        # All scenario modules present
        assert "підбір шин" in prompt
        assert "оформлення замовлення" in prompt
        assert "запис на шиномонтаж" in prompt
        assert "статусу замовлення" in prompt
        assert "консультація та інформація" in prompt
        assert "підбір → замовлення → монтаж" in prompt
        assert "запереченнями" in prompt

    def test_tire_search_includes_relevant_modules(self) -> None:
        """tire_search includes tire search, order, consultation, combined, objections."""
        prompt = assemble_prompt(scenario="tire_search")
        assert "підбір шин" in prompt
        assert "оформлення замовлення" in prompt
        assert "консультація та інформація" in prompt
        assert "підбір → замовлення → монтаж" in prompt
        assert "запереченнями" in prompt

    def test_tire_search_excludes_fitting_and_status(self) -> None:
        """tire_search excludes fitting and order_status modules."""
        prompt = assemble_prompt(scenario="tire_search")
        assert _MOD_FITTING not in prompt
        assert _MOD_ORDER_STATUS not in prompt

    def test_order_status_minimal(self) -> None:
        """order_status only includes status + consultation."""
        prompt = assemble_prompt(scenario="order_status")
        assert "статусу замовлення" in prompt
        assert "консультація та інформація" in prompt
        # Should NOT contain tire search, order flow, fitting
        assert _MOD_TIRE_SEARCH not in prompt
        assert _MOD_ORDER_FLOW not in prompt
        assert _MOD_FITTING not in prompt

    def test_fitting_includes_consultation(self) -> None:
        """fitting includes fitting + consultation, excludes others."""
        prompt = assemble_prompt(scenario="fitting")
        assert "запис на шиномонтаж" in prompt
        assert "консультація та інформація" in prompt
        assert _MOD_TIRE_SEARCH not in prompt
        assert _MOD_ORDER_FLOW not in prompt

    def test_consultation_minimal(self) -> None:
        """consultation is the most minimal scenario."""
        prompt = assemble_prompt(scenario="consultation")
        assert "консультація та інформація" in prompt
        assert _MOD_TIRE_SEARCH not in prompt
        assert _MOD_ORDER_FLOW not in prompt
        assert _MOD_FITTING not in prompt
        assert _MOD_ORDER_STATUS not in prompt

    def test_unknown_scenario_falls_back_to_full(self) -> None:
        """Unknown scenario falls back to full prompt (all modules)."""
        prompt = assemble_prompt(scenario="unknown_scenario")
        assert "підбір шин" in prompt
        assert "оформлення замовлення" in prompt
        assert "запис на шиномонтаж" in prompt

    def test_core_always_present(self) -> None:
        """Core identity/rules/style always present regardless of scenario."""
        for scenario in [None, "tire_search", "order_status", "fitting", "consultation"]:
            prompt = assemble_prompt(scenario=scenario)
            assert "Ти — голосовий асистент" in prompt
            assert "ЗАВЖДИ" in prompt
            assert "українською" in prompt
            assert "Формат відповіді" in prompt
            assert "Стиль живої розмови" in prompt

    def test_include_pronunciation_true(self) -> None:
        """Pronunciation rules included when include_pronunciation=True."""
        prompt = assemble_prompt(scenario=None, include_pronunciation=True)
        assert "Правила вимови" in prompt
        assert "Мішлен" in prompt

    def test_include_pronunciation_false(self) -> None:
        """Pronunciation rules excluded when include_pronunciation=False."""
        prompt = assemble_prompt(scenario=None, include_pronunciation=False)
        assert "Правила вимови" not in prompt
        assert "Мішлен" not in prompt

    def test_custom_pronunciation_rules(self) -> None:
        """Custom pronunciation rules override default."""
        custom = "## Custom pronunciation\n- Test rule"
        prompt = assemble_prompt(
            scenario=None,
            include_pronunciation=True,
            pronunciation_rules=custom,
        )
        assert "Custom pronunciation" in prompt
        assert "Test rule" in prompt
        # Default rules should NOT be present
        assert "Мішлен" not in prompt

    def test_consultation_shorter_than_full(self) -> None:
        """consultation prompt should be significantly shorter than full."""
        full = assemble_prompt(scenario=None, include_pronunciation=False)
        consult = assemble_prompt(scenario="consultation", include_pronunciation=False)
        # Consultation should be at most 60% of the full prompt
        assert len(consult) < len(full) * 0.6

    def test_order_status_shorter_than_tire_search(self) -> None:
        """order_status should be shorter than tire_search (fewer modules)."""
        status = assemble_prompt(scenario="order_status", include_pronunciation=False)
        tire = assemble_prompt(scenario="tire_search", include_pronunciation=False)
        assert len(status) < len(tire)


# ---------------------------------------------------------------------------
# TestComputeOrderStage
# ---------------------------------------------------------------------------


class TestComputeOrderStage:
    """Test compute_order_stage() returns correct stage."""

    def test_no_draft_no_order(self) -> None:
        assert compute_order_stage(None, None) is None

    def test_draft_without_delivery(self) -> None:
        draft: dict[str, Any] = {"items": [{"product_id": "123"}], "customer_phone": "+380"}
        assert compute_order_stage(draft, None) == "draft"

    def test_draft_with_delivery_type(self) -> None:
        draft: dict[str, Any] = {
            "items": [],
            "customer_phone": "+380",
            "delivery_type": "pickup",
        }
        assert compute_order_stage(draft, None) == "delivery_set"

    def test_confirmed_order(self) -> None:
        """order_id set + no draft = confirmed."""
        assert compute_order_stage(None, "AI-123") == "confirmed"

    def test_draft_with_empty_delivery_type(self) -> None:
        """Empty string delivery_type is falsy → still draft."""
        draft: dict[str, Any] = {
            "items": [],
            "delivery_type": "",
        }
        assert compute_order_stage(draft, None) == "draft"

    def test_draft_with_order_id(self) -> None:
        """Both draft and order_id present → delivery_set or draft (not confirmed)."""
        draft: dict[str, Any] = {"items": [], "delivery_type": "delivery"}
        # Even with order_id, if draft exists, it's still delivery_set
        assert compute_order_stage(draft, "AI-123") == "delivery_set"


# ---------------------------------------------------------------------------
# TestBuildSystemPromptWithContext
# ---------------------------------------------------------------------------


class TestBuildSystemPromptWithContext:
    """Test build_system_prompt_with_context() dynamic injection."""

    def test_season_hint_always_present(self) -> None:
        """Season hint is always injected."""
        result = build_system_prompt_with_context("base prompt")
        assert "Підказка по сезону" in result
        assert "base prompt" in result

    def test_caller_context_injected(self) -> None:
        """Caller phone and order_id injected when provided."""
        result = build_system_prompt_with_context(
            "base", caller_phone="+380671234567", order_id="DRAFT-123"
        )
        assert "CallerID клієнта: +380671234567" in result
        assert "Поточне замовлення (чорновик): DRAFT-123" in result

    def test_no_caller_context_when_empty(self) -> None:
        """No caller context section when phone/order_id are None."""
        result = build_system_prompt_with_context("base")
        assert "Контекст дзвінка" not in result

    def test_stage_injection_only_when_modular(self) -> None:
        """Stage injection only applies when is_modular=True."""
        result_not_modular = build_system_prompt_with_context(
            "base", is_modular=False, order_stage="delivery_set"
        )
        result_modular = build_system_prompt_with_context(
            "base", is_modular=True, order_stage="delivery_set"
        )
        assert "Підтвердження замовлення" not in result_not_modular
        assert "Підтвердження замовлення" in result_modular

    def test_stage_delivery_set_injects_confirmation(self) -> None:
        """delivery_set stage injects ORDER_CONFIRMATION checklist."""
        result = build_system_prompt_with_context(
            "base", is_modular=True, order_stage="delivery_set"
        )
        assert "Підтвердження замовлення" in result
        assert "confirm_order" in result

    def test_stage_confirmed_injects_offer_fitting(self) -> None:
        """confirmed stage injects OFFER_FITTING."""
        result = build_system_prompt_with_context(
            "base", is_modular=True, order_stage="confirmed"
        )
        assert "Замовлення підтверджено" in result
        assert "шиномонтаж" in result

    def test_stage_draft_no_injection(self) -> None:
        """draft stage doesn't inject any extra content."""
        result = build_system_prompt_with_context(
            "base", is_modular=True, order_stage="draft"
        )
        assert _STAGE_ORDER_CONFIRMATION not in result
        assert _STAGE_OFFER_FITTING not in result

    def test_no_stage_no_injection(self) -> None:
        """No stage = no injection."""
        result = build_system_prompt_with_context(
            "base", is_modular=True, order_stage=None
        )
        assert "Підтвердження замовлення" not in result
        assert "Замовлення підтверджено" not in result

    def test_safety_and_few_shot_injected(self) -> None:
        """Safety context and few-shot context are injected."""
        result = build_system_prompt_with_context(
            "base",
            safety_context="## Safety\nRule 1",
            few_shot_context="## Examples\nExample 1",
        )
        assert "Safety" in result
        assert "Examples" in result

    def test_pattern_context_injected(self) -> None:
        """Pattern context is injected at the end."""
        result = build_system_prompt_with_context(
            "base",
            pattern_context="## Patterns\nPattern 1",
        )
        assert "Patterns" in result

    @patch("src.agent.prompts.datetime")
    def test_winter_season_hint(self, mock_dt: Any) -> None:
        """Winter months produce winter season hint."""
        mock_dt.date.today.return_value = datetime.date(2026, 1, 15)
        result = build_system_prompt_with_context("base")
        assert "зимовий сезон" in result

    @patch("src.agent.prompts.datetime")
    def test_summer_season_hint(self, mock_dt: Any) -> None:
        """Summer months produce summer season hint."""
        mock_dt.date.today.return_value = datetime.date(2026, 7, 15)
        result = build_system_prompt_with_context("base")
        assert "літній сезон" in result

    @patch("src.agent.prompts.datetime")
    def test_transition_season_hint(self, mock_dt: Any) -> None:
        """April/October produce transition season hint."""
        mock_dt.date.today.return_value = datetime.date(2026, 4, 15)
        result = build_system_prompt_with_context("base")
        assert "міжсезоння" in result


# ---------------------------------------------------------------------------
# TestBackwardCompatibility
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Ensure SYSTEM_PROMPT constant is backward-compatible."""

    def test_system_prompt_contains_all_keywords(self) -> None:
        """SYSTEM_PROMPT constant must contain all required keywords."""
        assert "Ти — голосовий асистент" in SYSTEM_PROMPT
        assert "українською" in SYSTEM_PROMPT
        assert "ЗАВЖДИ" in SYSTEM_PROMPT
        assert "підбір шин" in SYSTEM_PROMPT
        assert "оформлення замовлення" in SYSTEM_PROMPT
        assert "запис на шиномонтаж" in SYSTEM_PROMPT
        assert "статусу замовлення" in SYSTEM_PROMPT
        assert "консультація та інформація" in SYSTEM_PROMPT
        assert "confirm_order" in SYSTEM_PROMPT
        assert "НІКОЛИ" in SYSTEM_PROMPT
        assert "Правила вимови" in SYSTEM_PROMPT
        assert "Мішлен" in SYSTEM_PROMPT

    def test_system_prompt_is_string(self) -> None:
        """SYSTEM_PROMPT must be a non-empty string."""
        assert isinstance(SYSTEM_PROMPT, str)
        assert len(SYSTEM_PROMPT) > 100
