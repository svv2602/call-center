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

from src.agent.parsers import APPLY_THRESHOLD, ParseContext
from src.agent.parsers.date_parser import PARSER, resolve_tool_date

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


class TestBareDayOfMonth:
    """«на 11» — a day with no month, and only while the bot asked for one.

    `a83655c5` was transferred on 2026-09-10 for saying «на 11» directly after
    «На яку дату записуємо?». It is the one call in that window where the
    caller answered the state's real question and the parser still returned
    nothing, so the off-question exemption in the seam could not save it.

    The gate is the whole design. A bare number is whatever the last question
    made it: across the same ten prod calls the identical shape carried a
    wheel diameter («19»), a time («900») and three chunks of a phone number.
    Reading any of those as a date books an appointment nobody asked for,
    which is strictly worse than the re-ask it replaces — so the tests below
    weigh far more heavily on what must *not* parse.
    """

    DATE_Q = "На яку дату записуємо?"
    DIAMETER_Q = "Який діаметр коліс у вас?"
    PHONE_Q = (
        "Продиктуйте, будь ласка, номер телефону, за яким могли здати шини "
        "— український мобільний, 10 цифр."
    )
    SLOTS_Q = "Вільний час на 15 вересня: 9:00, 10:20, 11:40. Який час зручний?"

    def asked(self, text: str, bot: str = DATE_Q) -> ParseContext:
        return ParseContext(customer_text=text, last_bot_utterance=bot, now=NOW)

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("на 11", "2026-09-11"),
            ("11", "2026-09-11"),
            ("давайте 11", "2026-09-11"),
            ("на 11-е", "2026-09-11"),  # ordinal suffix: a letter, not a digit
            ("на 7", "2026-09-07"),  # today — «today included», as elsewhere
        ],
    )
    def test_a_day_named_in_answer_to_the_date_question(
        self, text: str, expected: str
    ) -> None:
        outcome = PARSER.parse(self.asked(text))
        assert outcome.status == "value"
        assert outcome.value == expected

    def test_the_month_rolls_not_the_year(self) -> None:
        """`_next_occurrence` rolls the year because it already knows the month.

        A bare day has no month, so it needs the other helper: said on the
        7th, «на 5» is next month's 5th and not this year's — a year later is
        outside the 21-day booking window by three orders of magnitude.
        """
        assert PARSER.parse(self.asked("на 5")).value == "2026-10-05"

    def test_a_day_that_does_not_exist_in_the_next_month_keeps_rolling(self) -> None:
        """31 asked in a 30-day month is 31 October, not «unresolved»."""
        assert PARSER.parse(self.asked("на 31")).value == "2026-10-31"

    def test_the_confidence_sits_below_an_explicitly_named_month(self) -> None:
        """The month is inferred here and heard in «11 вересня».

        Both are applicable, and a re-ask that has to choose between two
        readings of one turn should prefer the one the caller actually said.
        """
        inferred = PARSER.parse(self.asked("на 11"))
        explicit = PARSER.parse(self.asked("на 11 вересня"))
        assert inferred.value == explicit.value == "2026-09-11"
        assert APPLY_THRESHOLD <= inferred.confidence < explicit.confidence

    # --- the gate ---------------------------------------------------------

    @pytest.mark.parametrize(
        "text,bot",
        [
            ("19", DIAMETER_Q),  # 39469f9f — a wheel diameter
            ("12", DIAMETER_Q),  # 380a280d — the same
            ("900", SLOTS_Q),  # bba035ff — 9:00 said as one number
            ("936 52 18", PHONE_Q),  # 30dd42fa — a phone number
            ("095 9362 18", PHONE_Q),  # 30dd42fa — and again
            ("на 11", "Ви ще на лінії?"),
            ("на 11", "Я на зв'язку. Якщо маєте запитання — я слухаю."),
            ("на 11", ""),  # first turn, or an unreadable history
        ],
    )
    def test_a_number_the_bot_did_not_ask_a_date_for_is_not_a_date(
        self, text: str, bot: str
    ) -> None:
        outcome = PARSER.parse(self.asked(text, bot))
        assert outcome.status == "not_mentioned"
        assert outcome.value is None

    @pytest.mark.parametrize("text", ["936 52 18", "095 9362 18", "0 95 93"])
    def test_several_numbers_in_one_turn_are_never_a_day(self, text: str) -> None:
        """Belt to the gate's braces, and it earns its place.

        The gate reads the *bot*; STT decides what the caller said. The bot
        can ask for a date and hear a phone number anyway — and the last run
        of «095 9362 18» is a perfectly legal day of the month.
        """
        assert PARSER.parse(self.asked(text)).status == "not_mentioned"

    @pytest.mark.parametrize("text", ["900", "0", "45", "99", "15:40", "на 11.09.2026"])
    def test_shapes_that_are_not_a_bare_day(self, text: str) -> None:
        """«900» in particular: a time said as one number, and 9 is a real day."""
        outcome = PARSER.parse(self.asked(text))
        assert outcome.value != "2026-09-09"
        assert outcome.confidence != 0.8

    def test_ctx_now_is_still_the_only_clock(self) -> None:
        """Same rule as every other branch — no reaching for the process clock."""
        outcome = PARSER.parse(
            ParseContext(customer_text="на 11", last_bot_utterance=self.DATE_Q, now=None)
        )
        assert outcome.status == "unresolved"
        assert outcome.value is None

    # --- everything that came before must be untouched ---------------------

    @pytest.mark.parametrize(
        "text,expected",
        [("завтра", "2026-09-08"), ("11 вересня", "2026-09-11"), ("11.09", "2026-09-11")],
    )
    def test_the_hint_path_is_unchanged_by_the_fallback(
        self, text: str, expected: str
    ) -> None:
        """The fallback only runs where `_detect_date_hint` returned None, so
        no utterance that used to parse may now take the weaker reading."""
        with_bot = PARSER.parse(self.asked(text))
        without_bot = PARSER.parse(ctx(text))
        assert with_bot.value == without_bot.value == expected
        assert with_bot.confidence == without_bot.confidence > 0.8

    def test_the_tool_layer_does_not_get_the_fallback(self) -> None:
        """`resolve_tool_date` builds a context with no bot utterance, so a
        bare «11» from an LLM tool call still passes through untouched rather
        than being resolved against a question nobody asked."""
        assert resolve_tool_date("11", NOW) == "11"

    def test_the_word_ordinal_gap_is_closed(self) -> None:
        """This gap was pinned open here, and Wave 18 closed it.

        The reason it was left open — «digits are what STT emits» — did not
        survive a 30-day corpus: five of the 80 distinct answers to the date
        question were word ordinals, and the bot reads every date back in that
        form itself. The gate the old decision protected is untouched, though:
        a word ordinal is rewritten to digits *before* `_bare_day`, so it goes
        through that door and never around it. Vocabulary lives in
        `test_date_parser_ordinals.py`.
        """
        assert PARSER.parse(self.asked("на одинадцяте")).value == "2026-09-11"
        assert PARSER.parse(ctx("на одинадцяте")).status == "not_mentioned"
