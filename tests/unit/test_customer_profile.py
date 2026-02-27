"""Unit tests for customer profile feature."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from src.agent.prompts import format_customer_profile
from src.logging.call_logger import CallLogger, _merge_vehicles


class TestFormatCustomerProfile:
    """Tests for format_customer_profile()."""

    def test_none_returns_none(self) -> None:
        assert format_customer_profile(None) is None

    def test_empty_dict_returns_none(self) -> None:
        assert format_customer_profile({}) is None

    def test_all_empty_fields_returns_none(self) -> None:
        profile = {"name": None, "city": None, "vehicles": [], "delivery_address": None}
        assert format_customer_profile(profile) is None

    def test_name_only(self) -> None:
        profile = {"name": "Іван", "city": None, "vehicles": [], "delivery_address": None}
        result = format_customer_profile(profile)
        assert result is not None
        assert "Іван" in result
        assert "Профіль клієнта" in result

    def test_full_profile(self) -> None:
        profile = {
            "name": "Олександр",
            "city": "Київ",
            "vehicles": [
                {"plate": "AA1234BB", "brand": "Toyota Camry", "tire_size": "205/55R16"}
            ],
            "delivery_address": "вул. Хрещатик, 1",
            "total_calls": 5,
        }
        result = format_customer_profile(profile)
        assert result is not None
        assert "Олександр" in result
        assert "Київ" in result
        assert "Toyota Camry" in result
        assert "AA1234BB" in result
        assert "205/55R16" in result
        assert "Хрещатик" in result
        assert "5" in result

    def test_multiple_vehicles(self) -> None:
        profile = {
            "name": "Марія",
            "vehicles": [
                {"plate": "AA1111BB", "brand": "BMW X5"},
                {"plate": "CC2222DD", "brand": "Kia Sportage", "tire_size": "225/60R17"},
            ],
        }
        result = format_customer_profile(profile)
        assert result is not None
        assert "BMW X5" in result
        assert "Kia Sportage" in result

    def test_vehicles_as_json_string(self) -> None:
        profile = {
            "name": "Тест",
            "vehicles": '[{"plate": "XX0000XX", "brand": "Honda CR-V"}]',
        }
        result = format_customer_profile(profile)
        assert result is not None
        assert "Honda CR-V" in result

    def test_total_calls_one_not_shown(self) -> None:
        profile = {"name": "Тест", "total_calls": 1}
        result = format_customer_profile(profile)
        assert result is not None
        assert "Всього дзвінків" not in result

    def test_instructions_present(self) -> None:
        profile = {"name": "Тест"}
        result = format_customer_profile(profile)
        assert result is not None
        assert "НЕ питай повторно" in result
        assert "update_customer_profile" in result


class TestMergeVehicles:
    """Tests for _merge_vehicles() helper."""

    def test_no_overlap(self) -> None:
        existing = [{"plate": "AA1111BB", "brand": "Toyota"}]
        incoming = [{"plate": "CC2222DD", "brand": "BMW"}]
        result = _merge_vehicles(existing, incoming)
        plates = {v["plate"] for v in result}
        assert plates == {"AA1111BB", "CC2222DD"}

    def test_update_existing(self) -> None:
        existing = [{"plate": "AA1111BB", "brand": "Toyota", "tire_size": "205/55R16"}]
        incoming = [{"plate": "AA1111BB", "brand": "Toyota Camry"}]
        result = _merge_vehicles(existing, incoming)
        assert len(result) == 1
        assert result[0]["brand"] == "Toyota Camry"
        assert result[0]["tire_size"] == "205/55R16"

    def test_preserve_unlisted(self) -> None:
        existing = [
            {"plate": "AA1111BB", "brand": "Toyota"},
            {"plate": "CC2222DD", "brand": "BMW"},
        ]
        incoming = [{"plate": "AA1111BB", "brand": "Toyota Camry"}]
        result = _merge_vehicles(existing, incoming)
        plates = {v["plate"] for v in result}
        assert plates == {"AA1111BB", "CC2222DD"}
        updated = next(v for v in result if v["plate"] == "AA1111BB")
        assert updated["brand"] == "Toyota Camry"

    def test_empty_existing(self) -> None:
        result = _merge_vehicles([], [{"plate": "AA1111BB", "brand": "Toyota"}])
        assert len(result) == 1

    def test_empty_incoming(self) -> None:
        existing = [{"plate": "AA1111BB", "brand": "Toyota"}]
        result = _merge_vehicles(existing, [])
        assert len(result) == 1

    def test_both_empty(self) -> None:
        result = _merge_vehicles([], [])
        assert result == []


class TestCallLoggerProfile:
    """Tests for CallLogger profile methods (mocked DB)."""

    @pytest.fixture
    def logger(self) -> CallLogger:
        cl = CallLogger.__new__(CallLogger)
        cl._redis = None
        return cl

    @pytest.mark.asyncio
    async def test_get_customer_profile_not_found(self, logger: CallLogger) -> None:
        with patch.object(logger, "_fetch_one", new_callable=AsyncMock, return_value=None):
            result = await logger.get_customer_profile("+380501234567")
            assert result is None

    @pytest.mark.asyncio
    async def test_get_customer_profile_found(self, logger: CallLogger) -> None:
        mock_row = {
            "name": "Іван",
            "city": "Київ",
            "vehicles": "[]",
            "delivery_address": None,
            "total_calls": 3,
            "first_call_at": datetime(2026, 1, 15),
        }
        with patch.object(logger, "_fetch_one", new_callable=AsyncMock, return_value=mock_row):
            result = await logger.get_customer_profile("+380501234567")
            assert result is not None
            assert result["name"] == "Іван"
            assert result["city"] == "Київ"

    @pytest.mark.asyncio
    async def test_update_customer_profile_not_found(self, logger: CallLogger) -> None:
        with patch.object(logger, "_fetch_one", new_callable=AsyncMock, return_value=None):
            result = await logger.update_customer_profile("+380501234567", name="Іван")
            assert result["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_update_customer_profile_name(self, logger: CallLogger) -> None:
        mock_row = {"id": "test-uuid", "vehicles": "[]"}
        with (
            patch.object(logger, "_fetch_one", new_callable=AsyncMock, return_value=mock_row),
            patch.object(logger, "_execute", new_callable=AsyncMock) as mock_exec,
        ):
            result = await logger.update_customer_profile("+380501234567", name="Петро")
            assert result["status"] == "updated"
            assert "name" in result["fields"]
            mock_exec.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_customer_profile_no_changes(self, logger: CallLogger) -> None:
        mock_row = {"id": "test-uuid", "vehicles": "[]"}
        with patch.object(logger, "_fetch_one", new_callable=AsyncMock, return_value=mock_row):
            result = await logger.update_customer_profile("+380501234567")
            assert result["status"] == "no_changes"

    @pytest.mark.asyncio
    async def test_update_customer_profile_vehicles_merge(self, logger: CallLogger) -> None:
        mock_row = {
            "id": "test-uuid",
            "vehicles": [{"plate": "AA1111BB", "brand": "Toyota"}],
        }
        with (
            patch.object(logger, "_fetch_one", new_callable=AsyncMock, return_value=mock_row),
            patch.object(logger, "_execute", new_callable=AsyncMock) as mock_exec,
        ):
            result = await logger.update_customer_profile(
                "+380501234567",
                vehicles=[{"plate": "AA1111BB", "brand": "Toyota Camry"}],
            )
            assert result["status"] == "updated"
            assert "vehicles" in result["fields"]
            # Check that merged vehicles were passed to execute
            call_args = mock_exec.call_args
            params = call_args[0][1]
            import json

            merged = json.loads(params["vehicles"])
            assert len(merged) == 1
            assert merged[0]["brand"] == "Toyota Camry"
