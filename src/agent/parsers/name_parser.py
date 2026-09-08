"""`name_parser` — the thirteenth parser, the one without a state.

There is no NAME state in §2.1 on purpose: the name arrives from the caller
profile or is auto-persisted on the first turn. But `name` is in CONFIRM's
`required_context`, so the parser is registered as **passive** — the engine
runs it on every turn while `fsm_filled_fields["name"]` is empty, and it never
moves the state (§3.4). This wave does not add a NAME state and must not.

Two paths, matching the two modes of §3.5:

* the bot just asked «Як до вас звертатися?» → `is_name_question` is true and
  `detect_name` runs ungated on a short answer («Юра»);
* otherwise only an explicit self-introduction counts, through
  `compound_parse._detect_name` («мене звати Олена»).

The gate is not optional. Ungated, `detect_name` accepts «Ммм», «Що?»,
«Завтра» and «Оболонь» as names, and a wrong name is how «Марина» — the bot's
own name — ended up in `book_fitting` on call fcfb26a9.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.agent.compound_parse import _detect_name
from src.agent.name_detect import detect_name, is_name_question
from src.agent.parsers.base import NOT_MENTIONED, ParseOutcome, graded

if TYPE_CHECKING:
    from src.agent.parsers.base import ParseContext

#: The Krok 0 question was asked; a short answer to it is the name.
_ASKED_CONFIDENCE = 1.0


class NameParser:
    """Passive. No `StateConfig` points at it."""

    name = "name_parser"
    field_name = "name"
    aresolve = None

    def parse(self, ctx: ParseContext) -> ParseOutcome:
        text = (ctx.customer_text or "").strip()
        if not text:
            return NOT_MENTIONED

        if is_name_question(ctx.last_bot_utterance):
            name = detect_name(text)
            if name:
                return graded(name, _ASKED_CONFIDENCE)
            return NOT_MENTIONED

        hit = _detect_name(text)
        if hit is None:
            return NOT_MENTIONED
        return graded(hit.value, hit.confidence, hit.spans)


PARSER = NameParser()
