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
    _COMPACT_MARKER,
    _MOD_FITTING,
    _MOD_ORDER_FLOW,
    _MOD_ORDER_STATUS,
    _MOD_TIRE_SEARCH,
    _STAGE_OFFER_FITTING,
    _STAGE_ORDER_CONFIRMATION,
    SYSTEM_PROMPT,
    assemble_prompt,
    build_system_prompt_with_context,
    compute_order_stage,
    detect_scenario_from_text,
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
        assert "зберігання шин" in prompt
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

    def test_fitting_includes_order_and_consultation(self) -> None:
        """fitting includes fitting + storage + order flow + consultation + combined."""
        prompt = assemble_prompt(scenario="fitting")
        assert "запис на шиномонтаж" in prompt
        assert "зберігання шин" in prompt
        assert "оформлення замовлення" in prompt
        assert "консультація та інформація" in prompt
        assert "підбір → замовлення → монтаж" in prompt
        assert _MOD_TIRE_SEARCH not in prompt

    def test_consultation_includes_all_action_modules(self) -> None:
        """consultation includes all modules for seamless topic switching."""
        prompt = assemble_prompt(scenario="consultation")
        assert "консультація та інформація" in prompt
        assert "підбір шин" in prompt
        assert "оформлення замовлення" in prompt
        assert "запис на шиномонтаж" in prompt
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

    def test_order_status_shortest_scenario(self) -> None:
        """order_status is the most minimal scenario (status + consultation only)."""
        status = assemble_prompt(scenario="order_status", include_pronunciation=False)
        full = assemble_prompt(scenario=None, include_pronunciation=False)
        # order_status should be significantly shorter than full
        assert len(status) < len(full) * 0.6

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
        result = build_system_prompt_with_context("base", is_modular=True, order_stage="confirmed")
        assert "Замовлення підтверджено" in result
        assert "шиномонтаж" in result

    def test_stage_draft_no_injection(self) -> None:
        """draft stage doesn't inject any extra content."""
        result = build_system_prompt_with_context("base", is_modular=True, order_stage="draft")
        assert _STAGE_ORDER_CONFIRMATION not in result
        assert _STAGE_OFFER_FITTING not in result

    def test_no_stage_no_injection(self) -> None:
        """No stage = no injection."""
        result = build_system_prompt_with_context("base", is_modular=True, order_stage=None)
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

    def test_stable_sections_before_dynamic(self) -> None:
        """Stable sections (date, safety, few-shot, promos, caller_history, storage)
        must appear before dynamic sections (stage injection, caller context, patterns)
        for optimal implicit cache hits."""
        result = build_system_prompt_with_context(
            "base prompt",
            is_modular=True,
            order_stage="delivery_set",
            safety_context="## Safety rules here",
            few_shot_context="## Few-shot examples",
            promotions_context="## Active promotions",
            caller_phone="+380671234567",
            order_id="DRAFT-999",
            pattern_context="## Pattern context",
            caller_history="## Caller history section",
            storage_context="## Storage contracts",
        )
        # Stable sections positions
        pos_date = result.index("Поточна дата")
        pos_season = result.index("Підказка по сезону")
        pos_safety = result.index("Safety rules here")
        pos_few_shot = result.index("Few-shot examples")
        pos_promos = result.index("Active promotions")
        pos_caller_history = result.index("Caller history section")
        pos_storage = result.index("Storage contracts")
        # Dynamic sections positions
        pos_stage = result.index("Підтвердження замовлення")
        pos_caller_ctx = result.index("Контекст дзвінка")
        pos_pattern = result.index("Pattern context")

        # All stable sections before all dynamic sections
        stable_positions = [
            pos_date, pos_season, pos_safety, pos_few_shot,
            pos_promos, pos_caller_history, pos_storage,
        ]
        dynamic_positions = [pos_stage, pos_caller_ctx, pos_pattern]
        assert max(stable_positions) < min(dynamic_positions), (
            "Stable sections must all appear before dynamic sections"
        )


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


# ---------------------------------------------------------------------------
# TestCompactRouter
# ---------------------------------------------------------------------------


class TestCompactRouter:
    """Test compact=True mode and scenario detection from text."""

    def test_compact_uses_router_module(self) -> None:
        """compact=True with no scenario produces MOD_CORE + MOD_ROUTER only."""
        prompt = assemble_prompt(scenario=None, compact=True, include_pronunciation=False)
        assert _COMPACT_MARKER in prompt
        assert "Ти — голосовий асистент" in prompt
        # Should NOT contain full scenario modules
        assert _MOD_TIRE_SEARCH not in prompt
        assert _MOD_FITTING not in prompt
        assert _MOD_ORDER_FLOW not in prompt
        assert _MOD_ORDER_STATUS not in prompt

    def test_compact_shorter_than_full(self) -> None:
        """Compact prompt should be significantly shorter than full."""
        compact = assemble_prompt(scenario=None, compact=True, include_pronunciation=False)
        full = assemble_prompt(scenario=None, compact=False, include_pronunciation=False)
        # Compact should be less than 50% of full
        assert len(compact) < len(full) * 0.5

    def test_compact_ignored_when_scenario_set(self) -> None:
        """compact=True has no effect when scenario is explicitly set."""
        prompt = assemble_prompt(scenario="fitting", compact=True, include_pronunciation=False)
        assert "запис на шиномонтаж" in prompt
        assert _COMPACT_MARKER not in prompt

    def test_compact_includes_pronunciation_when_requested(self) -> None:
        """Pronunciation rules still included in compact mode."""
        prompt = assemble_prompt(scenario=None, compact=True, include_pronunciation=True)
        assert "Правила вимови" in prompt

    def test_compact_to_full_upgrade_in_builder(self) -> None:
        """build_system_prompt_with_context upgrades compact→full when scenario detected."""
        compact_base = assemble_prompt(
            scenario=None, compact=True, include_pronunciation=False
        )
        assert _COMPACT_MARKER in compact_base

        # Simulate: scenario detected mid-call
        result = build_system_prompt_with_context(
            compact_base,
            is_modular=True,
            scenario="fitting",
        )
        # After upgrade, fitting module content should be present
        assert "запис на шиномонтаж" in result
        # Router module should be replaced
        assert _COMPACT_MARKER not in result

    def test_compact_no_upgrade_when_scenario_none(self) -> None:
        """No upgrade when scenario is still None."""
        compact_base = assemble_prompt(
            scenario=None, compact=True, include_pronunciation=False
        )
        result = build_system_prompt_with_context(
            compact_base,
            is_modular=True,
            scenario=None,
        )
        # Router module still present (no upgrade)
        assert _COMPACT_MARKER in result
        assert _MOD_FITTING not in result


