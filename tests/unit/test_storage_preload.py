"""Tests for automatic storage preload by CallerID.

Covers: format_storage_context(), _add_business_days(),
        build_system_prompt_with_context() with storage_context,
        and prompt module updates.
"""

import datetime
from unittest.mock import patch

from src.agent.prompts import (
    _MOD_FITTING,
    _MOD_STORAGE,
    _add_business_days,
    build_system_prompt_with_context,
    format_storage_context,
)

# ---------------------------------------------------------------------------
# _add_business_days
# ---------------------------------------------------------------------------


class TestAddBusinessDays:
    def test_weekday_to_weekday(self):
        """Monday + 3 business days = Thursday."""
        monday = datetime.date(2026, 3, 2)  # Monday
        assert _add_business_days(monday, 3) == datetime.date(2026, 3, 5)  # Thursday

    def test_friday_plus_3(self):
        """Friday + 3 business days = Wednesday (skips Sat/Sun)."""
        friday = datetime.date(2026, 2, 27)  # Friday
        assert _add_business_days(friday, 3) == datetime.date(2026, 3, 4)  # Wednesday

    def test_saturday_plus_3(self):
        """Saturday + 3 business days = Wednesday (next Mon+3)."""
        saturday = datetime.date(2026, 2, 28)  # Saturday
        assert _add_business_days(saturday, 3) == datetime.date(2026, 3, 4)  # Wednesday

    def test_sunday_plus_3(self):
        """Sunday + 3 business days = Wednesday."""
        sunday = datetime.date(2026, 3, 1)  # Sunday
        assert _add_business_days(sunday, 3) == datetime.date(2026, 3, 4)  # Wednesday

    def test_zero_days(self):
        """Adding 0 business days returns the same date."""
        monday = datetime.date(2026, 3, 2)
        assert _add_business_days(monday, 0) == monday


# ---------------------------------------------------------------------------
# format_storage_context
# ---------------------------------------------------------------------------


class TestFormatStorageContext:
    def test_empty_data_returns_none(self):
        assert format_storage_context({}) is None

    def test_empty_contracts_returns_none(self):
        assert format_storage_context({"contracts": []}) is None

    def test_none_returns_none(self):
        assert format_storage_context(None) is None  # type: ignore[arg-type]

    def test_non_dict_returns_none(self):
        assert format_storage_context("not a dict") is None  # type: ignore[arg-type]

    def test_single_warehouse_contract(self):
        data = {
            "contracts": [
                {
                    "contract_number": "ДЗ-2025-0142",
                    "owner_name": "Петренко Іван",
                    "tires": [
                        {
                            "brand": "Michelin",
                            "model": "Alpin 6",
                            "size": "205/55 R16",
                            "season": "winter",
                            "quantity": 4,
                        }
                    ],
                    "location": "warehouse",
                    "debt": 0,
                }
            ]
        }
        result = format_storage_context(data)
        assert result is not None
        assert "Зберігання шин клієнта" in result
        assert "ДЗ-2025-0142" in result
        assert "Петренко Іван" in result
        assert "4× Michelin Alpin 6 205/55 R16 (зимові)" in result
        assert "СКЛАД" in result
        assert "Борг: немає" in result
        assert "Мінімальна дата запису на монтаж" in result
        assert "3 робочих дні" in result
        assert "Одразу озвуч" in result

    def test_station_contract_no_min_date(self):
        data = {
            "contracts": [
                {
                    "contract_number": "ДЗ-2025-0200",
                    "owner_name": "Сидоренко Марія",
                    "tires": [
                        {
                            "brand": "Continental",
                            "model": "WinterContact",
                            "size": "225/45 R17",
                            "season": "winter",
                            "quantity": 4,
                        }
                    ],
                    "location": "station",
                    "debt": 0,
                }
            ]
        }
        result = format_storage_context(data)
        assert result is not None
        assert "шинний центр" in result
        # No minimum date for station location
        assert "Мінімальна дата" not in result

    def test_contract_with_debt(self):
        data = {
            "contracts": [
                {
                    "contract_number": "ДЗ-2025-0300",
                    "tires": [],
                    "location": "warehouse",
                    "debt": 1500,
                }
            ]
        }
        result = format_storage_context(data)
        assert result is not None
        assert "Борг: 1500 грн" in result

    def test_multiple_contracts(self):
        data = {
            "contracts": [
                {
                    "contract_number": "ДЗ-001",
                    "tires": [],
                    "location": "station",
                    "debt": 0,
                },
                {
                    "contract_number": "ДЗ-002",
                    "tires": [],
                    "location": "warehouse",
                    "debt": 0,
                },
            ]
        }
        result = format_storage_context(data)
        assert result is not None
        assert "2 договір(ів)" in result
        assert "ДЗ-001" in result
        assert "ДЗ-002" in result
        # Has warehouse → min date shown
        assert "Мінімальна дата" in result

    @patch("src.agent.prompts.datetime")
    def test_min_date_calculation(self, mock_dt):
        """Min date is 3 business days from today."""
        mock_dt.date.today.return_value = datetime.date(2026, 2, 26)  # Thursday
        mock_dt.timedelta = datetime.timedelta
        data = {
            "contracts": [
                {
                    "contract_number": "ДЗ-001",
                    "tires": [],
                    "location": "warehouse",
                    "debt": 0,
                }
            ]
        }
        result = format_storage_context(data)
        assert result is not None
        # Thursday + 3 business days = Tuesday (skip Sat, Sun)
        assert "2026-03-03" in result

    def test_all_season_tires(self):
        data = {
            "contracts": [
                {
                    "contract_number": "ДЗ-001",
                    "tires": [
                        {
                            "brand": "Goodyear",
                            "model": "Vector",
                            "size": "195/65 R15",
                            "season": "all_season",
                            "quantity": 4,
                        }
                    ],
                    "location": "station",
                    "debt": 0,
                }
            ]
        }
        result = format_storage_context(data)
        assert result is not None
        assert "всесезонні" in result


# ---------------------------------------------------------------------------
# build_system_prompt_with_context with storage_context
# ---------------------------------------------------------------------------


class TestBuildSystemPromptWithStorageContext:
    def test_storage_context_injected(self):
        prompt = build_system_prompt_with_context(
            "base prompt",
            storage_context="## Зберігання шин клієнта\ntest data",
        )
        assert "Зберігання шин клієнта" in prompt
        assert "test data" in prompt

    def test_no_storage_context(self):
        prompt = build_system_prompt_with_context("base prompt", storage_context=None)
        assert "Зберігання шин клієнта" not in prompt


# ---------------------------------------------------------------------------
# Prompt module updates
# ---------------------------------------------------------------------------


class TestPromptModuleUpdates:
    def test_fitting_step4_auto_check(self):
        assert "АВТОМАТИЧНО перевірила" in _MOD_FITTING
        assert "НЕ ВИКЛИКАЙ find_storage" in _MOD_FITTING
        assert "Запитай: «Чи є у вас шини на зберіганні" not in _MOD_FITTING

    def test_fitting_step4_transfer_on_not_found(self):
        assert "transfer_to_operator" in _MOD_FITTING

    def test_storage_step1_auto_check(self):
        assert "автоматично перевірила" in _MOD_STORAGE
        assert "Одразу виклич find_storage(phone=CallerID)" not in _MOD_STORAGE

    def test_storage_step1_manual_fallback(self):
        assert "find_storage(phone=...) вручну" in _MOD_STORAGE
