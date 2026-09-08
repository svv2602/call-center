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

    # --- Wave 6-A additions ------------------------------------------------

    @pytest.mark.parametrize("text", ["на 18.09", "18/09", "на 18-09"])
    def test_all_three_separators_of_the_numeric_form(self, text: str) -> None:
        """`_NUMERIC_HINT_RE` accepts `.`, `/` and `-` — all three are used."""
        outcome = PARSER.parse(ctx(text))
        assert outcome.status == "value"
        assert outcome.value == "2026-09-18"

    def test_a_two_digit_year_is_read_as_this_century(self) -> None:
        """«15.03.26» is 2026, not year 26."""
        assert PARSER.parse(ctx("15.03.26")).value == "2026-03-15"

    def test_an_explicit_year_is_taken_as_written_even_if_it_is_past(self) -> None:
        """No roll-forward once the caller named the year.

        `_next_occurrence` only runs for a bare `dd.mm`; a stated year is a
        statement, and quietly moving it to 2027 would book a different day
        from the one that was said out loud.
        """
        outcome = PARSER.parse(ctx("15.03.26"))
        assert outcome.value == "2026-03-15"
        assert outcome.value < NOW.date().isoformat()

    @pytest.mark.parametrize("text", ["на 29.02", "на 29 лютого"])
    def test_29_february_rolls_to_the_next_leap_year(self, text: str) -> None:
        """`_MAX_YEAR_ROLL` — 2027 does not exist, 2028 does."""
        outcome = PARSER.parse(ctx(text))
        assert outcome.status == "value"
        assert outcome.value == "2028-02-29"

    def test_the_roll_is_bounded(self) -> None:
        """Four years is enough to clear a 29 February and no more."""
        from src.agent.parsers.date_parser import _MAX_YEAR_ROLL

        assert _MAX_YEAR_ROLL == 4

    def test_ru_month_names_come_from_the_shared_alternation(self) -> None:
        """`_MONTH_NUMBERS` is built from `_MONTHS_RU`, so the two cannot drift."""
        from src.agent.compound_parse import _MONTHS_RU
        from src.agent.parsers.date_parser import _MONTH_NUMBERS

        for number, name in enumerate(_MONTHS_RU.split("|"), start=1):
            assert _MONTH_NUMBERS[name] == number

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("15 марта", "2027-03-15"),
            ("на 20 декабря", "2026-12-20"),
            ("10 января", "2027-01-10"),
        ],
    )
    def test_russian_months_resolve(self, text: str, expected: str) -> None:
        outcome = PARSER.parse(ctx(text))
        assert outcome.status == "value"
        assert outcome.value == expected

    def test_a_month_number_out_of_range_is_unresolved(self) -> None:
        outcome = PARSER.parse(ctx("на 10.13"))
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

    # --- Wave 6-A additions ------------------------------------------------

    @pytest.mark.parametrize(
        "text",
        ["R16", "Оболонь, Київ", "мене звати Олена", "так, підтверджую", "о 14:00"],
    )
    def test_not_mentioned_is_distinct_from_unresolved(self, text: str) -> None:
        """Silence about the date needs a different re-ask from «I could not pin it».

        Without this case a regression that stops detecting dates entirely is
        indistinguishable from one that detects them unconfidently.
        """
        outcome = PARSER.parse(ctx(text))
        assert outcome.status == "not_mentioned"
        assert outcome.value is None
        assert outcome.confidence == 0.0

    def test_all_three_statuses_are_reachable(self) -> None:
        statuses = {
            PARSER.parse(ctx(text)).status for text in ("завтра", "найближча", "білий Nissan")
        }
        assert statuses == {"value", "unresolved", "not_mentioned"}
