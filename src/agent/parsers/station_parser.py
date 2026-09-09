"""`station_parser` — STATION. A landmark is a search key, not a `station_id`.

`parse()` wraps `compound_parse._detect_station_hint`, which returns the
canonical landmark label the caller used («Оболонь», «Харківське шосе», «ЖМ
Перемога»). The detector is not touched: its patterns are ordered
longest-stem-first at build time so «жм перемог» wins over «перемог» and
«харківськ» is tried before the bare city stem.

**`parse()` can never reach `status="value"`, and that is the point.**
This parser's `field_name` is `station_id`; a landmark is not one. Pinning it
would let the FSM skip STATION with a value `book_fitting` cannot use — the
exact reason `station_hint` is deliberately absent from
`COMPOUND_TO_FSM_FIELD` today, and the same defect class as `c8c6601`. So the
landmark comes back as `unresolved` with the hint visible in `value` and a
confidence held below the apply threshold.

:func:`resolve_station_from_session` is the second half, and the only route to
`status="value"`: it turns the hint into an id using
`session.fitting_stations_seen` — the snapshot `get_fitting_stations` already
wrote when the state was entered. The spec (§3.5) names those session fields as
this parser's context.

Why this one is legal in shadow (Wave 6-C)
------------------------------------------
§3.2 rule 3 — «`aresolve` never runs in shadow» — is a rule about *network*,
not about the word `aresolve`. It exists to keep «no await → no network» true
for `_run_fsm_deterministic_step`. Resolving a landmark reads a dict that is
already in the session: no connection, no tool router, nothing to await. So the
seam calls :func:`resolve_station_from_session` **synchronously**, in shadow as
in live, and shadow does now see a `station_id`.

`brand_parser` is the contrasting case and stays live-only: its resolver needs
`ctx.conn` for the alias table, that is real I/O, and `ParseContext.conn` is
pinned to `None` in the seam precisely to forbid it.

Until Wave 6-C the id had no reachable path at all — `aresolve` had zero call
sites, so STATION was a structural dead end (12 of 16 replayed calls died
there). Calling the tool router from here is still **not** possible:
`ParseContext` carries a DB connection and no tool router, so
`get_fitting_stations(query=…)` has no route into a parser. It does not need
one — the snapshot is enough.
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


def _chosen_city(session: object | None) -> str:
    """The city already pinned for this call, normalised — or `""`.

    `session.fsm_filled_fields["city"]` is the only place a chosen city lives:
    `CallSession` has no `fitting_city`, and `render_context` derives its own
    `city` from the *picked station*, which is the value this resolver is being
    asked to produce. Whatever sits in `fsm_filled_fields` came through
    `APPLY_THRESHOLD` (`map_compound_fields_to_fsm` drops anything below it), so
    a low-confidence STT guess can not narrow anything here.
    """
    filled = getattr(session, "fsm_filled_fields", None) or {}
    value = filled.get("city") if isinstance(filled, dict) else None
    if not isinstance(value, str):
        return ""
    return _normalize(value).strip()


def _station_city(station: dict) -> str:
    """The station's own city, normalised — `""` when it does not say."""
    value = station.get("city")
    return _normalize(value).strip() if isinstance(value, str) else ""


def _in_city(station: dict, city: str) -> bool:
    """True when the station sits in `city`. Default-deny on anything unclear.

    Comparison mirrors `src/main.py:2483` (`_normalize_city` + containment both
    ways), which is how `get_fitting_stations` already filters the very list
    that ends up in `fitting_stations_seen` — «Дніпро» has to keep matching
    «Дніпропетровськ». That helper is not imported: `src/main.py` is the FastAPI
    application and it imports the parser package, so reaching back into it
    would be circular.

    Default-deny on **both** sides: a station that does not say which city it
    is in belongs to none of them, and nothing belongs to a blank city.
    Containment makes `""` a substring of everything, so without those two
    guards the filter would match every station instead of none — a hole rather
    than a guard (§1.6), and one that fails silently.
    """
    other = _station_city(station)
    if not city or not other:
        return False
    return other in city or city in other


