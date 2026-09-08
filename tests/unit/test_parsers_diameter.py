"""`diameter_parser` — the field where the targeted mode earns its keep.

A bare «16» is a diameter, an hour and a day of the month at once, so the broad
pass grades it `0.6` and refuses to act (calls f70deab5 2026-09-01, ebe7dfcb
2026-09-07 — the bot quoted «R16 у Києві коштує 354 грн» for a size nobody had
named). When the bot's own last turn *was* the diameter question the homonymy
is gone and the same «16» is worth `1.0`.

That confidence flip is the whole parser, so it is the first thing tested here.

Wave 6-A finding, pinned below rather than fixed
------------------------------------------------
`compound_parse` blanks the date/time spans out of the text before it runs
`detect_diameter` (`_blank()`), which is why «на 18 вересня» yields no diameter
there. **This parser does not blank anything** — it calls `detect_diameter` on
the raw utterance. So «на 18 вересня» does come back with `value=18`, held
`unresolved` by the `0.6` grade, and «о 14 годині» with `value=14`. Worse, when
`is_diameter_question` is true the same date reads `1.0` and becomes a value.
Handed to Wave 6-B; the tests below record what the code does today.
"""

from __future__ import annotations

import pytest

from src.agent.parsers import ParseContext
from src.agent.parsers.base import APPLY_THRESHOLD
from src.agent.parsers.diameter_parser import _ASKED_CONFIDENCE, PARSER

#: The bot's Krok question, as `is_diameter_question` recognises it.
DIAMETER_QUESTION = "Підкажіть, будь ласка, який діаметр коліс?"


def ctx(text: str, *, bot: str = "") -> ParseContext:
    return ParseContext(customer_text=text, last_bot_utterance=bot)


class TestTheConfidenceFlip:
    """The one thing this parser adds over the broad pass."""

    def test_bare_number_without_the_question_is_unresolved(self) -> None:
        outcome = PARSER.parse(ctx("16"))
        assert outcome.value == 16
        assert outcome.confidence == 0.6
        assert outcome.status == "unresolved"

    def test_the_same_number_with_the_question_is_a_value(self) -> None:
        outcome = PARSER.parse(ctx("16", bot=DIAMETER_QUESTION))
        assert outcome.value == 16
        assert outcome.confidence == _ASKED_CONFIDENCE
        assert outcome.status == "value"

    def test_the_flip_crosses_the_threshold(self) -> None:
        """Both grades are on the correct side of the single threshold.

        A test that only checked the numbers would stay green if
        `APPLY_THRESHOLD` moved under it.
        """
        bare = PARSER.parse(ctx("16"))
        asked = PARSER.parse(ctx("16", bot=DIAMETER_QUESTION))
        assert bare.confidence < APPLY_THRESHOLD <= asked.confidence
        assert bare.value == asked.value == 16

    def test_an_explicit_marker_needs_no_question(self) -> None:
        """«R16» carries its own marker — `_diameter_confidence` gives `1.0`."""
        outcome = PARSER.parse(ctx("у мене R16"))
        assert outcome.status == "value"
        assert outcome.value == 16
        assert outcome.confidence == 1.0

    @pytest.mark.parametrize("text", ["діаметр 17", "розмір 17", "17 дюймів"])
    def test_marker_words_lift_the_confidence(self, text: str) -> None:
        outcome = PARSER.parse(ctx(text))
        assert outcome.status == "value"
        assert outcome.value == 17


class TestWordOrdinals:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("шістнадцять", 16),
            ("шіснадцять", 16),  # STT drops the «т»
            ("сімнадцять", 17),
            ("шестнадцать", 16),  # RU
        ],
    )
    def test_spoken_size_is_detected(self, text: str, expected: int) -> None:
        outcome = PARSER.parse(ctx(text, bot=DIAMETER_QUESTION))
        assert outcome.value == expected
        assert outcome.status == "value"


class TestNotMentioned:
    @pytest.mark.parametrize(
        "text",
        [
            "",
            "   ",
            "білий Nissan",
            "Київ, Оболонь",
            "на 14:30",  # Wave 12 lookaround: a clock time is not a size
            "140",
            "2024",
        ],
    )
    def test_no_diameter_at_all(self, text: str) -> None:
        outcome = PARSER.parse(ctx(text))
        assert outcome.status == "not_mentioned"
        assert outcome.value is None

    def test_the_wave_12_guard_holds_even_when_the_bot_asked(self) -> None:
        """`detect_diameter` runs before the confidence flip, so its guard wins."""
        outcome = PARSER.parse(ctx("на 14:30", bot=DIAMETER_QUESTION))
        assert outcome.status == "not_mentioned"


class TestDatesAndHoursLeakThrough:
    """Wave 6-A finding — see the module docstring. Pinned, not fixed.

    `compound_parse` hides date/time spans from `detect_diameter` with
    `_blank()`; the parser does not. These assertions describe today's
    behaviour so that a Wave 6-B fix breaks them loudly instead of silently
    changing what the FSM believes.
    """

    def test_a_named_date_still_produces_a_number(self) -> None:
        outcome = PARSER.parse(ctx("запишіть на 18 вересня"))
        assert outcome.value == 18, "current behaviour: the day of month leaks in"
        assert outcome.status == "unresolved", "held below the threshold by 0.6"

    def test_an_hour_still_produces_a_number(self) -> None:
        outcome = PARSER.parse(ctx("о 14 годині"))
        assert outcome.value == 14
        assert outcome.status == "unresolved"

    def test_a_date_read_as_a_value_when_the_bot_asked_for_a_diameter(self) -> None:
        """The sharp edge: `1.0` on a number that is a calendar day.

        Reachable only when the bot's previous turn was the diameter question
        and the caller answered with a date. Recorded for Wave 6-B — the fix is
        to blank the date/time spans the way the broad pass does.
        """
        outcome = PARSER.parse(ctx("запишіть на 18 вересня", bot=DIAMETER_QUESTION))
        assert outcome.status == "value"
        assert outcome.value == 18


class TestContract:
    def test_registry_identity(self) -> None:
        assert PARSER.name == "diameter_parser"
        assert PARSER.field_name == "diameter"

    def test_it_is_registered_as_passive(self) -> None:
        """Passive parsers write the field and never advance the state (§3.4)."""
        from src.agent.parsers.registry import PASSIVE_PARSERS

        assert "diameter_parser" in PASSIVE_PARSERS

    def test_no_aresolve(self) -> None:
        assert PARSER.aresolve is None

    def test_all_three_statuses_are_reachable(self) -> None:
        statuses = {
            PARSER.parse(ctx(text, bot=bot)).status
            for text, bot in (("16", ""), ("16", DIAMETER_QUESTION), ("білий", ""))
        }
        assert statuses == {"unresolved", "value", "not_mentioned"}
