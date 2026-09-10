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
pinned to `None` in the seam precisely to forbid it. Wave 20 gives it a step of
its own in front of the seam, `CallPipeline._run_fsm_network_resolve`, which is
async, live-only, and holds the only connection the FSM ever sees. That step
dispatches on `aresolve is not None`, so it carries this parser too — harmless,
because `_aresolve_station` is a wrapper over the same function the seam calls.

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

from src.agent.compound_parse import _LANDMARK_KEYS, _detect_station_hint, _normalize
from src.agent.confirm_detect import is_confirmation
from src.agent.parsers.base import (
    NOT_MENTIONED,
    ParseOutcome,
    graded,
    recent_bot_utterances,
    unresolved,
)
from src.agent.station_proposal_detect import proposed_station

if TYPE_CHECKING:
    from src.agent.parsers.base import ParseContext

logger = logging.getLogger(__name__)

#: How far back a proposal is searched. Mirrors `yes_no_parser` and the Wave 15
#: block in `pipeline.py` — a filler re-ask sits between question and answer.
_BOT_TURN_WINDOW = 2

#: Held below the apply threshold on purpose — see the module docstring. The
#: detector's own `0.9` describes how sure we are of the *landmark*, not of a
#: `station_id`, and this field is the id.
_HINT_CONFIDENCE = 0.6

#: A single station matching the landmark in the list the bot already read out.
_RESOLVED_CONFIDENCE = 1.0


#: The fields a landmark is searched in — the same four `get_fitting_stations`
#: already matches its own `query` against (`src/main.py:2489-2499`). This
#: resolver exists to reproduce that tool's answer from the snapshot, so
#: searching a different surface than the tool is a bug by construction, and it
#: was one in both directions: `name` was read here and nowhere else, while
#: `landmarks` and `description` were read there and not here.
#:
#: `name` is dropped rather than kept for safety. It does match sometimes —
#: `000000022` spells «Бориса Кротова» out in full there — but never *alone*:
#: adding it back changes the answer for 0 of the 21 labels, because every
#: station it finds is already found through another field. What it does change
#: is that prod `name` is a station code carrying a second city in parentheses
#: («1Д (Днепр, пер. Добровольцев, 1д)»), so keeping it only widens the ways two
#: stations can collide.
_SEARCH_FIELDS = ("address", "district", "landmarks", "description")


def _matches(station: dict, keys: tuple[str, ...]) -> bool:
    """True when any of the landmark's search keys appears in the station text."""
    for field in _SEARCH_FIELDS:
        value = station.get(field)
        if isinstance(value, str):
            lowered = value.lower()
            if any(key in lowered for key in keys):
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

    keys = _LANDMARK_KEYS.get(outcome.value, (outcome.value.lower(),))
    hits = [s for s in stations if isinstance(s, dict) and _matches(s, keys)]
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


