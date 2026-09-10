"""`time_parser` — TIME. Only a slot that was actually offered.

The other half of the same latent P0 as `date_parser`: the seam in
`src/core/pipeline.py` maps `time_hint → "time"` with no check against
`session.fitting_slots_offered`, so «14:00» — a literal, graded `1.0` by the
broad pass — is pinned as the booking time whether or not that slot exists.
Call 2026-09-07 is what this costs when the two disagree: the bot read a
truncated list, the client answered «14 это 20», and the bot denied a slot it
went on to list a moment later.

`detect_time_choice` is the authority and is not modified. It **can pin an
existing slot but never invent one** (Wave 14), and this parser inherits that
property by construction: whatever comes back is a member of the offered list.

Three inputs, all taken from the context rather than passed in (§3.2) — the
same three the Wave 14 block in the pipeline assembles:

* `session.fitting_slots_offered` → the `HH:MM` values. The session stores
  `{"date": …, "time": …}` dicts, so the times are projected out here;
* `time_detect.hour_only_allowed(ctx.last_bot_utterance)` → `allow_hour_only`.
  A bare «на десяту» is unambiguous when the bot has just read the list out or
  has just asked which hour; anywhere else «17» is far more likely a wheel
  diameter;
* `session.selected_fitting_time` → the already pinned slot, which narrows the
  widening exactly as the pipeline does. The pin itself is never overwritten
  here: this parser reports, the engine writes.

An empty `fitting_slots_offered` yields `unresolved`, always. On the opening
turn there are no slots yet — that is the normal path, not a failure, so it is
logged at DEBUG. It stays `unresolved` rather than `not_mentioned` because the
one thing this parser must never do is let a time through unvalidated.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.agent.compound_parse import _detect_time_hint, _normalize
from src.agent.parsers.base import NOT_MENTIONED, ParseOutcome, graded, unresolved
from src.agent.time_detect import detect_time_choice, hour_only_allowed

if TYPE_CHECKING:
    from src.agent.parsers.base import ParseContext

logger = logging.getLogger(__name__)

#: The pick is a member of the offered list — there is nothing left to doubt.
_PICKED_CONFIDENCE = 1.0


def _offered_times(ctx: ParseContext) -> list[str]:
    """`HH:MM` values out of the session's slot snapshot."""
    session = ctx.session
    slots = list(getattr(session, "fitting_slots_offered", None) or []) if session else []
    return [
        slot["time"]
        for slot in slots
        if isinstance(slot, dict) and isinstance(slot.get("time"), str) and slot["time"]
    ]


def _mentions_time(text: str) -> bool:
    """Did the caller talk about a time at all?

    Reuses the broad pass's own extractor instead of a second regex — the
    §3.1 invariant is one detector per field, two call sites.
    """
    return _detect_time_hint(_normalize(text)) is not None


class TimeParser:
    """TIME."""

    name = "time_parser"
    field_name = "time"
    aresolve = None

    def parse(self, ctx: ParseContext) -> ParseOutcome:
        text = (ctx.customer_text or "").strip()
        if not text:
            return NOT_MENTIONED

        offered = _offered_times(ctx)
        if not offered:
            logger.debug(
                "time_parser: no slots offered yet — nothing to validate against, unresolved"
            )
            return unresolved()

        session = ctx.session
        pinned = getattr(session, "selected_fitting_time", None) if session else None
        picked = detect_time_choice(
            text,
            offered,
            allow_hour_only=(not pinned and hour_only_allowed(ctx.last_bot_utterance)),
        )
        if picked:
            return graded(picked, _PICKED_CONFIDENCE)

        if _mentions_time(text):
            # A time was named and it is not in the list — the caller has to
            # choose again. Distinct from silence, and phrased differently.
            logger.debug("time_parser: time mentioned but not among %d offered slots", len(offered))
            return unresolved()
        return NOT_MENTIONED


PARSER = TimeParser()
