"""`booking_id_parser` — CANCEL_INTERRUPT. Minimal by necessity.

CANCEL_INTERRUPT reads out «У вас 2 записи: 1) …; 2) …. Який скасовуємо?» and
the caller answers «перший». Turning that into a `booking_id` needs the list
that was read out, and nothing on `CallSession` holds it: `interrupts.py` keeps
only `pending_cancel_action` between turns, and there is no `bookings_offered`
counterpart to `fitting_slots_offered`.

So the parser recognises an ordinal as *mentioned* and stops there. README §4
hole, handed to Wave 6-B — `TestOrdinalSelectionIsUnresolved` pins it so the
fix breaks this file rather than passing through it.

The thing that must never happen is the ordinal being written into
`booking_id` as an integer: that is the same wrong-type defect the station
landmark is kept out of `station_id` for, and `cancel_fitting` would be handed
a «2».
"""

from __future__ import annotations

import pytest

from src.agent.parsers import ParseContext
from src.agent.parsers.booking_id_parser import _ID_CONFIDENCE, PARSER

#: A well-formed 1C booking GUID.
REAL_ID = "550e8400-e29b-41d4-a716-446655440000"
#: The placeholder `interrupts._is_valid_booking_id` rejects.
PLACEHOLDER_ID = "00000000-0000-0000-0000-000000000000"


def ctx(text: str) -> ParseContext:
    return ParseContext(customer_text=text)


class TestLiteralBookingId:
    def test_a_guid_is_a_value(self) -> None:
        outcome = PARSER.parse(ctx(f"скасуйте {REAL_ID}"))
        assert outcome.status == "value"
        assert outcome.value == REAL_ID
        assert outcome.confidence == _ID_CONFIDENCE

    def test_the_span_covers_the_id(self) -> None:
        outcome = PARSER.parse(ctx(f"скасуйте {REAL_ID}"))
        assert len(outcome.spans) == 1
        start, end = outcome.spans[0]
        assert f"скасуйте {REAL_ID}"[start:end] == REAL_ID

    def test_uppercase_guid_is_accepted(self) -> None:
        assert PARSER.parse(ctx(REAL_ID.upper())).status == "value"

    def test_the_placeholder_is_rejected(self) -> None:
        """Same rule as `interrupts._is_valid_booking_id` and the tool guard."""
        outcome = PARSER.parse(ctx(PLACEHOLDER_ID))
        assert outcome.status == "not_mentioned"
        assert outcome.value is None

    def test_a_malformed_guid_is_not_a_value(self) -> None:
        outcome = PARSER.parse(ctx("550e8400-e29b-41d4-a716"))
        assert outcome.status == "not_mentioned"


class TestOrdinalSelectionIsUnresolved:
    """README §4 hole — the index has nothing to index into. Wave 6-B debt."""

    @pytest.mark.parametrize(
        "text",
        [
            "другу",
            "перший",
            "перший запис",
            "другий",
            "останній",
            "1)",
            "номер 2",
            "первый",  # RU — callers switch language mid-call
        ],
    )
    def test_recognised_as_mentioned_but_not_resolved(self, text: str) -> None:
        outcome = PARSER.parse(ctx(text))
        assert outcome.status == "unresolved"
        assert outcome.value is None
        assert outcome.spans, "the ordinal is located, just not resolvable"

    def test_the_ordinal_is_never_written_into_the_field_as_a_number(self) -> None:
        """`field_name` is `booking_id`; an index is not one."""
        for text in ("перший", "другий", "1)", "номер 2"):
            outcome = PARSER.parse(ctx(text))
            assert not isinstance(outcome.value, int)
            assert outcome.value is None

    def test_no_booking_snapshot_exists_on_the_session_yet(self) -> None:
        """The reason the hole exists, asserted so it cannot be closed silently.

        When Wave 6-B adds a `bookings_offered` snapshot mirroring
        `fitting_slots_offered`, this assertion fails and the parser above has
        to grow the resolution path.
        """
        import uuid

        from src.core.call_session import CallSession

        session = CallSession(channel_uuid=uuid.uuid4())
        assert hasattr(session, "fitting_slots_offered")
        assert not hasattr(session, "bookings_offered")


class TestNotMentioned:
    @pytest.mark.parametrize(
        "text",
        ["", "   ", "білий Nissan", "завтра о 14:00", "скасуйте, будь ласка"],
    )
    def test_nothing_selecting_a_booking(self, text: str) -> None:
        outcome = PARSER.parse(ctx(text))
        assert outcome.status == "not_mentioned"
        assert outcome.value is None


class TestContract:
    def test_registry_identity(self) -> None:
        assert PARSER.name == "booking_id_parser"
        assert PARSER.field_name == "booking_id"

    def test_no_aresolve(self) -> None:
        """Resolution would need the booking list, not the network."""
        assert PARSER.aresolve is None

    def test_all_three_statuses_are_reachable(self) -> None:
        statuses = {PARSER.parse(ctx(text)).status for text in (REAL_ID, "перший", "білий Nissan")}
        assert statuses == {"value", "unresolved", "not_mentioned"}
