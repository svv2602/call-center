"""Shared contract for the targeted field parsers (Wave 5-A, T19).

Design spec: `doc/development/fsm-refactor.md` §3.1–3.4.

Why this package exists
-----------------------
There are two ways to pull a field out of a customer turn, and both are needed:

* **broad pass** — «one sweep over the utterance, take everything visible».
  That is :func:`src.agent.compound_parse.compound_parse`; it is written,
  tested and running in production.
* **targeted pass** — «the state asked for one field, parse the answer to it».
  Today that lives as a hand-rolled block inside
  `CallPipeline._transcript_processor_loop` (`is_name_question` → `detect_name`,
  `is_diameter_question` → `detect_diameter`, `bot_listed_slots` →
  `detect_time_choice`). This package is that block, made addressable by name.

**One detector per field, two call sites.** Every parser here *calls* the same
`src/agent/*_detect.py` function the broad pass calls. A targeted parser that
grows its own regex for a field the broad pass already handles is a review
defect, not an optimisation (§3.1). The difference between the two modes lives
entirely in the context on the input and, as a consequence, in the confidence.

Nothing in this package is wired to the FSM engine — that is Wave 6-B. Nothing
here does I/O in :meth:`FieldParser.parse`; the shadow-mode invariant «no await
→ no network» from `_run_fsm_deterministic_step` depends on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from src.agent.compound_parse import APPLY_THRESHOLD

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime

    from src.agent.fitting_fsm import FsmState
    from src.core.call_session import CallSession

__all__ = [
    "APPLY_THRESHOLD",
    "NOT_MENTIONED",
    "FieldParser",
    "ParseContext",
    "ParseOutcome",
    "ParseStatus",
    "graded",
    "recent_bot_utterances",
    "unresolved",
]

#: Three outcomes, not two — see :class:`ParseOutcome`.
ParseStatus = Literal["value", "unresolved", "not_mentioned"]


@dataclass(frozen=True)
class ParseContext:
    """Everything any of the parsers may ask for. One type for all of them.

    Parsers do **not** take their inputs as arguments — they reach into the
    context themselves (§3.2). `time_parser` needs three different things
    (offered slots, whether the bot just read the list out, the already pinned
    slot); `brand_parser` needs a DB connection; `diameter_parser` needs the
    bot's last turn. Giving each of them its own signature would push that
    difference into the engine, which is exactly what variant C of §3.1 was
    rejected for.
    """

    customer_text: str
    last_bot_utterance: str = ""
    state: FsmState | None = None
    session: CallSession | None = None
    #: Injected by the caller. Parsers never read the process clock themselves:
    #: a parser is a deterministic function of its context, and a test that can
    #: not pin "today" can not pin a relative date either.
    now: datetime | None = None
    #: DB connection. ``None`` forbids :attr:`FieldParser.aresolve`.
    conn: Any = None


@dataclass(frozen=True)
class ParseOutcome:
    """The result of parsing one field.

    Three outcomes, not two. ``not_mentioned`` («the caller said nothing about
    a date») and ``unresolved`` («they said something we could not pin down»)
    need different re-asks — the same distinction `CompoundParseResult` already
    keeps by leaving weak matches visible in ``fields``.

    Deliberately shaped like `compound_parse._Hit` (`value` / `confidence` /
    `spans`) so the seam between broad and targeted passes stays a field
    rename, not a model translation.
    """

    value: Any = None
    confidence: float = 0.0
    spans: tuple[tuple[int, int], ...] = ()
    status: ParseStatus = "not_mentioned"


#: The «caller said nothing about this field» answer. Immutable, so one shared
#: instance is safe.
NOT_MENTIONED = ParseOutcome()


def graded(
    value: Any,
    confidence: float,
    spans: tuple[tuple[int, int], ...] = (),
) -> ParseOutcome:
    """Build an outcome whose status follows the one threshold (§3.3).

    At or above :data:`APPLY_THRESHOLD` the field is applicable; below it the
    value is **not thrown away** — it stays visible so the engine can phrase a
    clarifying re-ask («Ви сказали „на шістнадцяту“ — це час чи діаметр?»)
    instead of repeating the plain question.

    There is exactly one threshold in the FSM and it is imported, never
    re-declared: a second copy is how a loosened upstream value gets silently
    inherited.
    """
    return ParseOutcome(
        value=value,
        confidence=confidence,
        spans=spans,
        status="value" if confidence >= APPLY_THRESHOLD else "unresolved",
    )


def unresolved(
    confidence: float = 0.0,
    spans: tuple[tuple[int, int], ...] = (),
    value: Any = None,
) -> ParseOutcome:
    """«The caller spoke about this field, but we could not pin it down.»

    `value` defaults to ``None`` on purpose. A parser whose field has a shape
    (an ISO date, a `station_id`, an `HH:MM` from the offered list) must not
    hand back a raw label *as if it were the value* — that is the latent P0
    this wave exists to close.

    Where a hint is carried deliberately (`station_parser` keeps the landmark
    so shadow mode can log it), it stays below the threshold **and** keeps this
    status, so the engine can never pin it as the field.
    """
    return ParseOutcome(value=value, confidence=confidence, spans=spans, status="unresolved")


def recent_bot_utterances(ctx: ParseContext, limit: int = 2) -> list[str]:
    """The bot's last `limit` turns, newest first.

    Mirrors the Wave 15 block in `src/core/pipeline.py`: a filler re-ask
    («Перепрошую, не розчула…») sits between the question and the answer often
    enough that looking only at the latest turn misses the exchange (call
    7462c08b). Falls back to `ctx.last_bot_utterance` when no session is
    attached, so a parser is still usable from a unit test.
    """
    session = ctx.session
    history = getattr(session, "dialog_history", None) if session is not None else None
    if not history:
        return [ctx.last_bot_utterance] if ctx.last_bot_utterance else []

    collected: list[str] = []
    for turn in reversed(history):
        if getattr(turn, "speaker", None) == "assistant" and getattr(turn, "content", None):
            collected.append(turn.content)
            if len(collected) >= limit:
                break
    return collected


class FieldParser(Protocol):
    """Contract every parser in this package satisfies (§3.2).

    `field_name` is ``str | None`` rather than the spec's bare ``str`` because
    `noop_parser` backs four states (WELCOME/BOOK/DONE/TRANSFER) whose
    `StateConfig.field_name` is itself ``None``. Widening the annotation was
    preferred over inventing a sentinel field name that no state owns.
    """

    #: Matches `StateConfig.parser` — this is the registry key.
    name: str
    #: Key in `session.fsm_filled_fields`; ``None`` for the no-op parser.
    field_name: str | None

    def parse(self, ctx: ParseContext) -> ParseOutcome:
        """Synchronous, no I/O, deterministic. Mandatory for every parser."""
        ...

    #: Optional network/DB resolution. ``None`` for all but brand and station.
    #:
    #: Call rules (§3.2), which the engine owns in Wave 6-B:
    #:   1. `parse()` always runs first — it alone decides if anything is left
    #:      to resolve;
    #:   2. `aresolve()` runs only when `parse()` came back below the threshold
    #:      or without a value, **and** the context carries what it needs,
    #:      **and** the FSM is in `live` mode;
    #:   3. in `shadow` it never runs — that is what keeps «no await → no
    #:      network» true;
    #:   4. a failure is logged at WARNING with the traceback and the engine
    #:      continues with the `parse()` result. `contextlib.suppress` on this
    #:      path is forbidden (`37fb2d0`).
    aresolve: Callable[[ParseContext, ParseOutcome], Awaitable[ParseOutcome]] | None
