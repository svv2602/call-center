"""Unit tests for Store Client fitting endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.store_client.client import StoreClient


@pytest.fixture
def client() -> StoreClient:
    return StoreClient(base_url="https://store.example.com", api_key="test-key")


class TestGetFittingStations:
    """Test get_fitting_stations method."""

    @pytest.mark.asyncio
    async def test_returns_stations(self, client: StoreClient) -> None:
        mock_response = {
            "data": [
                {
                    "id": "fs-001",
                    "name": "Шиномонтаж Позняки",
                    "city": "Київ",
                    "district": "Позняки",
                    "address": "вул. Здолбунівська, 7а",
                    "phone": "+380441234567",
                    "working_hours": "Пн-Сб 8:00-20:00",
                    "services": ["tire_change", "balancing"],
                },
                {
                    "id": "fs-002",
                    "name": "Шиномонтаж Подол",
                    "city": "Київ",
                    "district": "Подол",
                    "address": "вул. Сагайдачного, 15",
                    "phone": "+380441234568",
                    "working_hours": "Пн-Нд 9:00-21:00",
                    "services": ["tire_change", "balancing", "alignment"],
                },
            ],
        }
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=mock_response):
            result = await client.get_fitting_stations(city="Київ")
            assert result["total"] == 2
            assert len(result["stations"]) == 2
            assert result["stations"][0]["id"] == "fs-001"
            assert result["stations"][0]["name"] == "Шиномонтаж Позняки"
            assert result["stations"][0]["district"] == "Позняки"

    @pytest.mark.asyncio
    async def test_empty_stations(self, client: StoreClient) -> None:
        with patch.object(client, "_get", new_callable=AsyncMock, return_value={"data": []}):
            result = await client.get_fitting_stations(city="Невідоме")
            assert result["total"] == 0
            assert result["stations"] == []


class TestGetFittingSlots:
    """Test get_fitting_slots method."""

    @pytest.mark.asyncio
    async def test_returns_slots(self, client: StoreClient) -> None:
        mock_response = {
            "data": {
                "station_id": "fs-001",
                "slots": [
                    {
                        "date": "2026-03-15",
                        "times": [
                            {"time": "10:00", "available": True},
                            {"time": "11:00", "available": False},
                            {"time": "14:00", "available": True},
                        ],
                    },
                ],
            },
        }
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=mock_response):
            result = await client.get_fitting_slots(station_id="fs-001")
            assert result["station_id"] == "fs-001"
            assert len(result["slots"]) == 1
            assert result["slots"][0]["date"] == "2026-03-15"

    @pytest.mark.asyncio
    async def test_passes_query_params(self, client: StoreClient) -> None:
        with patch.object(
            client, "_get", new_callable=AsyncMock, return_value={"data": {"slots": []}}
        ) as mock_get:
            await client.get_fitting_slots(
                station_id="fs-001",
                date_from="2026-03-15",
                date_to="2026-03-20",
                service_type="tire_change",
            )
            call_args = mock_get.call_args
            params = call_args.kwargs.get("params") or call_args[1].get("params", {})
            assert params["date_from"] == "2026-03-15"
            assert params["date_to"] == "2026-03-20"
            assert params["service_type"] == "tire_change"


class TestBookFitting:
    """Test book_fitting method."""

    @pytest.mark.asyncio
    async def test_book_fitting_sends_idempotency_key(self, client: StoreClient) -> None:
        mock_response = {
            "data": {
                "id": "bk-001",
                "station": {"name": "Позняки", "address": "вул. Здолбунівська, 7а"},
                "date": "2026-03-15",
                "time": "14:00",
                "service_type": "tire_change",
                "estimated_duration_min": 45,
                "price": 800.00,
                "currency": "UAH",
                "sms_sent": True,
            },
        }
        with patch.object(client, "_post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            result = await client.book_fitting(
                station_id="fs-001",
                date="2026-03-15",
                time="14:00",
                customer_phone="+380501234567",
            )
            assert result["booking_id"] == "bk-001"
            assert result["price"] == 800.00
            assert result["sms_sent"] is True
            # Verify idempotency key
            call_kwargs = mock_post.call_args
            assert call_kwargs.kwargs.get("idempotency_key") is not None

    @pytest.mark.asyncio
    async def test_book_fitting_includes_source(self, client: StoreClient) -> None:
        with patch.object(
            client, "_post", new_callable=AsyncMock, return_value={"data": {"id": "bk-x"}}
        ) as mock_post:
            await client.book_fitting(
                station_id="fs-001",
                date="2026-03-15",
                time="14:00",
                customer_phone="+380501234567",
                vehicle_info="Toyota Camry 2020",
                linked_order_id="ord-123",
            )
            body = mock_post.call_args.kwargs.get("json_data")
            assert body["source"] == "ai_agent"
            assert body["vehicle_info"] == "Toyota Camry 2020"
            assert body["linked_order_id"] == "ord-123"


class TestCancelFitting:
    """Test cancel_fitting method."""

    @pytest.mark.asyncio
    async def test_cancel_booking(self, client: StoreClient) -> None:
        with patch.object(client, "_delete", new_callable=AsyncMock, return_value={}):
            result = await client.cancel_fitting(booking_id="bk-001", action="cancel")
            assert result["booking_id"] == "bk-001"
            assert result["action"] == "cancelled"

    @pytest.mark.asyncio
    async def test_reschedule_booking(self, client: StoreClient) -> None:
        mock_response = {
            "data": {
                "date": "2026-03-20",
                "time": "10:00",
            },
        }
        with patch.object(client, "_patch", new_callable=AsyncMock, return_value=mock_response):
            result = await client.cancel_fitting(
                booking_id="bk-001",
                action="reschedule",
                new_date="2026-03-20",
                new_time="10:00",
            )
            assert result["action"] == "rescheduled"
            assert result["new_date"] == "2026-03-20"
            assert result["new_time"] == "10:00"


class TestGetFittingPrice:
    """Test get_fitting_price method."""

    @pytest.mark.asyncio
    async def test_returns_prices(self, client: StoreClient) -> None:
        mock_response = {
            "data": [
                {
                    "service": "tire_change",
                    "label": "Заміна шин (4 шт.)",
                    "price": 800,
                    "currency": "UAH",
                },
                {
                    "service": "balancing",
                    "label": "Балансування (4 шт.)",
                    "price": 400,
                    "currency": "UAH",
                },
            ],
        }
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=mock_response):
            result = await client.get_fitting_price(tire_diameter=17)
            assert "prices" in result
            assert len(result["prices"]) == 2

    @pytest.mark.asyncio
    async def test_passes_params(self, client: StoreClient) -> None:
        with patch.object(
            client, "_get", new_callable=AsyncMock, return_value={"data": []}
        ) as mock_get:
            await client.get_fitting_price(
                tire_diameter=17,
                station_id="fs-001",
                service_type="tire_change",
            )
            call_args = mock_get.call_args
            params = call_args.kwargs.get("params") or call_args[1].get("params", {})
            assert params["tire_diameter"] == 17
            assert params["station_id"] == "fs-001"
            assert params["service_type"] == "tire_change"


class TestSearchKnowledgeBase:
    """Test search_knowledge_base method."""

    @pytest.mark.asyncio
    async def test_returns_articles(self, client: StoreClient) -> None:
        mock_response = {
            "data": [
                {
                    "title": "Michelin — преміум бренд шин",
                    "category": "brands",
                    "content": "Michelin — французький виробник шин...",
                    "relevance": 0.92,
                },
                {
                    "title": "Continental vs Michelin",
                    "category": "comparisons",
                    "content": "Порівняння двох преміум брендів...",
                    "relevance": 0.85,
                },
            ],
        }
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=mock_response):
            result = await client.search_knowledge_base(query="Michelin")
            assert result["total"] == 2
            assert len(result["articles"]) == 2
            assert result["articles"][0]["title"] == "Michelin — преміум бренд шин"

    @pytest.mark.asyncio
    async def test_limits_to_5_articles(self, client: StoreClient) -> None:
        mock_response = {
            "data": [{"title": f"Article {i}", "category": "faq", "content": "..."} for i in range(10)],
        }
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=mock_response):
            result = await client.search_knowledge_base(query="test")
            assert len(result["articles"]) <= 5

    @pytest.mark.asyncio
    async def test_empty_results(self, client: StoreClient) -> None:
        with patch.object(client, "_get", new_callable=AsyncMock, return_value={"data": []}):
            result = await client.search_knowledge_base(query="nonexistent")
            assert result["total"] == 0
            assert result["articles"] == []
