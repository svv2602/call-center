"""Unit tests for complex scenarios: fitting booking flow, expert consultation, full chain."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.agent.agent import ToolRouter


@pytest.fixture
def router() -> ToolRouter:
    """Create a ToolRouter with mocked fitting tool handlers."""
    r = ToolRouter()

    # Mock fitting handlers
    r.register("get_fitting_stations", AsyncMock(return_value={
        "total": 2,
        "stations": [
            {"id": "fs-001", "name": "Позняки", "address": "вул. Здолбунівська, 7а"},
            {"id": "fs-002", "name": "Подол", "address": "вул. Сагайдачного, 15"},
        ],
    }))
    r.register("get_fitting_slots", AsyncMock(return_value={
        "station_id": "fs-001",
        "slots": [{"date": "2026-03-15", "times": [
            {"time": "10:00", "available": True},
            {"time": "14:00", "available": True},
        ]}],
    }))
    r.register("book_fitting", AsyncMock(return_value={
        "booking_id": "bk-001",
        "station_name": "Позняки",
        "station_address": "вул. Здолбунівська, 7а",
        "date": "2026-03-15",
        "time": "14:00",
        "price": 800.00,
        "sms_sent": True,
    }))
    r.register("cancel_fitting", AsyncMock(return_value={
        "booking_id": "bk-001",
        "action": "cancelled",
    }))
    r.register("get_fitting_price", AsyncMock(return_value={
        "prices": [{"service": "tire_change", "price": 800}],
    }))
    r.register("search_knowledge_base", AsyncMock(return_value={
        "total": 2,
        "articles": [
            {"title": "Michelin vs Continental", "content": "Comparison..."},
            {"title": "Winter tire guide", "content": "Guide..."},
        ],
    }))

    # Mock existing tools
    r.register("search_tires", AsyncMock(return_value={
        "total": 2,
        "items": [
            {"id": "tire-1", "name": "Michelin 205/55 R16", "price": 3200},
            {"id": "tire-2", "name": "Continental 205/55 R16", "price": 2800},
        ],
    }))
    r.register("create_order_draft", AsyncMock(return_value={
        "order_id": "ord-001",
        "order_number": "ORD-100",
        "status": "draft",
        "total": 12800,
    }))
    r.register("confirm_order", AsyncMock(return_value={
        "order_id": "ord-001",
        "status": "confirmed",
        "sms_sent": True,
    }))

    return r


class TestFittingBookingFlow:
    """Test the full fitting booking flow: stations → slots → book."""

    @pytest.mark.asyncio
    async def test_full_booking_flow(self, router: ToolRouter) -> None:
        # Step 1: Get stations
        stations = await router.execute("get_fitting_stations", {"city": "Київ"})
        assert stations["total"] == 2
        station_id = stations["stations"][0]["id"]

        # Step 2: Get slots
        slots = await router.execute("get_fitting_slots", {"station_id": station_id})
        assert len(slots["slots"]) > 0

        # Step 3: Book
        booking = await router.execute("book_fitting", {
            "station_id": station_id,
            "date": "2026-03-15",
            "time": "14:00",
            "customer_phone": "+380501234567",
        })
        assert booking["booking_id"] == "bk-001"
        assert booking["sms_sent"] is True

    @pytest.mark.asyncio
    async def test_cancel_after_booking(self, router: ToolRouter) -> None:
        # Book first
        booking = await router.execute("book_fitting", {
            "station_id": "fs-001",
            "date": "2026-03-15",
            "time": "14:00",
            "customer_phone": "+380501234567",
        })
        # Cancel
        cancel = await router.execute("cancel_fitting", {
            "booking_id": booking["booking_id"],
            "action": "cancel",
        })
        assert cancel["action"] == "cancelled"

    @pytest.mark.asyncio
    async def test_price_inquiry(self, router: ToolRouter) -> None:
        prices = await router.execute("get_fitting_price", {"tire_diameter": 17})
        assert len(prices["prices"]) > 0


class TestExpertConsultation:
    """Test expert consultation using knowledge base."""

    @pytest.mark.asyncio
    async def test_knowledge_base_search(self, router: ToolRouter) -> None:
        result = await router.execute("search_knowledge_base", {
            "query": "Що краще Michelin чи Continental?",
        })
        assert result["total"] == 2
        assert len(result["articles"]) == 2

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, router: ToolRouter) -> None:
        result = await router.execute("nonexistent_tool", {})
        assert "error" in result


class TestFullChainScenario:
    """Test the full chain: search → order → fitting."""

    @pytest.mark.asyncio
    async def test_search_order_fitting_chain(self, router: ToolRouter) -> None:
        # Step 1: Search tires
        tires = await router.execute("search_tires", {
            "width": 205, "profile": 55, "diameter": 16, "season": "winter",
        })
        assert tires["total"] > 0
        tire_id = tires["items"][0]["id"]

        # Step 2: Create order
        order = await router.execute("create_order_draft", {
            "items": [{"product_id": tire_id, "quantity": 4}],
            "customer_phone": "+380501234567",
        })
        assert order["order_id"] == "ord-001"

        # Step 3: Confirm order
        confirmed = await router.execute("confirm_order", {
            "order_id": order["order_id"],
            "payment_method": "cod",
        })
        assert confirmed["status"] == "confirmed"

        # Step 4: Book fitting with linked order
        booking = await router.execute("book_fitting", {
            "station_id": "fs-001",
            "date": "2026-03-15",
            "time": "14:00",
            "customer_phone": "+380501234567",
            "linked_order_id": order["order_id"],
        })
        assert booking["booking_id"] is not None
        assert booking["sms_sent"] is True

        # Verify linked_order_id was passed
        book_handler = router._handlers["book_fitting"]
        call_kwargs = book_handler.call_args.kwargs
        assert call_kwargs.get("linked_order_id") == "ord-001"

    @pytest.mark.asyncio
    async def test_customer_declines_fitting(self, router: ToolRouter) -> None:
        """Test that the chain works even if customer declines fitting."""
        # Search and order
        tires = await router.execute("search_tires", {"diameter": 16})
        order = await router.execute("create_order_draft", {
            "items": [{"product_id": "tire-1", "quantity": 4}],
            "customer_phone": "+380501234567",
        })
        confirmed = await router.execute("confirm_order", {
            "order_id": order["order_id"],
            "payment_method": "cod",
        })
        assert confirmed["status"] == "confirmed"
        # Customer says "no" to fitting — no book_fitting call needed
        # Just verify order is confirmed without fitting
        fitting_handler = router._handlers["book_fitting"]
        # book_fitting was only called by previous test (if any)
        # The point: chain doesn't require fitting to complete
