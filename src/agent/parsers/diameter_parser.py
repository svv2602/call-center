"""`diameter_parser` — wrapper over `diameter_detect.detect_diameter`.

State-bound (PRICE_INTERRUPT) **and** passive: a caller names a diameter in
answer to a price question from anywhere in the main flow. A passive parser
writes `fsm_filled_fields[...]` and never calls `apply_field()` — doing so
would jump the machine to PRICE_INTERRUPT's `next_state` from wherever the
caller actually is (§3.4).

Confidence is where the targeted mode earns its keep. A bare «16» is a
diameter, an hour and a day of the month at once, so the broad pass grades it
`0.6` and refuses to act. When the bot's own last turn was the diameter
question the homonymy is gone and the same «16» is worth `1.0` — this is the
mechanism from §3.3, not a new one: `is_diameter_question` already gates the
Wave 12 auto-detect block in the pipeline.

Wave 12's guard inside `detect_diameter` (R-prefix first, multi-word ordinals
before single-word, lookarounds that exclude «14:30» / «140» / «2024») is left
untouched — it is the reason the bot stopped quoting «R16 у Києві коштує
354 грн» for a size nobody named (calls f70deab5, ebe7dfcb).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.agent.compound_parse import _diameter_confidence, _normalize
from src.agent.diameter_detect import detect_diameter, is_diameter_question
from src.agent.parsers.base import NOT_MENTIONED, ParseOutcome, graded

if TYPE_CHECKING:
    from src.agent.parsers.base import ParseContext

#: The bot just asked for the diameter — the answer can not be anything else.
_ASKED_CONFIDENCE = 1.0


class DiameterParser:
    """PRICE_INTERRUPT, plus passive on every turn."""

    name = "diameter_parser"
    field_name = "diameter"
    aresolve = None

    def parse(self, ctx: ParseContext) -> ParseOutcome:
        text = (ctx.customer_text or "").strip()
        if not text:
            return NOT_MENTIONED
        diameter = detect_diameter(text)
        if diameter is None:
            return NOT_MENTIONED

        if is_diameter_question(ctx.last_bot_utterance):
            return graded(diameter, _ASKED_CONFIDENCE)
        return graded(diameter, _diameter_confidence(_normalize(text)))


PARSER = DiameterParser()
