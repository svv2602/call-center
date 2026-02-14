"""Unit tests for order tool definitions and schemas."""

from __future__ import annotations

import pytest

from src.agent.tools import ALL_TOOLS, MVP_TOOLS, ORDER_TOOLS


class TestOrderToolsList:
    """Test ORDER_TOOLS list structure."""

    def test_four_order_tools_defined(self) -> None:
        tool_names = {t["name"] for t in ORDER_TOOLS}
        assert tool_names == {
            "get_order_status",
            "create_order_draft",
            "update_order_delivery",
            "confirm_order",
        }

    def test_all_tools_is_combined(self) -> None:
        assert ALL_TOOLS == MVP_TOOLS + ORDER_TOOLS
        assert len(ALL_TOOLS) == 7

    def test_canonical_tool_names(self) -> None:
        """Tool names must match canonical list from 00-overview.md."""
        all_names = {t["name"] for t in ALL_TOOLS}
        # Phase 1 + Phase 2 canonical names
        expected = {
            "search_tires",
            "check_availability",
            "transfer_to_operator",
            "get_order_status",
            "create_order_draft",  # NOT create_order
            "update_order_delivery",
            "confirm_order",
        }
        assert all_names == expected


class TestGetOrderStatusSchema:
    """Test get_order_status tool schema."""

    @pytest.fixture
    def tool(self) -> dict:
        return next(t for t in ORDER_TOOLS if t["name"] == "get_order_status")

    def test_has_phone_and_order_id(self, tool: dict) -> None:
        props = tool["input_schema"]["properties"]
        assert "phone" in props
        assert "order_id" in props
        assert props["phone"]["type"] == "string"
        assert props["order_id"]["type"] == "string"

    def test_no_required_fields(self, tool: dict) -> None:
        """At least one of phone/order_id needed, but neither is strictly required."""
        assert "required" not in tool["input_schema"]


class TestCreateOrderDraftSchema:
    """Test create_order_draft tool schema."""

    @pytest.fixture
    def tool(self) -> dict:
        return next(t for t in ORDER_TOOLS if t["name"] == "create_order_draft")

    def test_required_fields(self, tool: dict) -> None:
        required = set(tool["input_schema"]["required"])
        assert required == {"items", "customer_phone"}

    def test_items_is_array_of_objects(self, tool: dict) -> None:
        items_schema = tool["input_schema"]["properties"]["items"]
        assert items_schema["type"] == "array"
        item_props = items_schema["items"]["properties"]
        assert "product_id" in item_props
        assert "quantity" in item_props
        assert items_schema["items"]["required"] == ["product_id", "quantity"]

    def test_quantity_is_integer(self, tool: dict) -> None:
        item_props = tool["input_schema"]["properties"]["items"]["items"]["properties"]
        assert item_props["quantity"]["type"] == "integer"

    def test_customer_phone_is_string(self, tool: dict) -> None:
        props = tool["input_schema"]["properties"]
        assert props["customer_phone"]["type"] == "string"


class TestUpdateOrderDeliverySchema:
    """Test update_order_delivery tool schema."""

    @pytest.fixture
    def tool(self) -> dict:
        return next(t for t in ORDER_TOOLS if t["name"] == "update_order_delivery")

    def test_required_fields(self, tool: dict) -> None:
        required = set(tool["input_schema"]["required"])
        assert required == {"order_id", "delivery_type"}

    def test_delivery_type_enum(self, tool: dict) -> None:
        dt = tool["input_schema"]["properties"]["delivery_type"]
        assert set(dt["enum"]) == {"delivery", "pickup"}

    def test_has_address_fields(self, tool: dict) -> None:
        props = tool["input_schema"]["properties"]
        assert "city" in props
        assert "address" in props
        assert "pickup_point_id" in props


class TestConfirmOrderSchema:
    """Test confirm_order tool schema."""

    @pytest.fixture
    def tool(self) -> dict:
        return next(t for t in ORDER_TOOLS if t["name"] == "confirm_order")

    def test_required_fields(self, tool: dict) -> None:
        required = set(tool["input_schema"]["required"])
        assert required == {"order_id", "payment_method"}

    def test_payment_method_enum(self, tool: dict) -> None:
        pm = tool["input_schema"]["properties"]["payment_method"]
        assert set(pm["enum"]) == {"cod", "online", "card_on_delivery"}

    def test_has_customer_name(self, tool: dict) -> None:
        props = tool["input_schema"]["properties"]
        assert "customer_name" in props

    def test_description_requires_confirmation(self, tool: dict) -> None:
        """confirm_order description must mention mandatory confirmation."""
        assert "підтвердження" in tool["description"].lower() or "ОБОВ" in tool["description"]
