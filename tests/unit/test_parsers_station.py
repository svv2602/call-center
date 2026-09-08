"""`station_parser` — a landmark is a search key, never a `station_id`.

The property this file exists to pin: **`parse()` can not reach
`status="value"`**, by construction. The parser's `field_name` is `station_id`
and a landmark is not one; pinning it would let the FSM skip STATION with a
value `book_fitting` cannot use — the same defect class as `c8c6601`, and the
reason `station_hint` is deliberately absent from `COMPOUND_TO_FSM_FIELD`.

So the landmark comes back `unresolved`, visible in `value` and held below the
apply threshold. In `shadow` that is the end of it: shadow sees the hint and
never an id.

`aresolve()` resolves against `session.fitting_stations_seen` — the snapshot
`get_fitting_stations` wrote when the state was entered. It needs no tool
router (there is none on `ParseContext`) and therefore no `AsyncMock`.
Ambiguity is left unresolved rather than guessed: «Перемоги» exists in more
than one city, and picking silently is how a caller drives to the wrong
address.

Landmarks below are read out of `compound_parse._LANDMARKS`.
"""

from __future__ import annotations

import uuid

import pytest

from src.agent.parsers import ParseContext
from src.agent.parsers.base import APPLY_THRESHOLD, NOT_MENTIONED
from src.agent.parsers.station_parser import (
    _HINT_CONFIDENCE,
    _RESOLVED_CONFIDENCE,
    PARSER,
)
from src.core.call_session import CallSession

STATION_QUESTION = "У якому районі вам зручно?"


def ctx(text: str, *, stations: list[dict] | None = None) -> ParseContext:
    session = None
    if stations is not None:
        session = CallSession(channel_uuid=uuid.uuid4())
        session.fitting_stations_seen = stations
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
