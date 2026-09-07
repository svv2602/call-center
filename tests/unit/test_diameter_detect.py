"""Tests for Wave 12 diameter detection (backend guard for Wave 3 regression).

Root case: call ebe7dfcb 2026-09-07. Client «не песка жить в артист монтажу
На Харьковский» (STT-mangled «підкажіть вартість шиномонтажу на Харківське»).
Bot immediately replied «Комплексний шиномонтаж R16 у Києві коштує 354 грн»
— hallucinated default R16 without ever asking for the diameter. Wave 3
HARD GUARD in prompt did not hold under attention dilution; Wave 12 adds
a backend guard that rejects `get_fitting_price` when the customer has
not mentioned a diameter yet.
"""

from __future__ import annotations

import pytest

from src.agent.diameter_detect import (
    detect_diameter,
    is_diameter_question,
)


class TestBareNumbers:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("13", 13),
            ("14", 14),
            ("15", 15),
            ("16", 16),
            ("17", 17),
            ("18", 18),
            ("19", 19),
            ("20", 20),
            ("21", 21),
            ("22", 22),
            ("23", 23),
            ("24", 24),
        ],
    )
    def test_bare_number_in_range(self, text: str, expected: int) -> None:
        assert detect_diameter(text) == expected

    def test_below_range(self) -> None:
        assert detect_diameter("12") is None

    def test_above_range(self) -> None:
        assert detect_diameter("25") is None

    def test_number_with_context(self) -> None:
        assert detect_diameter("диаметр 20") == 20
        assert detect_diameter("розмір 17") == 17
        assert detect_diameter("на 16") == 16


class TestFalsePositiveGuards:
    """Guards against interpreting time / other numbers as diameter."""

    def test_time_with_colon_not_matched(self) -> None:
        """«14:30» is a time slot, not diameter."""
        assert detect_diameter("на 14:30") is None
        assert detect_diameter("15:00") is None

    def test_multi_digit_year_not_matched(self) -> None:
        assert detect_diameter("2024") is None
        assert detect_diameter("2026 рік") is None

    def test_three_digit_not_matched(self) -> None:
        assert detect_diameter("140") is None
        assert detect_diameter("240") is None


class TestRPrefix:
    def test_r_prefix_uppercase(self) -> None:
        assert detect_diameter("R17") == 17

    def test_r_prefix_lowercase(self) -> None:
        assert detect_diameter("r16") == 16

    def test_r_with_space(self) -> None:
        assert detect_diameter("R 20") == 20

    def test_stt_er_prefix(self) -> None:
        """STT may transcribe 'R' as 'эр' or 'ер'."""
        assert detect_diameter("эр 15") == 15
        assert detect_diameter("ер 18") == 18


class TestWordNumbers:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("тринадцять", 13),
            ("чотирнадцять", 14),
            ("п'ятнадцять", 15),
            ("пятнадцять", 15),
            ("шіснадцять", 16),
            ("шістнадцять", 16),
            ("сімнадцять", 17),
            ("вісімнадцять", 18),
            ("дев'ятнадцять", 19),
            ("двадцять", 20),
            ("двадцять один", 21),
            ("двадцять два", 22),
            ("двадцять три", 23),
            ("двадцять чотири", 24),
            # Russian
            ("шестнадцать", 16),
            ("семнадцать", 17),
            ("восемнадцать", 18),
            ("двадцать", 20),
            ("двадцать один", 21),
        ],
    )
    def test_word_number(self, text: str, expected: int) -> None:
        assert detect_diameter(text) == expected

    def test_word_in_sentence(self) -> None:
        assert detect_diameter("у мене двадцять") == 20
        assert detect_diameter("розмір сімнадцять") == 17

    def test_compound_wins_over_single(self) -> None:
        """«двадцять один» → 21, not 20."""
        assert detect_diameter("двадцять один") == 21
        assert detect_diameter("двадцять чотири") == 24


class TestEmpty:
    def test_empty_string(self) -> None:
        assert detect_diameter("") is None

    def test_whitespace(self) -> None:
        assert detect_diameter("   ") is None

    def test_no_number(self) -> None:
        assert detect_diameter("не знаю") is None
        assert detect_diameter("треба монтаж") is None


class TestDiameterQuestion:
    """Scope gate for pipeline integration: only trust detection if the
    bot just asked about diameter."""

    @pytest.mark.parametrize(
        "bot_text",
        [
            "Який діаметр коліс?",
            "Який діамет колес?",
            "Який розмір шин?",
            "На який радіус?",
            "На скільки дюймів?",
            "Какой диаметр?",
            "Какой размер?",
        ],
    )
    def test_is_diameter_question_true(self, bot_text: str) -> None:
        assert is_diameter_question(bot_text) is True

    @pytest.mark.parametrize(
        "bot_text",
        [
            "Назвіть колір авто.",
            "О котрій зручніше?",
            "Яка марка вашого авто?",
            "У якому місті?",
        ],
    )
    def test_is_diameter_question_false(self, bot_text: str) -> None:
        assert is_diameter_question(bot_text) is False

    def test_empty_bot_text(self) -> None:
        assert is_diameter_question("") is False


class TestRealCallExamples:
    """Anchor calls from Wave 12 batch analysis."""

    def test_call_ebe7dfcb_customer_diameter_answer(self) -> None:
        """Turn 12: 'диаметр 20' → detect 20."""
        assert detect_diameter("диаметр 20") == 20

    def test_call_ebe7dfcb_stt_variant(self) -> None:
        """Turn 11: 'инша диаметр' — no number, must not match."""
        assert detect_diameter("инша диаметр") is None

    def test_call_ebe7dfcb_turn_14(self) -> None:
        """'на монтаж диаметр 20' → 20."""
        assert detect_diameter("на монтаж диаметр 20") == 20

    def test_call_f2bec2d6_bare_17(self) -> None:
        """Turn 3: '17' → 17."""
        assert detect_diameter("17") == 17