def resolve_proposed_station(ctx: ParseContext) -> ParseOutcome:
    """«The bot named one station and the caller agreed» → `station_id`.

    The second route to `status="value"`, and deliberately built from the same
    parts as the first: `_detect_station_hint` reads the landmark out of the
    *bot's* turn exactly as it reads one out of the caller's, and `_matches` /
    `_in_city` then resolve it against the snapshot. A second copy of the
    matching rules is how «Перемоги» gets picked in one place and refused in
    the other — the same argument that keeps `_aresolve_station` a thin wrapper.

    Why this is not part of `parse()`
    ---------------------------------
    `parse()` answers «what did the caller name», and the caller named nothing —
    «так» carries no landmark. The evidence here is the *bot's* turn, so this is
    a resolver, not a detector, and it keeps the module invariant that `parse()`
    can never reach `status="value"`.

    Why it is not restricted to STATION
    -----------------------------------
    Call `011277ef` dies in CITY: the bot quoted a price for «у місті Дніпро,
    провулок Добровольців», the caller said «так», and the FSM was still in CITY
    because «на перемозі» resolves to a landmark in two cities and therefore to
    no city at all. A rule that only fires in STATION never reaches that call.
    The seam calls this independently of the current state.

    Default-deny throughout: no confirmation, no proposal, an empty snapshot,
    a landmark the bot's turn does not carry, or anything other than exactly one
    match — all return `NOT_MENTIONED` rather than a guess. Driving a caller to
    the wrong address is worse than handing them to an operator.
    """
    session = ctx.session
    if not is_confirmation(ctx.customer_text or ""):
        return NOT_MENTIONED

    recent = recent_bot_utterances(ctx, _BOT_TURN_WINDOW)
    if not proposed_station(recent):
        return NOT_MENTIONED

    stations = list(getattr(session, "fitting_stations_seen", None) or []) if session else []
    if not stations:
        logger.debug("station_proposal: agreement with no snapshot to resolve against")
        return NOT_MENTIONED

    city = _chosen_city(session)
    if city:
        in_city = [s for s in stations if isinstance(s, dict) and _in_city(s, city)]
        if not in_city:
            # Same shape as the hint resolver's mismatch: the caller has pinned a
            # city the snapshot cannot serve. Refusing here is what stops call
            # `bd95036c` — «підтверджую» to a *city change* prompt — from pinning
            # the Дніпро station that is still sitting in the snapshot.
            logger.warning(
                "fsm_station_proposal_city_mismatch: chosen city %r has none of "
                "the %d offered stations — proposal not resolved",
                city, len(stations),
            )
            return NOT_MENTIONED
        stations = in_city

    hint = _detect_station_hint(_normalize(" ".join(recent)))
    if hint is None or not isinstance(hint.value, str):
        logger.debug("station_proposal: bot turn carries no landmark — unresolved")
        return NOT_MENTIONED

    keys = _LANDMARK_KEYS.get(hint.value, (hint.value.lower(),))
    hits = [s for s in stations if isinstance(s, dict) and _matches(s, keys)]
    if len(hits) != 1:
        logger.debug(
            "station_proposal: %r matched %d of %d offered stations — unresolved",
            hint.value, len(hits), len(stations),
        )
        return NOT_MENTIONED

    station_id = hits[0].get("id")
    if not station_id:
        logger.warning("station_proposal: station %r matched but carries no id", hits[0])
        return NOT_MENTIONED
    return graded(str(station_id), _RESOLVED_CONFIDENCE)


def station_city(session: object | None, station_id: str) -> str | None:
    """The `city` the snapshot records for `station_id`, verbatim — or `None`.

    Returned unnormalised because the caller writes it into
    `fsm_filled_fields["city"]`, which holds display forms («Дніпро»), not the
    folded ones `_normalize` produces.
    """
    stations = getattr(session, "fitting_stations_seen", None) or []
    for station in stations:
        if isinstance(station, dict) and str(station.get("id") or "") == str(station_id):
            city = station.get("city")
            return city if isinstance(city, str) and city.strip() else None
    return None


def unanimous_snapshot_city(session: object | None) -> str | None:
    """The city when every station the bot has offered agrees on one — else `None`.

    Wave 6-H, the half that reaches call `011277ef`. That call dies in CITY
    because «на перемозі» is a landmark in two cities, so `_detect_city`
    returns nothing and the field can never be filled from the utterance — the
    caller is transferred while the session already holds four Дніпро stations
    the tool returned for that very city.

    This is not a guess about what the caller meant. `fitting_stations_seen` is
    filled by `get_fitting_stations(city=...)`, so a unanimous snapshot is the
    city the bot had already resolved and passed to the tool; the provenance
    was checked against the prod `tool_args` before the rule was written.

    Default-deny with no «nearly unanimous» branch, and the corpus shows why
    that matters rather than being tidy: the 9-station catalog spanning five
    cities is precisely the payload the tool returns when it did **not** know
    the city (`action_required: ask_district`). Measured over 42 calls the rule
    fills 25 and refuses 17 — the 8 with no snapshot at all and the 9 holding
    that catalog.

    It lives in this module, not in `city_parser`, because the evidence is the
    station snapshot and `_station_city` already states how a station's city is
    read. A second reading of that field elsewhere is how «Дніпро» comes to
    match «Дніпропетровськ» in one place and not the other.
    """
    stations = [
        s for s in (getattr(session, "fitting_stations_seen", None) or []) if isinstance(s, dict)
    ]
    if not stations:
        return None

    chosen: str | None = None
    folded: set[str] = set()
    for station in stations:
        city = _station_city(station)
        if not city:
            return None
        folded.add(city)
        if len(folded) > 1:
            return None
        if chosen is None:
            raw = station.get("city")
            chosen = raw if isinstance(raw, str) else None
    return chosen


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
