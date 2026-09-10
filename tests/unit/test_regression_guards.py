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

from src.agent.regression_guards import caller_named_city, check_krok1_regression


class TestCallerNamedCity:
    """Wave 18 — the caller's own words outrank the profile city.

    Call `cbb41e0d` (2026-09-10): the caller opened with «вартість монтажу в
    Дніпрі», `customers.city` said Запоріжжя, the profile block is injected
    into the prompt with «використовуй ЗА ЗАМОВЧУВАННЯМ», and the LLM sent
    `get_fitting_stations(city='Запоріжжя')`. The backend already had Дніпро
    at confidence 1.0 from turn one; nothing consulted it.
    """

    def test_the_call_that_caused_wave_18(self) -> None:
        assert (
            caller_named_city(
                [
                    "Добрий день Підкажіть вартість монтажу в Дніпрі",
                    "14",
                    "вартість монтажу діаметр 14",
                ]
            )
            == "Дніпро"
        )

    def test_nothing_said_about_a_city(self) -> None:
        assert caller_named_city(["Добрий день", "хочу записатися на монтаж"]) is None

    def test_no_utterances_at_all(self) -> None:
        assert caller_named_city([]) is None

    def test_the_latest_mention_wins(self) -> None:
        """A caller who changes their mind is followed, not pinned.

        The `bd95036c` shape from the other side: there the caller asked for
        Черкаси four times against a Дніпро snapshot and nothing recorded it.
        """
        assert caller_named_city(["запишіть у Дніпрі", "ні, у Черкасах"]) == "Черкаси"

    def test_two_cities_in_one_breath_is_not_guessed(self) -> None:
        assert caller_named_city(["я з Києва, але треба в Дніпрі"]) is None

    def test_an_ambiguous_turn_stops_the_walk(self) -> None:
        """It must not answer with a city that this very turn may have superseded."""
        assert caller_named_city(["у Дніпрі", "я з Києва, але треба в Дніпрі"]) is None

    def test_a_landmark_is_not_a_named_city(self) -> None:
        """«на Оболоні» is an inference; the prompt routes it through `query=`."""
        assert caller_named_city(["запишіть мене на Оболоні"]) is None

    def test_a_repeated_street_is_not_a_named_city(self) -> None:
        """Call `63d11ab4` — the input that inverted the landmark guard."""
        assert (
            caller_named_city(
                [
                    "Добрый день",
                    "Запиши на монтаж на Харьковское шоссе",
                    "ниткой на Харьковском шоссе",
                ]
            )
            is None
        )

    def test_russian_spelling_resolves_to_the_canonical_name(self) -> None:
        assert caller_named_city(["записаться хочу", "Виталий", "Харьков"]) == "Харків"

    def test_the_caller_turns_are_the_call_sites_job_to_select(self) -> None:
        """Pinned so the contract is visible: pass caller turns only.

        This function cannot tell who spoke. Were bot turns included, «Знайшла
        точку в Запоріжжі» would read as the caller naming Запоріжжя and the
        guard would cement the very mistake it exists to undo.
        """
        assert caller_named_city(["Знайшла точку в Запоріжжі, підходить?"]) == "Запоріжжя"


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


