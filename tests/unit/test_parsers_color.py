"""`color_parser` — the seam over `color_detect.detect_color`, not the detector.

`detect_color` has its own 87 tests (`test_color_detect.py`). What is tested
here is what the detector does not have: the three `ParseOutcome` statuses, the
single confidence level, and the §3.1 invariant «one detector per field» — a
wrapper that grows its own regex is a review defect.

Corpus is taken from the detector's own comments (call `dd3dd368`, «Червоны»)
and from its `_FALSE_POSITIVE_WORDS` list, so the phrases are the ones that
actually came off calls.
"""

from __future__ import annotations

import pytest

from src.agent.parsers import ParseContext
from src.agent.parsers.base import APPLY_THRESHOLD
from src.agent.parsers.color_parser import _COLOR_CONFIDENCE, PARSER


def ctx(text: str, *, bot: str = "") -> ParseContext:
    return ParseContext(customer_text=text, last_bot_utterance=bot)


class TestColorNamed:
    @pytest.mark.parametrize(
        "text,expected",
        [
            # Krok 5 answers, as they arrive off STT.
            ("білий", "білий"),
            ("чорна машина", "чорний"),
            # call dd3dd368: «Червоны» is the STT form the substring list missed.
            ("Червоны", "червоний"),
            ("серая", "сірий"),
            ("темно-синій", "темний"),
        ],
    )
    def test_value_above_the_threshold(self, text: str, expected: str) -> None:
        outcome = PARSER.parse(ctx(text))
        assert outcome.status == "value"
        assert outcome.value == expected
        assert outcome.confidence == _COLOR_CONFIDENCE

    def test_confidence_is_the_broad_pass_level(self) -> None:
        """A colour root is an inference, not a literal — `0.9`, not `1.0`."""
        assert _COLOR_CONFIDENCE == 0.9
        assert _COLOR_CONFIDENCE >= APPLY_THRESHOLD

    def test_normalisation_is_the_detectors_job(self) -> None:
        """Mixed case reaches the detector already lowercased by the wrapper."""
        assert PARSER.parse(ctx("БІЛИЙ")).value == "білий"


class TestNotMentioned:
    @pytest.mark.parametrize(
        "text",
        [
            "",
            "   ",
            "Nissan Qashqai",
            "завтра о 14:00",
            # `_FALSE_POSITIVE_WORDS` — a city and a name that share a colour root.
            "Черкаси",
            "Сергій",
            "Біла Церква",
            "Чернігів",
        ],
    )
    def test_no_colour_is_not_mentioned(self, text: str) -> None:
        outcome = PARSER.parse(ctx(text))
        assert outcome.status == "not_mentioned"
        assert outcome.value is None

    def test_false_positive_filter_is_not_reimplemented_here(self) -> None:
        """«Черкаси» must be rejected by the detector, not by the wrapper.

        If this ever starts passing only because the wrapper grew a filter of
        its own, the two lists have begun to drift — the §3.1 anti-pattern.
        """
        from src.agent.color_detect import detect_color

        assert detect_color("черкаси") is None
        assert PARSER.parse(ctx("Черкаси")).status == "not_mentioned"


class TestUnresolvedIsUnreachable:
    def test_the_wrapper_has_exactly_one_confidence(self) -> None:
        """Colour is graded at one fixed level, so `unresolved` cannot occur.

        Documented rather than skipped: the moment the parser gains a second,
        lower confidence (a fuzzy colour, say), this test fails and the author
        has to decide what the re-ask for it should be.
        """
        statuses = {
            PARSER.parse(ctx(text)).status
            for text in ("білий", "Черкаси", "", "чорна", "Nissan", "Червоны")
        }
        assert statuses == {"value", "not_mentioned"}
        assert "unresolved" not in statuses


class TestContract:
    def test_registry_identity(self) -> None:
        assert PARSER.name == "color_parser"
        assert PARSER.field_name == "color"

    def test_no_aresolve(self) -> None:
        """Colour needs no network — the escape hatch is the engine's (Wave 6-B)."""
        assert PARSER.aresolve is None
