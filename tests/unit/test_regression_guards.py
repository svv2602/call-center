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
        assert result["reason"] == "past_krok_2"


class TestWave10CrossCityRegression:
    """(A) cross-city trigger — root case call `8404d35b` 2026-09-04.

    Station pinned in Kyiv/Оболонь; LLM regressed by calling
    get_fitting_stations(city="Дніпро") mid-flow. Wave 9v1 signals hadn't
    materialised yet so cross-city trigger must catch it independently.
    """

    def test_cross_city_blocks_even_without_past_krok2(self) -> None:
        result = check_krok1_regression(
            last_fitting_station_id="000000006",
            storage_contract_guard_triggered=False,
            fitting_storage_contract=None,
            selected_fitting_date=None,
            fitting_slots_offered_count=0,
            pinned_station_city="Київ",
            incoming_city="Дніпро",
        )
        assert result is not None
        assert result["reason"] == "cross_city"
        assert result["action_required"] == "resume_checklist"
        assert "Київ" in result["message"]
        assert "Дніпро" in result["message"]
        assert "000000006" in result["message"]

    def test_same_city_no_regression_still_allowed(self) -> None:
        """Same city + no past-Krok-2 signals = legitimate list request."""
        result = check_krok1_regression(
            last_fitting_station_id="000000006",
            storage_contract_guard_triggered=False,
            fitting_storage_contract=None,
            selected_fitting_date=None,
            fitting_slots_offered_count=0,
            pinned_station_city="Київ",
            incoming_city="Київ",
        )
        assert result is None

    def test_case_insensitive_city_match(self) -> None:
        """City compare must be case + whitespace tolerant."""
        result = check_krok1_regression(
            last_fitting_station_id="000000006",
            storage_contract_guard_triggered=False,
            fitting_storage_contract=None,
            selected_fitting_date=None,
            fitting_slots_offered_count=0,
            pinned_station_city="  КИЇВ  ",
            incoming_city="київ",
        )
        assert result is None  # same city

    def test_empty_incoming_city_does_not_trigger_cross_city(self) -> None:
        """LLM calls get_fitting_stations() with empty city (self-heal path)
        — cross-city guard must NOT fire (we don't know target city)."""
        result = check_krok1_regression(
            last_fitting_station_id="000000006",
            storage_contract_guard_triggered=False,
            fitting_storage_contract=None,
            selected_fitting_date=None,
            fitting_slots_offered_count=0,
            pinned_station_city="Київ",
            incoming_city="",
        )
        assert result is None

    def test_no_pinned_city_falls_back_to_krok2_signals(self) -> None:
        """If we can't look up pinned city, use past-krok-2 signals only."""
        result = check_krok1_regression(
            last_fitting_station_id="000000006",
            storage_contract_guard_triggered=False,
            fitting_storage_contract=None,
            selected_fitting_date=None,
            fitting_slots_offered_count=0,
            pinned_station_city=None,
            incoming_city="Дніпро",
        )
        assert result is None

    def test_storage_choice_triggers_guard(self) -> None:
        """New Wave 10 signal: fitting_storage_choice='own' or 'contract'."""
        result = check_krok1_regression(
            last_fitting_station_id="000000006",
            storage_contract_guard_triggered=False,
            fitting_storage_contract=None,
            selected_fitting_date=None,
            fitting_slots_offered_count=0,
            fitting_storage_choice="own",
        )
        assert result is not None
        assert result["reason"] == "past_krok_2"

    def test_storage_contracts_found_triggers_guard(self) -> None:
        """New Wave 10 signal: find_storage returned contracts."""
        result = check_krok1_regression(
            last_fitting_station_id="000000006",
            storage_contract_guard_triggered=False,
            fitting_storage_contract=None,
            selected_fitting_date=None,
            fitting_slots_offered_count=0,
            storage_contracts_found_count=2,
        )
        assert result is not None
        assert result["reason"] == "past_krok_2"


class TestBackwardsCompat:
    """Existing call-sites use positional args — Wave 10 new args are kw-only."""

    def test_positional_v1_signature_still_works(self) -> None:
        """Pre-Wave-10 call — must not crash and must still guard."""
        result = check_krok1_regression(
            "000000001", True, None, None, 0
        )
        assert result is not None
        assert result["reason"] == "past_krok_2"
