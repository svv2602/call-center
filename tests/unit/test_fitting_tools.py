"""Unit tests for fitting tool definitions and schemas."""

from __future__ import annotations

import pytest

from src.agent.tools import ALL_TOOLS, FITTING_TOOLS, MVP_TOOLS, ORDER_TOOLS


class TestFittingToolsList:
    """Test FITTING_TOOLS list structure."""

    def test_fitting_tools_defined(self) -> None:
        tool_names = {t["name"] for t in FITTING_TOOLS}
        assert tool_names == {
            "get_fitting_stations",
            "get_fitting_slots",
            "book_fitting",
            "cancel_fitting",
            "get_fitting_price",
            "get_customer_bookings",
            "search_knowledge_base",
        }

    def test_all_tools_is_combined(self) -> None:
        assert ALL_TOOLS == MVP_TOOLS + ORDER_TOOLS + FITTING_TOOLS
        assert len(ALL_TOOLS) == 16

    def test_canonical_tool_names(self) -> None:
        """Tool names must match canonical list from 00-overview.md."""
        all_names = {t["name"] for t in ALL_TOOLS}
        expected = {
            # Phase 1
            "get_vehicle_tire_sizes",
            "search_tires",
            "check_availability",
            "transfer_to_operator",
            # Phase 2
            "get_order_status",
            "create_order_draft",
            "update_order_delivery",
            "confirm_order",
            "get_pickup_points",
            # Phase 3
            "get_fitting_stations",
            "get_fitting_slots",
            "book_fitting",
            "cancel_fitting",
            "get_fitting_price",
            "get_customer_bookings",
            "search_knowledge_base",
        }
        assert all_names == expected


class TestGetFittingStationsSchema:
    """Test get_fitting_stations tool schema."""

    @pytest.fixture
    def tool(self) -> dict:
        return next(t for t in FITTING_TOOLS if t["name"] == "get_fitting_stations")

    def test_city_optional(self, tool: dict) -> None:
        assert "city" not in tool["input_schema"]["required"]

    def test_city_is_string(self, tool: dict) -> None:
        props = tool["input_schema"]["properties"]
        assert props["city"]["type"] == "string"

    def test_has_description(self, tool: dict) -> None:
        assert "шиномонтаж" in tool["description"].lower()


class TestGetFittingSlotsSchema:
    """Test get_fitting_slots tool schema."""

    @pytest.fixture
    def tool(self) -> dict:
        return next(t for t in FITTING_TOOLS if t["name"] == "get_fitting_slots")

    def test_station_id_required(self, tool: dict) -> None:
        assert tool["input_schema"]["required"] == ["station_id"]

    def test_has_date_params(self, tool: dict) -> None:
        props = tool["input_schema"]["properties"]
        assert "date_from" in props
        assert "date_to" in props

    def test_service_type_enum(self, tool: dict) -> None:
        st = tool["input_schema"]["properties"]["service_type"]
        assert set(st["enum"]) == {"tire_change", "balancing", "full_service"}


class TestBookFittingSchema:
    """Test book_fitting tool schema."""

    @pytest.fixture
    def tool(self) -> dict:
        return next(t for t in FITTING_TOOLS if t["name"] == "book_fitting")

    def test_required_fields(self, tool: dict) -> None:
        required = set(tool["input_schema"]["required"])
        assert required == {"station_id", "date", "time", "customer_name", "customer_phone"}

    def test_has_optional_fields(self, tool: dict) -> None:
        props = tool["input_schema"]["properties"]
        assert "vehicle_info" in props
        assert "service_type" in props
        assert "tire_diameter" in props
        assert "linked_order_id" in props

    def test_tire_diameter_is_integer(self, tool: dict) -> None:
        props = tool["input_schema"]["properties"]
        assert props["tire_diameter"]["type"] == "integer"

    def test_linked_order_id_is_string(self, tool: dict) -> None:
        props = tool["input_schema"]["properties"]
        assert props["linked_order_id"]["type"] == "string"


