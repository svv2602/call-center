"""Unit tests for Store API client."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.store_client.client import StoreAPIError, StoreClient


class TestStoreClientFormatting:
    """Test response formatting for LLM consumption."""

    def test_format_tire_results_limits_to_5(self) -> None:
        data = {
            "total": 10,
            "items": [{"id": str(i), "name": f"Tire {i}", "price": 1000 + i} for i in range(10)],
        }
        result = StoreClient._format_tire_results(data)
        assert len(result["items"]) == 5
        assert result["total"] == 10

    def test_format_tire_results_strips_fields(self) -> None:
        data = {
            "items": [
                {
                    "id": "1",
                    "name": "Michelin Primacy 4",
                    "brand": "Michelin",
                    "size": "205/55 R16",
                    "season": "summer",
                    "price": 3200,
                    "in_stock": True,
                    "image_url": "https://example.com/img.jpg",
                    "description": "Long description...",
                }
            ]
        }
        result = StoreClient._format_tire_results(data)
        item = result["items"][0]
        assert "image_url" not in item
        assert "description" not in item
        assert item["brand"] == "Michelin"
        assert item["price"] == 3200

    def test_format_empty_results(self) -> None:
        result = StoreClient._format_tire_results({"items": []})
        assert result["items"] == []
        assert result["total"] == 0


class TestStoreAPIError:
    """Test StoreAPIError."""

    def test_error_message(self) -> None:
        err = StoreAPIError(404, "Not found")
        assert err.status == 404
        assert "404" in str(err)
        assert "Not found" in str(err)

    def test_error_is_exception(self) -> None:
        err = StoreAPIError(500, "Internal error")
        assert isinstance(err, Exception)


class TestSearchTiresNetworkFiltering:
    """Test that search_tires passes network to _search_tires_db."""

    @pytest.mark.asyncio
    async def test_search_tires_passes_network_to_db(self) -> None:
        client = StoreClient("http://example.com", "key", db_engine=MagicMock())
        with patch.object(
            client,
            "_search_tires_db",
            new_callable=AsyncMock,
            return_value={"total": 0, "items": []},
        ) as mock_db:
            await client.search_tires(network="Tshina", width=205)
            mock_db.assert_called_once_with(network="Tshina", width=205)

    @pytest.mark.asyncio
    async def test_search_tires_db_defaults_to_prokoleso(self) -> None:
        """_search_tires_db defaults network to ProKoleso when empty."""
        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = []
        mock_conn.execute.return_value = mock_result

        mock_engine = MagicMock()
        mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)

        client = StoreClient("http://example.com", "key", db_engine=mock_engine)
        await client._search_tires_db(width=205)

        # Verify the bind params include network="ProKoleso"
        call_args = mock_conn.execute.call_args
        bind_params = call_args[0][1]
        assert bind_params["network"] == "ProKoleso"

    @pytest.mark.asyncio
    async def test_search_tires_db_uses_provided_network(self) -> None:
        """_search_tires_db uses the provided network for the JOIN filter."""
        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = []
        mock_conn.execute.return_value = mock_result

        mock_engine = MagicMock()
        mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)

        client = StoreClient("http://example.com", "key", db_engine=mock_engine)
        await client._search_tires_db(network="Tshina", width=205)

        call_args = mock_conn.execute.call_args
        sql_text = str(call_args[0][0])
        bind_params = call_args[0][1]
        assert "s.trading_network = :network" in sql_text
        assert bind_params["network"] == "Tshina"


class TestCheckAvailabilityNetworkFiltering:
    """Test that check_availability filters by network."""

    @pytest.mark.asyncio
    async def test_check_availability_passes_network(self) -> None:
        client = StoreClient("http://example.com", "key", db_engine=MagicMock())
        with patch.object(
            client,
            "_check_availability_1c",
            new_callable=AsyncMock,
            return_value={"available": True, "quantity": 4, "price": 2500},
        ) as mock_1c:
            await client.check_availability(product_id="SKU123", network="Tshina")
            mock_1c.assert_called_once_with("SKU123", "", network="Tshina")


class TestGetStockFromRedisNetworkFiltering:
    """Test that _get_stock_from_redis uses single network lookup."""

    @pytest.mark.asyncio
    async def test_single_network_lookup(self) -> None:
        """Should look up only the specified network key, not loop."""
        mock_redis = AsyncMock()
        stock_data = json.dumps({"stock": 4, "price": 2500, "country": "UA", "year_issue": "2025"})
        mock_redis.hget = AsyncMock(return_value=stock_data)

        client = StoreClient("http://example.com", "key", redis=mock_redis)
        result = await client._get_stock_from_redis("SKU123", network="Tshina")

        mock_redis.hget.assert_called_once_with("onec:stock:Tshina", "SKU123")
        assert result is not None
        assert result["available"] is True
        assert result["quantity"] == 4
        assert result["price"] == 2500

    @pytest.mark.asyncio
    async def test_defaults_to_prokoleso(self) -> None:
        """Should default to ProKoleso when no network specified."""
        mock_redis = AsyncMock()
        mock_redis.hget = AsyncMock(return_value=None)

        client = StoreClient("http://example.com", "key", redis=mock_redis)
        await client._get_stock_from_redis("SKU123")

        mock_redis.hget.assert_called_once_with("onec:stock:ProKoleso", "SKU123")

    @pytest.mark.asyncio
    async def test_returns_none_when_no_redis(self) -> None:
        """Should return None when Redis is not configured."""
        client = StoreClient("http://example.com", "key")
        result = await client._get_stock_from_redis("SKU123", network="Tshina")
        assert result is None
