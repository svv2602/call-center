"""`time_parser` — only a slot that was actually offered (Wave 5-A).

Regression seed: call 2026-09-07. `get_fitting_slots` had returned 14:20, the
bot read a truncated list, the client answered «14 это 20» and the bot replied
«Слота на чотирнадцяту двадцять немає» — then listed the full set including
14:20. Wave 14 added the pin; this parser is the same guard on the FSM side,
where the seam still writes `time_hint → "time"` with no check at all.
"""

from __future__ import annotations

import uuid

import pytest

from src.agent.parsers import ParseContext
from src.agent.parsers.time_parser import PARSER
from src.core.call_session import CallSession

OFFERED = ["09:00", "14:00", "14:20", "16:40"]
BOT_LISTED = "Є вільно: 09:00, 14:00, 14:20, 16:40. Який час зручніше?"


def session_with(slots: list[str], *, pinned: str | None = None) -> CallSession:
    session = CallSession(channel_uuid=uuid.uuid4())
    session.fitting_slots_offered = [{"date": "2026-09-08", "time": t} for t in slots]
    session.selected_fitting_time = pinned
    return session


def ctx(text: str, slots: list[str], *, bot: str = BOT_LISTED, pinned: str | None = None):
    return ParseContext(
        customer_text=text,
        last_bot_utterance=bot,
        session=session_with(slots, pinned=pinned),
    )


class TestSlotFromTheOfferedList:
    def test_exact_slot(self) -> None:
        outcome = PARSER.parse(ctx("давайте на 14:20", OFFERED))
        assert outcome.status == "value"
        assert outcome.value == "14:20"

    def test_the_call_that_caused_wave_14(self) -> None:
        outcome = PARSER.parse(ctx("14 это 20", OFFERED))
        assert outcome.value == "14:20"

    def test_bare_hour_after_the_bot_read_the_list(self) -> None:
        outcome = PARSER.parse(ctx("давайте на дев'яту", OFFERED))
        assert outcome.status == "value"
        assert outcome.value == "09:00"

    def test_bare_hour_is_not_widened_once_a_slot_is_pinned(self) -> None:
        outcome = PARSER.parse(ctx("на дев'яту", OFFERED, pinned="14:20"))
        assert outcome.status != "value"


class TestSlotOutsideTheList:
    def test_time_named_but_never_offered(self) -> None:
        outcome = PARSER.parse(ctx("а можна о 18:00?", OFFERED))
        assert outcome.status == "unresolved"
        assert outcome.value is None

    def test_a_slot_from_a_different_list_is_not_accepted(self) -> None:
        """The guard: validation is against these slots, not against any time."""
        outcome = PARSER.parse(ctx("на 14:20", ["09:00", "16:40"]))
        assert outcome.value != "14:20"
        assert outcome.status == "unresolved"


class TestNoSlotsYet:
    def test_empty_offer_list_is_always_unresolved(self) -> None:
        """The normal first-turn path: nothing has been offered yet."""
        outcome = PARSER.parse(ctx("на другу", []))
        assert outcome.status == "unresolved"
        assert outcome.value is None

    def test_empty_offer_list_never_yields_a_value(self) -> None:
        outcome = PARSER.parse(ctx("14:20", []))
        assert outcome.status != "value"
        assert outcome.value is None


class TestNothingSaidAboutTime:
    def test_not_mentioned(self) -> None:
        outcome = PARSER.parse(ctx("білий Nissan", OFFERED))
        assert outcome.status == "not_mentioned"
        assert outcome.value is None

    # --- Wave 6-A additions ------------------------------------------------

    @pytest.mark.parametrize(
        "text", ["R16", "Оболонь, Київ", "мене звати Олена", "так, підтверджую"]
    )
    def test_silence_about_time_with_a_full_slot_list(self, text: str) -> None:
        """`not_mentioned` needs a different re-ask from «that slot is taken»."""
        outcome = PARSER.parse(ctx(text, OFFERED))
        assert outcome.status == "not_mentioned"
        assert outcome.value is None
        assert outcome.confidence == 0.0

    def test_empty_text(self) -> None:
        assert PARSER.parse(ctx("   ", OFFERED)).status == "not_mentioned"