class TestCallerCorrectionDefeatsTheGuard:
    """Wave 18 — the guard used to cement a wrong pin against the caller.

    Demonstrated on the stand while investigating `cbb41e0d`: with a station
    pinned in Запоріжжя, a call for Дніпро came back `cross_city` with the
    message «Клієнт не змінював місто» — a claim of fact, and false whenever
    the caller had just said «в Дніпрі». The pin won every time.
    """

    def test_a_named_city_defeats_cross_city(self) -> None:
        result = check_krok1_regression(
            last_fitting_station_id="000000001",
            storage_contract_guard_triggered=False,
            fitting_storage_contract=None,
            selected_fitting_date=None,
            fitting_slots_offered_count=0,
            pinned_station_city="Запоріжжя",
            incoming_city="Дніпро",
            caller_city="Дніпро",
        )
        assert result is None

    def test_a_named_city_defeats_past_krok2_too(self) -> None:
        """The pin was wrong, so everything collected after it is wrong too.

        Blocking here would answer «you already picked a date» to a caller who
        just said the whole booking is in the wrong city.
        """
        result = check_krok1_regression(
            last_fitting_station_id="000000001",
            storage_contract_guard_triggered=True,
            fitting_storage_contract="contract-1",
            selected_fitting_date="2026-09-11",
            fitting_slots_offered_count=6,
            pinned_station_city="Запоріжжя",
            incoming_city="Дніпро",
            caller_city="Дніпро",
            fitting_storage_choice="own",
            storage_contracts_found_count=2,
        )
        assert result is None

    def test_without_the_caller_saying_it_the_guard_still_fires(self) -> None:
        """The `8404d35b` case — an LLM losing context, not a correction."""
        result = check_krok1_regression(
            last_fitting_station_id="000000001",
            storage_contract_guard_triggered=False,
            fitting_storage_contract=None,
            selected_fitting_date=None,
            fitting_slots_offered_count=0,
            pinned_station_city="Київ",
            incoming_city="Дніпро",
            caller_city=None,
        )
        assert result is not None
        assert result["reason"] == "cross_city"

    def test_the_hatch_needs_the_caller_and_the_llm_to_agree(self) -> None:
        """Caller said Черкаси, LLM asked for Дніпро — that is still a regression."""
        result = check_krok1_regression(
            last_fitting_station_id="000000001",
            storage_contract_guard_triggered=False,
            fitting_storage_contract=None,
            selected_fitting_date=None,
            fitting_slots_offered_count=0,
            pinned_station_city="Київ",
            incoming_city="Дніпро",
            caller_city="Черкаси",
        )
        assert result is not None
        assert result["reason"] == "cross_city"

    def test_naming_the_pinned_city_is_not_a_correction(self) -> None:
        """Caller confirmed the pin; a call for another city is the LLM's doing."""
        result = check_krok1_regression(
            last_fitting_station_id="000000001",
            storage_contract_guard_triggered=False,
            fitting_storage_contract=None,
            selected_fitting_date=None,
            fitting_slots_offered_count=0,
            pinned_station_city="Київ",
            incoming_city="Дніпро",
            caller_city="Київ",
        )
        assert result is not None
        assert result["reason"] == "cross_city"

    def test_confirming_the_pinned_city_does_not_disable_the_wave_9_guard(self) -> None:
        """The hatch must need a *change* of city, not merely a mention of one.

        Found by a surviving mutation: dropping `caller_norm != pinned_norm`
        left every other test green, because none of them had the caller name
        the city they were already pinned in. That is the ordinary case —
        «Київ» named once at Krok 1 and pinned there — so without this
        condition the hatch would fire on almost every call and switch the
        past-Krok-2 guard off wholesale, which is the `88dc3c77` regression
        the guard was built for.
        """
        result = check_krok1_regression(
            last_fitting_station_id="000000001",
            storage_contract_guard_triggered=True,
            fitting_storage_contract=None,
            selected_fitting_date="2026-09-11",
            fitting_slots_offered_count=6,
            pinned_station_city="Київ",
            incoming_city="Київ",
            caller_city="Київ",
        )
        assert result is not None
        assert result["reason"] == "past_krok_2"

    def test_the_message_is_only_printed_when_it_is_true(self) -> None:
        """«Клієнт не змінював місто» must not be asserted over a correction."""
        blocked = check_krok1_regression(
            last_fitting_station_id="000000001",
            storage_contract_guard_triggered=False,
            fitting_storage_contract=None,
            selected_fitting_date=None,
            fitting_slots_offered_count=0,
            pinned_station_city="Київ",
            incoming_city="Дніпро",
            caller_city=None,
        )
        assert blocked is not None
        assert "не змінював" in blocked["message"]


class TestBackwardsCompat:
    """Existing call-sites use positional args — Wave 10 new args are kw-only."""

    def test_positional_v1_signature_still_works(self) -> None:
        """Pre-Wave-10 call — must not crash and must still guard."""
        result = check_krok1_regression(
            "000000001", True, None, None, 0
        )
        assert result is not None
        assert result["reason"] == "past_krok_2"
