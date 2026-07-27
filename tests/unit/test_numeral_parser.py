"""Unit tests for Ukrainian numeral → digit converter.

Real-world coverage anchored to call 934b9d31 (2026-07-27) where the bot
looped 15+ turns trying to capture a plate spoken as «два одинадцять».
Also covers case forms (nominative/genitive/accusative/dative/instrumental/
locative), ordinals («-надцятий»), and mixed digit/word input.
"""

from __future__ import annotations

from src.stt.numeral_parser import words_to_digits


class TestPlateFromWords:
    """Direct plate-number scenarios — the primary use case."""

    def test_two_and_eleven(self) -> None:
        # Real STT input pattern from call 934b9d31: customer said
        # «два одинадцять» meaning plate 2-11.
        assert words_to_digits("два одинадцять") == ("211", 1)

    def test_two_and_teen_variants(self) -> None:
        assert words_to_digits("два дванадцять")[0] == "212"
        assert words_to_digits("два тринадцять")[0] == "213"
        assert words_to_digits("два чотирнадцять")[0] == "214"
        assert words_to_digits("два п'ятнадцять")[0] == "215"
        assert words_to_digits("два шістнадцять")[0] == "216"
        assert words_to_digits("два сімнадцять")[0] == "217"
        assert words_to_digits("два вісімнадцять")[0] == "218"
        assert words_to_digits("два дев'ятнадцять")[0] == "219"

    def test_four_digit_words(self) -> None:
        # Testers spoke plate as pairs: «сімнадцять двадцять п'ять» → 1725
        assert words_to_digits("сімнадцять двадцять п'ять")[0] == "1725"

    def test_pairs_used_on_call_2d83ec67(self) -> None:
        # «14 11» → «чотирнадцять одинадцять» → 1411
        assert words_to_digits("чотирнадцять одинадцять")[0] == "1411"


class TestCompoundTens:
    """Additive combination for «двадцять один», «сто одинадцять», etc."""

    def test_twenty_one(self) -> None:
        assert words_to_digits("двадцять один")[0] == "21"

    def test_forty_five(self) -> None:
        assert words_to_digits("сорок п'ять")[0] == "45"

    def test_ninety_nine(self) -> None:
        assert words_to_digits("дев'яносто дев'ять")[0] == "99"

    def test_one_hundred_one(self) -> None:
        assert words_to_digits("сто один")[0] == "101"

    def test_one_hundred_eleven(self) -> None:
        assert words_to_digits("сто одинадцять")[0] == "111"

    def test_two_hundred_twenty(self) -> None:
        # «двісті» + «двадцять» — cannot combine (200 has 2 zeros, 20 fits)
        # → 220
        assert words_to_digits("двісті двадцять")[0] == "220"

    def test_two_hundred_twenty_five(self) -> None:
        assert words_to_digits("двісті двадцять п'ять")[0] == "225"


class TestCaseForms:
    """Ukrainian case-form inflections still resolve to same digit."""

    def test_odynadtsyat_all_forms(self) -> None:
        # 11 across cases + ordinal masculine/feminine/neuter
        assert words_to_digits("одинадцять")[0] == "11"
        assert words_to_digits("одинадцяти")[0] == "11"
        assert words_to_digits("одинадцятий")[0] == "11"
        assert words_to_digits("одинадцяту")[0] == "11"
        assert words_to_digits("одинадцятому")[0] == "11"
        assert words_to_digits("одинадцятою")[0] == "11"
        assert words_to_digits("одинадцяте")[0] == "11"
        assert words_to_digits("одинадцята")[0] == "11"

    def test_dvadtsyat_ordinal(self) -> None:
        # «двадцятий/двадцяту/двадцятою» — all = 20
        for w in ("двадцятий", "двадцяту", "двадцятого", "двадцятому", "двадцятою"):
            assert words_to_digits(w)[0] == "20", f"failed: {w}"

    def test_odyn_all_genders(self) -> None:
        # masc/fem/neut + oblique cases
        for w in ("один", "одна", "одне", "одно", "одну", "одному", "одним"):
            assert words_to_digits(w)[0] == "1", f"failed: {w}"

    def test_dva_dvi(self) -> None:
        # masc «два», fem «дві» — both = 2
        assert words_to_digits("два")[0] == "2"
        assert words_to_digits("дві")[0] == "2"


