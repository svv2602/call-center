"""Unit tests for Store Client order endpoints."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.store_client.client import StoreClient


@pytest.fixture
def client() -> StoreClient:
    return StoreClient(base_url="https://store.example.com", api_key="test-key")


class TestSearchOrders:
    """Test search_orders method."""

    @pytest.mark.asyncio
    async def test_search_by_phone_returns_orders(self, client: StoreClient) -> None:
        mock_response = {
            "total": 2,
            "items": [
                {
                    "id": "order-1",
                    "order_number": "ORD-001",
                    "status": "confirmed",
                    "total": 12800,
                },
                {
                    "id": "order-2",
                    "order_number": "ORD-002",
                    "status": "delivered",
                    "total": 6400,
                },
            ],
        }
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=mock_response):
            result = await client.search_orders(phone="+380501234567")
            assert result["found"] is True
            assert result["total"] == 2
            assert len(result["orders"]) == 2

    @pytest.mark.asyncio
    async def test_search_by_phone_no_results(self, client: StoreClient) -> None:
        with patch.object(client, "_get", new_callable=AsyncMock, return_value={"items": []}):
            result = await client.search_orders(phone="+380501234567")
            assert result["found"] is False

    @pytest.mark.asyncio
    async def test_search_by_order_id(self, client: StoreClient) -> None:
        mock_order = {
            "id": "order-1",
            "order_number": "ORD-001",
            "status": "confirmed",
            "total": 12800,
        }
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=mock_order):
            result = await client.search_orders(order_id="order-1")
            assert result["found"] is True
            assert len(result["orders"]) == 1

    @pytest.mark.asyncio
    async def test_search_no_params(self, client: StoreClient) -> None:
        result = await client.search_orders()
        assert result["found"] is False


class TestCreateOrder:
    """Test create_order method."""

    @pytest.mark.asyncio
    async def test_create_order_sends_idempotency_key(self, client: StoreClient) -> None:
        mock_response = {
            "id": "order-new",
            "order_number": "ORD-100",
            "status": "draft",
            "items": [{"product_id": "tire-1", "quantity": 4}],
            "subtotal": 12800,
            "total": 12800,
        }
        with patch.object(
            client, "_post", new_callable=AsyncMock, return_value=mock_response
        ) as mock_post:
            result = await client.create_order(
                items=[{"product_id": "tire-1", "quantity": 4}],
                customer_phone="+380501234567",
            )
            assert result["order_id"] == "order-new"
            assert result["status"] == "draft"
            # Verify idempotency key was passed
            call_kwargs = mock_post.call_args
            assert call_kwargs.kwargs.get("idempotency_key") is not None

    @pytest.mark.asyncio
    async def test_create_order_includes_source(self, client: StoreClient) -> None:
        with patch.object(
            client, "_post", new_callable=AsyncMock, return_value={"id": "x"}
        ) as mock_post:
            await client.create_order(
                items=[{"product_id": "tire-1", "quantity": 4}],
                customer_phone="+380501234567",
                call_id="call-123",
            )
            body = mock_post.call_args.kwargs.get("json_data") or mock_post.call_args[1].get(
                "json_data"
            )
            assert body["source"] == "ai_agent"
            assert body["call_id"] == "call-123"


class TestUpdateDelivery:
    """Test update_delivery method."""

    @pytest.mark.asyncio
    async def test_update_delivery_delivery_type(self, client: StoreClient) -> None:
        mock_response = {
            "id": "order-1",
            "delivery_type": "delivery",
            "delivery_cost": 150,
            "estimated_days": 2,
            "total": 12950,
        }
        with patch.object(client, "_patch", new_callable=AsyncMock, return_value=mock_response):
            result = await client.update_delivery(
                order_id="order-1",
                delivery_type="delivery",
                city="Київ",
                address="вул. Хрещатик 1",
            )
            assert result["delivery_type"] == "delivery"
            assert result["delivery_cost"] == 150

    @pytest.mark.asyncio
    async def test_update_delivery_pickup(self, client: StoreClient) -> None:
        mock_response = {
            "id": "order-1",
            "delivery_type": "pickup",
            "delivery_cost": 0,
            "total": 12800,
        }
        with patch.object(client, "_patch", new_callable=AsyncMock, return_value=mock_response):
            result = await client.update_delivery(
                order_id="order-1",
                delivery_type="pickup",
                pickup_point_id="pp-1",
            )
            assert result["delivery_type"] == "pickup"


class TestConfirmOrder:
    """Test confirm_order method."""

    @pytest.mark.asyncio
    async def test_confirm_order_with_idempotency(self, client: StoreClient) -> None:
        mock_response = {
            "id": "order-1",
            "order_number": "ORD-001",
            "status": "confirmed",
            "estimated_delivery": "2026-02-18",
            "sms_sent": True,
            "total": 12950,
        }
        with patch.object(
            client, "_post", new_callable=AsyncMock, return_value=mock_response
        ) as mock_post:
            result = await client.confirm_order(
                order_id="order-1",
                payment_method="cod",
            )
            assert result["status"] == "confirmed"
            assert result["sms_sent"] is True
            assert result["order_number"] == "ORD-001"
            # Verify idempotency key
            call_kwargs = mock_post.call_args
            assert call_kwargs.kwargs.get("idempotency_key") is not None


class TestFormatOrder:
    """Test order formatting helper."""

    def test_format_order_extracts_key_fields(self) -> None:
        data: dict[str, Any] = {
            "id": "order-1",
            "order_number": "ORD-001",
            "status": "confirmed",
            "status_label": "Підтверджено",
            "items_summary": "4x Michelin 205/55 R16",
            "total": 12800,
            "estimated_delivery": "2026-02-18",
            "extra_field": "should be ignored",
        }
        result = StoreClient._format_order(data)
        assert result["id"] == "order-1"
        assert result["order_number"] == "ORD-001"
        assert result["status"] == "confirmed"
        assert "extra_field" not in result


class TestGetPickupPoints:
    """Test get_pickup_points method."""

    @pytest.mark.asyncio
    async def test_returns_formatted_points(self, client: StoreClient) -> None:
        mock_response = {
            "total": 2,
            "items": [
                {
                    "id": "pp-1",
                    "name": "Магазин центр",
                    "address": "вул. Центральна 1",
                    "city": "Київ",
                },
                {"id": "pp-2", "name": "Магазин схід", "address": "вул. Східна 5", "city": "Київ"},
            ],
        }
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=mock_response):
            result = await client.get_pickup_points(city="Київ")
            assert result["total"] == 2
            assert len(result["points"]) == 2
            assert result["points"][0]["id"] == "pp-1"
