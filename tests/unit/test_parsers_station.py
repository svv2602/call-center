"""`station_parser` — a landmark is a search key, never a `station_id`.

The property this file exists to pin: **`parse()` can not reach
`status="value"`**, by construction. The parser's `field_name` is `station_id`
and a landmark is not one; pinning it would let the FSM skip STATION with a
value `book_fitting` cannot use — the same defect class as `c8c6601`, and the
reason `station_hint` is deliberately absent from `COMPOUND_TO_FSM_FIELD`.

So the landmark comes back `unresolved`, visible in `value` and held below the
apply threshold.

`resolve_station_from_session` is the only route to `value`. It resolves
against `session.fitting_stations_seen` — the snapshot `get_fitting_stations`
wrote when the state was entered — so it needs no tool router (there is none on
`ParseContext`), no connection and, since Wave 6-C, no `await`: the shadow seam
calls it synchronously. `aresolve` is the same function behind the protocol's
awaitable shape, and `TestTheWrapperAndTheCoreAgree` pins that they cannot
drift apart.

Ambiguity is left unresolved rather than guessed: «Перемоги» exists in more
than one city, and picking silently is how a caller drives to the wrong
address.

Landmarks below are read out of `compound_parse._LANDMARKS`.
"""

from __future__ import annotations

import inspect
import logging
import uuid
from typing import ClassVar
from unittest.mock import patch

import pytest

from src.agent.parsers import ParseContext
from src.agent.parsers.base import APPLY_THRESHOLD, NOT_MENTIONED
from src.agent.parsers.station_parser import (
    _HINT_CONFIDENCE,
    _RESOLVED_CONFIDENCE,
    PARSER,
    resolve_proposed_station,
    resolve_station_from_session,
)
from src.core.call_session import CallSession

STATION_QUESTION = "У якому районі вам зручно?"


def ctx(
    text: str,
    *,
    stations: list[dict] | None = None,
    city: str | None = None,
    filled: dict | None = None,
) -> ParseContext:
    """A context for the resolver.

    `city` goes into `fsm_filled_fields`, which is where a chosen city actually
    lives — `CallSession` has no `fitting_city`. Passing nothing leaves
    `fsm_filled_fields` empty, so every test written before Wave 6-D keeps
    describing the un-narrowed behaviour.
    """
    session = None
    if stations is not None or city is not None or filled is not None:
        session = CallSession(channel_uuid=uuid.uuid4())
        session.fitting_stations_seen = stations if stations is not None else []
        if city is not None:
            session.fsm_filled_fields["city"] = city
        if filled:
            session.fsm_filled_fields.update(filled)
    return ParseContext(
        customer_text=text,
        last_bot_utterance=STATION_QUESTION,
        session=session,
    )


class TestSyncPassNeverPinsAnId:
    """README §4 — constructive, not a bug to be fixed here."""

    @pytest.mark.parametrize(
        "text,label",
        [
            ("на Оболоні", "Оболонь"),
            ("біля Речпорту", "Речпорт"),
            ("на Холодногірській", "Холодногірська"),
            ("ЖМ Перемога", "ЖМ Перемога"),
            ("Харківське шосе", "Харківське шосе"),
            ("на Тимошенка", "Тимошенка"),
            ("Караван", "Караван"),
        ],
    )
    def test_landmark_is_unresolved_with_the_hint_visible(self, text: str, label: str) -> None:
        outcome = PARSER.parse(ctx(text))
        assert outcome.status == "unresolved"
        assert outcome.value == label
        assert outcome.confidence == _HINT_CONFIDENCE

    def test_the_hint_confidence_is_below_the_threshold(self) -> None:
        assert _HINT_CONFIDENCE < APPLY_THRESHOLD

    def test_no_utterance_can_make_the_sync_pass_return_a_value(self) -> None:
        """The invariant, stated over the whole landmark vocabulary."""
        from src.agent.compound_parse import _LANDMARKS

        for stem, _label, _city in _LANDMARKS:
            outcome = PARSER.parse(ctx(f"мені зручно {stem}"))
            assert outcome.status != "value", stem

    def test_the_detectors_own_confidence_is_deliberately_not_reused(self) -> None:
        """`_detect_station_hint` says `0.9` about the *landmark*, not the id."""
        from src.agent.compound_parse import _detect_station_hint

        assert _detect_station_hint("на оболоні").confidence == 0.9
        assert PARSER.parse(ctx("на Оболоні")).confidence == _HINT_CONFIDENCE

    def test_spans_point_at_the_landmark(self) -> None:
        outcome = PARSER.parse(ctx("мені зручно на Оболоні"))
        start, end = outcome.spans[0]
        assert "мені зручно на оболоні"[start:end] == "оболон"


class TestPeremohyIsNeverCertain:
    """«Перемоги» exists in more than one city ⇒ never `1.0`."""

    def test_confidence_is_never_full(self) -> None:
        outcome = PARSER.parse(ctx("на Перемоги"))
        assert outcome.confidence < 1.0
        assert outcome.status != "value"

    def test_the_longer_stem_wins(self) -> None:
        """Ordered longest-first at build time: «жм перемог» over «перемог»."""
        assert PARSER.parse(ctx("ЖМ Перемога")).value == "ЖМ Перемога"
        assert PARSER.parse(ctx("вулиця Перемоги")).value == "Перемоги"

    def test_city_agnostic_landmarks_carry_no_city(self) -> None:
        """«Перемоги» belongs in this set, and used not to be in it.

        This class already knew the landmark was ambivalent — it just enforced
        it one layer too high, on the confidence of the *station*, while
        `_LANDMARKS` went on pinning `city="Запоріжжя"` at 0.9 against a
        threshold of 0.7. On b394f6c1 that pin outlived the caller's explicit
        «Днепро» four turns later, because filled fields are written with
        `setdefault` (`pipeline.py:1227`).

        Labels, not stems: «Перемоги» has two stem rows («перемог», «перемоз»)
        and both must stay city-less. The set is spelled out so a new ambivalent
        landmark cannot appear — nor an existing one quietly regain a city —
        without this test saying so.

        «Героїв Дніпра» joined the set for a different reason: its city was not
        ambivalent, it was wrong. The row said Київ (the metro station), and the
        only point in the catalog on that street is `000000007` in Черкаси — so
        the pin invented a Kyiv station for one caller and overwrote Черкаси for
        the other. Черкаси is not the fix either: pinning any city off a landmark
        that names a place in another one is the b394f6c1 defect. With no city
        the resolver finds the Cherkasy point whenever the snapshot holds it.
        """
        from src.agent.compound_parse import _LANDMARKS

        agnostic = {label for _stem, label, city in _LANDMARKS if city is None}
        assert agnostic == {
            "Лівий берег",
            "Правий берег",
            "Автовокзал",
            "Перемоги",
            "Героїв Дніпра",
        }


