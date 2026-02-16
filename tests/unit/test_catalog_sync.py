"""Unit tests for CatalogSyncService."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.onec_client.sync import (
    SEASON_MAP,
    CatalogSyncService,
    _normalize_season,
    _safe_bool,
    _safe_int,
)


class TestNormalization:
    """Test data normalization helpers."""

    def test_normalize_season_winter(self) -> None:
        assert _normalize_season("Зимняя") == "winter"

    def test_normalize_season_summer(self) -> None:
        assert _normalize_season("Летняя") == "summer"

    def test_normalize_season_all_season(self) -> None:
        assert _normalize_season("Всесезонная") == "all_season"

    def test_normalize_season_case_insensitive(self) -> None:
        assert _normalize_season("зимняя") == "winter"
        assert _normalize_season("ЛЕТНЯЯ") == "summer"

    def test_normalize_season_unknown_passthrough(self) -> None:
        assert _normalize_season("Custom") == "Custom"

    def test_normalize_season_with_whitespace(self) -> None:
        assert _normalize_season(" Зимняя ") == "winter"

    def test_safe_int_string(self) -> None:
        assert _safe_int("13") == 13
        assert _safe_int("205") == 205

    def test_safe_int_empty(self) -> None:
        assert _safe_int("") == 0
        assert _safe_int(None) == 0

    def test_safe_int_already_int(self) -> None:
        assert _safe_int(16) == 16

    def test_safe_int_invalid(self) -> None:
        assert _safe_int("abc") == 0

    def test_safe_int_default(self) -> None:
        assert _safe_int("", default=-1) == -1

    def test_safe_bool_empty_string(self) -> None:
        assert _safe_bool("") is False

    def test_safe_bool_none(self) -> None:
        assert _safe_bool(None) is False

    def test_safe_bool_true_values(self) -> None:
        assert _safe_bool("1") is True
        assert _safe_bool("true") is True
        assert _safe_bool("да") is True

    def test_safe_bool_already_bool(self) -> None:
        assert _safe_bool(True) is True
        assert _safe_bool(False) is False


class TestCatalogSyncService:
    """Test sync service logic."""

    @pytest.fixture
    def mock_onec(self) -> AsyncMock:
        client = AsyncMock()
        client.get_wares_full.return_value = {
            "success": True,
            "data": [
                {
                    "type": "000000001",
                    "model_id": "000167134",
                    "model": "Winter1",
                    "manufacturer_id": "000022523",
                    "manufacturer": "Tigar",
                    "seasonality": "Зимняя",
                    "tread_pattern_type": "Направленный",
                    "product": [
                        {
                            "sku": "00000019835",
                            "text": "155/70 R13 Tigar Winter1 [75T]",
                            "diametr": "13",
                            "size": "155/70R13",
                            "profile_height": "70",
                            "profile_width": "155",
                            "speed_rating": "T",
                            "load_rating": "75",
                            "studded": "",
                        },
                    ],
                },
            ],
            "errors": [],
        }
        client.get_stock.return_value = {
            "success": True,
            "TradingNetwork": "ProKoleso",
            "data": [
                {
                    "sku": "00000019835",
                    "price": "1850",
                    "stock": "24",
                    "foreign_product": "1",
                    "price_tshina": "1850",
                    "year_issue": "25-24",
                    "country": "Сербія",
                },
            ],
        }
        client.get_wares_incremental.return_value = {"success": True, "data": [], "errors": []}
        client.confirm_wares_receipt.return_value = {"success": True}
        return client

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Create a mock AsyncEngine with proper context managers."""
        engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        # engine.begin() returns async context manager yielding connection
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        engine.begin.return_value = ctx

        # engine.connect() returns async context manager
        conn_ctx = AsyncMock()
        conn_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        conn_ctx.__aexit__ = AsyncMock(return_value=False)
        engine.connect.return_value = conn_ctx

        return engine

    @pytest.fixture
    def mock_redis(self) -> MagicMock:
        redis = MagicMock()
        pipe = MagicMock()
        pipe.hset = MagicMock()
        pipe.expire = MagicMock()
        pipe.execute = AsyncMock()
        redis.pipeline.return_value = pipe
        redis.hget = AsyncMock(return_value=None)
        return redis

    @pytest.mark.asyncio
    async def test_full_sync_calls_wares_and_stock(
        self, mock_onec: AsyncMock, mock_engine: MagicMock, mock_redis: AsyncMock
    ) -> None:
        service = CatalogSyncService(mock_onec, mock_engine, mock_redis)
        await service.full_sync()

        mock_onec.get_wares_full.assert_called_once()
        # get_stock called for both networks
        assert mock_onec.get_stock.call_count == 2

    @pytest.mark.asyncio
    async def test_full_sync_upserts_to_db(
        self, mock_onec: AsyncMock, mock_engine: MagicMock, mock_redis: AsyncMock
    ) -> None:
        service = CatalogSyncService(mock_onec, mock_engine, mock_redis)
        await service.full_sync()

        # Should have executed SQL for model + product + stock
        ctx = mock_engine.begin.return_value
        mock_conn = ctx.__aenter__.return_value
        assert mock_conn.execute.call_count > 0

    @pytest.mark.asyncio
    async def test_incremental_sync_both_networks(
        self, mock_onec: AsyncMock, mock_engine: MagicMock, mock_redis: AsyncMock
    ) -> None:
        service = CatalogSyncService(mock_onec, mock_engine, mock_redis)
        await service.incremental_sync()

        # Called for both ProKoleso and Tshina
        assert mock_onec.get_wares_incremental.call_count == 2

    @pytest.mark.asyncio
    async def test_incremental_sync_confirms_receipt(
        self, mock_onec: AsyncMock, mock_engine: MagicMock, mock_redis: AsyncMock
    ) -> None:
        # Return data for incremental so receipt gets confirmed
        mock_onec.get_wares_incremental.return_value = {
            "success": True,
            "data": [
                {
                    "model_id": "m1",
                    "model": "Test",
                    "manufacturer_id": "mfr1",
                    "manufacturer": "TestBrand",
                    "seasonality": "Летняя",
                    "tread_pattern_type": "",
                    "type": "1",
                    "product": [],
                },
            ],
        }
        service = CatalogSyncService(mock_onec, mock_engine, mock_redis)
        await service.incremental_sync()

        assert mock_onec.confirm_wares_receipt.call_count == 2

    @pytest.mark.asyncio
    async def test_sync_stock_updates_redis(
        self, mock_onec: AsyncMock, mock_engine: MagicMock, mock_redis: AsyncMock
    ) -> None:
        service = CatalogSyncService(mock_onec, mock_engine, mock_redis, stock_cache_ttl=300)
        await service.sync_stock()

        pipe = mock_redis.pipeline.return_value
        # hset called for each stock item in each network
        assert pipe.hset.call_count > 0
        assert pipe.expire.call_count > 0

    @pytest.mark.asyncio
    async def test_full_sync_empty_wares_warns(
        self, mock_onec: AsyncMock, mock_engine: MagicMock, mock_redis: AsyncMock
    ) -> None:
        mock_onec.get_wares_full.return_value = {"success": True, "data": [], "errors": []}
        service = CatalogSyncService(mock_onec, mock_engine, mock_redis)
        # Should not raise, just log warning
        await service.full_sync()
