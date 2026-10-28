"""Unit tests for sandbox mock tools."""

from __future__ import annotations

import pytest

from src.agent import delivery_quote as dq
from src.sandbox.mock_tools import (
    MOCK_DELIVERY_COST,
    MOCK_RESPONSES,
    DeliveryCostMock,
    build_mock_tool_router,
)


class TestMockResponses:
    """Test mock response data completeness."""

    def test_all_canonical_tools_have_mocks(self) -> None:
        """All 18 canonical tools should have mock data."""
        expected_tools = {
            "get_vehicle_tire_sizes",
            "search_tires",
            "check_availability",
            "transfer_to_operator",
            "get_order_status",
            "create_order_draft",
            "update_order_delivery",
            "confirm_order",
            "submit_order_request",
            "get_pickup_points",
            "get_fitting_stations",
            "get_fitting_slots",
            "book_fitting",
            "cancel_fitting",
            "get_fitting_price",
            "get_customer_bookings",
            "find_storage",
            "search_knowledge_base",
            "update_customer_profile",
            "get_delivery_cost",
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
        result = await router.execute("search_knowledge_base", {"query": "Michelin зимові шини"})
        assert "results" in result
        assert len(result["results"]) >= 1
        assert "Michelin" in result["results"][0]["title"]

    @pytest.mark.asyncio
    async def test_knowledge_base_generic_query(self) -> None:
        """Generic query returns fallback comparison data."""
        router = build_mock_tool_router()
        result = await router.execute("search_knowledge_base", {"query": "які шини обрати"})
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


class TestDeliveryCostMock:
    """The sandbox get_delivery_cost keeps the live decision order (main.py)."""

    @staticmethod
    async def _offered(mock: DeliveryCostMock) -> None:
        await mock.observe("search_tires", {}, MOCK_RESPONSES["search_tires"], 0, True)

    @pytest.mark.asyncio
    async def test_default_router_answers_the_tariff_without_a_sum(self) -> None:
        router = build_mock_tool_router()
        out = await router.execute(
            "get_delivery_cost",
            {"city": "Одеса", "items": [{"product_id": "tire-001", "quantity": 4}]},
        )
        assert out["status"] == dq.STATUS_NEED_MORE
        assert out["phrase"].startswith(dq.tariff_phrase(None))

    @pytest.mark.asyncio
    async def test_missing_city_is_need_more(self) -> None:
        mock = DeliveryCostMock()
        mock.caller_lines.append("доставка до Одеси, а може до Києва")
        await self._offered(mock)
        out = await mock(city=" ", items=[{"product_id": "tire-001", "quantity": 4}])
        assert out == {
            "status": dq.STATUS_NEED_MORE,
            "phrase": f"{dq.tariff_phrase(None)} {dq.NEED_MORE_HINT}",
            "missing": "city",
        }

    @pytest.mark.asyncio
    async def test_quote_is_the_owner_wording(self) -> None:
        mock = DeliveryCostMock()
        mock.caller_lines.append("доставка до Одеси, а може до Києва")
        await self._offered(mock)
        out = await mock(city="Одеса", items=[{"product_id": "tire-002", "quantity": 4}])
        assert out["status"] == dq.STATUS_QUOTED
        assert out["cost"] == MOCK_DELIVERY_COST
        assert out["phrase"].endswith(dq.COD_FEE_PHRASE)
        assert "орієнтовно 810 грн" in out["phrase"]
        # Only `courier is True` asks the door price: a truthy string does not.
        door = await mock(
            city="Одеса", items=[{"product_id": "tire-002", "quantity": 4}], courier="так"
        )
        assert "кур'єр" not in door["phrase"]

    @pytest.mark.asyncio
    async def test_brand_promotion_on_some_items_goes_to_the_manager(self) -> None:
        from datetime import date

        from src.agent.promotions import ActivePromotion, promo_overrides

        promo = ActivePromotion(
            title="Безкоштовна доставка Michelin",
            bot_text="Доставка шин Michelin — безкоштовна.",
            valid_to=date(2099, 12, 31),
            overrides={"free_delivery": True},
            mention_brands=("michelin",),
        )
        mock = DeliveryCostMock(None, promo_overrides([promo]))
        mock.caller_lines.append("доставка до Одеси, а може до Києва")
        await self._offered(mock)
        only = await mock(city="Київ", items=[{"product_id": "tire-001", "quantity": 4}])
        assert only == {"status": dq.STATUS_PROMO_FREE, "phrase": promo.bot_text}
        mixed = await mock(
            city="Київ",
            items=[
                {"product_id": "tire-001", "quantity": 2},
                {"product_id": "tire-002", "quantity": 2},
            ],
        )
        assert mixed == {"status": dq.STATUS_PROMO_MIXED, "phrase": dq.PROMO_MIXED_PHRASE}


@pytest.mark.asyncio
async def test_delivery_mock_answers_a_promotion_before_a_tyre_is_chosen() -> None:
    """As live: nothing chosen yet, a promotion covers the caller's line → its text."""
    from datetime import date

    from src.agent import delivery_quote as dq
    from src.agent.network_policy import NetworkPolicy
    from src.agent.promotions import ActivePromotion, promo_overrides
    from src.sandbox.mock_tools import DeliveryCostMock

    promo = ActivePromotion(
        title="free",
        bot_text="Безкоштовна доставка на шини Michelin.",
        valid_to=date(2099, 1, 1),
        overrides={"free_delivery": True},
        mention_brands=("Michelin",),
    )
    policy = NetworkPolicy.from_tenant_config({"sales_enabled": True, "network_policy": {}})
    mock = DeliveryCostMock(policy, promo_overrides([promo]))
    mock.caller_lines.append("скільки коштує доставка шин Michelin?")
    out = await mock()
    assert out == {"status": dq.STATUS_PROMO_FREE, "phrase": "Безкоштовна доставка на шини Michelin."}
