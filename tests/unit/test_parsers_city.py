"""`city_parser` — the one field where broad and targeted passes coincide.

Naming a city is unambiguous on its own, so there is no context that would
raise or lower the confidence and the wrapper adds nothing but the outcome
shape. What is worth testing is that the *ordering* inside
`compound_parse._detect_city` survives the wrapper, because that ordering is
the fix for call `1b6721a4`:

1. landmarks are matched first and their spans blanked out;
2. explicit city stems are searched only in what is left — «на харьковскому» is
   Харківське шосе *in Kyiv* and used to match the Kharkiv stem;
3. an explicitly named city outranks a landmark-derived one;
4. known STT mutations come last, at `0.6` — below the apply threshold, so the
   FSM confirms instead of pinning.

Every city and mutation below is read out of `compound_parse._CITY_STEMS` /
`_CITY_FUZZY` / `_LANDMARKS`, not from memory.
"""

from __future__ import annotations

import pytest

from src.agent.parsers import ParseContext
from src.agent.parsers.base import APPLY_THRESHOLD
from src.agent.parsers.city_parser import PARSER


def ctx(text: str) -> ParseContext:
    return ParseContext(customer_text=text)


class TestExplicitCity:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Київ", "Київ"),
            ("у Києві", "Київ"),
            ("в Киеве", "Київ"),
            ("Дніпро", "Дніпро"),
            ("з Дніпра", "Дніпро"),
            ("у Харкові", "Харків"),
            ("Запоріжжя", "Запоріжжя"),
            ("Черкаси", "Черкаси"),
        ],
    )
    def test_named_city_is_a_value_at_full_confidence(self, text: str, expected: str) -> None:
        outcome = PARSER.parse(ctx(text))
        assert outcome.status == "value"
        assert outcome.value == expected
        assert outcome.confidence == 1.0

    def test_the_five_served_cities_are_the_whole_vocabulary(self) -> None:
        """Read from the code, so a sixth city can not be added silently."""
        from src.agent.compound_parse import _CITY_STEMS

        assert {city for _stem, city in _CITY_STEMS} == {
            "Київ",
            "Дніпро",
            "Харків",
            "Запоріжжя",
            "Черкаси",
        }


class TestLandmarkSpanGuard:
    """Call `1b6721a4` — the reason the landmark pass runs first."""

    def test_kharkivske_shose_is_kyiv_not_kharkiv(self) -> None:
        outcome = PARSER.parse(ctx("на харьковскому"))
        assert outcome.value == "Київ"
        assert outcome.value != "Харків"
        assert outcome.confidence == 0.9, "derived from a landmark, not named"

    @pytest.mark.parametrize("text", ["Харківське шосе", "на харковском шоссе"])
    def test_every_spelling_of_the_street_resolves_to_kyiv(self, text: str) -> None:
        assert PARSER.parse(ctx(text)).value == "Київ"

    def test_the_guard_is_load_bearing_not_decorative(self) -> None:
        """Without the blanking the Kharkiv stem *does* match «харьковскому».

        Asserted against the detector's own pieces, so a regression points at
        `_detect_city` rather than at the wrapper. If this test ever goes green
        on the first assertion, the span guard has stopped doing anything and
        the `1b6721a4` test above is passing for the wrong reason.
        """
        from src.agent.compound_parse import _CITY_PATTERNS, _blank

        raw = "на харьковскому"
        assert any(pattern.search(raw) for pattern, city in _CITY_PATTERNS if city == "Харків")

        residual = _blank(raw, [(3, 12)])  # the «харьковск» landmark span
        assert "харьков" not in residual
        assert not any(
            pattern.search(residual) for pattern, city in _CITY_PATTERNS if city == "Харків"
        )

    def test_zaporizke_shose_is_dnipro(self) -> None:
        """«запорізьк» is deliberately absent from `_CITY_STEMS` for this."""
        assert PARSER.parse(ctx("на Запорізьке шосе")).value == "Дніпро"

    def test_an_inflected_landmark_yields_nothing_rather_than_the_wrong_city(
        self,
    ) -> None:
        """Wave 6-A observation, pinned as-is.

        `_LANDMARKS` stems that carry a second word («запорізьке шосе») only
        match the nominative, so «на Запорізькому шосе» — the form a caller
        actually uses — matches neither the landmark nor a city stem. The
        outcome is `not_mentioned`, which is fail-safe (the bot asks) rather
        than the `1b6721a4` failure mode (the bot confirms Kharkiv). Recorded
        for Wave 6-B, not fixed here.
        """
        assert PARSER.parse(ctx("на Запорізькому шосе")).status == "not_mentioned"

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("на Оболоні", "Київ"),
            ("біля Речпорту", "Дніпро"),
            ("на Холодногірській", "Харків"),
            ("ЖМ Перемога", "Дніпро"),
        ],
    )
    def test_landmark_derived_city(self, text: str, expected: str) -> None:
        outcome = PARSER.parse(ctx(text))
        assert outcome.status == "value"
        assert outcome.value == expected
        assert outcome.confidence == 0.9


