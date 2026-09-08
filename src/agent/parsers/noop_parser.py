"""`noop_parser` — the states that collect nothing.

WELCOME, BOOK, DONE and TRANSFER all declare `parser="noop_parser"` in
`STATES`. They have no `field_name`: WELCOME speaks the greeting, BOOK is a
tool call, DONE and TRANSFER are terminal. The registry still needs an entry
for them, because a lookup that raises `KeyError` on a legitimate state name
would turn a no-op into a dropped call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.agent.parsers.base import NOT_MENTIONED, ParseOutcome

if TYPE_CHECKING:
    from src.agent.parsers.base import ParseContext


class NoopParser:
    """Always `not_mentioned`. Never writes a field."""

    name = "noop_parser"
    field_name: str | None = None
    aresolve = None

    def parse(self, ctx: ParseContext) -> ParseOutcome:
        """`ctx` is accepted and ignored — the signature is the contract."""
        del ctx
        return NOT_MENTIONED


PARSER = NoopParser()
