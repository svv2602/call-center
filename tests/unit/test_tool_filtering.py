"""Tests for lazy tool filtering by conversation state (OPT-2)."""

from __future__ import annotations

from src.agent.tools import ALL_TOOLS, filter_tools_by_state


def _tool_names(tools: list[dict]) -> set[str]:  # type: ignore[type-arg]
    return {t["name"] for t in tools}


class TestFilterToolsByState:
    """Tests for filter_tools_by_state()."""

    def test_no_filtering_when_no_state(self) -> None:
        """No order_stage and no fitting_booked → removes order mid-flow tools."""
        result = filter_tools_by_state(ALL_TOOLS, order_stage=None, fitting_booked=False)
        names = _tool_names(result)
        # update_order_delivery and confirm_order should be excluded
        assert "update_order_delivery" not in names
        assert "confirm_order" not in names
        # Other tools remain
        assert "search_tires" in names
        assert "create_order_draft" in names
        assert "book_fitting" in names

    def test_draft_stage_excludes_confirm(self) -> None:
        """draft stage → confirm_order excluded."""
        result = filter_tools_by_state(ALL_TOOLS, order_stage="draft")
        names = _tool_names(result)
        assert "confirm_order" not in names
        assert "update_order_delivery" in names
        assert "create_order_draft" in names

    def test_delivery_set_keeps_all_order_tools(self) -> None:
        """delivery_set stage → all order tools available."""
        result = filter_tools_by_state(ALL_TOOLS, order_stage="delivery_set")
        names = _tool_names(result)
        assert "create_order_draft" in names
        assert "update_order_delivery" in names
        assert "confirm_order" in names

    def test_confirmed_excludes_order_mutation_tools(self) -> None:
        """confirmed stage → create/update/confirm excluded."""
        result = filter_tools_by_state(ALL_TOOLS, order_stage="confirmed")
        names = _tool_names(result)
        assert "create_order_draft" not in names
        assert "update_order_delivery" not in names
        assert "confirm_order" not in names
        # Other tools remain
        assert "search_tires" in names
        assert "book_fitting" in names

    def test_fitting_booked_excludes_booking_tools(self) -> None:
        """fitting_booked → book_fitting and get_fitting_slots excluded."""
        result = filter_tools_by_state(ALL_TOOLS, order_stage=None, fitting_booked=True)
        names = _tool_names(result)
        assert "book_fitting" not in names
        assert "get_fitting_slots" not in names
        # Other fitting tools remain
        assert "get_fitting_stations" in names
        assert "get_fitting_price" in names
        assert "cancel_fitting" in names

    def test_confirmed_and_fitting_booked_combined(self) -> None:
        """Both confirmed + fitting_booked → cumulative exclusion."""
        result = filter_tools_by_state(
            ALL_TOOLS, order_stage="confirmed", fitting_booked=True
        )
        names = _tool_names(result)
        assert "create_order_draft" not in names
        assert "confirm_order" not in names
        assert "book_fitting" not in names
        assert "get_fitting_slots" not in names
        # Remaining tools
        assert "search_tires" in names
        assert "transfer_to_operator" in names
        assert "get_fitting_stations" in names

    def test_does_not_mutate_original(self) -> None:
        """filter_tools_by_state should not mutate the input list."""
        original_len = len(ALL_TOOLS)
        filter_tools_by_state(ALL_TOOLS, order_stage="confirmed", fitting_booked=True)
        assert len(ALL_TOOLS) == original_len

    def test_empty_tools_list(self) -> None:
        """Empty tools list → empty result."""
        result = filter_tools_by_state([], order_stage="draft")
        assert result == []

    def test_delivery_set_no_exclusions(self) -> None:
        """delivery_set without fitting_booked → full count minus 0."""
        result = filter_tools_by_state(ALL_TOOLS, order_stage="delivery_set", fitting_booked=False)
        assert len(result) == len(ALL_TOOLS)

    def test_all_tools_count_unchanged(self) -> None:
        """ALL_TOOLS should have 17 tool definitions."""
        assert len(ALL_TOOLS) == 17