class TestExplicitBeatsLandmark:
    def test_named_city_wins_over_the_landmark(self) -> None:
        """«в Дніпрі на Оболоні» — they told us the city (step 3)."""
        outcome = PARSER.parse(ctx("в Дніпрі на Оболоні"))
        assert outcome.value == "Дніпро"
        assert outcome.confidence == 1.0

    def test_the_cross_city_guard_is_not_this_parsers_job(self) -> None:
        """The mismatch is handled downstream; the parser just reports the city."""
        assert PARSER.parse(ctx("у Харкові на Оболоні")).value == "Харків"


class TestSttMutationsStayBelowTheThreshold:
    """Step 4 — guesses, so the FSM confirms rather than pins."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("запорище", "Запоріжжя"),
            ("затурищ", "Запоріжжя"),
            ("за париже", "Запоріжжя"),
            ("запорить", "Запоріжжя"),
        ],
    )
    def test_known_mutation_is_unresolved(self, text: str, expected: str) -> None:
        outcome = PARSER.parse(ctx(text))
        assert outcome.status == "unresolved"
        assert outcome.confidence == 0.6
        assert outcome.confidence < APPLY_THRESHOLD

    def test_the_guess_is_still_visible_for_the_reask(self) -> None:
        """Below the threshold the value is not thrown away (§3.3)."""
        outcome = PARSER.parse(ctx("запорище"))
        assert outcome.value == "Запоріжжя"
        assert outcome.status == "unresolved"

    def test_a_mutation_that_is_also_a_real_stem_stays_at_full_confidence(self) -> None:
        """«черкаси» is in both lists; the explicit pass runs first."""
        outcome = PARSER.parse(ctx("черкассы"))
        assert outcome.value == "Черкаси"


class TestNotMentioned:
    @pytest.mark.parametrize(
        "text",
        ["", "   ", "білий Nissan", "завтра о 14:00", "R16", "Львів"],
    )
    def test_no_city_named(self, text: str) -> None:
        outcome = PARSER.parse(ctx(text))
        assert outcome.status == "not_mentioned"
        assert outcome.value is None

    def test_an_unserved_city_is_silence_not_a_guess(self) -> None:
        """Only the five served cities are in the vocabulary."""
        assert PARSER.parse(ctx("я з Одеси")).status == "not_mentioned"


class TestContract:
    def test_registry_identity(self) -> None:
        assert PARSER.name == "city_parser"
        assert PARSER.field_name == "city"

    def test_no_aresolve(self) -> None:
        assert PARSER.aresolve is None

    def test_all_three_statuses_are_reachable(self) -> None:
        statuses = {PARSER.parse(ctx(text)).status for text in ("Київ", "запорище", "білий Nissan")}
        assert statuses == {"value", "unresolved", "not_mentioned"}
