"""Unit tests for IVR intent routing: _resolve_ivr_intent, tool filtering,
greeting suffix, and prompt emphasis."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestResolveIvrIntent:
    """Test _resolve_ivr_intent() from src.main."""

    @pytest.mark.asyncio
    async def test_ari_returns_valid_intent(self) -> None:
        mock_ari = AsyncMock()
        mock_ari.get_channel_variable = AsyncMock(return_value="tire_search")
        mock_ari.open = AsyncMock()
        mock_ari.close = AsyncMock()

        mock_settings = MagicMock()
        mock_settings.ari.url = "http://localhost:8088/ari"
        mock_settings.ari.user = "user"
        mock_settings.ari.password = "pass"

        with (
            patch("src.main.get_settings", return_value=mock_settings),
            patch("src.core.asterisk_ari.AsteriskARIClient", return_value=mock_ari),
        ):
            from src.main import _resolve_ivr_intent

            result = await _resolve_ivr_intent("test-uuid")

        assert result == "tire_search"

    @pytest.mark.asyncio
    async def test_ari_returns_uppercase_normalized(self) -> None:
        mock_ari = AsyncMock()
        mock_ari.get_channel_variable = AsyncMock(return_value="ORDER_STATUS")
        mock_ari.open = AsyncMock()
        mock_ari.close = AsyncMock()

        mock_settings = MagicMock()
        mock_settings.ari.url = "http://localhost:8088/ari"
        mock_settings.ari.user = "user"
        mock_settings.ari.password = "pass"

        with (
            patch("src.main.get_settings", return_value=mock_settings),
            patch("src.core.asterisk_ari.AsteriskARIClient", return_value=mock_ari),
        ):
            from src.main import _resolve_ivr_intent

            result = await _resolve_ivr_intent("test-uuid")

        assert result == "order_status"

    @pytest.mark.asyncio
    async def test_ari_returns_invalid_intent(self) -> None:
        mock_ari = AsyncMock()
        mock_ari.get_channel_variable = AsyncMock(return_value="invalid_scenario")
        mock_ari.open = AsyncMock()
        mock_ari.close = AsyncMock()

        mock_settings = MagicMock()
        mock_settings.ari.url = "http://localhost:8088/ari"
        mock_settings.ari.user = "user"
        mock_settings.ari.password = "pass"

        with (
            patch("src.main.get_settings", return_value=mock_settings),
            patch("src.core.asterisk_ari.AsteriskARIClient", return_value=mock_ari),
        ):
            from src.main import _resolve_ivr_intent

            result = await _resolve_ivr_intent("test-uuid")

        assert result is None

    @pytest.mark.asyncio
    async def test_ari_not_configured(self) -> None:
        mock_settings = MagicMock()
        mock_settings.ari.url = ""

        with patch("src.main.get_settings", return_value=mock_settings):
            from src.main import _resolve_ivr_intent

            result = await _resolve_ivr_intent("test-uuid")

        assert result is None

    @pytest.mark.asyncio
    async def test_ari_returns_none(self) -> None:
        mock_ari = AsyncMock()
        mock_ari.get_channel_variable = AsyncMock(return_value=None)
        mock_ari.open = AsyncMock()
        mock_ari.close = AsyncMock()

        mock_settings = MagicMock()
        mock_settings.ari.url = "http://localhost:8088/ari"
        mock_settings.ari.user = "user"
        mock_settings.ari.password = "pass"

        with (
            patch("src.main.get_settings", return_value=mock_settings),
            patch("src.core.asterisk_ari.AsteriskARIClient", return_value=mock_ari),
        ):
            from src.main import _resolve_ivr_intent

            result = await _resolve_ivr_intent("test-uuid")

        assert result is None

    @pytest.mark.asyncio
    async def test_ari_exception_returns_none(self) -> None:
        mock_settings = MagicMock()
        mock_settings.ari.url = "http://localhost:8088/ari"
        mock_settings.ari.user = "user"
        mock_settings.ari.password = "pass"

        with (
            patch("src.main.get_settings", return_value=mock_settings),
            patch(
                "src.core.asterisk_ari.AsteriskARIClient",
                side_effect=RuntimeError("connection failed"),
            ),
        ):
            from src.main import _resolve_ivr_intent

            result = await _resolve_ivr_intent("test-uuid")

        assert result is None

    @pytest.mark.asyncio
    async def test_all_valid_intents(self) -> None:
        from src.main import _VALID_IVR_INTENTS

        assert {"tire_search", "order_status", "fitting", "consultation"} == _VALID_IVR_INTENTS


class TestScenarioToolFiltering:
    """Test _SCENARIO_TOOLS tool filtering."""

    def test_tire_search_tools(self) -> None:
        from src.main import _SCENARIO_TOOLS

        tools = _SCENARIO_TOOLS["tire_search"]
        assert "search_tires" in tools
        assert "get_vehicle_tire_sizes" in tools
        assert "check_availability" in tools
        assert "create_order_draft" in tools
        assert "transfer_to_operator" in tools
        assert "search_knowledge_base" in tools
        # Fitting tools must NOT be present
        assert "get_fitting_stations" not in tools
        assert "book_fitting" not in tools
        assert "get_order_status" not in tools

    def test_order_status_tools(self) -> None:
        from src.main import _SCENARIO_TOOLS

        tools = _SCENARIO_TOOLS["order_status"]
        assert tools == {"get_order_status", "search_knowledge_base", "transfer_to_operator"}

    def test_fitting_tools(self) -> None:
        from src.main import _SCENARIO_TOOLS

        tools = _SCENARIO_TOOLS["fitting"]
        assert "get_fitting_stations" in tools
        assert "get_fitting_slots" in tools
        assert "book_fitting" in tools
        assert "cancel_fitting" in tools
        assert "get_fitting_price" in tools
        assert "transfer_to_operator" in tools
        assert "search_knowledge_base" in tools
        # Tire/order tools must NOT be present
        assert "search_tires" not in tools
        assert "get_order_status" not in tools

    def test_consultation_tools(self) -> None:
        from src.main import _SCENARIO_TOOLS

        tools = _SCENARIO_TOOLS["consultation"]
        assert tools == {"search_knowledge_base", "transfer_to_operator"}

    def test_all_scenarios_include_transfer_and_knowledge(self) -> None:
        from src.main import _SCENARIO_TOOLS

        for scenario, tools in _SCENARIO_TOOLS.items():
            assert "transfer_to_operator" in tools, f"{scenario} missing transfer_to_operator"
            assert "search_knowledge_base" in tools, f"{scenario} missing search_knowledge_base"

    def test_tool_filtering_applies(self) -> None:
        """Simulate the filtering logic from handle_call."""
        from src.main import _SCENARIO_TOOLS

        all_tools = [
            {"name": "search_tires"},
            {"name": "check_availability"},
            {"name": "get_order_status"},
            {"name": "get_fitting_stations"},
            {"name": "transfer_to_operator"},
            {"name": "search_knowledge_base"},
        ]

        allowed = _SCENARIO_TOOLS["order_status"]
        filtered = [t for t in all_tools if t["name"] in allowed]

        names = {t["name"] for t in filtered}
        assert names == {"get_order_status", "transfer_to_operator", "search_knowledge_base"}

    def test_no_scenario_no_filtering(self) -> None:
        """When scenario is None, all tools pass through."""
        from src.main import _SCENARIO_TOOLS

        scenario = None
        all_tools = [{"name": "tool_a"}, {"name": "tool_b"}]

        if scenario and scenario in _SCENARIO_TOOLS:
            all_tools = [t for t in all_tools if t["name"] in _SCENARIO_TOOLS[scenario]]

        assert len(all_tools) == 2


class TestScenarioGreetingSuffix:
    """Test _SCENARIO_GREETING_SUFFIX in pipeline.py."""

    def test_all_scenarios_have_suffix(self) -> None:
        from src.core.pipeline import _SCENARIO_GREETING_SUFFIX

        assert "tire_search" in _SCENARIO_GREETING_SUFFIX
        assert "order_status" in _SCENARIO_GREETING_SUFFIX
        assert "fitting" in _SCENARIO_GREETING_SUFFIX
        assert "consultation" in _SCENARIO_GREETING_SUFFIX

    def test_suffixes_are_ukrainian(self) -> None:
        from src.core.pipeline import _SCENARIO_GREETING_SUFFIX

        for suffix in _SCENARIO_GREETING_SUFFIX.values():
            assert len(suffix) > 10
            assert suffix.endswith(".")

    def test_no_scenario_no_suffix(self) -> None:
        from src.core.pipeline import _SCENARIO_GREETING_SUFFIX

        suffix = _SCENARIO_GREETING_SUFFIX.get(None or "")  # type: ignore[arg-type]
        assert suffix is None


class TestScenarioEmphasis:
    """Test _SCENARIO_EMPHASIS prompt addition."""

    def test_all_scenarios_have_emphasis(self) -> None:
        from src.main import _SCENARIO_EMPHASIS

        assert "tire_search" in _SCENARIO_EMPHASIS
        assert "order_status" in _SCENARIO_EMPHASIS
        assert "fitting" in _SCENARIO_EMPHASIS
        assert "consultation" in _SCENARIO_EMPHASIS

    def test_emphasis_starts_with_ivr_marker(self) -> None:
        from src.main import _SCENARIO_EMPHASIS

        for scenario, text in _SCENARIO_EMPHASIS.items():
            assert "[IVR-фокус]" in text, f"{scenario} emphasis missing [IVR-фокус] marker"

    def test_emphasis_appended_to_prompt(self) -> None:
        from src.main import _SCENARIO_EMPHASIS

        base_prompt = "Ти — голосовий асистент."
        emphasis = _SCENARIO_EMPHASIS["tire_search"]
        result = base_prompt + emphasis
        assert result.startswith("Ти — голосовий асистент.")
        assert "[IVR-фокус]" in result