class TestCancelFittingSchema:
    """Test cancel_fitting tool schema."""

    @pytest.fixture
    def tool(self) -> dict:
        return next(t for t in FITTING_TOOLS if t["name"] == "cancel_fitting")

    def test_required_fields(self, tool: dict) -> None:
        required = set(tool["input_schema"]["required"])
        assert required == {"booking_id", "action"}

    def test_action_enum(self, tool: dict) -> None:
        action = tool["input_schema"]["properties"]["action"]
        assert set(action["enum"]) == {"cancel", "reschedule"}

    def test_has_new_date_time(self, tool: dict) -> None:
        props = tool["input_schema"]["properties"]
        assert "new_date" in props
        assert "new_time" in props


class TestGetFittingPriceSchema:
    """Test get_fitting_price tool schema."""

    @pytest.fixture
    def tool(self) -> dict:
        return next(t for t in FITTING_TOOLS if t["name"] == "get_fitting_price")

    def test_tire_diameter_required(self, tool: dict) -> None:
        assert tool["input_schema"]["required"] == ["tire_diameter"]

    def test_tire_diameter_is_integer(self, tool: dict) -> None:
        props = tool["input_schema"]["properties"]
        assert props["tire_diameter"]["type"] == "integer"

    def test_has_optional_station_and_service(self, tool: dict) -> None:
        props = tool["input_schema"]["properties"]
        assert "station_id" in props
        assert "service_type" in props


class TestGetCustomerBookingsSchema:
    """Test get_customer_bookings tool schema."""

    @pytest.fixture
    def tool(self) -> dict:
        return next(t for t in FITTING_TOOLS if t["name"] == "get_customer_bookings")

    def test_phone_required(self, tool: dict) -> None:
        assert tool["input_schema"]["required"] == ["phone"]

    def test_has_optional_station_id(self, tool: dict) -> None:
        props = tool["input_schema"]["properties"]
        assert "station_id" in props

    def test_phone_is_string(self, tool: dict) -> None:
        props = tool["input_schema"]["properties"]
        assert props["phone"]["type"] == "string"

    def test_description_mentions_bookings(self, tool: dict) -> None:
        assert "записи" in tool["description"].lower() or "бронюванн" in tool["description"].lower()


class TestSearchKnowledgeBaseSchema:
    """Test search_knowledge_base tool schema."""

    @pytest.fixture
    def tool(self) -> dict:
        return next(t for t in FITTING_TOOLS if t["name"] == "search_knowledge_base")

    def test_query_required(self, tool: dict) -> None:
        assert tool["input_schema"]["required"] == ["query"]

    def test_has_category_filter(self, tool: dict) -> None:
        props = tool["input_schema"]["properties"]
        assert "category" in props
        categories = set(props["category"]["enum"])
        # Must include original 4 + 6 extended categories from categories.py
        assert {"brands", "guides", "faq", "comparisons"}.issubset(categories)
        assert {"policies", "procedures", "returns", "warranty", "delivery", "general"}.issubset(
            categories
        )

    def test_description_mentions_knowledge(self, tool: dict) -> None:
        assert "знань" in tool["description"].lower() or "знан" in tool["description"].lower()


class TestResolveDateHelper:
    """Test _resolve_date helper from main.py."""

    def test_empty_returns_empty(self) -> None:
        from src.main import _resolve_date

        assert _resolve_date("") == ""

    def test_today_returns_iso_date(self) -> None:
        from datetime import UTC, datetime

        from src.main import _resolve_date

        result = _resolve_date("today")
        assert result == datetime.now(tz=UTC).date().isoformat()

    def test_tomorrow_returns_next_day(self) -> None:
        from datetime import UTC, datetime, timedelta

        from src.main import _resolve_date

        result = _resolve_date("tomorrow")
        expected = (datetime.now(tz=UTC).date() + timedelta(days=1)).isoformat()
        assert result == expected

    def test_zavtra_returns_next_day(self) -> None:
        from datetime import UTC, datetime, timedelta

        from src.main import _resolve_date

        result = _resolve_date("завтра")
        expected = (datetime.now(tz=UTC).date() + timedelta(days=1)).isoformat()
        assert result == expected

    def test_iso_date_passthrough(self) -> None:
        from src.main import _resolve_date

        assert _resolve_date("2026-02-25") == "2026-02-25"

    def test_strips_whitespace(self) -> None:
        from src.main import _resolve_date

        assert _resolve_date("  2026-03-01  ") == "2026-03-01"
