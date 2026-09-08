"""`station_parser` — STATION. A landmark is a search key, not a `station_id`.

`parse()` wraps `compound_parse._detect_station_hint`, which returns the
canonical landmark label the caller used («Оболонь», «Харківське шосе», «ЖМ
Перемога»). The detector is not touched: its patterns are ordered
longest-stem-first at build time so «жм перемог» wins over «перемог» and
«харківськ» is tried before the bare city stem.

**The sync pass can never reach `status="value"`, and that is the point.**
This parser's `field_name` is `station_id`; a landmark is not one. Pinning it
would let the FSM skip STATION with a value `book_fitting` cannot use — the
exact reason `station_hint` is deliberately absent from
`COMPOUND_TO_FSM_FIELD` today, and the same defect class as `c8c6601`. So the
landmark comes back as `unresolved` with the hint visible in `value` and a
confidence held below the apply threshold. In `shadow` that is the end of it:
shadow sees the hint and never a `station_id`, as designed (§3.2 rule 3).

`aresolve()` turns the hint into an id from `session.fitting_stations_seen` —
the snapshot `get_fitting_stations` already wrote when the state was entered.
The spec (§3.5) names those session fields as this parser's context, and
resolving from them needs no network at all. Calling the tool router directly
is **not** possible from here: `ParseContext` carries a DB connection and no
tool router, so `get_fitting_stations(query=…)` has no route into a parser.
That gap is recorded for Wave 6-B rather than papered over with a global
import.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.agent.compound_parse import _detect_station_hint, _normalize
from src.agent.parsers.base import NOT_MENTIONED, ParseOutcome, graded, unresolved

if TYPE_CHECKING:
    from src.agent.parsers.base import ParseContext

logger = logging.getLogger(__name__)

#: Held below the apply threshold on purpose — see the module docstring. The
#: detector's own `0.9` describes how sure we are of the *landmark*, not of a
#: `station_id`, and this field is the id.
_HINT_CONFIDENCE = 0.6

#: A single station matching the landmark in the list the bot already read out.
_RESOLVED_CONFIDENCE = 1.0


def _matches(station: dict, needle: str) -> bool:
    """True when the landmark appears in the station's district or address."""
    for key in ("district", "address", "name"):
        value = station.get(key)
        if isinstance(value, str) and needle in value.lower():
            return True
    return False


async def _aresolve_station(ctx: ParseContext, outcome: ParseOutcome) -> ParseOutcome:
    """Landmark → `station_id`, using the stations already offered.

    Ambiguity is left unresolved rather than guessed: «Перемоги» exists in two
    cities, and picking one of them silently is how a caller ends up driving to
    the wrong address.
    """
    if not isinstance(outcome.value, str) or not outcome.value:
        return outcome
    session = ctx.session
    stations = list(getattr(session, "fitting_stations_seen", None) or []) if session else []
    if not stations:
        logger.debug("station_parser.aresolve: no stations in session — hint kept unresolved")
        return outcome

    needle = outcome.value.lower()
    hits = [s for s in stations if isinstance(s, dict) and _matches(s, needle)]
    if len(hits) != 1:
        logger.debug(
            "station_parser.aresolve: %r matched %d of %d offered stations — unresolved",
            outcome.value, len(hits), len(stations),
        )
        return outcome

    station_id = hits[0].get("id")
    if not station_id:
        logger.warning(
            "station_parser.aresolve: station %r matched %r but carries no id",
            hits[0], outcome.value,
        )
        return outcome
    return graded(str(station_id), _RESOLVED_CONFIDENCE)


class StationParser:
    """STATION. Sync gives a hint; `aresolve` gives the id."""

    name = "station_parser"
    field_name = "station_id"
    aresolve = staticmethod(_aresolve_station)

    def parse(self, ctx: ParseContext) -> ParseOutcome:
        text = (ctx.customer_text or "").strip()
        if not text:
            return NOT_MENTIONED
        hit = _detect_station_hint(_normalize(text))
        if hit is None:
            return NOT_MENTIONED
        return unresolved(confidence=_HINT_CONFIDENCE, spans=hit.spans, value=hit.value)


PARSER = StationParser()
