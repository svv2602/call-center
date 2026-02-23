"""Unit tests for 1C order creation (create_order_1c)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.onec_client.client import OneCClient


@pytest.fixture
def onec_client() -> OneCClient:
    return OneCClient(
        base_url="http://192.168.11.9",
        username="web_service",
        password="44332211",
        timeout=10,
    )


class TestCreateOrder1CBody:
    """Test order body mapping for 1C REST API."""

    @pytest.mark.asyncio
    async def test_basic_order_body(self, onec_client: OneCClient) -> None:
        with patch.object(
            onec_client, "_post", new_callable=AsyncMock, return_value={"success": True}
        ) as mock_post:
            await onec_client.create_order_1c(
                order_number="AI-42",
                items=[{"product_id": "SKU-001", "quantity": 4, "price": 3200}],
                customer_phone="0501234567",
                payment_method="cod",
                delivery_type="pickup",
                network="ProKoleso",
            )

            mock_post.assert_called_once()
            _, kwargs = mock_post.call_args
            body = kwargs["json_data"]

            assert body["order_number"] == "AI-42"
            assert body["store"] == "prokoleso"
            assert body["order_channel"] == "AI_AGENT"
            assert body["fizlico"] == "ФизЛицо"
            assert body["phone"] == "0501234567"
            assert body["payment_type"] == "1"  # cod → 1
            assert body["delivery_type"] == "Точки выдачи"  # pickup
            assert len(body["items"]) == 1
            assert body["items"][0]["sku"] == "SKU-001"
            assert body["items"][0]["quantity"] == 4

    @pytest.mark.asyncio
    async def test_online_payment(self, onec_client: OneCClient) -> None:
        with patch.object(
            onec_client, "_post", new_callable=AsyncMock, return_value={"success": True}
        ) as mock_post:
            await onec_client.create_order_1c(
                order_number="AI-1",
                items=[{"product_id": "SKU-001", "quantity": 2}],
                customer_phone="0501234567",
                payment_method="online",
            )
            body = mock_post.call_args[1]["json_data"]
            assert body["payment_type"] == "6"  # online → 6

    @pytest.mark.asyncio
    async def test_card_on_delivery_payment(self, onec_client: OneCClient) -> None:
        with patch.object(
            onec_client, "_post", new_callable=AsyncMock, return_value={"success": True}
        ) as mock_post:
            await onec_client.create_order_1c(
                order_number="AI-2",
                items=[{"product_id": "SKU-001", "quantity": 1}],
                customer_phone="0501234567",
                payment_method="card_on_delivery",
            )
            body = mock_post.call_args[1]["json_data"]
            assert body["payment_type"] == "4"  # card_on_delivery → 4

    @pytest.mark.asyncio
    async def test_delivery_type_novapost(self, onec_client: OneCClient) -> None:
        with patch.object(
            onec_client, "_post", new_callable=AsyncMock, return_value={"success": True}
        ) as mock_post:
            await onec_client.create_order_1c(
                order_number="AI-3",
                items=[{"product_id": "SKU-001", "quantity": 4}],
                customer_phone="0501234567",
                delivery_type="delivery",
                delivery_city="Київ",
                delivery_address="вул. Хрещатик, 1",
            )
            body = mock_post.call_args[1]["json_data"]
            assert body["delivery_type"] == "NovaPost"
            assert body["delivery_city"] == "Київ"
            assert body["delivery_address"] == "вул. Хрещатик, 1"

    @pytest.mark.asyncio
    async def test_pickup_with_point_id(self, onec_client: OneCClient) -> None:
        with patch.object(
            onec_client, "_post", new_callable=AsyncMock, return_value={"success": True}
        ) as mock_post:
            await onec_client.create_order_1c(
                order_number="AI-4",
                items=[{"product_id": "SKU-001", "quantity": 4}],
                customer_phone="0501234567",
                delivery_type="pickup",
                pickup_point_id="000000054",
            )
            body = mock_post.call_args[1]["json_data"]
            assert body["delivery_type"] == "Точки выдачи"
            assert body["pickup_point_id"] == "000000054"

    @pytest.mark.asyncio
    async def test_tshina_network(self, onec_client: OneCClient) -> None:
        with patch.object(
            onec_client, "_post", new_callable=AsyncMock, return_value={"success": True}
        ) as mock_post:
            await onec_client.create_order_1c(
                order_number="AI-5",
                items=[{"product_id": "SKU-001", "quantity": 2}],
                customer_phone="0501234567",
                network="Tshina",
            )
            body = mock_post.call_args[1]["json_data"]
            assert body["store"] == "tshina"

    @pytest.mark.asyncio
    async def test_customer_name_included(self, onec_client: OneCClient) -> None:
        with patch.object(
            onec_client, "_post", new_callable=AsyncMock, return_value={"success": True}
        ) as mock_post:
            await onec_client.create_order_1c(
                order_number="AI-6",
                items=[{"product_id": "SKU-001", "quantity": 1}],
                customer_phone="0501234567",
                customer_name="Іван Петренко",
            )
            body = mock_post.call_args[1]["json_data"]
            assert body["person"] == "Іван Петренко"

    @pytest.mark.asyncio
    async def test_multiple_items(self, onec_client: OneCClient) -> None:
        with patch.object(
            onec_client, "_post", new_callable=AsyncMock, return_value={"success": True}
        ) as mock_post:
            await onec_client.create_order_1c(
                order_number="AI-7",
                items=[
                    {"product_id": "SKU-001", "quantity": 4, "price": 3200},
                    {"product_id": "SKU-002", "quantity": 1, "price": 600},
                ],
                customer_phone="0501234567",
            )
            body = mock_post.call_args[1]["json_data"]
            assert len(body["items"]) == 2
            assert body["items"][0]["sku"] == "SKU-001"
            assert body["items"][1]["sku"] == "SKU-002"
            assert body["items"][1]["price"] == 600


class TestCreateOrder1CEndpoint:
    """Test that create_order_1c calls the correct endpoint."""

    @pytest.mark.asyncio
    async def test_calls_post_zakaz(self, onec_client: OneCClient) -> None:
        with patch.object(
            onec_client, "_post", new_callable=AsyncMock, return_value={"success": True}
        ) as mock_post:
            await onec_client.create_order_1c(
                order_number="AI-1",
                items=[{"product_id": "SKU-001", "quantity": 1}],
                customer_phone="0501234567",
            )
            args, kwargs = mock_post.call_args
            assert args[0] == "/Trade/hs/site/zakaz/"


class TestPostHelper:
    """Test _post() helper method."""

    @pytest.mark.asyncio
    async def test_post_calls_request_with_json(self, onec_client: OneCClient) -> None:
        with patch.object(
            onec_client, "_request", new_callable=AsyncMock, return_value={"ok": True}
        ) as mock_request:
            result = await onec_client._post("/test/path", json_data={"key": "val"})
            mock_request.assert_called_once_with(
                "POST", "/test/path", json_data={"key": "val"}
            )
            assert result == {"ok": True}


class TestOrderNumberFormat:
    """Test AI order number format."""

    def test_order_number_format(self) -> None:
        """AI order numbers follow the AI-{seq} format."""
        assert "AI-42".startswith("AI-")
        assert "AI-42"[3:].isdigit()


class TestDefaultPaymentMapping:
    """Test unknown payment method defaults to '1' (cod)."""

    @pytest.mark.asyncio
    async def test_unknown_payment_defaults_to_cod(self, onec_client: OneCClient) -> None:
        with patch.object(
            onec_client, "_post", new_callable=AsyncMock, return_value={"success": True}
        ) as mock_post:
            await onec_client.create_order_1c(
                order_number="AI-99",
                items=[{"product_id": "SKU-001", "quantity": 1}],
                customer_phone="0501234567",
                payment_method="unknown_method",
            )
            body = mock_post.call_args[1]["json_data"]
            assert body["payment_type"] == "1"  # defaults to cod
