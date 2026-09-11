"""Wave 18: a day of the month spoken as a word.

The bot reads every date back as an ordinal — `ua_datetime.date_to_words`
turns `2026-09-14` into «чотирнадцяте вересня» — and until this wave the
parser understood none of them. Mined from the 30 days of prod to 2026-09-11:
of the 80 distinct short answers to «На яку дату записуємо?», five were word
ordinals and all five were read as «no date at all».

The risk this carries is not the vocabulary, it is the collision: the same
stem is a date, an hour and a wheel diameter depending on how it ends. So the
tests below are weighted towards what must *not* move — «на другу» is two
o'clock, «на друге» is the 2nd, and one letter is the whole difference.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.agent.compound_parse import _normalize
from src.agent.fitting_fsm import FsmState
from src.agent.parsers.base import ParseContext, ParseOutcome
from src.agent.parsers.date_parser import (
    _DAY_ORDINAL_ENDINGS,
    _DAY_ORDINAL_STEMS,
    PARSER,
    _ordinals_to_digits,
)
from src.agent.ua_datetime import date_to_words

NOW = datetime(2026, 9, 11, 10, 0, tzinfo=UTC)
ASKED = "На яку дату записуємо?"


def day_of(text: str, *, bot: str = ASKED) -> int | None:
    """The day-of-month the parser lands on, or None if it refused."""
    outcome = PARSER.parse(
        ParseContext(customer_text=text, last_bot_utterance=bot, state=FsmState.DATE, now=NOW)
    )
    if outcome.status != "value" or not outcome.value:
        return None
    return int(str(outcome.value)[-2:])


class TestTheFiveFormsProdSent:
    """Verbatim from `call_turns`, 2026-08-12..2026-09-11."""

    @pytest.mark.parametrize(
        ("said", "day"),
        [
            ("на третє", 3),
            ("над пятого", 5),  # STT dropped the «а» of «на п'ятого»
            ("давайте другого начнем", 2),
            ("давайте на третьего разням", 3),
        ],
    )
    def test_it_resolves(self, said: str, day: int) -> None:
        assert day_of(said) == day

    def test_the_masculine_locative_is_still_refused(self) -> None:
        """«на третій вересня» — the fifth form, and it stays unresolved.

        «третій» is the ending «о третій» takes, so the utterance is a date
        and an hour at the same time. A re-ask costs one turn; booking three
        o'clock as the 3rd costs an appointment.
        """
        assert day_of("на третій вересня") is None


class TestTheEndingIsTheDiscriminator:
    """One letter apart, and the other reading must survive untouched."""

    @pytest.mark.parametrize("hour_form", ["на другу", "о третій", "до третьої", "на чотирнадцяту"])
    def test_an_hour_is_not_a_day(self, hour_form: str) -> None:
        assert day_of(hour_form) is None

    @pytest.mark.parametrize("diameter", ["п'ятнадцять", "сімнадцяти", "шістнадцяти"])
    def test_a_diameter_is_not_a_day(self, diameter: str) -> None:
        """The cardinal endings ь/и, which `detect_diameter` reads.

        Only the word forms: «на 17 дюймів» is a bare digit and has belonged
        to `_bare_day`'s gate since `d54547e` — the bot asking for a date is
        what makes it the 17th, and that is a different decision from this one.
        """
        assert day_of(diameter) is None

    def test_a_weekday_is_not_a_day_number(self) -> None:
        """«вівторник» ends in the stem «втор» and must not read as the 2nd."""
        assert day_of("у вівторник") not in (2,)

    @pytest.mark.parametrize("innocent", ["запорізьке шосе", "перш ніж почати"])
    def test_ordinary_words_are_left_alone(self, innocent: str) -> None:
        assert _ordinals_to_digits(innocent) == (innocent, ())


class TestTheBotsOwnWords:
    """Every date the bot can speak must parse back to the day it came from.

    This is the anti-drift mechanism. `_DAY_ORDINAL_STEMS` is not derived from
    `ua_datetime._DAY_ORDINAL_NEUTER_UA` — deriving it would miss the two-word
    forms and the genitive endings anyway — so the round trip is what keeps
    the two vocabularies from parting company.
    """

    @pytest.mark.parametrize("day", range(1, 32))
    def test_round_trip(self, day: int) -> None:
        spoken = date_to_words(f"2026-10-{day:02d}")
        assert spoken is not None
        # `_normalize` first, exactly as `parse()` does: `date_to_words` emits
        # U+02BC and every pattern in this package is written against the
        # folded `'`. Skipping it here passed 25 of 31 days and hid the six
        # that carry an apostrophe.
        rewritten, spans = _ordinals_to_digits(_normalize(spoken))
        assert rewritten.split()[0] == str(day), f"{spoken!r} → {rewritten!r}"
        assert spans, "a rewrite that changed the text reported no span"


class TestTheGateStillHolds:
    """A word ordinal goes through the same door «на 14» does, never around it."""

    def test_an_ordinal_needs_the_bots_question(self) -> None:
        assert day_of("на чотирнадцяте", bot="У якому місті вам зручніше?") is None

    def test_a_month_name_does_not_need_it(self) -> None:
        """An explicit month names one specific day; the gate is for bare ones."""
        assert day_of("на чотирнадцяте вересня", bot="У якому місті?") == 14

    def test_a_second_number_still_refuses(self) -> None:
        """«095 чотирнадцяте» is a phone number with a day glued to it."""
        assert day_of("095 чотирнадцяте") is None


class TestTwoWordDays:
    @pytest.mark.parametrize(
        ("said", "day"),
        [
            ("двадцять перше", 21),
            ("на двадцять третє вересня", 23),
            ("тридцять першого", 31),
            ("на двадцять пʼяте", 25),
            ("на двадцать пятое", 25),
        ],
    )
    def test_it_resolves(self, said: str, day: int) -> None:
        assert day_of(said) == day

    @pytest.mark.parametrize("nonsense", ["на двадцять одинадцяте", "на тридцять третє"])
    def test_an_impossible_day_is_left_as_words(self, nonsense: str) -> None:
        assert _ordinals_to_digits(nonsense) == (nonsense, ())


class TestRussianForms:
    @pytest.mark.parametrize(
        ("said", "day"),
        [("на первое октября", 1), ("на третьего", 3), ("на восьмое", 8)],
    )
    def test_it_resolves(self, said: str, day: int) -> None:
        assert day_of(said) == day


class TestTheSpansPointAtTheCallersOwnWords:
    """`spans` are in the **input**'s coordinates, never the rewritten string's.

    Nothing outside this package reads a date outcome's spans today, so this
    invariant is invisible in production and a mutation that reported «на 14»'s
    offsets against «на чотирнадцяте» survived the whole suite. It is pinned
    here because `compound_parse` masks consumed spans before reading a wheel
    diameter out of what is left, and offsets that point two characters into a
    word would blank the wrong text the day that path is wired up.
    """

    def outcome(self, text: str) -> ParseOutcome:
        return PARSER.parse(
            ParseContext(customer_text=text, last_bot_utterance=ASKED, state=FsmState.DATE, now=NOW)
        )

    @pytest.mark.parametrize(
        ("said", "word"),
        [
            ("на чотирнадцяте", "чотирнадцяте"),  # bare day — spans come from _bare_day
            ("на чотирнадцяте вересня", "чотирнадцяте"),  # month path
            ("на двадцять третє", "двадцять третє"),  # two words, one span
        ],
    )
    def test_the_span_slices_back_to_the_ordinal(self, said: str, word: str) -> None:
        outcome = self.outcome(said)
        assert outcome.status == "value"
        assert len(outcome.spans) == 1
        start, end = outcome.spans[0]
        assert said[start:end] == word


class TestTheVocabularyCannotGrowIntoAnAmbiguity:
    def test_no_stem_can_complete_inside_another(self) -> None:
        """What makes «п'ятнадцяте» the 15th and not the 5th.

        `_DAY_ORDINAL_ALT` is emitted in declaration order and the regex engine
        backtracks out of «п'ят» because «надцяте» is not an ending. Sorting the
        alternation longest-first *looks* like the safeguard, but removing the
        sort changed no outcome anywhere in the suite.

        This is the conservative form of what backtracking relies on: a shorter
        stem must not leave a tail that *starts* like an ending. The trailing
        `\\b` would often rescue such a stem anyway — «тр» + «е» inside «третє»
        dies on the word boundary — so the rule refuses a class of additions
        slightly wider than the ones that would actually misread. That is the
        trade we want in front of a field that books an appointment.
        """
        stems = [stem for stem, _ in _DAY_ORDINAL_STEMS]
        for short in stems:
            for long in stems:
                if long == short or not long.startswith(short):
                    continue
                tail = long[len(short) :]
                offender = next((e for e in _DAY_ORDINAL_ENDINGS if tail.startswith(e)), None)
                assert offender is None, (
                    f"{short!r} completes inside {long!r} via {offender!r}: "
                    f"«{long}е» would read as {dict(_DAY_ORDINAL_STEMS)[short]}"
                )


class TestNothingDigitsUsedToDoChanged:
    @pytest.mark.parametrize(
        ("said", "day"),
        [("на 14", 14), ("14 вересня", 14), ("11.09", 11), ("на понеділок", 14)],
    )
    def test_it_still_resolves(self, said: str, day: int) -> None:
        assert day_of(said) == day

    @pytest.mark.parametrize("not_a_date", ["на 6 вечора", "о 14:00", "найближча"])
    def test_it_still_refuses(self, not_a_date: str) -> None:
        assert day_of(not_a_date) is None
