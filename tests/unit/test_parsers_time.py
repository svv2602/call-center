"""`time_parser` — only a slot that was actually offered (Wave 5-A).

Regression seed: call 2026-09-07. `get_fitting_slots` had returned 14:20, the
bot read a truncated list, the client answered «14 это 20» and the bot replied
«Слота на чотирнадцяту двадцять немає» — then listed the full set including
14:20. Wave 14 added the pin; this parser is the same guard on the FSM side,
where the seam still writes `time_hint → "time"` with no check at all.
"""

from __future__ import annotations

import uuid

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
