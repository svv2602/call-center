"""`date_parser` — the raw label must never get out (Wave 5-A).

Regression seed: the seam in `src/core/pipeline.py` maps `date_hint → "date"`
with the hint's own confidence, so «завтра» — graded `1.0` by
`compound_parse._detect_date_hint` — was written into
`session.fsm_filled_fields["date"]` as the literal string. Above the apply
threshold, DATE's `auto_skip_if` sees a filled field and the state is skipped
on a value `book_fitting` cannot use. Harmless in shadow, a `c8c6601`-class
defect in live.

These are the only tests Wave 5-A writes (the rest are Wave 6-A). They exist
because without them the «no raw label escapes» requirement is held up by
nothing.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.agent.parsers import ParseContext
from src.agent.parsers.date_parser import PARSER

# A Monday, so weekday arithmetic is easy to read in the assertions.
NOW = datetime(2026, 9, 7, 11, 30, tzinfo=UTC)


def ctx(text: str, *, now: datetime | None = NOW) -> ParseContext:
    return ParseContext(customer_text=text, now=now)


class TestRelativeDays:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("завтра", "2026-09-08"),
            ("давайте на завтра", "2026-09-08"),
            ("сьогодні", "2026-09-07"),
            ("післязавтра", "2026-09-09"),
            ("после завтра", "2026-09-09"),
        ],
    )
    def test_resolved_to_iso(self, text: str, expected: str) -> None:
        outcome = PARSER.parse(ctx(text))
        assert outcome.status == "value"
        assert outcome.value == expected

    def test_no_raw_label_ever_reaches_the_value(self) -> None:
        """The whole point of the parser."""
        for text in ("завтра", "п'ятницю", "15 березня", "найближча", "сьогодні"):
            outcome = PARSER.parse(ctx(text))
            assert outcome.value != text
            if outcome.status == "value":
                # Only ever an ISO date, never a label.
                date.fromisoformat(outcome.value)


class TestReferenceDateIsMandatory:
    def test_tomorrow_without_now_is_unresolved(self) -> None:
        outcome = PARSER.parse(ctx("завтра", now=None))
        assert outcome.status == "unresolved"
        assert outcome.value is None

    def test_without_now_it_does_not_fall_back_to_the_current_day(self) -> None:
        """A parser that reads the wall clock is not a function of its context."""
        outcome = PARSER.parse(ctx("завтра", now=None))
        assert outcome.value is None


class TestDayAndMonth:
    def test_named_month_resolves(self) -> None:
        outcome = PARSER.parse(ctx("запишіть на 18 вересня"))
        assert outcome.status == "value"
        assert outcome.value == "2026-09-18"

    def test_russian_month_name(self) -> None:
        outcome = PARSER.parse(ctx("15 марта"))
        assert outcome.status == "value"
        assert outcome.value == "2027-03-15"  # already past in 2026 → next year

    def test_today_counts_for_an_explicitly_named_day(self) -> None:
        outcome = PARSER.parse(ctx("на 7 вересня"))
        assert outcome.value == "2026-09-07"

    def test_numeric_date(self) -> None:
        assert PARSER.parse(ctx("на 18.09")).value == "2026-09-18"
        assert PARSER.parse(ctx("18/09/2026")).value == "2026-09-18"

    def test_impossible_day_is_unresolved(self) -> None:
        outcome = PARSER.parse(ctx("на 45.09"))
        assert outcome.status != "value"
        assert outcome.value is None


class TestWeekdays:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("у вівторок", "2026-09-08"),
            ("на п'ятницю", "2026-09-11"),
            ("в неділю", "2026-09-13"),
        ],
    )
    def test_next_occurrence(self, text: str, expected: str) -> None:
        outcome = PARSER.parse(ctx(text))
        assert outcome.status == "value"
        assert outcome.value == expected

    def test_the_same_weekday_as_today_means_next_week(self) -> None:
        """«сьогодні понеділок» + «понеділок» → the next one, never today.

        A caller who means today says «сьогодні»; a weekday name is a
        recurring label. Booking someone for today when they meant next week
        makes them miss a slot they never agreed to.
        """
        outcome = PARSER.parse(ctx("давайте в понеділок"))
        assert outcome.value == "2026-09-14"


class TestVagueAndAbsent:
    def test_naiblizhcha_stays_unresolved(self) -> None:
        """Confidence 0.6 by design — the caller handed the choice back."""
        outcome = PARSER.parse(ctx("найближча"))
        assert outcome.status == "unresolved"
        assert outcome.value is None

    def test_no_date_at_all(self) -> None:
        outcome = PARSER.parse(ctx("білий Nissan"))
        assert outcome.status == "not_mentioned"
        assert outcome.value is None

    def test_empty_text(self) -> None:
        assert PARSER.parse(ctx("   ")).status == "not_mentioned"
