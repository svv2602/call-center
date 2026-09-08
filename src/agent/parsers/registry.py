"""Registry: `StateConfig.parser` → the parser instance (§3.4).

The keys are the strings that already sit in `STATES`. Inventing a second
naming scheme on top of the existing one is how two half-correct maps end up
in the same codebase, so the file names, the class `name` attributes and the
registry keys are all the same twelve strings.

`intent_classifier` is **not** in :data:`PARSERS`
------------------------------------------------
INTENT declares `parser="intent_classifier"`, but
`src/agent/intent_classifier.py:classify_intent` is `async def`, takes a
router and calls an LLM. It does not satisfy `FieldParser` — a synchronous,
I/O-free `parse(ctx)`. Registering it anyway would make
`PARSERS["intent_classifier"].parse(ctx)` a lie that fails at the first live
call, so it is listed separately in :data:`NON_FIELD_PARSERS` and the engine
dispatches it on its own path. This is the one state whose handler is async by
nature, and shadow mode must never reach it (LLM call ⇒ network).

Passive parsers
---------------
`name_parser` and `diameter_parser` are also reachable through
:data:`PASSIVE_PARSERS`. They run on every turn while their field is empty and
they **only write `fsm_filled_fields`** — never `apply_field()`, which would
advance the machine along the parser's own state instead of the caller's
(§3.4).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.agent.parsers import (
    booking_id_parser,
    brand_parser,
    city_parser,
    color_parser,
    date_parser,
    diameter_parser,
    name_parser,
    noop_parser,
    station_parser,
    storage_choice_parser,
    time_parser,
    yes_no_parser,
)

if TYPE_CHECKING:
    from src.agent.parsers.base import FieldParser

__all__ = [
    "NON_FIELD_PARSERS",
    "PARSERS",
    "PASSIVE_PARSERS",
    "get_parser",
    "unregistered_state_parsers",
]

#: Every `FieldParser` in the package, keyed by `StateConfig.parser`.
PARSERS: dict[str, FieldParser] = {
    parser.name: parser
    for parser in (
        noop_parser.PARSER,
        city_parser.PARSER,
        station_parser.PARSER,
        storage_choice_parser.PARSER,
        date_parser.PARSER,
        time_parser.PARSER,
        color_parser.PARSER,
        brand_parser.PARSER,
        yes_no_parser.PARSER,
        diameter_parser.PARSER,
        booking_id_parser.PARSER,
        name_parser.PARSER,
    )
}

#: Handlers a state may name that are deliberately *not* `FieldParser`s.
#: The value is the import path of the real implementation.
NON_FIELD_PARSERS: dict[str, str] = {
    "intent_classifier": "src.agent.intent_classifier.classify_intent",
}

#: Run on every turn while the field is empty; never advance the state.
PASSIVE_PARSERS: tuple[str, ...] = ("name_parser", "diameter_parser")


def get_parser(name: str) -> FieldParser | None:
    """The parser for a `StateConfig.parser` value, or `None`.

    `None` is a legitimate answer for `intent_classifier`: the caller has to
    route it elsewhere rather than treat it as a missing parser.
    """
    return PARSERS.get(name)


def unregistered_state_parsers() -> set[str]:
    """`StateConfig.parser` values this package covers by neither route.

    The invariant Wave 6-A pins down: every parser named in `STATES` is either
    a `FieldParser` here or an acknowledged exception. An empty set is the
    only acceptable result.
    """
    from src.agent.fitting_fsm import STATES

    named = {cfg.parser for cfg in STATES.values()}
    return named - set(PARSERS) - set(NON_FIELD_PARSERS)