def resolve_station_from_session(ctx: ParseContext, outcome: ParseOutcome) -> ParseOutcome:
    """Landmark → `station_id`, using the stations already offered.

    Ambiguity is left unresolved rather than guessed: «Перемоги» exists in two
    cities, and picking one of them silently is how a caller ends up driving to
    the wrong address.

    **Narrowing by the chosen city is not guessing** (Wave 6-D, phase 02). When
    the caller has already named a city, the stations in every *other* city were
    never candidates — dropping them removes an ambiguity that does not exist
    rather than resolving one that does. Call `b394f6c1`: «перемога» matched two
    stations, the flow died on STATION, and the city had been pinned for six
    turns. Ambiguity *inside* one city is still refused, which is the rule the
    paragraph above states.

    With no city pinned the behaviour is byte-for-byte what it was: this must
    not turn into a new «city first» precondition.

    Once a city *is* pinned the filter is default-deny over the whole snapshot:
    a station that does not say which city it is in is dropped, exactly like one
    that names a different city. There is no «the snapshot is too sparse to
    narrow, so keep everything» branch — that shape is a hole, not a guard
    (§1.6), and it is the escape hatch this project has already been bitten by
    twice (waves 15 and 16). Prod cannot produce it either: `main.py:2516` writes
    `city` into every entry unconditionally, and 603 stored payloads / 1026
    stations back to 2026-07-10 have it on all of them. If 1C ever did blank the
    field the whole snapshot would be refused and the call would reach an
    operator — never a wrong address — and the WARNING below says so out loud.

    Synchronous on purpose. Every input comes from
    `ctx.session.fitting_stations_seen`, so there is nothing to await — see the
    module docstring for why that makes this callable from the shadow seam
    while a *network* resolver stays shut.
    """
    if not isinstance(outcome.value, str) or not outcome.value:
        return outcome
    session = ctx.session
    stations = list(getattr(session, "fitting_stations_seen", None) or []) if session else []
    if not stations:
        logger.debug("station_parser.aresolve: no stations in session — hint kept unresolved")
        return outcome

    city = _chosen_city(session)
    if city:
        in_city = [s for s in stations if isinstance(s, dict) and _in_city(s, city)]
        if not in_city:
            # Distinct from «unresolved»: the snapshot holds no station in the
            # city the caller picked. Call `bd95036c` asked for Черкаси three
            # times against a Дніпро snapshot and the log read exactly like a
            # caller mumbling nonsense. Wave 6-E needs to tell the two apart, so
            # this gets its own marker and WARNING — a DEBUG line would be
            # filtered out in prod, which is where the calls are.
            logger.warning(
                "fsm_station_city_mismatch: chosen city %r has none of the %d "
                "offered stations (%r) — hint %r kept unresolved",
                city,
                len(stations),
                sorted(
                    {
                        s.get("city")
                        for s in stations
                        if isinstance(s, dict) and isinstance(s.get("city"), str)
                    }
                ),
                outcome.value,
            )
            return outcome
        stations = in_city

    needle = outcome.value.lower()
    hits = [s for s in stations if isinstance(s, dict) and _matches(s, needle)]
    if len(hits) != 1:
        logger.debug(
            "station_parser.aresolve: %r matched %d of %d offered stations "
            "(narrowed by city: %s) — unresolved",
            outcome.value, len(hits), len(stations), city or "no",
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


async def _aresolve_station(ctx: ParseContext, outcome: ParseOutcome) -> ParseOutcome:
    """`FieldParser.aresolve` shape over :func:`resolve_station_from_session`.

    A thin wrapper, and deliberately nothing more: the protocol says `aresolve`
    is awaitable, the resolution itself is not I/O. Two copies of the matching
    rules is how «Перемоги» gets picked in one of them and refused in the other.
    """
    return resolve_station_from_session(ctx, outcome)


class StationParser:
    """STATION. `parse` gives a hint; the resolver gives the id."""

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