class TestNotMentioned:
    @pytest.mark.parametrize("text", ["", "   ", "білий Nissan", "завтра о 14:00", "R16"])
    def test_no_landmark(self, text: str) -> None:
        outcome = PARSER.parse(ctx(text))
        assert outcome.status == "not_mentioned"
        assert outcome.value is None


class TestAresolve:
    """`value` only ever comes from here, and only on a unique match."""

    async def test_single_match_becomes_the_id(self) -> None:
        stations = [
            {"id": "st-1", "name": "6К (Київ, Тимошенка 7)", "district": "Оболонь, Правий берег"},
            {"id": "st-2", "name": "7К (Київ, Драгоманова 2)", "district": "Позняки, Дарницький"},
        ]
        parsed = PARSER.parse(ctx("на Оболоні", stations=stations))
        resolved = await PARSER.aresolve(ctx("на Оболоні", stations=stations), parsed)

        assert resolved.status == "value"
        assert resolved.value == "st-1"
        assert resolved.confidence == _RESOLVED_CONFIDENCE

    async def test_two_matches_are_left_unresolved(self) -> None:
        """Silently picking one is how a caller drives to the wrong address."""
        stations = [
            {"id": "st-zp", "name": "9З", "address": "м. Запоріжжя, вул. Перемоги, 72б"},
            {"id": "st-dp", "name": "1Д", "district": "Перемога, Правий берег"},
        ]
        parsed = PARSER.parse(ctx("на Перемоги", stations=stations))
        resolved = await PARSER.aresolve(ctx("на Перемоги", stations=stations), parsed)

        assert resolved is parsed
        assert resolved.status == "unresolved"
        assert resolved.value == "Перемоги"

    async def test_no_snapshot_means_no_resolution(self) -> None:
        parsed = PARSER.parse(ctx("на Оболоні", stations=[]))
        resolved = await PARSER.aresolve(ctx("на Оболоні", stations=[]), parsed)
        assert resolved is parsed
        assert resolved.status == "unresolved"

    async def test_no_session_at_all(self) -> None:
        parsed = PARSER.parse(ctx("на Оболоні"))
        resolved = await PARSER.aresolve(ctx("на Оболоні"), parsed)
        assert resolved is parsed

    async def test_a_match_without_an_id_is_not_a_value(self) -> None:
        stations = [{"name": "6К (Київ, Тимошенка 7)", "district": "Оболонь, Правий берег"}]
        parsed = PARSER.parse(ctx("на Оболоні", stations=stations))
        resolved = await PARSER.aresolve(ctx("на Оболоні", stations=stations), parsed)
        assert resolved is parsed
        assert resolved.status == "unresolved"

    async def test_nothing_to_resolve_passes_through(self) -> None:
        stations = [{"id": "st-1", "name": "6К", "district": "Оболонь"}]
        resolved = await PARSER.aresolve(ctx("білий Nissan", stations=stations), NOT_MENTIONED)
        assert resolved is NOT_MENTIONED

    @pytest.mark.parametrize("field", ["address", "district", "landmarks", "description"])
    async def test_every_field_the_prod_tool_searches_is_searched_here(self, field: str) -> None:
        """The surface is `get_fitting_stations`' own (`main.py:2489-2499`).

        This resolver exists to reproduce that tool's answer from the snapshot,
        so a field the tool matches and this does not is a station the caller
        was offered and cannot then name. `landmarks` and `description` were
        exactly that until Wave 6-E.
        """
        stations = [{"id": "st-9", field: "ТЦ Оболонь, вул. Полярна 3"}]
        parsed = PARSER.parse(ctx("на Оболоні", stations=stations))
        resolved = await PARSER.aresolve(ctx("на Оболоні", stations=stations), parsed)
        assert resolved.value == "st-9"

    async def test_name_is_not_searched(self) -> None:
        """Prod `name` is a station code («1Д (Днепр, пер. Добровольцев, 1д)»).

        It carried zero of the 30 landmarks when the whole table was matched
        against the catalog, and the prod tool does not search it either. Dead
        surface only widens the ways two stations can collide, so it is out.
        """
        stations = [{"id": "st-9", "name": "ТЦ Оболонь, вул. Полярна 3"}]
        parsed = PARSER.parse(ctx("на Оболоні", stations=stations))
        resolved = await PARSER.aresolve(ctx("на Оболоні", stations=stations), parsed)
        assert resolved is parsed
        assert resolved.status == "unresolved"

    async def test_the_id_is_stringified(self) -> None:
        stations = [{"id": 42, "name": "6К", "district": "Оболонь"}]
        parsed = PARSER.parse(ctx("на Оболоні", stations=stations))
        resolved = await PARSER.aresolve(ctx("на Оболоні", stations=stations), parsed)
        assert resolved.value == "42"


class TestContract:
    def test_registry_identity(self) -> None:
        assert PARSER.name == "station_parser"
        assert PARSER.field_name == "station_id"

    def test_aresolve_is_declared(self) -> None:
        assert PARSER.aresolve is not None

    def test_no_tool_router_reaches_a_parser(self) -> None:
        """Recorded gap: `ParseContext` carries a connection and no router.

        `get_fitting_stations(query=…)` therefore has no route into a parser,
        which is why `aresolve` reads the session snapshot instead. Wave 6-B
        decides whether the router belongs on the context.
        """
        from dataclasses import fields

        assert "router" not in {f.name for f in fields(ParseContext)}

    def test_the_three_statuses_across_both_passes(self) -> None:
        stations = [{"id": "st-1", "name": "6К", "district": "Оболонь"}]
        sync_statuses = {PARSER.parse(ctx(text)).status for text in ("на Оболоні", "білий Nissan")}
        assert sync_statuses == {"unresolved", "not_mentioned"}
        assert stations, "value is reached only through aresolve — see TestAresolve"


