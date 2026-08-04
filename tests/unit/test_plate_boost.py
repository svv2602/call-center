"""Tests for licence-plate boost phrase set (Phase A).

Covers the plate letter/region-code constants and the extended
_build_adaptation that emits a second boosted PhraseSet.
"""

from __future__ import annotations

from src.stt.phrase_hints import (
    BASE_PLATE_LETTERS,
    BASE_PLATE_REGION_CODES,
    PLATE_BOOST_VALUE,
    get_plate_boost_phrases,
)


class TestPlateConstants:
    def test_letters_are_exactly_the_12_ukrainian_plate_letters(self) -> None:
        # DSTU 4278 defines exactly these 12 Cyrillic letters visually
        # identical to Latin. No Б, Г, Д, Ж, etc.
        expected = {"А", "В", "Е", "І", "К", "М", "Н", "О", "Р", "С", "Т", "Х"}
        assert set(BASE_PLATE_LETTERS) == expected
        assert len(BASE_PLATE_LETTERS) == 12

    def test_letters_are_cyrillic_not_latin(self) -> None:
        # STT for uk-UA/ru-RU outputs Cyrillic only — Latin lookalikes
        # would never match. Verify every letter is Cyrillic (U+0400..U+04FF).
        for letter in BASE_PLATE_LETTERS:
            assert len(letter) == 1
            assert 0x0400 <= ord(letter) <= 0x04FF, f"{letter!r} is not Cyrillic"

    def test_region_codes_are_two_uppercase_letters(self) -> None:
        for code in BASE_PLATE_REGION_CODES:
            assert len(code) == 2
            assert code.isupper()
            # Each character must be a valid plate letter.
            for ch in code:
                assert ch in BASE_PLATE_LETTERS, f"{code!r} contains {ch!r} not in plate alphabet"


class TestGetPlateBoostPhrases:
    def test_returns_tuples(self) -> None:
        pairs = get_plate_boost_phrases()
        assert len(pairs) > 0
        for pair in pairs:
            assert isinstance(pair, tuple)
            assert len(pair) == 2
            phrase, boost = pair
            assert isinstance(phrase, str)
            assert isinstance(boost, float)

    def test_all_at_expected_boost(self) -> None:
        for _, boost in get_plate_boost_phrases():
            assert boost == PLATE_BOOST_VALUE

    def test_boost_is_high_but_below_ceiling(self) -> None:
        # Google STT v2 boost is documented to have a soft ceiling around 20.
        # Anything much higher risks the model over-fitting to letter output
        # during normal Ukrainian speech. Anything much lower defeats the point.
        assert 10.0 <= PLATE_BOOST_VALUE <= 20.0

    def test_no_duplicates_across_letters_and_codes(self) -> None:
        # Single letters and two-letter codes must never collide, and even
        # within the two-letter set there shouldn't be repeats.
        pairs = get_plate_boost_phrases()
        phrases = [p for p, _ in pairs]
        assert len(phrases) == len(set(phrases))

    def test_contains_all_letters(self) -> None:
        phrases = {p for p, _ in get_plate_boost_phrases()}
        for letter in BASE_PLATE_LETTERS:
            assert letter in phrases

    def test_contains_common_kyiv_prefix(self) -> None:
        # Sanity check that the region-code list wasn't accidentally emptied.
        phrases = {p for p, _ in get_plate_boost_phrases()}
        assert "АА" in phrases
        assert "КА" in phrases


class TestBuildAdaptationWithBoost:
    """Google STT SpeechAdaptation builder — with and without boost set."""

    def test_empty_inputs_returns_none(self) -> None:
        from src.stt.google_stt import _build_adaptation

        assert _build_adaptation((), ()) is None

    def test_only_general_hints_produces_one_phrase_set(self) -> None:
        from src.stt.google_stt import _build_adaptation

        adaptation = _build_adaptation(("шиномонтаж", "Мішлен"), ())
        assert adaptation is not None
        assert len(adaptation.phrase_sets) == 1
        # Boost defaults to 0 on the general set.
        for phrase in adaptation.phrase_sets[0].inline_phrase_set.phrases:
            assert phrase.boost == 0.0

    def test_only_boost_produces_one_phrase_set(self) -> None:
        from src.stt.google_stt import _build_adaptation

        adaptation = _build_adaptation((), (("А", 15.0), ("В", 15.0)))
        assert adaptation is not None
        assert len(adaptation.phrase_sets) == 1
        for phrase in adaptation.phrase_sets[0].inline_phrase_set.phrases:
            assert phrase.boost == 15.0

    def test_both_produce_two_phrase_sets(self) -> None:
        from src.stt.google_stt import _build_adaptation

        adaptation = _build_adaptation(("шиномонтаж",), (("А", 15.0),))
        assert adaptation is not None
        assert len(adaptation.phrase_sets) == 2

        general = adaptation.phrase_sets[0].inline_phrase_set.phrases
        boosted = adaptation.phrase_sets[1].inline_phrase_set.phrases
        assert general[0].value == "шиномонтаж"
        assert general[0].boost == 0.0
        assert boosted[0].value == "А"
        assert boosted[0].boost == 15.0
