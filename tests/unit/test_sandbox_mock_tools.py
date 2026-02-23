"""Unit tests for sandbox mock tools."""

from __future__ import annotations

import pytest

from src.sandbox.mock_tools import MOCK_RESPONSES, build_mock_tool_router


class TestMockResponses:
    """Test mock response data completeness."""

    def test_all_canonical_tools_have_mocks(self) -> None:
        """All 16 canonical tools should have mock data."""
        expected_tools = {
            "get_vehicle_tire_sizes",
            "search_tires",
            "check_availability",
            "transfer_to_operator",
            "get_order_status",
            "create_order_draft",
            "update_order_delivery",
            "confirm_order",
            "get_pickup_points",
            "get_fitting_stations",
            "get_fitting_slots",
            "book_fitting",
            "cancel_fitting",
            "get_fitting_price",
            "get_customer_bookings",
            "search_knowledge_base",
        }
        assert set(MOCK_RESPONSES.keys()) == expected_tools

    def test_search_tires_has_items(self) -> None:
        data = MOCK_RESPONSES["search_tires"]
        assert "items" in data
        assert len(data["items"]) >= 2
        assert all("price" in item for item in data["items"])

    def test_fitting_stations_has_list(self) -> None:
        data = MOCK_RESPONSES["get_fitting_stations"]
        assert "stations" in data
        assert len(data["stations"]) >= 1

    def test_order_status_has_orders(self) -> None:
        data = MOCK_RESPONSES["get_order_status"]
        assert "orders" in data
        assert len(data["orders"]) >= 1

    def test_knowledge_base_is_dynamic(self) -> None:
        assert MOCK_RESPONSES["search_knowledge_base"] == "dynamic"

    @pytest.mark.asyncio
    async def test_knowledge_base_brand_query(self) -> None:
        """Brand-specific query returns relevant brand data."""
        router = build_mock_tool_router()
        result = await router.execute(
            "search_knowledge_base", {"query": "Michelin зимові шини"}
        )
        assert "results" in result
        assert len(result["results"]) >= 1
        assert "Michelin" in result["results"][0]["title"]

    @pytest.mark.asyncio
    async def test_knowledge_base_generic_query(self) -> None:
        """Generic query returns fallback comparison data."""
        router = build_mock_tool_router()
        result = await router.execute(
            "search_knowledge_base", {"query": "які шини обрати"}
        )
        assert "results" in result
        assert len(result["results"]) >= 1


class TestBuildMockToolRouter:
    """Test mock tool router construction."""

    def test_router_has_all_tools(self) -> None:
        router = build_mock_tool_router()
        for tool_name in MOCK_RESPONSES:
            assert tool_name in router._handlers

    @pytest.mark.asyncio
    async def test_mock_handler_returns_copy(self) -> None:
        """Each call should return a fresh copy to avoid mutation."""
        router = build_mock_tool_router()
        result1 = await router.execute("search_tires", {})
        result2 = await router.execute("search_tires", {})
        assert result1 == result2
        assert result1 is not result2

    @pytest.mark.asyncio
    async def test_mock_handler_returns_data(self) -> None:
        router = build_mock_tool_router()
        result = await router.execute("check_availability", {})
        assert result["available"] is True
        assert result["product_id"] == "tire-001"

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self) -> None:
        router = build_mock_tool_router()
        result = await router.execute("nonexistent_tool", {})
        assert "error" in result