class TestApostropheVariants:
    """Ukrainian apostrophe forms — STT emits ' or ʼ inconsistently."""

    def test_pyat_all_apostrophes(self) -> None:
        assert words_to_digits("п'ять")[0] == "5"
        assert words_to_digits("пʼять")[0] == "5"
        assert words_to_digits("пять")[0] == "5"  # dropped apostrophe

    def test_devyat(self) -> None:
        assert words_to_digits("дев'ять")[0] == "9"
        assert words_to_digits("девʼять")[0] == "9"
        assert words_to_digits("девять")[0] == "9"

    def test_pyatnadtsyat(self) -> None:
        assert words_to_digits("п'ятнадцять")[0] == "15"
        assert words_to_digits("пʼятнадцять")[0] == "15"
        assert words_to_digits("пятнадцять")[0] == "15"


class TestPassthrough:
    """Non-numeral tokens must survive intact."""

    def test_empty(self) -> None:
        assert words_to_digits("") == ("", 0)

    def test_no_numerals(self) -> None:
        assert words_to_digits("держ номер авто") == ("держ номер авто", 0)

    def test_digits_untouched(self) -> None:
        # Digits already present should pass through — this module only
        # rewrites word forms.
        assert words_to_digits("номер 1725")[0] == "номер 1725"

    def test_mixed_with_bookend_words(self) -> None:
        # Real utterance shape: "держ номер два одинадцять"
        result, n = words_to_digits("держ номер два одинадцять")
        assert result == "держ номер 211"
        assert n == 1

    def test_multiple_runs_separated(self) -> None:
        # Two separate numeral runs with a non-numeral in the middle
        result, n = words_to_digits("два одинадцять і сімнадцять")
        assert result == "211 і 17"
        assert n == 2


class TestRegressionSafety:
    """Guards against false positives that would break other flows."""

    def test_plate_letter_words_not_touched(self) -> None:
        # «АЕ 1541 КМ» — plate letters (not numeral words), should pass through
        assert words_to_digits("а е 1541 к м")[0] == "а е 1541 к м"

    def test_punctuation_preserved(self) -> None:
        # STT sometimes inserts commas/dashes between spoken digits.
        result, _ = words_to_digits("два, одинадцять")
        # Comma between numeral words → flush and restart run.
        # Expected: "2, 11" (two separate runs).
        assert result == "2, 11"

    def test_bare_yes_no(self) -> None:
        assert words_to_digits("так")[0] == "так"
        assert words_to_digits("ні")[0] == "ні"
        assert words_to_digits("да")[0] == "да"


class TestSurroundingText:
    """Numerals embedded in longer sentences (real STT output shape)."""

    def test_intro_phrase(self) -> None:
        result, n = words_to_digits("Держ номер два одинадцять")
        assert result == "Держ номер 211"
        assert n == 1

    def test_trailing_confirmation(self) -> None:
        result, n = words_to_digits("два одинадцять, так")
        assert result == "211, так"
        assert n == 1

    def test_uppercase_first_letter(self) -> None:
        # STT capitalizes sentence-initial word; lookup is case-insensitive.
        result, n = words_to_digits("Два одинадцять")
        assert result == "211"
        assert n == 1


class TestNumeralRunEdgeCases:
    """Boundary cases in run detection."""

    def test_single_word(self) -> None:
        assert words_to_digits("одинадцять") == ("11", 1)

    def test_zero(self) -> None:
        # «нуль» / «ноль» — zero
        assert words_to_digits("нуль")[0] == "0"
        assert words_to_digits("ноль")[0] == "0"

    def test_zero_in_run(self) -> None:
        # «нуль два дев'ять чотири» → "0294" (real plate shape from call
        # f07e5d27 turn 8)
        assert words_to_digits("нуль два дев'ять чотири")[0] == "0294"


