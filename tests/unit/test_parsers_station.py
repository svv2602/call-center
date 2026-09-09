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
        from src.agent.compound_parse import _LANDMARKS

        agnostic = {label for _stem, label, city in _LANDMARKS if city is None}
        assert agnostic == {"Лівий берег", "Правий берег", "Автовокзал"}


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
            {"id": "st-1", "name": "Оболонь", "district": "Оболонський"},
            {"id": "st-2", "name": "Позняки", "district": "Дарницький"},
        ]
        parsed = PARSER.parse(ctx("на Оболоні", stations=stations))
        resolved = await PARSER.aresolve(ctx("на Оболоні", stations=stations), parsed)

        assert resolved.status == "value"
        assert resolved.value == "st-1"
        assert resolved.confidence == _RESOLVED_CONFIDENCE

    async def test_two_matches_are_left_unresolved(self) -> None:
        """Silently picking one is how a caller drives to the wrong address."""
        stations = [
            {"id": "st-zp", "name": "Перемоги 72Б", "district": "Запоріжжя"},
            {"id": "st-dp", "name": "Перемоги 15", "district": "Дніпро"},
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
        stations = [{"name": "Оболонь", "district": "Оболонський"}]
        parsed = PARSER.parse(ctx("на Оболоні", stations=stations))
        resolved = await PARSER.aresolve(ctx("на Оболоні", stations=stations), parsed)
        assert resolved is parsed
        assert resolved.status == "unresolved"

    async def test_nothing_to_resolve_passes_through(self) -> None:
        stations = [{"id": "st-1", "name": "Оболонь"}]
        resolved = await PARSER.aresolve(ctx("білий Nissan", stations=stations), NOT_MENTIONED)
        assert resolved is NOT_MENTIONED

    async def test_matching_looks_at_address_as_well_as_name(self) -> None:
        stations = [{"id": "st-9", "address": "ТЦ Оболонь, вул. Полярна 3"}]
        parsed = PARSER.parse(ctx("на Оболоні", stations=stations))
        resolved = await PARSER.aresolve(ctx("на Оболоні", stations=stations), parsed)
        assert resolved.value == "st-9"

    async def test_the_id_is_stringified(self) -> None:
        stations = [{"id": 42, "name": "Оболонь"}]
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
        stations = [{"id": "st-1", "name": "Оболонь"}]
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
            {"id": "st-1", "name": "Оболонь", "district": "Оболонський"},
            {"id": "st-2", "name": "Позняки", "district": "Дарницький"},
        ]
        c = ctx("на Оболоні", stations=stations)
        resolved = resolve_station_from_session(c, PARSER.parse(c))

        assert resolved.status == "value"
        assert resolved.value == "st-1"
        assert resolved.confidence == _RESOLVED_CONFIDENCE

    def test_two_matches_are_left_unresolved(self) -> None:
        """Ambiguity is refused, not guessed — cross-city guard `13e9ea4`."""
        stations = [
            {"id": "st-zp", "name": "Перемоги 72Б", "district": "Запоріжжя"},
            {"id": "st-dp", "name": "Перемоги 15", "district": "Дніпро"},
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
        stations = [{"name": "Оболонь", "district": "Оболонський"}]
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
        stations = [{"id": "st-1", "name": "Оболонь"}]
        c = ctx("білий Nissan", stations=stations)
        assert resolve_station_from_session(c, NOT_MENTIONED) is NOT_MENTIONED

    def test_it_needs_no_connection(self) -> None:
        """The gate that keeps the *network* resolvers out is `conn is None`."""
        stations = [{"id": "st-1", "name": "Оболонь"}]
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
        ("на Оболоні", [{"id": "st-1", "name": "Оболонь"}]),
        (
            "на Перемоги",
            [
                {"id": "st-zp", "name": "Перемоги 72Б"},
                {"id": "st-dp", "name": "Перемоги 15"},
            ],
        ),
        ("на Оболоні", []),
        ("на Оболоні", [{"name": "Оболонь"}]),
        ("білий Nissan", [{"id": "st-1", "name": "Оболонь"}]),
        ("на Оболоні", [{"id": 42, "name": "Оболонь"}]),
        ("на Оболоні", [{"id": "st-9", "address": "ТЦ Оболонь, вул. Полярна 3"}]),
    ]

    @pytest.mark.parametrize("text,stations", CASES)
    async def test_same_answer_on_the_same_input(self, text: str, stations: list[dict]) -> None:
        c = ctx(text, stations=stations)
        parsed = PARSER.parse(c)

        assert await PARSER.aresolve(c, parsed) == resolve_station_from_session(c, parsed)

    async def test_the_wrapper_adds_nothing_of_its_own(self) -> None:
        """Delegation, not a second implementation."""
        stations = [{"id": "st-1", "name": "Оболонь"}]
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
    {"id": "st-dp", "city": "Дніпро", "name": "Перемоги 15"},
    {"id": "st-zp", "city": "Запоріжжя", "name": "Перемоги 72Б"},
]