class TestSyncResolve:
    """Wave 6-C — the resolver is synchronous, and that is what unblocks shadow.

    §3.2 rule 3 («`aresolve` never runs in shadow») is a rule about *network*.
    Resolving a landmark reads `session.fitting_stations_seen`, a dict already
    in the session, so there is nothing to await — and the shadow seam calls it
    directly. `brand_parser` is the contrasting case: its resolver needs
    `ctx.conn`, that is real I/O, and it stays live-only.
    """

    def test_it_is_not_a_coroutine_function(self) -> None:
        assert not inspect.iscoroutinefunction(resolve_station_from_session)

    def test_single_match_becomes_the_id(self) -> None:
        stations = [
            {"id": "st-1", "name": "6К (Київ, Тимошенка 7)", "district": "Оболонь, Правий берег"},
            {"id": "st-2", "name": "7К (Київ, Драгоманова 2)", "district": "Позняки, Дарницький"},
        ]
        c = ctx("на Оболоні", stations=stations)
        resolved = resolve_station_from_session(c, PARSER.parse(c))

        assert resolved.status == "value"
        assert resolved.value == "st-1"
        assert resolved.confidence == _RESOLVED_CONFIDENCE

    def test_two_matches_are_left_unresolved(self) -> None:
        """Ambiguity is refused, not guessed — cross-city guard `13e9ea4`."""
        stations = [
            {"id": "st-zp", "name": "9З", "address": "м. Запоріжжя, вул. Перемоги, 72б"},
            {"id": "st-dp", "name": "1Д", "district": "Перемога, Правий берег"},
        ]
        c = ctx("на Перемоги", stations=stations)
        parsed = PARSER.parse(c)
        resolved = resolve_station_from_session(c, parsed)

        assert resolved is parsed
        assert resolved.status == "unresolved"
        assert resolved.value == "Перемоги"

    def test_an_empty_snapshot_resolves_nothing(self) -> None:
        c = ctx("на Оболоні", stations=[])
        parsed = PARSER.parse(c)
        assert resolve_station_from_session(c, parsed) is parsed

    def test_a_match_without_an_id_is_not_a_value(self, caplog) -> None:
        """WARNING, not DEBUG: a station row with no id is a data defect."""
        stations = [{"name": "6К (Київ, Тимошенка 7)", "district": "Оболонь, Правий берег"}]
        c = ctx("на Оболоні", stations=stations)
        parsed = PARSER.parse(c)

        with caplog.at_level(logging.WARNING, logger="src.agent.parsers.station_parser"):
            resolved = resolve_station_from_session(c, parsed)

        assert resolved is parsed
        assert resolved.status == "unresolved"
        assert any(
            r.levelno >= logging.WARNING and "carries no id" in r.getMessage()
            for r in caplog.records
        ), "a station without an id must be loud"

    def test_nothing_to_resolve_passes_through(self) -> None:
        stations = [{"id": "st-1", "name": "6К", "district": "Оболонь"}]
        c = ctx("білий Nissan", stations=stations)
        assert resolve_station_from_session(c, NOT_MENTIONED) is NOT_MENTIONED

    def test_it_needs_no_connection(self) -> None:
        """The gate that keeps the *network* resolvers out is `conn is None`."""
        stations = [{"id": "st-1", "name": "6К", "district": "Оболонь"}]
        c = ctx("на Оболоні", stations=stations)
        assert c.conn is None
        assert resolve_station_from_session(c, PARSER.parse(c)).value == "st-1"


class TestTheWrapperAndTheCoreAgree:
    """One implementation, two shapes. Two copies is how they drift apart."""

    #: One case per branch of the resolver: hit, ambiguous, empty snapshot,
    #: id-less match, no landmark at all, non-string id, address-only match.
    #: `ClassVar` because a bare mutable class attribute is RUF012 — and the
    #: list is read by `parametrize` at class-body time, never mutated.
    CASES: ClassVar[list[tuple[str, list[dict]]]] = [
        ("на Оболоні", [{"id": "st-1", "name": "6К", "district": "Оболонь"}]),
        (
            "на Перемоги",
            [
                {"id": "st-zp", "address": "м. Запоріжжя, вул. Перемоги, 72б"},
                {"id": "st-dp", "district": "Перемога, Правий берег"},
            ],
        ),
        ("на Оболоні", []),
        ("на Оболоні", [{"name": "6К", "district": "Оболонь"}]),
        ("білий Nissan", [{"id": "st-1", "name": "6К", "district": "Оболонь"}]),
        ("на Оболоні", [{"id": 42, "name": "6К", "district": "Оболонь"}]),
        ("на Оболоні", [{"id": "st-9", "address": "ТЦ Оболонь, вул. Полярна 3"}]),
    ]

    @pytest.mark.parametrize("text,stations", CASES)
    async def test_same_answer_on_the_same_input(self, text: str, stations: list[dict]) -> None:
        c = ctx(text, stations=stations)
        parsed = PARSER.parse(c)

        assert await PARSER.aresolve(c, parsed) == resolve_station_from_session(c, parsed)

    async def test_the_wrapper_adds_nothing_of_its_own(self) -> None:
        """Delegation, not a second implementation."""
        stations = [{"id": "st-1", "name": "6К", "district": "Оболонь"}]
        c = ctx("на Оболоні", stations=stations)
        parsed = PARSER.parse(c)

        with patch(
            "src.agent.parsers.station_parser.resolve_station_from_session",
            return_value="sentinel",
        ) as core:
            assert await PARSER.aresolve(c, parsed) == "sentinel"

        core.assert_called_once_with(c, parsed)


# Wave 6-D phase 02 ───────────────────────────────────────────────────────────
#
# «Перемоги» in Дніпро and «Перемоги» in Запоріжжя are two stations only while
# nobody has said which city. Once the caller has, the other one was never a
# candidate, so dropping it removes an ambiguity that does not exist — it does
# not resolve one that does. That distinction is the whole phase, and
# `TestAmbiguityInsideOneCityIsStillRefused` is the half that must not move.

