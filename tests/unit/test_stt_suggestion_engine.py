"""Unit tests for the STT correction suggestion engine.

Covers the pure logic that has no async DB / Redis dependencies:
  * _dam_lev (Damerau-Levenshtein with transpositions)
  * tokenize (filters short tokens, digits, apostrophes)
  * detect_context (bot question → domain tag)
  * suggest_replacement (fuzzy match against context dictionary)
"""

from __future__ import annotations

from src.stt.suggestion_engine import (
    _dam_lev,
    detect_context,
    suggest_replacement,
    tokenize,
)


class TestDamerauLevenshtein:
    def test_identical(self) -> None:
        assert _dam_lev("вівторок", "вівторок") == 0

    def test_empty(self) -> None:
        assert _dam_lev("", "abc") == 3
        assert _dam_lev("abc", "") == 3
        assert _dam_lev("", "") == 0

    def test_substitution(self) -> None:
        assert _dam_lev("cat", "cot") == 1

    def test_insertion(self) -> None:
        assert _dam_lev("cat", "cast") == 1

    def test_deletion(self) -> None:
        assert _dam_lev("cast", "cat") == 1

    def test_transposition(self) -> None:
        # 'act' → 'cat' is 1 transposition in Damerau (not 2 substitutions).
        assert _dam_lev("cat", "act") == 1

    def test_kitten_sitting(self) -> None:
        # Classic Levenshtein example. Damerau doesn't help here (no adjacent swaps).
        assert _dam_lev("kitten", "sitting") == 3

    def test_ukrainian_close(self) -> None:
        # STT-generated variant with one insertion.
        assert _dam_lev("поненеділок", "понеділок") == 2

    def test_ukrainian_far(self) -> None:
        # "приятный" (STT hallucination) is orthographically far from "пʼятницю".
        assert _dam_lev("приятный", "пʼятницю") >= 4


class TestTokenize:
    def test_empty(self) -> None:
        assert tokenize("") == []

    def test_all_digits(self) -> None:
        # Digits get filtered even when spaces separate them.
        assert tokenize("44 84 100") == []

    def test_short_tokens_dropped(self) -> None:
        # min length is 4 chars.
        assert tokenize("це і те") == []

    def test_basic_ua(self) -> None:
        # Preserves apostrophe inside token; drops "на" (too short).
        assert tokenize("на приятный трюм") == ["приятный", "трюм"]

    def test_lowercases(self) -> None:
        assert tokenize("Автомобіль") == ["автомобіль"]

    def test_deduplicates(self) -> None:
        # Repeated tokens are emitted only once, order preserved.
        assert tokenize("привіт привіт світе") == ["привіт", "світе"]

    def test_mixed_punctuation(self) -> None:
        assert tokenize("Записуйте, будь-ласка, на понеділок!") == [
            "записуйте",
            "будь",
            "ласка",
            "понеділок",
        ]

    def test_apostrophe_variants(self) -> None:
        # Both curly, straight, and Ukrainian-modified apostrophes are kept
        # inside tokens; leading/trailing ones are stripped.
        assert "пʼятницю" in tokenize("на пʼятницю")

    def test_digit_containing_token_dropped(self) -> None:
        # A single stray digit inside a word disqualifies it.
        assert tokenize("R16 205x55") == []


class TestDetectContext:
    def test_none_or_empty(self) -> None:
        assert detect_context(None) == "any"
        assert detect_context("") == "any"

    def test_neutral_returns_any(self) -> None:
        assert detect_context("щось нейтральне") == "any"

    def test_plate(self) -> None:
        assert detect_context("Повторіть, будь ласка, держномер авто") == "plate"
        assert detect_context("Скажіть номер автомобіля") == "plate"

    def test_date(self) -> None:
        assert detect_context("на яку дату записати?") == "date"
        assert detect_context("на який день?") == "date"
        assert detect_context("якого числа?") == "date"

    def test_time(self) -> None:
        assert detect_context("о котрій годині?") == "time"
        assert detect_context("на який час: 14:00 підходить?") == "time"

    def test_address(self) -> None:
        assert detect_context("в якому районі?") == "address"
        assert detect_context("яка адреса?") == "address"
        assert detect_context("з якого міста?") == "address"

    def test_tire_size(self) -> None:
        assert detect_context("який розмір шин?") == "tire_size"
        assert detect_context("R16 підходить?") == "tire_size"

    def test_name(self) -> None:
        assert detect_context("як вас звати?") == "name"
        assert detect_context("представтеся, будь ласка") == "name"


class TestSuggestReplacement:
    def test_no_dict_for_context_returns_none(self) -> None:
        # 'plate' has no fuzzy candidate list — always None.
        assert suggest_replacement("абвгд", "plate") == (None, None)

    def test_close_date_match(self) -> None:
        # 'поненеділок' is 2 edits away from 'понеділок' — accepted.
        replacement, distance = suggest_replacement("поненеділок", "date")
        assert replacement == "понеділок"
        assert distance == 2

    def test_far_date_no_match(self) -> None:
        # 'приятный' is too far orthographically from any weekday — rejected.
        assert suggest_replacement("приятный", "date") == (None, None)

    def test_close_city_match(self) -> None:
        # 'харких' → 'Харків' at distance 2.
        replacement, distance = suggest_replacement("харких", "address")
        assert replacement == "Харків"
        assert distance == 2

    def test_unknown_context_returns_none(self) -> None:
        # Unknown context → no dictionary → no match, no crash.
        assert suggest_replacement("anything", "made_up_context") == (None, None)

    def test_any_context_returns_none(self) -> None:
        # 'any' has no dictionary either — manager fills replacement manually.
        assert suggest_replacement("something", "any") == (None, None)