class TestLeadingZeros:
    """Plate numbers that start with 0 — real telco caller-input shape."""

    def test_zero_two_eleven_ua(self) -> None:
        # «нуль два одинадцять» → 0-2-11 → "0211"
        assert words_to_digits("нуль два одинадцять")[0] == "0211"

    def test_zero_two_eleven_ru(self) -> None:
        # RU-speaking caller: «ноль два одиннадцать» — RU numerals
        # resolved via _RU_NUMERALS defensive layer.
        assert words_to_digits("ноль два одиннадцать")[0] == "0211"

    def test_bare_digit_leading_zero_preserved(self) -> None:
        # Already-normalized plate "0294" from previous turn must survive
        # a re-pass through the parser — leading zero must not be dropped.
        assert words_to_digits("0294")[0] == "0294"

    def test_two_zeros_start(self) -> None:
        assert words_to_digits("нуль нуль два дев'ять чотири")[0] == "00294"

    def test_zero_between_digits(self) -> None:
        # «два нуль два» → "202"
        assert words_to_digits("два нуль два")[0] == "202"

    def test_digit_zero_plus_words(self) -> None:
        # Mixed: digit "0" then numeral words
        assert words_to_digits("0 два одинадцять")[0] == "0211"


class TestZeroInMiddle:
    """An explicit «нуль»/«ноль» between numerals means the caller is
    literally spelling out digit positions — never fold zero into the
    trailing-zero region of a preceding tens/hundreds number."""

    def test_thirteen_zero_three(self) -> None:
        # Real customer readout: «тринадцять ноль три» = plate 1303
        assert words_to_digits("тринадцять ноль три")[0] == "1303"

    def test_ru_thirteen_zero_three(self) -> None:
        assert words_to_digits("тринадцать ноль три")[0] == "1303"

    def test_twenty_zero_three_not_23(self) -> None:
        # «двадцять ноль три» = 2003, NOT 23 (which would be «двадцять три»).
        # The intervening «ноль» blocks the tens-units fold.
        assert words_to_digits("двадцять ноль три")[0] == "2003"

    def test_hundred_zero_five_literal(self) -> None:
        # «сто ноль п'ять» — «сто» contributes its 3 digits (1,0,0),
        # then explicit 0, then 5 → "10005". Callers who mean 105 would
        # say «сто п'ять»; those who mean 1005 would say «десять нуль
        # п'ять» or «один нуль нуль п'ять».
        assert words_to_digits("сто ноль п'ять")[0] == "10005"

    def test_ten_zero_five_1005(self) -> None:
        # «десять нуль п'ять» = 1005 (10-0-5, plate readout convention)
        assert words_to_digits("десять нуль п'ять")[0] == "1005"

    def test_digit_by_digit_1005(self) -> None:
        # «один нуль нуль п'ять» spelled literally
        assert words_to_digits("один нуль нуль п'ять")[0] == "1005"

    def test_normal_compound_still_folds(self) -> None:
        # No zero → normal arithmetic fold still works.
        assert words_to_digits("двадцять один")[0] == "21"
        assert words_to_digits("сто одинадцять")[0] == "111"


class TestRussianNumeralsDefensive:
    """RU numerals go through _RU_NUMERALS defense layer even if Redis
    rules don't fire (rule disabled, hybrid form STT-produced)."""

    def test_ru_eleven_via_defense(self) -> None:
        assert words_to_digits("одиннадцать")[0] == "11"

    def test_ru_twelve(self) -> None:
        assert words_to_digits("двенадцать")[0] == "12"

    def test_ru_twenty(self) -> None:
        assert words_to_digits("двадцать")[0] == "20"

    def test_hybrid_double_n_uk_ending(self) -> None:
        # STT produces «одиннадцять» (RU double-n + UA -ять ending)
        assert words_to_digits("одиннадцять")[0] == "11"
