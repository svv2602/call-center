"""`yes_no_parser` — CONFIRM, wrapping `confirm_detect` (Wave 15).

Both halves of the Wave 15 fix are preserved and neither is re-implemented:

* `asked_for_confirmation` takes a **list** of the bot's recent turns, newest
  first, not a single string. On call `7462c08b` the bot's *last* turn was the
  re-ask «Скажіть, будь ласка, "так" щоб підтвердити…», which does not contain
  «Підтверджуєте» — looking only at the latest turn missed an exchange that
  was still open, and the LLM then announced a booking it had never made.
  The window is two turns, the same as the pipeline's.
* `is_confirmation` accepts a multi-word pure agreement («так підтверджує»)
  but refuses anything longer than four tokens, because «так, але давайте на
  пʼятницю» carries new information and must reach the LLM unmodified.

**There is no negative detector in the repository.** «ні» therefore comes back
as `unresolved`, not as `False`. Inventing a rejection list here would be a
new regex for a field that already has a detector — the §3.1 anti-pattern —
and getting it wrong means cancelling a booking the caller wanted. The CONFIRM
state re-asks instead; Wave 6-B decides whether a `confirmed=False` path is
worth a detector of its own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.agent.confirm_detect import asked_for_confirmation, is_confirmation
from src.agent.parsers.base import (
    NOT_MENTIONED,
    ParseOutcome,
    graded,
    recent_bot_utterances,
    unresolved,
)

if TYPE_CHECKING:
    from src.agent.parsers.base import ParseContext

#: How far back the open-question marker is searched. Mirrors `pipeline.py`.
_BOT_TURN_WINDOW = 2

#: A pure agreement to an open confirmation question is unambiguous.
_CONFIRMED_CONFIDENCE = 1.0


class YesNoParser:
    """CONFIRM. Writes `confirmed=True` or nothing."""

    name = "yes_no_parser"
    field_name = "confirmed"
    aresolve = None

    def parse(self, ctx: ParseContext) -> ParseOutcome:
        text = (ctx.customer_text or "").strip()
        if not text:
            return NOT_MENTIONED

        if not asked_for_confirmation(recent_bot_utterances(ctx, _BOT_TURN_WINDOW)):
            # No open Krok 8 question — a stray «так» mid-flow is agreement to
            # something else entirely and must not book anything.
            return NOT_MENTIONED

        if is_confirmation(text):
            return graded(True, _CONFIRMED_CONFIDENCE)
        return unresolved()


PARSER = YesNoParser()
