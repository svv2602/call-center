"""`color_parser` — thin wrapper over `color_detect.detect_color`.

`detect_color` matches an inflected colour root and already carries its own
false-positive filter (Черкаси, Біла Церква, Сергій…), so the wrapper adds
nothing but the outcome shape. Confidence stays at the `0.9` the broad pass
uses for the same detector — a colour root is pattern-based and solid, but it
is an inference, not a literal.

The COLOR escape hatch («не назвали» after three re-asks, §2.2 row 19) is
*not* here: it is a `max_parser_null` branch that writes a default value, and
its reader is the engine. Wave 6-B owns it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.agent.color_detect import detect_color
from src.agent.compound_parse import _normalize
from src.agent.parsers.base import NOT_MENTIONED, ParseOutcome, graded

if TYPE_CHECKING:
    from src.agent.parsers.base import ParseContext

#: Same level `compound_parse` assigns to a colour root.
_COLOR_CONFIDENCE = 0.9


class ColorParser:
    """COLOR."""

    name = "color_parser"
    field_name = "color"
    aresolve = None

    def parse(self, ctx: ParseContext) -> ParseOutcome:
        text = (ctx.customer_text or "").strip()
        if not text:
            return NOT_MENTIONED
        color = detect_color(_normalize(text))
        if not color:
            return NOT_MENTIONED
        return graded(color, _COLOR_CONFIDENCE)


PARSER = ColorParser()
