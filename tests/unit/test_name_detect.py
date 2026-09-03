"""Tests for customer name auto-detection (Wave 6).

Regression seed: call fcfb26a9 turn 1 «Юра» → LLM did not persist name
→ session.fitting_customer_name stayed None → LLM later invented
«Марина» (bot's name) and «Василь» (from prompt anti-pattern example).
"""

from __future__ import annotations

import pytest

from src.agent.name_detect import detect_name, is_name_question


class TestIsNameQuestion:
    @pytest.mark.parametrize(
        "text",
        [
            "Як до вас звертатися?",
            "Як мо́жу до вас зверта́тися?",
            "як до вас звертатися",
            "Перепрошую, як до вас звертатися?",
            "Перепрошую, не розчула ім'я. Назвіть, будь ласка, як до вас звертатися?",
            "Як вас звати?",
        ],
    )
    def test_positive(self, text: str) -> None:
        assert is_name_question(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "У якому місті записуємо?",
            "Назвіть колір автомобіля.",
            "На яку дату записуємо?",
            "",
        ],
    )
    def test_negative(self, text: str) -> None:
        assert is_name_question(text) is False


class TestBasicNames:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Юра", "Юра"),
            ("Юрій", "Юрій"),
            ("Олексій", "Олексій"),
            ("Іван", "Іван"),
            ("Анна", "Анна"),
            ("Ганна", "Ганна"),
            ("Тарас", "Тарас"),
            ("Богдан", "Богдан"),
            ("Оксана", "Оксана"),
            ("Наталя", "Наталя"),
            ("Владислав", "Владислав"),
        ],
    )
    def test_ua_names(self, text: str, expected: str) -> None:
        assert detect_name(text) == expected

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Алексей", "Алексей"),
            ("Дмитрий", "Дмитрий"),
            ("Сергей", "Сергей"),
            ("Ольга", "Ольга"),
            ("Владимир", "Владимир"),
        ],
    )
    def test_ru_names(self, text: str, expected: str) -> None:
        assert detect_name(text) == expected


class TestSTTMutations:
    def test_lowercase_ok(self) -> None:
        # STT sometimes drops capitalization.
        assert detect_name("юра") == "Юра"
        assert detect_name("олексій") == "Олексій"

    def test_trailing_punctuation(self) -> None:
        assert detect_name("Юра.") == "Юра"
        assert detect_name("Юра!") == "Юра"
        assert detect_name("Юра,") == "Юра"

    def test_leading_whitespace(self) -> None:
        assert detect_name("  Юра  ") == "Юра"


class TestTwoWords:
    def test_first_last(self) -> None:
        # Take only the first word — patronymic/surname are unused.
        assert detect_name("Олена Петрівна") == "Олена"
        assert detect_name("Тарас Шевченко") == "Тарас"


class TestRejectsNoise:
    @pytest.mark.parametrize(
        "text",
        [
            "",
            "  ",
            "Алло",
            "алло",
            "Так",
            "Ні",
            "ОК",
            "Мгм",
            "Ага",
            "Шиномонтаж",
            "Черкаси",
            "Дніпро",
            "Оператор",
            "Марина",  # bot's own name
            "марина",
        ],
    )
    def test_stop_words_rejected(self, text: str) -> None:
        assert detect_name(text) is None

    def test_long_sentence_rejected(self) -> None:
        # Real answer that isn't a name.
        assert detect_name("я хочу дізнатися вартість послуг в місті Черкаси") is None

    def test_three_or_more_words_rejected(self) -> None:
        assert detect_name("мене звати Юра") is None

    def test_numbers_rejected(self) -> None:
        assert detect_name("1234") is None
        assert detect_name("Юра 123") is None

    def test_latin_rejected(self) -> None:
        # We don't accept English names via this path.
        assert detect_name("Toyota") is None
        assert detect_name("john") is None


class TestRealCallScenarios:
    """Direct regressions from call fcfb26a9."""

    def test_yura_from_call(self) -> None:
        assert detect_name("Юра") == "Юра"

    def test_marina_from_prompt_leak_blocked(self) -> None:
        # LLM invented «Марина» (bot's own name) as customer name.
        # If STT ever returns «Марина» in the name slot, block it.
        assert detect_name("Марина") is None

    def test_vasyl_from_anti_pattern_not_blocked(self) -> None:
        # «Василь» is a legitimate name — only Марина is blocked as
        # bot-name confusion. If a real Василь calls, we accept.
        assert detect_name("Василь") == "Василь"
