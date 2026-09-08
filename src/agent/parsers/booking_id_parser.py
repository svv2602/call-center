"""`booking_id_parser` — CANCEL_INTERRUPT. Minimal by necessity, not by choice.

CANCEL_INTERRUPT reads out «У вас 2 записи: 1) …; 2) …. Який скасовуємо?» and
the caller answers «перший» or «на п'ятницю». Turning that into a `booking_id`
needs the list that was read out — and **nothing on `CallSession` holds it**.
`interrupts.py` gets the bookings straight from `get_customer_bookings` and
keeps only `pending_cancel_action` («awaiting_selection» / «awaiting_confirmation:<id>»)
between turns. There is no `bookings_offered` counterpart to
`fitting_slots_offered`.

So this parser does the only honest thing available synchronously:

* a literal booking id in the utterance (a 1C GUID) is a value — validated by
  the same rule `interrupts._is_valid_booking_id` and the `cancel_fitting`
  guard apply, so a placeholder like «00000000-…» is rejected here too;
* an ordinal selection («перший», «другий», «1») is recognised as *mentioned*
  and returned `unresolved`, because the index has nothing to index into. It
  is not converted to an integer: `field_name` is `booking_id`, and writing an
  ordinal into it is the same wrong-type defect the station landmark is kept
  out of `station_id` for;
* anything else is `not_mentioned`.

Wave 6-B debt: snapshot the bookings on the session (mirroring
`fitting_slots_offered`, which exists for exactly this reason on the slot
side), and this parser resolves «перший» without an LLM.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import TYPE_CHECKING

from src.agent.parsers.base import NOT_MENTIONED, ParseOutcome, graded, unresolved

if TYPE_CHECKING:
    from src.agent.parsers.base import ParseContext

logger = logging.getLogger(__name__)

#: An explicit id needs no interpretation.
_ID_CONFIDENCE = 1.0

_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)

#: Same placeholders `interrupts._is_valid_booking_id` rejects.
_PLACEHOLDER_IDS: frozenset[str] = frozenset({
    "00000000-0000-0000-0000-000000000000",
})

# «перший запис», «другий», «1)», «номер 2» — an index into a list that was
# read out loud. Ukrainian and Russian ordinals, stem-matched.
_ORDINAL_RE = re.compile(
    r"\b(?:перш|втор|друг|трет|четверт|останн|последн|перв)\w*|"
    r"\bномер\s*\d|\b[1-9]\s*[)\.]",
)


def _is_valid_booking_id(candidate: str) -> bool:
    trimmed = candidate.strip()
    if not trimmed or trimmed.lower() in _PLACEHOLDER_IDS:
        return False
    if all(char in "0-" for char in trimmed):
        return False
    try:
        uuid.UUID(trimmed)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


class BookingIdParser:
    """CANCEL_INTERRUPT."""

    name = "booking_id_parser"
    field_name = "booking_id"
    aresolve = None

    def parse(self, ctx: ParseContext) -> ParseOutcome:
        text = (ctx.customer_text or "").strip()
        if not text:
            return NOT_MENTIONED

        match = _UUID_RE.search(text)
        if match and _is_valid_booking_id(match.group(0)):
            return graded(match.group(0), _ID_CONFIDENCE, (match.span(),))

        ordinal = _ORDINAL_RE.search(text.lower())
        if ordinal:
            logger.debug(
                "booking_id_parser: ordinal selection %r has no booking list on the "
                "session to index — unresolved (Wave 6-B debt)",
                ordinal.group(0),
            )
            return unresolved(spans=(ordinal.span(),))

        return NOT_MENTIONED


PARSER = BookingIdParser()
