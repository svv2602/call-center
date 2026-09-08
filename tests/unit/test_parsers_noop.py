"""`noop_parser` — the states that collect nothing, plus the registry invariant.

WELCOME / BOOK / DONE / TRANSFER all declare `parser="noop_parser"`. The point
of the entry is that a lookup on a legitimate state name never raises: a
`KeyError` there turns a no-op into a dropped call.

The registry invariant lives here too, because it is the same question one
level up — «is every `StateConfig.parser` reachable?». `registry.py` names it
as the invariant Wave 6-A pins down, and it is cheap enough that the only
excuse for not having it is forgetting.
"""

from __future__ import annotations

import pytest

from src.agent.parsers import ParseContext
from src.agent.parsers.noop_parser import PARSER


def ctx(text: str) -> ParseContext:
    return ParseContext(customer_text=text)


class TestAlwaysNotMentioned:
    @pytest.mark.parametrize(
        "text",
        [
            "",
            "   ",
            "так",
            "з собою",
            "Київ",
            "мене звати Олена",
            "550e8400-e29b-41d4-a716-446655440000",
        ],
    )
    def test_every_input_is_not_mentioned(self, text: str) -> None:
        outcome = PARSER.parse(ctx(text))
        assert outcome.status == "not_mentioned"
        assert outcome.value is None
        assert outcome.confidence == 0.0
        assert outcome.spans == ()

    def test_the_other_two_statuses_are_unreachable(self) -> None:
        """`parse` has one `return` and it is `NOT_MENTIONED`.

        Stated as a test rather than left implicit: if a future wave gives the
        no-op parser a value path, this is the line that has to be deleted on
        purpose.
        """
        statuses = {
            PARSER.parse(ctx(text)).status
            for text in ("", "з собою", "Київ", "так", "R16", "завтра о 14:00")
        }
        assert statuses == {"not_mentioned"}


class TestContract:
    def test_field_name_is_none(self) -> None:
        """The four states it backs have no `field_name` either."""
        assert PARSER.field_name is None

    def test_name_matches_the_registry_key(self) -> None:
        assert PARSER.name == "noop_parser"

    def test_no_aresolve(self) -> None:
        assert PARSER.aresolve is None

    def test_context_is_ignored_not_inspected(self) -> None:
        """A `ParseContext` with nothing filled in must still be accepted."""
        assert PARSER.parse(ParseContext(customer_text="")).status == "not_mentioned"


class TestRegistryInvariant:
    """`registry.unregistered_state_parsers()` — an empty set or a bug."""

    def test_every_state_parser_is_reachable(self) -> None:
        from src.agent.parsers.registry import unregistered_state_parsers

        assert unregistered_state_parsers() == set()

    def test_registry_keys_match_the_parser_names(self) -> None:
        from src.agent.parsers.registry import PARSERS

        for key, parser in PARSERS.items():
            assert parser.name == key

    def test_intent_classifier_is_not_a_field_parser(self) -> None:
        """It is `async def` and calls an LLM — registering it would be a lie."""
        from src.agent.parsers.registry import NON_FIELD_PARSERS, PARSERS, get_parser

        assert "intent_classifier" not in PARSERS
        assert "intent_classifier" in NON_FIELD_PARSERS
        assert get_parser("intent_classifier") is None

    def test_passive_parsers_exist_in_the_registry(self) -> None:
        from src.agent.parsers.registry import PARSERS, PASSIVE_PARSERS

        for name in PASSIVE_PARSERS:
            assert name in PARSERS
