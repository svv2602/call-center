"""`city_parser` — thin wrapper over `compound_parse._detect_city`.

The city is the one field where the broad and targeted passes coincide
completely (§3.5): naming a city is unambiguous on its own, so there is no
context that would raise or lower the confidence.

**The detector is not reimplemented here.** `_detect_city` runs four ordered
steps, and the order is the fix for call `1b6721a4`: landmarks are matched
first and their spans blanked out before the city stems are searched, because
«на харьковскому» is Харківське шосе *in Kyiv* and matched the Kharkiv stem.
Rewriting the match here would lose that guard silently.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.agent.compound_parse import _detect_city, _normalize
from src.agent.parsers.base import NOT_MENTIONED, ParseOutcome, graded

if TYPE_CHECKING:
    from src.agent.parsers.base import ParseContext


class CityParser:
    """CITY. Confidence comes straight from the detector.

    `1.0` for an explicitly named city, `0.9` for one derived from a landmark,
    `0.6` for a known STT mutation — the last one lands below the threshold on
    purpose so the FSM confirms rather than pins.
    """

    name = "city_parser"
    field_name = "city"
    aresolve = None

    def parse(self, ctx: ParseContext) -> ParseOutcome:
        text = (ctx.customer_text or "").strip()
        if not text:
            return NOT_MENTIONED
        hit = _detect_city(_normalize(text))
        if hit is None:
            return NOT_MENTIONED
        return graded(hit.value, hit.confidence, hit.spans)


PARSER = CityParser()
