"""Tests for Wave 9 fitting-flow regression guards.

Root case: call `88dc3c77` 2026-09-04. After the customer picked a station
(Turn 11), confirmed storage 'свої' (Turn 14), and gave a date «завтра»
(Turn 16), the LLM invented all book_fitting fields → Wave 7 Krok 3/4 guard
rejected → LLM misinterpreted the error and called get_fitting_stations
instead of get_fitting_slots → bot listed districts again, regressing the
whole flow back to Krok 1.
"""

from __future__ import annotations

import pytest

from src.agent.regression_guards import check_krok1_regression


class TestBlocksRegressionWhenStationPinnedAndPastKrok2:
    """Guard fires when station_id is set + any past-Krok-2 signal exists."""

    def test_storage_guard_triggered(self) -> None:
        result = check_krok1_regression(
            last_fitting_station_id="000000001",
            storage_contract_guard_triggered=True,
            fitting_storage_contract=None,
            selected_fitting_date=None,
            fitting_slots_offered_count=0,
        )
        assert result is not None
        assert result["error"] is True
        assert result["action_required"] == "call_get_fitting_slots"
        assert "000000001" in result["message"]
        assert "get_fitting_slots" in result["message"]
        assert "Регресія Кроку 1" in result["message"]

    def test_storage_contract_selected(self) -> None:
        result = check_krok1_regression(
            last_fitting_station_id="000000007",
            storage_contract_guard_triggered=False,
            fitting_storage_contract="Номер 123",
            selected_fitting_date=None,
            fitting_slots_offered_count=0,
        )
        assert result is not None

    def test_date_selected(self) -> None:
        """Root-case scenario: customer said «завтра», session pinned it."""
        result = check_krok1_regression(
            last_fitting_station_id="000000001",
            storage_contract_guard_triggered=True,
            fitting_storage_contract=None,
            selected_fitting_date="2026-09-05",
            fitting_slots_offered_count=0,
        )
        assert result is not None
        assert "2026-09-05" in result["message"]  # date used in hint

    def test_slots_offered(self) -> None:
        result = check_krok1_regression(
            last_fitting_station_id="000000001",
            storage_contract_guard_triggered=False,
            fitting_storage_contract=None,
            selected_fitting_date="2026-09-05",
            fitting_slots_offered_count=3,
        )
        assert result is not None


class TestAllowsLegitimateCalls:
    """Guard MUST NOT fire before Krok 1 is closed or during Krok 1 rework."""

    def test_no_station_pinned(self) -> None:
        """Initial call from LLM to discover cities/stations."""
        result = check_krok1_regression(
            last_fitting_station_id=None,
            storage_contract_guard_triggered=False,
            fitting_storage_contract=None,
            selected_fitting_date=None,
            fitting_slots_offered_count=0,
        )
        assert result is None

    def test_station_pinned_but_no_krok2_progress(self) -> None:
        """LLM just pinned station in Krok 1, no Krok 2 signals yet.
        Legitimate follow-up: LLM confirms station or asks storage question."""
        result = check_krok1_regression(
            last_fitting_station_id="000000001",
            storage_contract_guard_triggered=False,
            fitting_storage_contract=None,
            selected_fitting_date=None,
            fitting_slots_offered_count=0,
        )
        assert result is None

    def test_empty_string_date_treated_as_no_date(self) -> None:
        """Empty selected_fitting_date must not trigger past-krok-2 signal."""
        result = check_krok1_regression(
            last_fitting_station_id="000000001",
            storage_contract_guard_triggered=False,
            fitting_storage_contract=None,
            selected_fitting_date="",
            fitting_slots_offered_count=0,
        )
        assert result is None


class TestErrorMessageContent:
    """Message must be actionable and specific enough for LLM recovery."""

    def test_uses_pinned_station_id_in_recovery_hint(self) -> None:
        result = check_krok1_regression(
            last_fitting_station_id="000000042",
            storage_contract_guard_triggered=True,
            fitting_storage_contract=None,
            selected_fitting_date="2026-09-05",
            fitting_slots_offered_count=0,
        )
        assert result is not None
        # station_id must appear in both the "already pinned" note and the
        # recovery hint arg — LLM copies it verbatim
        assert result["message"].count("000000042") >= 2

    def test_uses_selected_date_in_recovery_hint_when_known(self) -> None:
        result = check_krok1_regression(
            last_fitting_station_id="000000001",
            storage_contract_guard_triggered=True,
            fitting_storage_contract=None,
            selected_fitting_date="2026-09-05",
            fitting_slots_offered_count=0,
        )
        assert result is not None
        assert "date_from='2026-09-05'" in result["message"]

    def test_falls_back_to_placeholder_when_no_date(self) -> None:
        """If date wasn't set (storage-guard-only trigger), use placeholder."""
        result = check_krok1_regression(
            last_fitting_station_id="000000001",
            storage_contract_guard_triggered=True,
            fitting_storage_contract=None,
            selected_fitting_date=None,
            fitting_slots_offered_count=0,
        )
        assert result is not None
        assert "YYYY-MM-DD" in result["message"]

    def test_action_required_field_is_stable(self) -> None:
        """Frontend/logging keys off action_required — don't change casually."""
        result = check_krok1_regression(
            last_fitting_station_id="000000001",
            storage_contract_guard_triggered=True,
            fitting_storage_contract=None,
            selected_fitting_date=None,
            fitting_slots_offered_count=0,
        )
        assert result["action_required"] == "call_get_fitting_slots"
