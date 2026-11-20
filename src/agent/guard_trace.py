"""Per-turn trace of the guards that fired on a bot reply.

A guard is code that replaced, cut or blocked what the model said or called.
Until now a firing left a Prometheus counter and a log line, and the log line
is gone with the next deploy. The trace is the same fact written where it
survives: the bot's row in `call_turns.guards_fired`.

Shape of one entry: the counter's name without `callcenter_` / `_total`, plus
the counter's own labels under their own names::

    {"guard": "tire_claim_refused", "rule": "cheapest"}
    {"guard": "control_plane_prose_dropped", "form": "json", "site": "stream"}

Nothing else goes in. `call_turns.content` already holds the reply, and a trace
that copied text into a second column would copy the caller's phone or name
with it; label values are what Prometheus already receives.

Why a context variable holding a mutable object, and not a variable re-set per
entry. A whole turn runs in the call's `_transcript_processor_loop` task and in
tasks created *during* the turn (tool `gather`, the wait phrase, TTS). A task
copies the context when it is created, so a child sees the object the parent
put there and its `append` is visible to the parent; a `ContextVar.set()` made
in the child would not be. The other half of that rule is the limit of this
design: a task created *before* `begin_turn()` does not see the turn at all
(its `record()` is a no-op). No guard runs in such a task today —
`tests/unit/test_guard_trace.py` pins both halves.

Two calls never share a trace: each call's loop is its own task with its own
context, and nothing here is process-wide.
"""

from __future__ import annotations

import contextvars
import logging

logger = logging.getLogger(__name__)

#: A label value is a short code (`rule`, `field`, `form`). One of them —
#: `false_transfer_blocked.reason` — is an argument the model wrote, so it is
#: capped rather than trusted to be short.
_MAX_VALUE_CHARS = 80


class _TurnTrace:
    """What fired on one turn of one call."""

    __slots__ = ("call_id", "entries", "open")

    def __init__(self, call_id: str) -> None:
        self.call_id = call_id
        self.entries: list[dict[str, str]] = []
        self.open = True


_current: contextvars.ContextVar[_TurnTrace | None] = contextvars.ContextVar(
    "guard_trace_turn", default=None
)


def begin_turn(call_id: object = None) -> None:
    """Open an empty trace for the turn that starts here.

    A trace the previous turn left open (the caller barged in before the bot
    spoke, so no bot row took it) is closed, not carried over: its entries
    belong to a reply that was never written.
    """
    previous = _current.get()
    if previous is not None:
        previous.open = False
    _current.set(_TurnTrace(str(call_id) if call_id is not None else "unknown"))


def record(guard: str, /, **labels: object) -> None:
    """Add one firing to the current turn; outside a turn this does nothing.

    Called beside the guard's counter increment, with the counter's labels.
    Twice on one turn is two entries — the count is part of the evidence.

    Never raises: this runs inside the guard, on the road to the caller's ear,
    and a trace is not worth a broken reply. A failure is logged with the call.
    """
    trace = _current.get()
    if trace is None or not trace.open:
        return
    try:
        entry = {"guard": guard}
        for name, value in labels.items():
            entry[name] = str(value)[:_MAX_VALUE_CHARS]
        entry["guard"] = guard
        trace.entries.append(entry)
    except Exception:
        logger.warning(
            "Guard trace entry lost: call=%s guard=%s", trace.call_id, guard, exc_info=True
        )


def take() -> list[dict[str, str]] | None:
    """Close the turn and hand over what fired on it.

    `[]` — a turn was open and no guard fired. `None` — there was no turn
    (a phrase spoken by code, not by the model). The two are different facts
    and are stored differently: `[]` against SQL `NULL`.
    """
    trace = _current.get()
    if trace is None:
        return None
    _current.set(None)
    trace.open = False
    return trace.entries


def discard() -> None:
    """Close the turn without writing it anywhere."""
    take()