# ---------------------------------------------------------------------------
# TestDetectScenarioFromText
# ---------------------------------------------------------------------------


class TestDetectScenarioFromText:
    """Test detect_scenario_from_text() keyword matching."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Хочу записатися на шиномонтаж", "fitting"),
            ("Мені потрібно перезувати колеса", "fitting"),
            ("Записати на монтаж", "fitting"),
            ("Хочу переобутися", "fitting"),
            ("Шукаю зимові шини", "tire_search"),
            ("Підібрати резину на авто", "tire_search"),
            ("Потрібні літні колеса", "tire_search"),
            ("Де моє замовлення?", "order_status"),
            ("Статус замовлення", "order_status"),
            ("Хочу відстежити заказ", "order_status"),
            ("Підкажіть що краще вибрати", "consultation"),
            ("Яка різниця між брендами", "consultation"),
            ("Порівняти Continental і Michelin", "consultation"),
        ],
    )
    def test_detects_known_scenarios(self, text: str, expected: str) -> None:
        assert detect_scenario_from_text(text) == expected

    def test_returns_none_for_unrecognized(self) -> None:
        assert detect_scenario_from_text("Добрий день") is None
        assert detect_scenario_from_text("Мене звати Олександр") is None
        assert detect_scenario_from_text("") is None

    def test_case_insensitive(self) -> None:
        assert detect_scenario_from_text("ШИНОМОНТАЖ") == "fitting"
        assert detect_scenario_from_text("ШИНИ") == "tire_search"

    def test_mixed_language(self) -> None:
        """Russian keywords also detected (customers may speak Russian)."""
        assert detect_scenario_from_text("Хочу поменять резину") == "fitting"
        assert detect_scenario_from_text("покрышки нужны") == "tire_search"


# ---------------------------------------------------------------------------
# TestTopicSwitching
# ---------------------------------------------------------------------------


class TestTopicSwitching:
    """Test that active_scenarios adds modules when customer changes topic."""

    def test_fitting_plus_tire_search(self) -> None:
        """Starting with fitting, switching to tire search adds tire module."""
        base = assemble_prompt(scenario="fitting", include_pronunciation=False)
        # Before topic switch — no tire search
        assert _MOD_TIRE_SEARCH not in base

        result = build_system_prompt_with_context(
            base,
            is_modular=True,
            scenario="fitting",
            active_scenarios={"fitting", "tire_search"},
        )
        # After topic switch — tire search module present
        assert "підбір шин" in result
        # Fitting still present
        assert "запис на шиномонтаж" in result

    def test_tire_search_plus_fitting(self) -> None:
        """Starting with tire search, switching to fitting adds fitting module."""
        base = assemble_prompt(scenario="tire_search", include_pronunciation=False)
        assert _MOD_FITTING not in base

        result = build_system_prompt_with_context(
            base,
            is_modular=True,
            scenario="tire_search",
            active_scenarios={"tire_search", "fitting"},
        )
        assert "запис на шиномонтаж" in result
        assert "підбір шин" in result

    def test_single_scenario_no_expansion(self) -> None:
        """Single active scenario doesn't add extra modules."""
        base = assemble_prompt(scenario="fitting", include_pronunciation=False)
        result = build_system_prompt_with_context(
            base,
            is_modular=True,
            scenario="fitting",
            active_scenarios={"fitting"},
        )
        # No tire search added — only fitting's modules
        assert _MOD_TIRE_SEARCH not in result

    def test_no_active_scenarios_no_expansion(self) -> None:
        """None active_scenarios doesn't cause expansion."""
        base = assemble_prompt(scenario="fitting", include_pronunciation=False)
        result = build_system_prompt_with_context(
            base,
            is_modular=True,
            scenario="fitting",
            active_scenarios=None,
        )
        assert _MOD_TIRE_SEARCH not in result

    def test_no_duplicate_modules(self) -> None:
        """Modules shared between scenarios should not be duplicated."""
        base = assemble_prompt(scenario="fitting", include_pronunciation=False)
        # Both fitting and consultation include _MOD_CONSULTATION
        result = build_system_prompt_with_context(
            base,
            is_modular=True,
            scenario="fitting",
            active_scenarios={"fitting", "consultation"},
        )
        # _MOD_CONSULTATION should appear only once
        assert result.count("## Сценарій: консультація та інформація") == 1
