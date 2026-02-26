"""Tests for dynamic module injection mid-call (OPT-3)."""

from __future__ import annotations

from src.agent.prompts import (
    SCENARIO_MODULES,
    _MOD_COMBINED_FLOW,
    _MOD_CONSULTATION,
    _MOD_FITTING,
    _MOD_ORDER_FLOW,
    _MOD_ORDER_STATUS,
    _MOD_STORAGE,
    _MOD_TIRE_SEARCH,
    build_system_prompt_with_context,
    infer_expanded_modules,
)


class TestInferExpandedModules:
    """Tests for infer_expanded_modules()."""

    def test_no_tools_called_returns_none(self) -> None:
        """No tools called → no expansion."""
        assert infer_expanded_modules("order_status", set()) is None
        assert infer_expanded_modules("order_status", None) is None

    def test_order_status_plus_search_expands_tire_search(self) -> None:
        """order_status scenario + search_tires called → adds tire search module."""
        result = infer_expanded_modules("order_status", {"search_tires"})
        assert result is not None
        assert _MOD_TIRE_SEARCH in result

    def test_order_status_plus_get_order_no_expansion(self) -> None:
        """order_status + get_order_status (already in scenario) → no expansion."""
        result = infer_expanded_modules("order_status", {"get_order_status"})
        assert result is None

    def test_tire_search_plus_fitting_expands(self) -> None:
        """tire_search scenario + get_fitting_stations → adds fitting module."""
        result = infer_expanded_modules("tire_search", {"get_fitting_stations"})
        assert result is not None
        assert _MOD_FITTING in result

    def test_confirm_order_adds_fitting_and_combined(self) -> None:
        """order_status + confirm_order → adds order flow + fitting + combined."""
        result = infer_expanded_modules("order_status", {"confirm_order"})
        assert result is not None
        assert _MOD_ORDER_FLOW in result
        assert _MOD_FITTING in result
        assert _MOD_COMBINED_FLOW in result

    def test_no_duplicates_in_expansion(self) -> None:
        """Multiple tools from same module don't produce duplicate modules."""
        result = infer_expanded_modules(
            "order_status",
            {"search_tires", "check_availability", "get_vehicle_tire_sizes"},
        )
        assert result is not None
        # All three map to _MOD_TIRE_SEARCH — should appear only once
        assert result.count(_MOD_TIRE_SEARCH) == 1

    def test_full_scenario_no_expansion(self) -> None:
        """None scenario (full prompt) → never expands (all modules present)."""
        result = infer_expanded_modules(None, {"search_tires", "book_fitting"})
        assert result is None

    def test_transfer_to_operator_no_expansion(self) -> None:
        """transfer_to_operator maps to empty list → no expansion."""
        result = infer_expanded_modules("order_status", {"transfer_to_operator"})
        assert result is None

    def test_build_prompt_includes_expanded_module(self) -> None:
        """Integration: build_system_prompt_with_context includes expanded module text."""
        from src.agent.prompts import assemble_prompt

        # order_status prompt doesn't include tire search
        base = assemble_prompt(scenario="order_status", include_pronunciation=False)
        assert "підбір шин" not in base

        # But with tools_called={search_tires}, it should be expanded
        result = build_system_prompt_with_context(
            base,
            is_modular=True,
            tools_called={"search_tires"},
            scenario="order_status",
        )
        assert "підбір шин" in result