class TestHourOnlyWidening:
    """Wave 6-A additions — `allow_hour_only` and what turns it off."""

    def test_a_bare_hour_needs_the_bot_to_have_asked_or_listed(self) -> None:
        """Neither signal present — «9» is as likely an R9 diameter.

        The bot utterance used to be «Який час зручніше?», which prod shows is
        one of the two ways it asks Krok 4 bare (2026-09-10), so that string
        now widens on purpose. A turn about the car is the real negative.
        """
        outcome = PARSER.parse(
            ctx("на дев'яту", OFFERED, bot="Яка марка вашого автомобіля?")
        )
        assert outcome.status != "value", "later in the dialog «9» may be a diameter"

    def test_a_bare_hour_after_the_question_alone_is_enough(self) -> None:
        """Call `431e60fb` (2026-09-10): the bot asked without reading the list."""
        outcome = PARSER.parse(ctx("о 9", OFFERED, bot="О котрій зручніше?"))
        assert outcome.status == "value"
        assert outcome.value == "09:00"

    def test_a_pinned_slot_turns_the_widening_off(self) -> None:
        """Wave 14: once a slot is pinned, a bare hour must not silently move it."""
        assert PARSER.parse(ctx("на дев'яту", OFFERED, pinned="14:20")).status != "value"
        assert PARSER.parse(ctx("на дев'яту", OFFERED)).status == "value"

    def test_the_pin_is_never_overwritten_by_the_parser(self) -> None:
        """This parser reports; the engine writes."""
        context = ctx("на 14:20", OFFERED, pinned="09:00")
        PARSER.parse(context)
        assert context.session.selected_fitting_time == "09:00"

    def test_a_shared_hour_resolves_to_the_slot_on_the_hour(self) -> None:
        """«на 14 годину» names 14:00, though 14:20 shares the hour."""
        outcome = PARSER.parse(ctx("на 14 годину", OFFERED))
        assert outcome.status == "value"
        assert outcome.value == "14:00"

    def test_an_hour_with_nothing_on_it_stays_ambiguous(self) -> None:
        """Two slots in the hour and none of them on it — still a coin flip."""
        outcome = PARSER.parse(ctx("на 14 годину", ["14:20", "14:40"]))
        assert outcome.status == "unresolved"
        assert outcome.value is None

    def test_named_minutes_are_not_rounded_to_the_hour(self) -> None:
        """«14:40» is not on offer, and 14:00 is not a substitute for it."""
        outcome = PARSER.parse(ctx("на 14:40", OFFERED))
        assert outcome.status == "unresolved"
        assert outcome.value is None


class TestFeminineOrdinalHours:
    """The Wave 6-A gap, now closed.

    `compound_parse._HOUR_ORDINALS` has understood the feminine-ordinal form
    («на другу» = 14:00, «о шостій» = 18:00) since Wave 6-A, and it is what
    `_mentions_time` consults. `time_detect._NUM_WORDS` — the vocabulary that
    actually pins a slot — carried only the *cardinal* stems, so six of the
    twenty ordinals (перш, друг, трет, четверт, шост, сьом) were heard as talk
    about time and then matched against nothing.

    The two tables are now checked against each other below rather than kept in
    step by hand.
    """

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("на другу", "14:00"),
            ("о шостій", "18:00"),
            ("о третій", "15:00"),
            ("на четверту", "16:00"),
            ("до третьої", "15:00"),
            ("шоста година", "18:00"),
        ],
    )
    def test_ordinal_hours_pin_the_offered_slot(self, text: str, expected: str) -> None:
        outcome = PARSER.parse(ctx(text, ["09:00", "14:00", "15:00", "16:00", "18:00"]))
        assert outcome.status == "value"
        assert outcome.value == expected

    @pytest.mark.parametrize("text", ["на дев'яту", "на десяту"])
    def test_ordinals_sharing_a_cardinal_stem_do_resolve(self, text: str) -> None:
        outcome = PARSER.parse(ctx(text, ["09:00", "10:00", "14:20"]))
        assert outcome.status == "value"

    def test_the_two_vocabularies_now_agree(self) -> None:
        """Every ordinal `_mentions_time` recognises can also pin a slot."""
        from src.agent.compound_parse import _HOUR_ORDINALS
        from src.agent.time_detect import _NUM_WORDS

        missing = [
            stem
            for stem, _hour in _HOUR_ORDINALS
            if not any(
                stem == word or word.startswith(stem) or stem.startswith(word)
                for word in _NUM_WORDS
            )
        ]
        assert missing == []

    @pytest.mark.parametrize("text", ["сьомого вересня", "третього вересня", "другого числа"])
    def test_the_masculine_genitive_is_a_date_not_an_hour(self, text: str) -> None:
        """The reason the endings are spelled out instead of the stems admitted.

        «сьомого вересня» is the 7th of September. A bare `сьом` stem would
        make it the number 7, and the 12-hour reading would then offer it as
        19:00 — a slot the caller never asked for, on a turn that was about the
        date.
        """
        from src.agent.time_detect import _extract_numbers

        assert _extract_numbers(text) == []
        assert PARSER.parse(ctx(text, ["09:00", "15:00", "19:00"])).value is None

    def test_shosta_ranku_is_a_part_of_day_not_an_hour(self) -> None:
        """«шоста ранку» reads as «ранок», and 06:00 is not reachable from it.

        `_detect_time_hint` grades a part of day `0.6` — a preference, not a
        time. `detect_time_choice` does now read the 6, but only as the
        afternoon hour it maps to, so an early-morning slot on offer is not
        matched by it.
        """
        from src.agent.compound_parse import _detect_time_hint

        assert _detect_time_hint("шоста ранку").value == "ранок"
        outcome = PARSER.parse(ctx("шоста ранку", ["06:00", "09:00", "14:20"]))
        assert outcome.status == "unresolved"
        assert outcome.value is None


class TestStatuses:
    def test_all_three_are_reachable(self) -> None:
        statuses = {
            PARSER.parse(ctx(text, OFFERED)).status
            for text in ("давайте на 14:20", "а можна о 18:00?", "білий Nissan")
        }
        assert statuses == {"value", "unresolved", "not_mentioned"}