#: Same landmark, two cities. The shape of call `b394f6c1`.
TWO_CITIES: list[dict] = [
    {"id": "st-dp", "city": "Дніпро", "district": "Перемога, Правий берег"},
    {"id": "st-zp", "city": "Запоріжжя", "address": "м. Запоріжжя, вул. Перемоги, 72б"},
]

#: Same landmark twice inside one city. Genuinely ambiguous, city or no city.
ONE_CITY_TWICE: list[dict] = [
    {"id": "st-dp-a", "city": "Дніпро", "district": "Перемога, Правий берег"},
    {"id": "st-dp-b", "city": "Дніпро", "district": "Перемога-6, Придніпровськ"},
]


class TestCityNarrowing:
    """A chosen city narrows the candidates before the landmark is matched."""

    def test_the_same_landmark_in_two_cities_resolves_to_the_chosen_one(self) -> None:
        """Call `b394f6c1`: died on STATION with the city pinned six turns."""
        c = ctx("на Перемоги", stations=TWO_CITIES, city="Дніпро")
        resolved = resolve_station_from_session(c, PARSER.parse(c))

        assert resolved.status == "value"
        assert resolved.value == "st-dp"
        assert resolved.confidence == _RESOLVED_CONFIDENCE

    def test_the_other_city_is_reachable_too(self) -> None:
        """Not a hard-coded winner: the answer follows the chosen city."""
        c = ctx("на Перемоги", stations=TWO_CITIES, city="Запоріжжя")
        assert resolve_station_from_session(c, PARSER.parse(c)).value == "st-zp"

    def test_without_a_chosen_city_nothing_changes(self) -> None:
        """The pre-6-D behaviour, on stations that *do* carry a city.

        Narrowing must not become a «city first» precondition: with no city
        pinned the two candidates are still two candidates.
        """
        c = ctx("на Перемоги", stations=TWO_CITIES)
        parsed = PARSER.parse(c)
        resolved = resolve_station_from_session(c, parsed)

        assert resolved is parsed
        assert resolved.status == "unresolved"
        assert resolved.value == "Перемоги"

    def test_an_empty_city_string_does_not_narrow(self) -> None:
        """`fsm_filled_fields["city"] = ""` is «not chosen», not «no city»."""
        c = ctx("на Перемоги", stations=TWO_CITIES, city="")
        assert resolve_station_from_session(c, PARSER.parse(c)).status == "unresolved"

    # The two below say «unchanged» in the only way that can fail: a case that
    # must still reach `value`. Stating it on an *ambiguous* input instead would
    # be an assertion about intent — «unresolved» is also what a filter running
    # against no city at all produces, so the test would pass either way. That
    # is the shape that let a mutation survive in phase 01.

    @pytest.mark.parametrize("chosen", [None, ""])
    def test_with_no_city_chosen_a_unique_station_still_resolves(self, chosen: str | None) -> None:
        stations = [
            {"id": "st-1", "city": "Київ", "district": "Оболонь"},
            {"id": "st-2", "city": "Київ", "district": "Позняки"},
        ]
        c = ctx("на Оболоні", stations=stations, city=chosen)
        resolved = resolve_station_from_session(c, PARSER.parse(c))

        assert resolved.status == "value"
        assert resolved.value == "st-1"

    @pytest.mark.parametrize("chosen", [None, ""])
    def test_with_no_city_chosen_nothing_is_ever_a_mismatch(
        self, chosen: str | None, caplog
    ) -> None:
        stations = [{"id": "st-1", "city": "Київ", "district": "Оболонь"}]
        c = ctx("на Оболоні", stations=stations, city=chosen)

        with caplog.at_level(logging.DEBUG, logger="src.agent.parsers.station_parser"):
            resolve_station_from_session(c, PARSER.parse(c))

        assert not any("fsm_station_city_mismatch" in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize("written", ["дніпро", "  Дніпро  ", "ДНІПРО"])
    def test_the_city_comparison_ignores_case_and_padding(self, written: str) -> None:
        c = ctx("на Перемоги", stations=TWO_CITIES, city=written)
        assert resolve_station_from_session(c, PARSER.parse(c)).value == "st-dp"

    @pytest.mark.parametrize(
        "station_city,chosen",
        [
            ("Дніпропетровськ", "Дніпро"),
            ("Дніпро", "Дніпропетровськ"),
            ("м. Київ", "Київ"),
        ],
    )
    def test_the_old_and_long_forms_of_a_city_still_match(
        self, station_city: str, chosen: str
    ) -> None:
        """Containment both ways, exactly as `src/main.py:2483` filters.

        Дніпро and Дніпропетровськ are the same place and 1C says so in both
        forms; strict equality here would call that a cross-city mismatch and
        refuse a station the caller can actually drive to.
        """
        stations = [
            {"id": "st-dp", "city": station_city, "district": "Перемога"},
            {"id": "st-zp", "city": "Запоріжжя", "address": "м. Запоріжжя, вул. Перемоги, 72б"},
        ]
        c = ctx("на Перемоги", stations=stations, city=chosen)
        assert resolve_station_from_session(c, PARSER.parse(c)).value == "st-dp"

    def test_a_station_with_a_blank_city_is_not_in_every_city(self) -> None:
        """Default-deny (§1.6). `"" in "дніпро"` is true — the guard is not.

        Without it a station whose city 1C left empty would be a candidate in
        every city at once, which is worse than the ambiguity being fixed here.
        """
        stations = [
            {"id": "st-blank", "city": "", "district": "Перемога"},
            {"id": "st-dp", "city": "Дніпро", "district": "Перемога"},
        ]
        c = ctx("на Перемоги", stations=stations, city="Дніпро")
        assert resolve_station_from_session(c, PARSER.parse(c)).value == "st-dp"

    def test_a_non_string_city_field_does_not_narrow(self) -> None:
        """A malformed session field is «no city», not a crash."""
        c = ctx("на Перемоги", stations=TWO_CITIES, filled={"city": 42})
        assert resolve_station_from_session(c, PARSER.parse(c)).status == "unresolved"

    def test_a_snapshot_that_names_no_city_is_refused(self, caplog) -> None:
        """No exemption for a snapshot that declares no cities at all.

        The tempting reading is «absence of evidence, not evidence of a
        different city» — so skip the filter and resolve as before. That is an
        escape hatch, not a guard (§1.6): it is a data shape an attacker of the
        rule (or a 1C outage) can produce to switch the guard off wholesale,
        which is precisely the defect waves 15 and 16 closed elsewhere.

        Prod cannot reach this branch: `main.py:2516` writes `city` into every
        entry unconditionally, and all 1026 stations across 603 stored payloads
        carry it. If it ever did go blank the failure direction is the safe one
        — refuse, stay in STATION, hand the caller to an operator — and the
        WARNING below makes it loud instead of silent.
        """
        stations = [
            {"id": "st-1", "name": "6К (Київ, Тимошенка 7)", "district": "Оболонь, Правий берег"},
            {"id": "st-2", "name": "7К (Київ, Драгоманова 2)", "district": "Позняки, Дарницький"},
        ]
        c = ctx("на Оболоні", stations=stations, city="Київ")

        with caplog.at_level(logging.DEBUG, logger="src.agent.parsers.station_parser"):
            resolved = resolve_station_from_session(c, PARSER.parse(c))

        assert resolved.status == "unresolved"
        assert any("fsm_station_city_mismatch" in r.getMessage() for r in caplog.records)

    def test_one_declared_city_is_enough_to_narrow_by(self) -> None:
        """Partial data still narrows — the undeclared row stays default-deny.

        A station that does not say where it is belongs to no city, so it is
        dropped alongside the ones naming a different city, and the single
        declared match is what remains.
        """
        stations = [
            {"id": "st-silent", "district": "Перемога"},
            {"id": "st-dp", "city": "Дніпро", "district": "Перемога"},
        ]
        c = ctx("на Перемоги", stations=stations, city="Дніпро")
        assert resolve_station_from_session(c, PARSER.parse(c)).value == "st-dp"


class TestAmbiguityInsideOneCityIsStillRefused:
    """The rule the module docstring states, unchanged by the narrowing.

    «Picking one of them silently is how a caller ends up driving to the wrong
    address» — a chosen city does not license a guess inside that city.
    """

    def test_two_stations_of_the_same_name_in_the_chosen_city(self) -> None:
        c = ctx("на Перемоги", stations=ONE_CITY_TWICE, city="Дніпро")
        parsed = PARSER.parse(c)
        resolved = resolve_station_from_session(c, parsed)

        assert resolved is parsed
        assert resolved.status == "unresolved"

    def test_and_without_a_city_as_well(self) -> None:
        c = ctx("на Перемоги", stations=ONE_CITY_TWICE)
        parsed = PARSER.parse(c)
        assert resolve_station_from_session(c, parsed) is parsed

    def test_a_match_without_an_id_still_warns_after_narrowing(self, caplog) -> None:
        """2.2 — the id-less station stays loud on the narrowed path too."""
        stations = [{"city": "Київ", "name": "6К (Київ, Тимошенка 7)", "district": "Оболонь"}]
        c = ctx("на Оболоні", stations=stations, city="Київ")
        parsed = PARSER.parse(c)

        with caplog.at_level(logging.WARNING, logger="src.agent.parsers.station_parser"):
            resolved = resolve_station_from_session(c, parsed)

        assert resolved is parsed
        assert any("carries no id" in r.getMessage() for r in caplog.records)


class TestCrossCityMismatchIsItsOwnDiagnosis:
    """Call `bd95036c` — Дніпро snapshot, «Черкаси» asked for three times.

    Zero candidates after narrowing is not «the caller said nonsense»: it is a
    cleanly diagnosable cross-city mismatch, and Wave 6-E has to be able to see
    the difference in the logs.
    """

    STATIONS: ClassVar[list[dict]] = [
        {"id": "st-dp", "city": "Дніпро", "district": "Перемога, Правий берег"},
        {"id": "st-dp-2", "city": "Дніпро", "district": "Робоча"},
    ]

    def test_the_hint_is_not_pinned(self) -> None:
        c = ctx("на Перемоги", stations=self.STATIONS, city="Черкаси")
        parsed = PARSER.parse(c)
        resolved = resolve_station_from_session(c, parsed)

        assert resolved is parsed
        assert resolved.status == "unresolved"

    def test_it_is_logged_under_its_own_marker(self, caplog) -> None:
        c = ctx("на Перемоги", stations=self.STATIONS, city="Черкаси")

        with caplog.at_level(logging.WARNING, logger="src.agent.parsers.station_parser"):
            resolve_station_from_session(c, PARSER.parse(c))

        assert any(
            r.levelno >= logging.WARNING and "fsm_station_city_mismatch" in r.getMessage()
            for r in caplog.records
        ), "a cross-city mismatch must be greppable, not another `unresolved`"

    def test_a_plain_unresolved_does_not_claim_a_mismatch(self, caplog) -> None:
        """The separation only pays off if the other branch stays quiet."""
        c = ctx("на Перемоги", stations=ONE_CITY_TWICE, city="Дніпро")

        with caplog.at_level(logging.DEBUG, logger="src.agent.parsers.station_parser"):
            resolve_station_from_session(c, PARSER.parse(c))

        assert not any("fsm_station_city_mismatch" in r.getMessage() for r in caplog.records)

    def test_no_seen_station_belongs_to_the_chosen_city(self) -> None:
        """2.3 — behaves like «no candidates», and above all does not raise."""
        c = ctx("на Оболоні", stations=self.STATIONS, city="Львів")
        parsed = PARSER.parse(c)
        assert resolve_station_from_session(c, parsed) is parsed


class TestNarrowingDoesNotTouchThePhase01Pin:
    """2.5 — the auto-pin and the resolver are two independent routes.

    `FsmEngine._pin_single_station` writes `fsm_filled_fields["station_id"]`
    from `fitting_station_ids` and never looks at a landmark; this resolver
    reads a landmark and never writes to the session at all. Neither cancels
    nor duplicates the other, and this file pins the half it owns.
    """

    def test_the_resolver_writes_nothing_into_the_session(self) -> None:
        c = ctx("на Перемоги", stations=TWO_CITIES, city="Дніпро")
        before = dict(c.session.fsm_filled_fields)

        resolve_station_from_session(c, PARSER.parse(c))

        assert c.session.fsm_filled_fields == before
        assert "station_id" not in c.session.fsm_filled_fields

    def test_an_already_pinned_station_survives_a_cross_city_mismatch(self) -> None:
        """The mismatch branch refuses to *add* a value; it clears nothing."""
        c = ctx(
            "на Перемоги",
            stations=[{"id": "st-dp", "city": "Дніпро", "district": "Перемога, Правий берег"}],
            filled={"city": "Черкаси", "station_id": "st-dp"},
        )
        resolve_station_from_session(c, PARSER.parse(c))

        assert c.session.fsm_filled_fields["station_id"] == "st-dp"


class TestTheWrapperAndTheCoreAgreeOnCities:
    """Two copies of the matching rules is how they drift (6-C invariant).

    Extends `TestTheWrapperAndTheCoreAgree` over the branches phase 02 adds:
    narrowed hit, narrowed ambiguity, cross-city mismatch, no city chosen.
    """

    CASES: ClassVar[list[tuple[str, list[dict], str | None]]] = [
        ("на Перемоги", TWO_CITIES, "Дніпро"),
        ("на Перемоги", TWO_CITIES, "Запоріжжя"),
        ("на Перемоги", TWO_CITIES, None),
        ("на Перемоги", TWO_CITIES, ""),
        ("на Перемоги", ONE_CITY_TWICE, "Дніпро"),
        ("на Перемоги", ONE_CITY_TWICE, "Черкаси"),
        ("на Оболоні", [{"id": "st-1", "city": "Київ", "district": "Оболонь"}], "Київ"),
        ("на Оболоні", [{"id": "st-1", "city": "Київ", "district": "Оболонь"}], "Дніпро"),
    ]

    @pytest.mark.parametrize("text,stations,city", CASES)
    async def test_same_answer_on_the_same_input(
        self, text: str, stations: list[dict], city: str | None
    ) -> None:
        c = ctx(text, stations=stations, city=city)
        parsed = PARSER.parse(c)

        assert await PARSER.aresolve(c, parsed) == resolve_station_from_session(c, parsed)


# Wave 6-E phase 02 ───────────────────────────────────────────────────────────
#
# The detector matches a *stem* («перемог») and reports a canonical *label*
# («Перемоги»); the two are not interchangeable against a station's own text.
# Ukrainian inflection breaks the label («район Дніпрошин**и**» does not contain
# «Дніпрошина»), and the Russian stems break on their own, because the catalog
# is written in Ukrainian. So the resolver searches the label together with
# *every* stem sharing it — and over the four fields the prod tool matches its
# own `query` against, which is not the three this file used to assume.
#
# Stations below are copied verbatim out of prod, not invented: `_LANDMARKS`
# is matched against real catalog text, so a fixture that reads plausibly but
# places the landmark in a field 1C never uses proves nothing.

#: Call `b394f6c1`, the snapshot taken after turn 5 — four stations, all Дніпро.
B394F6C1_SNAPSHOT: list[dict] = [
    {
        "id": "000000003",
        "city": "Дніпро",
        "name": "1Д (Днепр, пер. Добровольцев, 1Д)",
        "phone": "(067) 130-36-03",
        "address": "м. Дніпро, пров. Добровольців, 1д",
        "district": "Перемога, Правий берег, Перемога-6, Победа-6, шоста Перемога, Придніпровськ",
        "landmarks": "їхати у бік Південного мосту, набережна Перемоги, поряд Пітлайн (Питлайн), навпроти Яхт-клуб Січ (Сич), Куряче озеро, метро Придніпровська, ЖМ Перемога-1, Перемога-2, Перемога-3, Перемога-4, Перемога-5, Перемога-6, Перемога-7",
        "description": "Днепр и днепропетровск это одно и тоже. Провулок Добровольців 1Д — єдина точка шиномонтажу на ЖМ Перемога",
    },
    {
        "id": "000000005",
        "city": "Дніпро",
        "name": "3Д (Днепр, Зап. шоссе, 55К)",
        "phone": "(067) 130-36-08",
        "address": "м. Дніпро, Запорізьке шосе, 55к",
        "district": "Тополь, Правий берег, Епіцентр, Эпицентр",
        "landmarks": "виїзд з міста на Запоріжжя, виїзд на Запоріжжя, Опитне, Дослідне, район Тополя, район Епіцентру, выезд из города на Запорожье, Опытное",
        "description": "Запшоссе, Запорожское шоссе, Запорізьке шосе. Виїзд на Запоріжжя, район Тополь / Опитне поле. STT-варіанти (спотворення): паризьке шосе, парижское шоссе, запорить шосе, до сливного, до слитно, дословно, дослідного",
    },
    {
        "id": "000000001",
        "city": "Дніпро",
        "name": "7Д (Днепр, Дон. шоссе, 69)",
        "phone": "(067) 130-36-26",
        "address": "м. Дніпро, Донецьке шосе, 69",
        "district": "Донецьке шосе, Лівий берег, район Каравану, район озера Куряче, Слобожанський проспект",
        "landmarks": "Донецьке шосе 69, поряд ТРЦ Караван, біля Каравану, Слобожанський проспект, поряд з АЗС ОККО, виїзд на Донецьк, вулиця Петрозаводська, район Петрозаводської, район Передової, район Петразаводской улицы, Передовая, озеро Куряче",
        "description": "Також кажуть: Донецкое шоссе, левый берег, если ехать от Кайдацкого мост то не доезжая до Каравана",
    },
    {
        "id": "000000019",
        "city": "Дніпро",
        "name": "15Д (Днепр, ул. Княгини Ольги, 24А)",
        "phone": "(067) 130-36-82",
        "address": "м. Дніпро, вул. Княгині Ольги,24 А",
        "district": "Речпорт, Правий берег, Річпорт",
        "landmarks": "вул. Княгині Ольги 24А, район Речпорту, Річпорт, район Річпорту, біля набережної",
    },
]

#: Rows the discriminating tests below need, likewise verbatim.
_PROD_ROWS: list[dict] = [
    {
        "id": "000000006",
        "name": "4К (Киев, ул. М. Тимошенко, 7)",
        "address": "м. Київ, вул. Маршала Тимошенка, 7",
        "city": "Київ",
        "district": "Оболонь, Правий берег",
        "landmarks": "магазин Еко, метро Мінське, район Оболоні, Магазин Эко, метро Минское, Лукьяненко, Лук'яненко, Левка Лук'яненка, вул. Тимошенка, Маршала Тимошенка",
        "description": "Левка Лукьяненко 7",
    },
    {
        "id": "000000007",
        "name": "5Ч (Черкассы, ул. Героев Днепра, 7)",
        "address": "м. Черкаси, вул.Героїв Дніпра,7",
        "district": "Черкассы, Центр",
        "landmarks": "вул. Героїв Дніпра 7, район автовокзалу",
        "city": "Черкаси",
        "description": "Героїв , Героев",
    },
    {
        "id": "000000015",
        "name": "11К (Київ, Харьківске шосе, 165)",
        "address": "м. Київ, Харьківске шосе, 165",
        "district": "Харківське шосе, Лівий берег, Автосалон Тойота",
        "landmarks": "Зупинка транспорту: вул. Грузинська\nБізнес-центр: Кристал \nКиївська міська клінічна лікарня\nМагазин Сільпо",
        "description": "Остановка транспорта: ул. Грузинская\nКиевская городская клиническая больница\nМагазин Сильпо\nна левом берегу",
        "city": "Київ",
    },
    {
        "id": "000000022",
        "name": "Камион Aeolus, Днепр, ул. Бориса Кротова. 21К",
        "address": "м. Дніпро, вул. Б.Кротова, 21К",
        "city": "Дніпро",
        "district": "Правий берег, район Дніпрошини",
        "landmarks": "вул. Б.Кротова 21К, район заводу Дніпрошина",
        "description": "Каміонний шиномонтаж Aeolus",
    },
]
PROD: dict[str, dict] = {s["id"]: s for s in _PROD_ROWS}


class TestTheLabelAndTheStemAreBothSearched:
    """The second break in the `b394f6c1` chain, on the data that broke it."""

    def test_the_real_snapshot_resolves_once_the_city_is_known(self) -> None:
        """The call this wave exists for, end to end on its own snapshot.

        «перемога» died here for six turns with `city="Дніпро"` already pinned:
        the label «Перемоги» is absent from every field of `000000003`, while
        the stem «перемог» sits in its `district` and «набережна Перемоги» in
        its `landmarks`. Both halves of the phase reach it independently, which
        is why this test anchors the outcome and the three below pin the halves.
        """
        c = ctx("на перемозі", stations=B394F6C1_SNAPSHOT, city="Дніпро")
        resolved = resolve_station_from_session(c, PARSER.parse(c))

        assert resolved.status == "value"
        assert resolved.value == "000000003"

    def test_the_snapshot_is_the_shape_prod_writes(self) -> None:
        """Guards the fixture, not the code (`feedback_gate_added_to_pass_tests`).

        `main.py:2516` writes `city` into every entry unconditionally; a
        snapshot without it would silently switch the city filter off and make
        the test above pass for the wrong reason.
        """
        assert len(B394F6C1_SNAPSHOT) == 4
        assert all(s["city"] == "Дніпро" for s in B394F6C1_SNAPSHOT)

    def test_a_landmark_only_a_sibling_stem_can_reach(self) -> None:
        """«Бориса Кротова» — the label is in `name`, and `name` is not searched.

        `address` and `landmarks` both abbreviate it to «вул. Б.Кротова», so the
        stem «кротов» is the only key that can match. Searching by the label
        alone loses this station outright.
        """
        c = ctx("на Кротова", stations=[PROD["000000022"]], city="Дніпро")
        assert resolve_station_from_session(c, PARSER.parse(c)).value == "000000022"

    def test_a_landmark_only_the_wider_surface_can_reach(self) -> None:
        """«ЖМ Перемога» lives in `landmarks` and `description`, nowhere else.

        Those two fields were not searched before this phase although the prod
        tool matches them, so the caller was offered a station on a landmark it
        then could not accept.
        """
        c = ctx("на ЖМ Перемога", stations=B394F6C1_SNAPSHOT, city="Дніпро")
        resolved = resolve_station_from_session(c, PARSER.parse(c))

        assert resolved.status == "value"
        assert resolved.value == "000000003"

    def test_the_stt_form_of_lukyanenka_resolves_through_its_siblings(self) -> None:
        """2.6 — «лукяненк» matches nothing; «лукьяненк» does, and they are kin.

        The detector fires on whichever stem the caller happened to produce, and
        the apostrophe-less form is a plausible STT output that appears nowhere
        in the catalog. Because the resolver searches *every* stem of the label
        rather than the one that fired, the station is still found — this needed
        no row of its own, which is the property being pinned.
        """
        c = ctx("на Лукяненка", stations=[PROD["000000006"]], city="Київ")
        assert resolve_station_from_session(c, PARSER.parse(c)).value == "000000006"

    def test_harkivske_shose_did_not_regress(self) -> None:
        """2.4 — the widened key set must not cost a landmark that worked.

        «Харківське шосе» resolved before the phase and resolves after it; the
        Russian stems it also carries («харьковск») match nothing in a Ukrainian
        catalog, and adding them must stay harmless rather than ambiguating it.
        """
        c = ctx("на Харківському шосе", stations=[PROD["000000015"]], city="Київ")
        assert resolve_station_from_session(c, PARSER.parse(c)).value == "000000015"

    def test_heroiv_dnipra_finds_the_cherkasy_point(self) -> None:
        """2.6 — the row named Київ; the only station on that street is Черкаси.

        Dropping the city does not cost the resolution, which is the whole
        argument for `None` over `Черкаси`: the snapshot already says where the
        station is, so the landmark does not have to guess.
        """
        c = ctx("біля Героїв Дніпра", stations=[PROD["000000007"]])
        assert resolve_station_from_session(c, PARSER.parse(c)).value == "000000007"

    def test_kyiv_has_no_heroiv_dnipra_to_offer(self) -> None:
        """The other direction of the same defect: the pin invented a station.

        A Kyiv caller naming the metro gets nothing, because nothing is there —
        an honest refusal that reaches an operator, not a Cherkasy address.
        """
        c = ctx("біля Героїв Дніпра", stations=[PROD["000000006"]], city="Київ")
        parsed = PARSER.parse(c)
        assert resolve_station_from_session(c, parsed) is parsed


# --- Wave 6-H: «the bot proposed a station and the caller agreed» -----------

PROPOSAL_KYIV = "Знайшла точку біля Оболоні, на вулиці Маршала Тимошенка, 7. Записуємо туди?"
REASK_YES_NO = (
    'Перепрошую, не розчула. Скажіть, будь ласка, "так" щоб підтвердити або "ні" щоб змінити.'
)
CITY_CHANGE_PROMPT = (
    "Для зміни міста потрібне підтвердження. Підтвердіть, будь ласка, "
    "що замінюємо місто на Черкаси."
)
KEEP_THIS_STATION = (
    "Ваше місто зараз Дніпро. Перейдімо до Черкас, зараз знайду точки шиномонтажу "
    "в Черкасах. У вас у записі обрана точка шиномонтажу в Дніпрі, провулок "
    "Добровольців, 1де. Продовжимо запис на цю точку?"
)


def proposal_ctx(
    text: str,
    bot_turns: list[str],
    *,
    stations: list[dict],
    city: str | None = None,
) -> ParseContext:
    """A context whose `dialog_history` the proposal resolver can read.

    `ctx` above cannot serve: it leaves `dialog_history` empty, so
    `recent_bot_utterances` falls back to `last_bot_utterance` and only ever
    yields one turn. The two-turn cases are exactly what this rule turns on.

    `bot_turns` is chronological — oldest first, the order a call happens in.
    """
    session = CallSession(channel_uuid=uuid.uuid4())
    session.fitting_stations_seen = stations
    if city is not None:
        session.fsm_filled_fields["city"] = city
    for turn in bot_turns:
        session.add_assistant_turn(turn)
    return ParseContext(customer_text=text, last_bot_utterance=bot_turns[-1], session=session)


class TestTheProposalIsResolvedFromTheSnapshot:
    """«так» to a proposed station becomes a `station_id`, or nothing at all."""

    def test_agreement_to_a_proposal_pins_the_station(self) -> None:
        """Call `3412071b`: the bot named the Оболонь point, the caller agreed."""
        c = proposal_ctx("так", [PROPOSAL_KYIV], stations=[PROD["000000006"]])
        outcome = resolve_proposed_station(c)

        assert outcome.status == "value"
        assert outcome.value == "000000006"
        assert isinstance(outcome.value, str)
        assert outcome.confidence == _RESOLVED_CONFIDENCE

    def test_a_reask_between_the_question_and_the_answer_is_crossed(self) -> None:
        """Call `2536a21d`: proposal, silence, re-ask, «так»."""
        c = proposal_ctx("так", [PROPOSAL_KYIV, REASK_YES_NO], stations=[PROD["000000006"]])
        assert resolve_proposed_station(c).value == "000000006"

    def test_without_agreement_nothing_is_pinned(self) -> None:
        c = proposal_ctx("а де це", [PROPOSAL_KYIV], stations=[PROD["000000006"]])
        assert resolve_proposed_station(c) is NOT_MENTIONED

    def test_an_empty_snapshot_is_refused(self) -> None:
        c = proposal_ctx("так", [PROPOSAL_KYIV], stations=[])
        assert resolve_proposed_station(c) is NOT_MENTIONED

    def test_two_matching_stations_are_refused(self) -> None:
        """Ambiguity keeps its Wave 6-D answer: an operator, not a guess."""
        c = proposal_ctx("так", ["Знайшла точку на Перемоги. Записуємо туди?"], stations=TWO_CITIES)
        assert resolve_proposed_station(c) is NOT_MENTIONED

    def test_a_proposal_the_chosen_city_cannot_serve_is_refused(self) -> None:
        """Cross-city narrowing applies here exactly as it does to a landmark."""
        c = proposal_ctx("так", [PROPOSAL_KYIV], stations=[PROD["000000006"]], city="Черкаси")
        assert resolve_proposed_station(c) is NOT_MENTIONED

    def test_a_bot_turn_carrying_no_landmark_is_refused(self) -> None:
        """The marker says a proposal happened; the address says which one.

        Without the second, the rule would pick the snapshot's only entry on
        the strength of the question alone.
        """
        c = proposal_ctx("так", ["Записуємо туди?"], stations=[PROD["000000006"]])
        assert resolve_proposed_station(c) is NOT_MENTIONED


class TestTheCityChangePromptIsRefusedWithoutHelpFromTheCity:
    """Call `bd95036c` — the control the whole construction is built around.

    The bot asks to move the booking to Черкаси and the caller says
    «підтверджую», while four Дніпро stations sit in the snapshot and a genuine
    proposal marker sits two turns back.

    Both tests below run with **no city chosen**. That is the point: on the real
    corpus `compound_parse` reads «Черкасах» from an earlier caller turn, the
    city narrowing fires, and the refusal looks safe for a reason that has
    nothing to do with this rule. Take the city away and the flat window pins
    `000000003`.
    """

    def test_the_stale_proposal_is_not_accepted(self) -> None:
        c = proposal_ctx(
            "підтверджую",
            [KEEP_THIS_STATION, CITY_CHANGE_PROMPT],
            stations=B394F6C1_SNAPSHOT,
        )
        assert c.session.fsm_filled_fields.get("city") in (None, "")
        assert resolve_proposed_station(c) is NOT_MENTIONED

    def test_the_snapshot_really_does_hold_the_station_it_must_not_pin(self) -> None:
        """Guards the fixture: a snapshot that could not resolve anyway would
        make the test above pass without the rule doing anything."""
        c = proposal_ctx("підтверджую", [KEEP_THIS_STATION], stations=B394F6C1_SNAPSHOT)
        assert resolve_proposed_station(c).value == "000000003"
