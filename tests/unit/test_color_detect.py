"""Tests for color auto-detection (Wave 5, replaces substring hint list).

Regression seed: call dd3dd368 2026-09-03 — «Червоны» (Russian, plural
form / STT mutation of «Красный») was NOT matched by the old substring
list because it only contained «червоний» (UA nominative masculine).
Bot said «не розчула колір» twice, then wrongly triggered the «колір не
назвали» escape hatch.
"""

from __future__ import annotations

import pytest

from src.agent.color_detect import detect_color


class TestBaseColorsUkrainian:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("червоний", "червоний"),
            ("червона", "червоний"),
            ("червоне", "червоний"),
            ("червоні", "червоний"),
            ("білий", "білий"),
            ("біла", "білий"),
            ("біле", "білий"),
            ("чорний", "чорний"),
            ("чорна", "чорний"),
            ("сірий", "сірий"),
            ("сіра", "сірий"),
            ("синій", "синій"),
            ("синя", "синій"),
            ("синє", "синій"),
            ("зелений", "зелений"),
            ("зелена", "зелений"),
            ("жовтий", "жовтий"),
            ("жовта", "жовтий"),
            ("бежевий", "бежевий"),
        ],
    )
    def test_ua_inflections(self, text: str, expected: str) -> None:
        assert detect_color(text) == expected


class TestBaseColorsRussian:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("красный", "червоний"),
            ("красная", "червоний"),
            ("красное", "червоний"),
            ("красную", "червоний"),
            ("белый", "білий"),
            ("белая", "білий"),
            ("белое", "білий"),
            ("чёрный", "чорний"),
            ("черный", "чорний"),
            ("чёрная", "чорний"),
            ("серый", "сірий"),
            ("серая", "сірий"),
            ("серое", "сірий"),
            ("синий", "синій"),
            ("синяя", "синій"),
            ("зелёный", "зелений"),
            ("зеленый", "зелений"),
            ("жёлтый", "жовтий"),
            ("желтый", "жовтий"),
        ],
    )
    def test_ru_inflections(self, text: str, expected: str) -> None:
        assert detect_color(text) == expected


class TestSTTMutations:
    """Regression: call dd3dd368 turn 57/65."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("червоны", "червоний"),
            ("Червоны", "червоний"),
            ("червона", "червоний"),
            ("червоно", "червоний"),
            ("червонное", "червоний"),
        ],
    )
    def test_chervony_variants(self, text: str, expected: str) -> None:
        assert detect_color(text.lower()) == expected

    def test_case_insensitive(self) -> None:
        assert detect_color("Красный") is None  # Must lowercase before calling
        assert detect_color("красный") == "червоний"


class TestSilverVsGrey:
    """«сер» and «серебр» share a prefix — silver must win."""

    def test_silver_ru(self) -> None:
        assert detect_color("серебристый") == "срібний"
        assert detect_color("серебряный") == "срібний"

    def test_silver_ua(self) -> None:
        assert detect_color("срібний") == "срібний"
        assert detect_color("сріблястий") == "срібний"

    def test_grey_ru(self) -> None:
        assert detect_color("серый") == "сірий"

    def test_silver_before_grey(self) -> None:
        # «серебристый» must match «серебрист» (silver) not «сер» (grey).
        assert detect_color("серебристый металлик") == "срібний"


class TestCompoundColors:
    def test_mokry_asfalt(self) -> None:
        assert detect_color("мокрий асфальт") == "мокрий асфальт"
        assert detect_color("мокрый асфальт") == "мокрий асфальт"

    def test_temno_prefix(self) -> None:
        assert detect_color("темно-синій") == "темний"
        assert detect_color("темно синій") == "темний"

    def test_yaskravo_prefix(self) -> None:
        assert detect_color("яскраво-червоний") == "яскравий"


class TestFancyColors:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("перламутр", "перламутр"),
            ("антрацит", "антрацит"),
            ("маренго", "маренго"),
            ("шампань", "шампань"),
            ("шоколад", "шоколадний"),
            ("шоколадний", "шоколадний"),
            ("графіт", "графіт"),
            ("графит", "графіт"),
            ("оливковий", "оливковий"),
            ("титан", "титан"),
            ("платина", "платина"),
            ("металік", "металік"),
            ("металлик", "металік"),
            ("гранат", "гранатовий"),
            ("вишневий", "вишневий"),
        ],
    )
    def test_fancy_words(self, text: str, expected: str) -> None:
        assert detect_color(text) == expected


class TestFalsePositives:
    """City names and other words that share a color root must NOT match."""

    def test_cherkasy_not_black(self) -> None:
        # Черкаси starts with «черк» — no color root should fire.
        assert detect_color("черкаси") is None
        assert detect_color("хочу в черкаси") is None

    def test_chernihiv_not_black(self) -> None:
        assert detect_color("чернігів") is None
        assert detect_color("чернигов") is None

    def test_krasnoyarsk_not_red(self) -> None:
        assert detect_color("красноярск") is None
        assert detect_color("з красноярска") is None

    def test_bila_tserkva_not_white(self) -> None:
        assert detect_color("біла церква") is None

    def test_sergey_not_grey(self) -> None:
        assert detect_color("сергей") is None
        assert detect_color("сергій") is None

    def test_serpen_not_grey(self) -> None:
        # «серпень» (August) starts with «сер» but is not grey.
        assert detect_color("серпень") is None

    def test_belka_not_white(self) -> None:
        assert detect_color("белка") is None


class TestInSentence:
    """Colors embedded in fuller customer utterances."""

    def test_polite_answer(self) -> None:
        assert detect_color("білий, будь ласка") == "білий"

    def test_with_prefix(self) -> None:
        assert detect_color("у мене червоний") == "червоний"

    def test_with_model(self) -> None:
        # «біла Toyota» — should match «біла».
        assert detect_color("біла toyota") == "білий"

    def test_answering_with_noise(self) -> None:
        # STT sometimes prepends filler words.
        assert detect_color("ну красный, наверное") == "червоний"


class TestNoMatch:
    @pytest.mark.parametrize(
        "text",
        [
            "",
            "не пам'ятаю",
            "не знаю",
            "забув",
            "алло",
            "мгм",
            "1234",
            "aa1234bb",
            "toyota",
            "mercedes",
        ],
    )
    def test_no_color(self, text: str) -> None:
        assert detect_color(text) is None
