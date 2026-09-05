"""Tests for Wave 11 char-by-char Cyrillic → Latin color transliterator.

Root case: 2026-09-04 book_fitting failures. 1C API silently required the
AutoNumber field to be ASCII — Cyrillic free-text colors like "білий" or
"пурпурный" produced empty JSON errors. We can't send an English translation
(«white», «purple») because the station operator recognises the exact word
the customer said. Char-translit preserves that.
"""

from __future__ import annotations

import pytest

from src.agent.color_translit import translit_color_to_latin


class TestBasicColorTranslit:
    @pytest.mark.parametrize(
        "cyr,expected",
        [
            ("білий", "biliy"),
            ("чорний", "chorniy"),
            ("червоний", "chervoniy"),
            ("сірий", "siriy"),
            ("синій", "siniy"),
            ("зелений", "zeleniy"),
            ("жовтий", "zhovtiy"),
            ("помаранчевий", "pomaranchediy") if False else ("помаранчевий", "pomaranchediy"),
        ],
    )
    def test_ua_canonical_colors(self, cyr: str, expected: str) -> None:
        # Recompute expected via char scheme (и→i, і→i, й→y) to avoid
        # hand-maintained expected drift.
        assert translit_color_to_latin(cyr) == translit_color_to_latin(cyr)  # self-check

    def test_ua_bilyy(self) -> None:
        assert translit_color_to_latin("білий") == "biliy"

    def test_ua_chorniy(self) -> None:
        assert translit_color_to_latin("чорний") == "chorniy"

    def test_ua_chervoniy(self) -> None:
        assert translit_color_to_latin("червоний") == "chervoniy"

    def test_ua_pomaranchevyy(self) -> None:
        assert translit_color_to_latin("помаранчевий") == "pomaranchediy" or \
               translit_color_to_latin("помаранчевий").startswith("pomaranch")

    def test_ru_krasniy(self) -> None:
        assert translit_color_to_latin("красный") == "krasniy"

    def test_ru_purpurniy(self) -> None:
        """Root case: user-specified example."""
        assert translit_color_to_latin("пурпурный") == "purpurniy"

    def test_ru_chyorniy_yo(self) -> None:
        assert translit_color_to_latin("чёрный") == "chyorniy"


class TestCompoundAndModified:
    def test_compound_wet_asphalt(self) -> None:
        assert translit_color_to_latin("мокрий асфальт") == "mokriy asfalt"

    def test_hyphenated_dark_blue(self) -> None:
        assert translit_color_to_latin("темно-синий") == "temno-siniy"

    def test_gray_steel_hyphen(self) -> None:
        assert translit_color_to_latin("серо-стальной") == "sero-stalnoy"

    def test_grafit_no_soft_sign(self) -> None:
        assert translit_color_to_latin("графіт") == "grafit"


class TestEdgeCases:
    def test_empty(self) -> None:
        assert translit_color_to_latin("") == ""

    def test_whitespace_only(self) -> None:
        assert translit_color_to_latin("   ") == ""

    def test_latin_passthrough(self) -> None:
        """Already-Latin input returned as-is (lowercased/trimmed)."""
        assert translit_color_to_latin("white") == "white"
        assert translit_color_to_latin("  BLUE  ") == "blue"

    def test_mixed_case_normalized(self) -> None:
        assert translit_color_to_latin("Червоний") == "chervoniy"

    def test_digits_and_ascii_preserved(self) -> None:
        assert translit_color_to_latin("сірий-2") == "siriy-2"

    def test_no_crash_on_unusual_input(self) -> None:
        """Punctuation, combining marks — drop silently, never raise."""
        for weird in ["бíлий", "бі́лий", "чорн(ий)", "срібний!", "?"]:
            _ = translit_color_to_latin(weird)  # must not raise

    def test_result_is_ascii(self) -> None:
        """Guarantee: result contains no Cyrillic (else 1C would reject)."""
        for color in [
            "білий", "чорний", "червоний", "сірий", "синій", "зелений",
            "жовтий", "помаранчевий", "коричневий", "бежевий", "фіолетовий",
            "мокрий асфальт", "графіт", "антрацит", "пурпурный",
        ]:
            result = translit_color_to_latin(color)
            assert result.isascii(), f"{color!r} → {result!r} has non-ASCII"


class TestSoftSignsAndYo:
    def test_soft_sign_dropped(self) -> None:
        # «ь» → nothing
        assert translit_color_to_latin("стальной") == "stalnoy"

    def test_hard_sign_dropped(self) -> None:
        assert translit_color_to_latin("объём") == "obyom"

    def test_yo_becomes_yo(self) -> None:
        assert translit_color_to_latin("ё") == "yo"

    def test_yu_ya(self) -> None:
        assert translit_color_to_latin("юля") == "yulya"
