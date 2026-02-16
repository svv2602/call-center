"""Unit tests for StoreClient 1C integration (search_tires via DB, check_availability via Redis/DB)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.store_client.client import StoreClient


@pytest.fixture
def store_client_legacy() -> StoreClient:
    """StoreClient without 1C integration (backward compat)."""
    return StoreClient(base_url="http://localhost:3000/api/v1", api_key="test-key")


@pytest.fixture
def mock_db_engine() -> MagicMock:
    """Mock AsyncEngine with proper context managers."""
    engine = MagicMock()
    mock_conn = AsyncMock()

    # For connect() context manager
    conn_ctx = AsyncMock()
    conn_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    conn_ctx.__aexit__ = AsyncMock(return_value=False)
    engine.connect.return_value = conn_ctx

    return engine


@pytest.fixture
def mock_redis() -> AsyncMock:
    redis = AsyncMock()
    redis.hget = AsyncMock(return_value=None)
    return redis


@pytest.fixture
def store_client_1c(mock_db_engine: MagicMock, mock_redis: AsyncMock) -> StoreClient:
    """StoreClient with 1C integration."""
    return StoreClient(
        base_url="http://localhost:3000/api/v1",
        api_key="test-key",
        db_engine=mock_db_engine,
        redis=mock_redis,
        stock_cache_ttl=300,
    )


class TestBackwardCompat:
    """Test that StoreClient without 1C deps works as before."""

    def test_no_db_engine(self, store_client_legacy: StoreClient) -> None:
        assert store_client_legacy._db_engine is None

    def test_no_redis(self, store_client_legacy: StoreClient) -> None:
        assert store_client_legacy._redis is None

    @pytest.mark.asyncio
    async def test_search_tires_uses_http(self, store_client_legacy: StoreClient) -> None:
        mock_data = {
            "items": [
                {
                    "id": "1",
                    "name": "Test",
                    "brand": "X",
                    "size": "205/55R16",
                    "season": "summer",
                    "price": 3000,
                    "in_stock": True,
                }
            ],
            "total": 1,
        }
        with patch.object(
            store_client_legacy, "_get", new_callable=AsyncMock, return_value=mock_data
        ):
            result = await store_client_legacy.search_tires(width=205, profile=55, diameter=16)
            assert result["items"][0]["brand"] == "X"

    @pytest.mark.asyncio
    async def test_check_availability_uses_http(self, store_client_legacy: StoreClient) -> None:
        mock_data = {"in_stock": True, "quantity": 10, "price": 3000, "delivery_days": 1}
        with patch.object(
            store_client_legacy, "_get", new_callable=AsyncMock, return_value=mock_data
        ):
            result = await store_client_legacy.check_availability(product_id="tire-001")
            assert result["available"] is True
            assert result["quantity"] == 10


class TestSearchTiresDB:
    """Test search_tires with PostgreSQL backend."""

    @pytest.mark.asyncio
    async def test_vehicle_search_returns_message(self, store_client_1c: StoreClient) -> None:
        result = await store_client_1c.search_tires(
            vehicle_make="Toyota", vehicle_model="Camry", vehicle_year="2020"
        )
        assert result["total"] == 0
        assert "недоступний" in result["message"]

    @pytest.mark.asyncio
    async def test_search_by_size(
        self, store_client_1c: StoreClient, mock_db_engine: MagicMock
    ) -> None:
        # Set up mock result
        mock_row = {
            "id": "00000019835",
            "brand": "Tigar",
            "model": "Winter1",
            "size": "155/70R13",
            "season": "winter",
            "price": 1850,
            "stock_quantity": 24,
        }
        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = [mock_row]

        conn_ctx = mock_db_engine.connect.return_value
        mock_conn = conn_ctx.__aenter__.return_value
        mock_conn.execute = AsyncMock(return_value=mock_result)

        result = await store_client_1c.search_tires(width=155, profile=70, diameter=13)

        assert result["total"] == 1
        assert result["items"][0]["id"] == "00000019835"
        assert result["items"][0]["brand"] == "Tigar"
        assert result["items"][0]["in_stock"] is True

    @pytest.mark.asyncio
    async def test_search_empty_result(
        self, store_client_1c: StoreClient, mock_db_engine: MagicMock
    ) -> None:
        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = []

        conn_ctx = mock_db_engine.connect.return_value
        mock_conn = conn_ctx.__aenter__.return_value
        mock_conn.execute = AsyncMock(return_value=mock_result)

        result = await store_client_1c.search_tires(width=999, profile=99, diameter=99)

        assert result["total"] == 0
        assert result["items"] == []


class TestCheckAvailability1C:
    """Test check_availability with Redis/DB backend."""

    @pytest.mark.asyncio
    async def test_redis_cache_hit(
        self, store_client_1c: StoreClient, mock_redis: AsyncMock
    ) -> None:
        stock_data = json.dumps(
            {"price": 1850, "stock": 24, "country": "Сербія", "year_issue": "25-24"}
        )
        mock_redis.hget = AsyncMock(return_value=stock_data.encode())

        result = await store_client_1c.check_availability(product_id="00000019835")

        assert result["available"] is True
        assert result["quantity"] == 24
        assert result["price"] == 1850
        mock_redis.hget.assert_called()

    @pytest.mark.asyncio
    async def test_redis_miss_db_fallback(
        self, store_client_1c: StoreClient, mock_redis: AsyncMock, mock_db_engine: MagicMock
    ) -> None:
        mock_redis.hget = AsyncMock(return_value=None)

        mock_row = {
            "price": 1850,
            "stock_quantity": 24,
            "country": "Сербія",
            "year_issue": "25-24",
            "trading_network": "ProKoleso",
        }
        mock_result = MagicMock()
        mock_result.mappings.return_value.first.return_value = mock_row

        conn_ctx = mock_db_engine.connect.return_value
        mock_conn = conn_ctx.__aenter__.return_value
        mock_conn.execute = AsyncMock(return_value=mock_result)

        result = await store_client_1c.check_availability(product_id="00000019835")

        assert result["available"] is True
        assert result["quantity"] == 24

    @pytest.mark.asyncio
    async def test_not_found_in_db(
        self, store_client_1c: StoreClient, mock_redis: AsyncMock, mock_db_engine: MagicMock
    ) -> None:
        mock_redis.hget = AsyncMock(return_value=None)

        mock_result = MagicMock()
        mock_result.mappings.return_value.first.return_value = None

        conn_ctx = mock_db_engine.connect.return_value
        mock_conn = conn_ctx.__aenter__.return_value
        mock_conn.execute = AsyncMock(return_value=mock_result)

        result = await store_client_1c.check_availability(product_id="nonexistent")

        assert result["available"] is False
        assert "відсутні" in result["message"]

    @pytest.mark.asyncio
    async def test_no_product_id_returns_error(self, store_client_1c: StoreClient) -> None:
        result = await store_client_1c.check_availability()
        assert result["available"] is False

    @pytest.mark.asyncio
    async def test_out_of_stock(self, store_client_1c: StoreClient, mock_redis: AsyncMock) -> None:
        stock_data = json.dumps(
            {"price": 1850, "stock": 0, "country": "Сербія", "year_issue": "25-24"}
        )
        mock_redis.hget = AsyncMock(return_value=stock_data.encode())

        result = await store_client_1c.check_availability(product_id="00000019835")

        assert result["available"] is False
        assert result["quantity"] == 0