#: Same landmark twice inside one city. Genuinely ambiguous, city or no city.
ONE_CITY_TWICE: list[dict] = [
    {"id": "st-dp-a", "city": "Дніпро", "name": "Перемоги 15"},
    {"id": "st-dp-b", "city": "Дніпро", "name": "Перемоги 21"},
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
            {"id": "st-1", "city": "Київ", "name": "Оболонь"},
            {"id": "st-2", "city": "Київ", "name": "Позняки"},
        ]
        c = ctx("на Оболоні", stations=stations, city=chosen)
        resolved = resolve_station_from_session(c, PARSER.parse(c))

        assert resolved.status == "value"
        assert resolved.value == "st-1"

    @pytest.mark.parametrize("chosen", [None, ""])
    def test_with_no_city_chosen_nothing_is_ever_a_mismatch(
        self, chosen: str | None, caplog
    ) -> None:
        stations = [{"id": "st-1", "city": "Київ", "name": "Оболонь"}]
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
            {"id": "st-dp", "city": station_city, "name": "Перемоги 15"},
            {"id": "st-zp", "city": "Запоріжжя", "name": "Перемоги 72Б"},
        ]
        c = ctx("на Перемоги", stations=stations, city=chosen)
        assert resolve_station_from_session(c, PARSER.parse(c)).value == "st-dp"

    def test_a_station_with_a_blank_city_is_not_in_every_city(self) -> None:
        """Default-deny (§1.6). `"" in "дніпро"` is true — the guard is not.

        Without it a station whose city 1C left empty would be a candidate in
        every city at once, which is worse than the ambiguity being fixed here.
        """
        stations = [
            {"id": "st-blank", "city": "", "name": "Перемоги 15"},
            {"id": "st-dp", "city": "Дніпро", "name": "Перемоги 21"},
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
            {"id": "st-1", "name": "Оболонь", "district": "Оболонський"},
            {"id": "st-2", "name": "Позняки", "district": "Дарницький"},
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
            {"id": "st-silent", "name": "Перемоги 15"},
            {"id": "st-dp", "city": "Дніпро", "name": "Перемоги 21"},
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
        stations = [{"city": "Київ", "name": "Оболонь", "district": "Оболонський"}]
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
        {"id": "st-dp", "city": "Дніпро", "name": "Перемоги 15"},
        {"id": "st-dp-2", "city": "Дніпро", "name": "Робоча 20"},
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
            stations=[{"id": "st-dp", "city": "Дніпро", "name": "Перемоги 15"}],
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
        ("на Оболоні", [{"id": "st-1", "city": "Київ", "name": "Оболонь"}], "Київ"),
        ("на Оболоні", [{"id": "st-1", "city": "Київ", "name": "Оболонь"}], "Дніпро"),
    ]

    @pytest.mark.parametrize("text,stations,city", CASES)
    async def test_same_answer_on_the_same_input(
        self, text: str, stations: list[dict], city: str | None
    ) -> None:
        c = ctx(text, stations=stations, city=city)
        parsed = PARSER.parse(c)

        assert await PARSER.aresolve(c, parsed) == resolve_station_from_session(c, parsed)
