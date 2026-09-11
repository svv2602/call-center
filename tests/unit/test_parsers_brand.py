"""`brand_parser` — curated list synchronously, 14 561 aliases on demand.

`parse()` is the curated whole-word list `preparse.preparse_fitting` owns; it
deliberately holds STT mutations («билл» → BYD, «лікер» → Zeekr, «нісан джук» →
Nissan Juke off call 2026-09-03), which is why a hit is `0.9` and not `1.0`.

`aresolve()` is the Wave 8 alias table (`e67c6d7`). It is a DB round-trip, so
in `shadow` the parser is the curated list and nothing more — «no await → no
network» depends on `parse()` never reaching it.

Mocking rules observed here
---------------------------
`AsyncMock` is always built with `spec=` bound to the **real**
`resolve_by_alias`. A mock without a spec makes a call signature green that
production does not have, which has already cost this project once.

`src.agent.vehicle_alias_lookup` imports SQLAlchemy at module scope and the
local dev venv does not carry it (63 collection errors on `tests/unit/` come
from the same gap). The `alias_lookup` fixture installs a minimal `sqlalchemy`
stub **only when the package is genuinely absent**, so these tests run both
here and in CI — rather than skipping, which would hide the seam exactly where
`spec=` is supposed to be checked.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from src.agent.parsers import ParseContext
from src.agent.parsers.base import NOT_MENTIONED
from src.agent.parsers.brand_parser import (
    _ALIAS_CONFIDENCE,
    _CURATED_CONFIDENCE,
    _MAX_CANDIDATES,
    PARSER,
    _candidates,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


def ctx(text: str, *, conn: Any = None) -> ParseContext:
    return ParseContext(customer_text=text, conn=conn)


@pytest.fixture
def alias_lookup() -> Iterator[Any]:
    """The real `vehicle_alias_lookup` module, importable in a bare venv.

    `resolve_by_alias` is restored unconditionally. The tests below swap it for
    a mock on the live module object, and where SQLAlchemy is genuinely
    installed — CI, prod image — the module is never re-imported, so without
    this the first test leaves a mock behind and every later `spec=` reads that
    mock instead of the real function (`InvalidSpecError`).
    """
    injected = importlib.util.find_spec("sqlalchemy") is None
    if injected:
        stub = types.ModuleType("sqlalchemy")
        stub.text = lambda statement: statement  # type: ignore[attr-defined]
        sys.modules["sqlalchemy"] = stub
    module = importlib.import_module("src.agent.vehicle_alias_lookup")
    original = module.resolve_by_alias
    try:
        yield module
    finally:
        module.resolve_by_alias = original
        if injected:
            sys.modules.pop("src.agent.vehicle_alias_lookup", None)
            sys.modules.pop("sqlalchemy", None)


class TestCuratedListSync:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Тойота", "Toyota"),
            ("BMW", "BMW"),
            ("у мене Nissan Qashqai", "Nissan"),
            # STT mutations that are in the curated list on purpose.
            ("билл", "BYD"),  # → BYD
            ("лікер", "Zeekr"),  # → Zeekr
            ("нісан джук", "Nissan Juke"),  # call 2026-09-03 Wave 4 #3
            ("деву", "Daewoo"),  # call 1a799364
        ],
    )
    def test_hit_is_a_value(self, text: str, expected: str) -> None:
        outcome = PARSER.parse(ctx(text))
        assert outcome.status == "value"
        assert outcome.value == expected
        assert outcome.confidence == _CURATED_CONFIDENCE

    def test_confidence_is_not_one(self) -> None:
        """The list holds STT mutations — a hit is solid, not literal."""
        assert _CURATED_CONFIDENCE == 0.9

    def test_parse_does_no_io(self) -> None:
        """A connection in the context changes nothing about the sync pass."""
        sentinel = object()
        assert PARSER.parse(ctx("Тойота", conn=sentinel)).value == "Toyota"

    def test_duster_is_curated_despite_being_a_model(self) -> None:
        """Wave 6-A note: «Дастер» *is* in the curated list («дастер» → Renault).

        The plan's anchor named «Дастер» and «Тігуан» together as alias-table-only.
        Only «Тігуан» is — this pins the difference so the two do not get
        conflated again.
        """
        assert PARSER.parse(ctx("Дастер")).value == "Renault"


class TestNotMentioned:
    @pytest.mark.parametrize(
        "text",
        ["", "   ", "не пам'ятаю", "завтра о 14:00", "Оболонь", "Тігуан"],
    )
    def test_nothing_in_the_curated_list(self, text: str) -> None:
        outcome = PARSER.parse(ctx(text))
        assert outcome.status == "not_mentioned"
        assert outcome.value is None


class TestUnresolvedIsUnreachableSync:
    def test_the_sync_pass_grades_at_one_level(self) -> None:
        statuses = {
            PARSER.parse(ctx(text)).status for text in ("Тойота", "BMW", "Тігуан", "", "билл")
        }
        assert statuses == {"value", "not_mentioned"}


class TestCandidateOrdering:
    def test_the_whole_utterance_comes_first(self) -> None:
        assert _candidates("тігуан")[0] == "тігуан"

    def test_words_follow_longest_first(self) -> None:
        candidates = _candidates("у мене фольксваген тігуан")
        assert candidates[0] == "у мене фольксваген тігуан"
        words = candidates[1:]
        assert words == sorted(words, key=len, reverse=True)

    def test_short_words_are_dropped(self) -> None:
        """«БМВ» is three characters — anything shorter is not worth a trip."""
        assert "у" not in _candidates("у мене тігуан")

    def test_round_trips_are_capped(self) -> None:
        long_text = "мене цікавить ось така машина марки якоїсь невідомої зовсім"
        assert len(_candidates(long_text)) <= _MAX_CANDIDATES

    def test_empty_text_yields_nothing(self) -> None:
        assert _candidates("   ") == []


class TestAresolve:
    async def test_no_connection_means_no_round_trip(self, alias_lookup: Any) -> None:
        """§3.2 rule 2 — `aresolve` needs what it needs, or it does nothing."""
        mock = AsyncMock(spec=alias_lookup.resolve_by_alias)
        alias_lookup.resolve_by_alias = mock

        outcome = await PARSER.aresolve(ctx("Тігуан", conn=None), NOT_MENTIONED)

        assert outcome is NOT_MENTIONED
        mock.assert_not_awaited()

    async def test_alias_hit_becomes_a_value(self, alias_lookup: Any) -> None:
        """«Тігуан» is a model, not a brand — only the Wave 8 table has it."""
        mock = AsyncMock(
            spec=alias_lookup.resolve_by_alias,
            return_value=alias_lookup.ResolveResult(brand_name="Volkswagen"),
        )
        alias_lookup.resolve_by_alias = mock

        outcome = await PARSER.aresolve(ctx("Тігуан", conn=object()), NOT_MENTIONED)

        assert outcome.status == "value"
        assert outcome.value == "Volkswagen"
        assert outcome.confidence == _ALIAS_CONFIDENCE
        mock.assert_awaited()

    async def test_ambiguous_alias_is_not_guessed(self, alias_lookup: Any) -> None:
        mock = AsyncMock(
            spec=alias_lookup.resolve_by_alias,
            return_value=alias_lookup.ResolveResult(brand_name="BMW", ambiguous=True),
        )
        alias_lookup.resolve_by_alias = mock

        outcome = await PARSER.aresolve(ctx("пятсотка", conn=object()), NOT_MENTIONED)

        assert outcome is NOT_MENTIONED
        assert outcome.value is None

    async def test_a_miss_keeps_the_parse_result(self, alias_lookup: Any) -> None:
        mock = AsyncMock(
            spec=alias_lookup.resolve_by_alias,
            return_value=alias_lookup.ResolveResult(),
        )
        alias_lookup.resolve_by_alias = mock

        outcome = await PARSER.aresolve(ctx("невідомо що", conn=object()), NOT_MENTIONED)

        assert outcome is NOT_MENTIONED

    async def test_a_db_failure_is_not_swallowed(self, alias_lookup: Any) -> None:
        """§3.2 rule 4 / `37fb2d0`: the engine logs it, the parser re-raises."""
        mock = AsyncMock(
            spec=alias_lookup.resolve_by_alias,
            side_effect=RuntimeError("connection reset"),
        )
        alias_lookup.resolve_by_alias = mock

        with pytest.raises(RuntimeError, match="connection reset"):
            await PARSER.aresolve(ctx("Тігуан", conn=object()), NOT_MENTIONED)

    def test_the_spec_is_bound_to_the_real_function(self, alias_lookup: Any) -> None:
        """What `spec=` buys: the mock cannot grow an API prod does not have.

        `resolve_by_alias` really exists and really is a coroutine function; a
        bare `AsyncMock()` would answer to any attribute and make a call path
        green that production cannot reach.
        """
        import inspect

        assert inspect.iscoroutinefunction(alias_lookup.resolve_by_alias)

        mock = AsyncMock(spec=alias_lookup.resolve_by_alias)
        assert inspect.iscoroutinefunction(mock)
        with pytest.raises(AttributeError):
            mock.resolve_by_alias_v2  # noqa: B018 — the point is the lookup


class TestContract:
    def test_registry_identity(self) -> None:
        assert PARSER.name == "brand_parser"
        assert PARSER.field_name == "brand"

    def test_aresolve_is_declared(self) -> None:
        assert PARSER.aresolve is not None
