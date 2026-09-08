"""Side-door interrupt handlers for mid-booking caller turns.

Wave 3-B (2026-09-08) of the FSM refactor — tasks T4 (PRICE) and T5 (CANCEL).

While the fitting-booking flow runs, a caller may inject two off-flow intents:

* PRICE — «А скільки коштує шиномонтаж?» → quote a price, then return to the
  step the flow was on.
* CANCEL — «Скасуйте мій запис» → list active bookings, confirm, cancel.

Both handlers are pure turn-level functions: they read a `CallSession`, call at
most two tools through the router, and report what they changed. They do **not**
drive the FSM — freezing and resuming main-flow states is Wave 4-B. Here the FSM
is only *read*, for one thing: the phrase to speak when the caller is brought
back (`StateConfig.resume_phrase`).

Why this file is written the way it is
--------------------------------------
The first version of this module (`96879a6`) shipped to production and was
reverted the same week (`c8c6601`). Three defects, three structural answers:

1. *The fire counter did not survive a session reload.* It lived on the session
   object but never went through `to_dict()`/`from_dict()`. The Call Processor
   is stateless — the session is re-read from Redis every turn — so the cap
   reset instantly and the PRICE handler answered five turns in a row with the
   same sentence («так», «17», «мені» all re-triggered it). The counter now
   lives in `CallSession.interrupt_counts`, which is serialized.

2. *A hardcoded resume phrase* («let's continue the booking»), appended
   unconditionally even when no booking had been started. Resume phrases now
   come from `fitting_fsm.STATES[state].resume_phrase` only, and only when the
   session is actually parked on a resumable main-flow state. With
   `fsm_state is None` there is no phrase and `resume_state` is `None`. This
   module contains no Ukrainian resume wording of its own.

3. *`handled=True` with an empty `session_updates` and `advanced=False`* — the
   handler claimed the turn but nothing moved. `_result()` now downgrades any
   such return to `handled=False` and logs it at ERROR.

Two standing project rules also apply:

* **Default-deny.** A handler fires only on positive evidence in the caller's
  own words (or as an explicit continuation of a question it asked last turn).
  Everything else falls through to the LLM. A named subset of "allowed" cases
  leaves an escape hatch and the model finds it (Waves 15–16).
* **No silent swallow.** Tool failures are logged at ERROR and counted in
  `tool_call_errors_total`. `contextlib.suppress(Exception)` plus a DEBUG line
  is how three of three bookings were lost without a trace (`37fb2d0`).
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.agent.fitting_fsm import FROZEN_STATES, STATES, FsmState

if TYPE_CHECKING:
    from src.core.call_session import CallSession

logger = logging.getLogger(__name__)

# Prometheus counter for tool failures. Guarded so this module still imports in
# environments without `prometheus_client` (same pattern as `fitting_fsm`); a
# missing metric must never break a live call, but it is logged, not hidden.
try:  # pragma: no cover - import-time branch
    from src.monitoring.metrics import tool_call_errors_total

    _METRICS_AVAILABLE = True
except ImportError:  # pragma: no cover - metrics deps not installed
    tool_call_errors_total = None  # type: ignore[assignment]
    _METRICS_AVAILABLE = False
    logger.warning(
        "interrupts: tool_call_errors_total not importable — tool failures will be "
        "logged but not measured"
    )


# --- Tuning constants -------------------------------------------------------

#: Handler names used as keys in `CallSession.interrupt_counts`.
PRICE_HANDLER = "price"
CANCEL_HANDLER = "cancel"

#: How many times a handler may be *entered* from scratch during one call.
#: This is the loop-breaker. Two is deliberately small: a caller who genuinely
#: needs a third price quote is better served by the LLM (or an operator) than
#: by a short-circuiting handler that cannot tell it is repeating itself.
MAX_INTERRUPT_FIRES = 2

#: How many follow-up turns one multi-turn interrupt may consume before the
#: handler gives up and lets the LLM take over. A cancel with several bookings
#: legitimately needs two (pick a booking, then confirm); the third is the
#: budget for a single "не зрозуміла" re-ask.
MAX_INTERRUPT_FOLLOWUPS = 3

#: Suffix for the follow-up counters kept in the same `interrupt_counts` dict.
_FOLLOWUP_SUFFIX = "_followup"

#: Valid wheel diameters for fitting (inclusive).
MIN_DIAMETER = 13
MAX_DIAMETER = 24

#: Cancel sub-flow markers stored in `CallSession.pending_cancel_action`.
#: The confirmation marker carries its booking id (`awaiting_confirmation:<id>`)
#: because the flow must remember *which* booking the caller picked and the
#: session contract for this wave allows exactly one string field for it.
CANCEL_AWAITING_SELECTION = "awaiting_selection"
CANCEL_AWAITING_CONFIRMATION = "awaiting_confirmation"

#: Booking ids the 1C layer never issues — mirrors the guard in
#: `_cancel_fitting` (src/main.py). Kept in sync deliberately: a handler that
#: passes one of these through would be rejected downstream anyway, and the
#: caller would hear an error instead of an answer.
_PLACEHOLDER_BOOKING_IDS: frozenset[str] = frozenset(
    {"", "0", "000000000", "00000000-0000-0000-0000-000000000000", "unknown", "none", "null"}
)


# --- Result -----------------------------------------------------------------


@dataclass
class InterruptResult:
    """Outcome of one interrupt-handler invocation.

    Attributes:
        handled: True when the handler owns this turn and `reply_to_customer`
            should be spoken. False means "fall through to the LLM agent" —
            always a safe answer, and the default whenever anything is unclear.
        reply_to_customer: Ukrainian text for TTS. Empty when `handled=False`.
        resume_state: Name of the main-flow FSM state the caller should be
            returned to once the interrupt closes, or None when there is
            nothing to return to (no booking in progress, or the flow ended).
        session_updates: Exactly the session fields the handler wrote. The
            handler also applies them to `session` in place — the pipeline is
            not trusted to replay them, because the loop-breaker counter must
            be correct even if a caller hangs up mid-turn.
        advanced: True when the dialog moved forward (a question was answered,
            a new question was asked, a booking was cancelled).
    """

    handled: bool
    reply_to_customer: str = ""
    resume_state: str | None = None
    session_updates: dict[str, Any] = field(default_factory=dict)
    advanced: bool = False


def _result(
    handled: bool,
    reply: str = "",
    resume_state: str | None = None,
    session_updates: dict[str, Any] | None = None,
    advanced: bool = False,
) -> InterruptResult:
    """Build an `InterruptResult`, enforcing the "must move or refuse" rule.

    A `handled=True` return with nothing to say, or with neither `advanced` nor
    any `session_updates`, means the handler consumed a turn without changing
    anything — the caller then hears the same sentence again next turn. That is
    the `c8c6601` failure mode, so it is downgraded to `handled=False` here
    rather than left to code review.
    """
    updates = dict(session_updates or {})
    if not handled:
        return InterruptResult(
            handled=False,
            reply_to_customer="",
            resume_state=None,
            session_updates=updates,
            advanced=False,
        )
    if not reply.strip():
        logger.error(
            "interrupts: contract violation — handled=True with an empty reply; "
            "falling through to the LLM (updates=%s)",
            updates,
        )
        return InterruptResult(handled=False, session_updates=updates)
    if not advanced and not updates:
        logger.error(
            "interrupts: contract violation — handled=True but nothing advanced and "
            "no session updates; falling through to the LLM (reply=%r)",
            reply,
        )
        return InterruptResult(handled=False)
    return InterruptResult(
        handled=True,
        reply_to_customer=reply,
        resume_state=resume_state,
        session_updates=updates,
        advanced=advanced,
    )


# --- Intent evidence (default-deny) -----------------------------------------

#: Substrings that count as the caller asking about price. Ukrainian first,
#: Russian variants next — STT returns both (primary uk-UA, alternative ru-RU).
_PRICE_MARKERS: tuple[str, ...] = (
    "скільки кошт",
    "скільки бу",
    "скільки з мене",
    "скільки вийде",
    "скільки платити",
    "скільки треба плат",
    "почому",
    "по чому",
    "ціна",
    "ціни",
    "ціну",
    "цінами",
    "вартіст",
    "вартість",
    "прайс",
    "сколько сто",
    "сколько буд",
    "сколько с меня",
    "цена",
    "цену",
    "цены",
    "стоимост",
)

#: Explicit denials — the caller is talking *about* not wanting a price. These
#: win over the markers above so «мене не цікавить ціна» never opens the flow.
_PRICE_DENIALS: tuple[str, ...] = (
    "не про ціну",
    "не про ціни",
    "не про вартість",
    "не питаю про ціну",
    "не цікавить ціна",
    "не цікавить вартість",
    "не про цену",
    "не спрашиваю про цену",
)

#: Substrings that count as the caller asking to cancel an existing booking.
_CANCEL_MARKERS: tuple[str, ...] = (
    "скасуй",
    "скасув",
    "скасов",
    "відмін",
    "відміни",
    "анулюй",
    "анулю",
    "отмени",
    "отменит",
    "отменю",
    "аннулир",
    "не приїду",
    "не зможу приїхати",
    "не зможу прийти",
    "не приеду",
    "зніміть запис",
    "прибрати запис",
    "прибери запис",
    "убрать запись",
)

#: Denials for the cancel flow — «не треба скасовувати» must not cancel.
_CANCEL_DENIALS: tuple[str, ...] = (
    "не скасовуй",
    "не скасовуйте",
    "не треба скасов",
    "не потрібно скасов",
    "не хочу скасов",
    "не отменяй",
    "не надо отмен",
)

_YES_MARKERS: tuple[str, ...] = (
    "так",
    "да",
    "ага",
    "угу",
    "звичайно",
    "звісно",
    "конечно",
    "давайте",
    "давай",
    "підтверджую",
    "підтверджу",
    "вірно",
    "правильно",
    "скасовуйте",
    "скасуйте",
    "отменяйте",
    "yes",
)

_NO_MARKERS: tuple[str, ...] = (
    "ні",
    "нет",
    "не треба",
    "не потрібно",
    "не хочу",
    "не буду",
    "залиш",
    "оставь",
    "оставьте",
    "передумав",
    "передумала",
    "помилка",
    "no",
)


def _norm(text: str | None) -> str:
    """Lowercase, apostrophe-normalized text for marker matching."""
    if not text:
        return ""
    return text.lower().replace("’", "'").replace("`", "'").strip()


#: «скільки коштує» with filler words in between. The substring markers above
#: are contiguous, so «скільки **це** коштує» — the ordinary way to ask — slid
#: past them and the handler declined the turn even when the classifier had
#: called it PRICE. Up to two words of slack, no more: the two halves have to
#: stay in the same clause.
_PRICE_GAPPED: tuple[re.Pattern[str], ...] = (
    re.compile(r"скільки\s+(?:\S+\s+){0,2}кошт"),
    re.compile(r"сколько\s+(?:\S+\s+){0,2}сто"),
    re.compile(r"скільки\s+(?:\S+\s+){0,2}пла[тч]"),
)

#: Roots matched at a word start, so every case ending is covered («ціна»,
#: «ціни», «ціні», «цінами») without listing them. `_starts_word` is what keeps
#: «оцініть» from reading as a price question — a bare substring would not.
_PRICE_ROOTS: tuple[str, ...] = ("цін", "цен", "вартіс", "вартост", "стоимост")


def _mentions_price(text: str) -> bool:
    """True when the caller's own words ask about price (default-deny)."""
    lowered = _norm(text)
    if not lowered:
        return False
    if any(denial in lowered for denial in _PRICE_DENIALS):
        return False
    if any(marker in lowered for marker in _PRICE_MARKERS):
        return True
    if any(pattern.search(lowered) for pattern in _PRICE_GAPPED):
        return True
    return any(_starts_word(lowered, root) for root in _PRICE_ROOTS)


def _mentions_cancel(text: str) -> bool:
    """True when the caller's own words ask to cancel (default-deny)."""
    lowered = _norm(text)
    if not lowered:
        return False
    if any(denial in lowered for denial in _CANCEL_DENIALS):
        return False
    return any(marker in lowered for marker in _CANCEL_MARKERS)


def classify_interrupt_text(text: str) -> str | None:
    """Which interrupt the caller's own words open, by markers alone.

    Returns `"price"`, `"cancel"` or `None`. Pure and synchronous on purpose:
    the FSM seam runs in shadow mode, where reaching the intent classifier
    would mean a network call. These are the same marker lists the live
    handlers use, so the seam and `_maybe_handle_intent` agree on what counts
    as an interrupt instead of drifting apart into two definitions.

    Deliberately narrower than the classifier: it recognises only the two
    intents that actually have a handler. A knowledge-base question
    («чи є гарантія?») is not detected here and keeps charging the parser-null
    budget, which is today's behaviour — widening this would need a
    general question detector, and inventing one is out of scope.
    """
    if _mentions_cancel(text):
        # Checked first: «скасуйте, скільки поверне?» is a cancellation that
        # happens to mention money, not a price question.
        return "cancel"
    if _mentions_price(text):
        return "price"
    return None


#: Character class used for word-boundary checks. `\b` is unreliable here: the
#: transcripts mix Ukrainian, Russian and Latin, and a plain substring match
#: turns «мені» into a «ні» (= "no"), which is how a booking gets cancelled by
#: accident.
_WORD_CHARS = "а-яіїєґёa-z0-9'"


def _starts_word(lowered: str, marker: str) -> bool:
    """True when `marker` occurs at the start of a word (suffixes allowed)."""
    return re.search(rf"(?<![{_WORD_CHARS}]){re.escape(marker)}", lowered) is not None


def _whole_word(lowered: str, marker: str) -> bool:
    """True when `marker` occurs as a complete word."""
    pattern = rf"(?<![{_WORD_CHARS}]){re.escape(marker)}(?![{_WORD_CHARS}])"
    return re.search(pattern, lowered) is not None


def _yes_no(text: str) -> str:
    """Classify a confirmation answer as "yes" / "no" / "unclear".

    "no" is evaluated first and matched on word stems («залиш» → «залиште»),
    "yes" only on whole words — an accidental yes cancels a real booking, an
    accidental no merely keeps it.
    """
    lowered = _norm(text)
    if not lowered:
        return "unclear"
    if any(_starts_word(lowered, marker) for marker in _NO_MARKERS):
        return "no"
    if any(_whole_word(lowered, marker) for marker in _YES_MARKERS):
        return "yes"
    return "unclear"


# --- Diameter parsing -------------------------------------------------------

#: Spoken diameters, uk + ru. Matched longest-first so «двадцять один» beats
#: «двадцять».
_DIAMETER_WORDS: dict[str, int] = {
    "тринадцять": 13,
    "тринадцать": 13,
    "чотирнадцять": 14,
    "четырнадцать": 14,
    "п'ятнадцять": 15,
    "пятнадцять": 15,
    "пятнадцать": 15,
    "шістнадцять": 16,
    "шестнадцять": 16,
    "шестнадцать": 16,
    "сімнадцять": 17,
    "семнадцять": 17,
    "семнадцать": 17,
    "вісімнадцять": 18,
    "вісімнадцать": 18,
    "восемнадцать": 18,
    "дев'ятнадцять": 19,
    "девятнадцять": 19,
    "девятнадцать": 19,
    "двадцять": 20,
    "двадцать": 20,
    "двадцять один": 21,
    "двадцять два": 22,
    "двадцять три": 23,
    "двадцять чотири": 24,
}


def _extract_diameter(text: str) -> int | None:
    """Extract a plausible wheel diameter (13..24) from caller text.

    Accepts «R17», «р17», «17 радіус», a bare «17», or a spoken word form.
    Returns None when nothing in range is found — never a default, because a
    guessed diameter is a wrong price (Wave 12 regression, call ebe7dfcb).
    """
    lowered = _norm(text)
    if not lowered:
        return None
    for word in sorted(_DIAMETER_WORDS, key=len, reverse=True):
        if word in lowered:
            candidate = _DIAMETER_WORDS[word]
            if MIN_DIAMETER <= candidate <= MAX_DIAMETER:
                return candidate
    match = re.search(r"(?:^|[^0-9])(?:[rrрр]\s*)?(\d{2})(?![0-9])", lowered)
    if match:
        candidate = int(match.group(1))
        if MIN_DIAMETER <= candidate <= MAX_DIAMETER:
            return candidate
    return None


# --- Booking-id guard -------------------------------------------------------


def _is_valid_booking_id(booking_id: Any) -> bool:
    """True when `booking_id` looks like a real 1C booking GUID.

    Rejects placeholders and anything that is not a UUID. The downstream
    `cancel_fitting` guard rejects the same set; catching it here means the
    caller hears a graceful fallback instead of a 1C parse error.
    """
    if not isinstance(booking_id, str):
        return False
    trimmed = booking_id.strip()
    if not trimmed or trimmed.lower() in _PLACEHOLDER_BOOKING_IDS:
        return False
    if all(char in "0-" for char in trimmed):
        return False
    try:
        uuid.UUID(trimmed)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


# --- Session helpers --------------------------------------------------------


def _counts(session: CallSession) -> dict[str, int]:
    """The live `interrupt_counts` dict, tolerating a session without one."""
    counts = getattr(session, "interrupt_counts", None)
    if not isinstance(counts, dict):
        counts = {}
        session.interrupt_counts = counts
    return counts


def _is_capped(session: CallSession, handler: str, *, entry: bool) -> bool:
    """True when this handler has already used up its budget for the call.

    Entries (fresh side-door openings) and follow-up turns of one open
    interrupt are counted separately: a multi-turn cancel legitimately spans
    three turns, while three *separate* price questions are the symptom the cap
    exists to stop.
    """
    counts = _counts(session)
    if entry:
        return counts.get(handler, 0) >= MAX_INTERRUPT_FIRES
    return counts.get(handler + _FOLLOWUP_SUFFIX, 0) >= MAX_INTERRUPT_FOLLOWUPS


def _bump(session: CallSession, handler: str, *, entry: bool) -> dict[str, int]:
    """Record one firing and return a copy of the counters for the result."""
    counts = _counts(session)
    key = handler if entry else handler + _FOLLOWUP_SUFFIX
    counts[key] = counts.get(key, 0) + 1
    return dict(counts)


def _reset_followups(session: CallSession, handler: str) -> dict[str, int]:
    """Clear the follow-up budget once an interrupt closes."""
    counts = _counts(session)
    counts[handler + _FOLLOWUP_SUFFIX] = 0
    return dict(counts)


def _apply(session: CallSession, updates: dict[str, Any]) -> dict[str, Any]:
    """Write `updates` onto the session and return them for the result.

    Applying in place is intentional. The pipeline saves the session to Redis
    at the end of the turn; if the handler only *reported* the new counter and
    the pipeline forgot to replay it, the cap would silently stop working —
    the exact regression that got the first version reverted.
    """
    for name, value in updates.items():
        setattr(session, name, value)
    return dict(updates)


def _resume(session: CallSession) -> tuple[str | None, str]:
    """Resolve where to return after the interrupt, and what to say.

    Returns `(resume_state, resume_phrase)`. Both are empty/None unless the
    session is parked on a main-flow state that can actually be resumed:

    * `fsm_state is None` → the booking flow was never started, so there is
      nothing to continue and the handler must not pretend otherwise;
    * a terminal or side state → likewise nothing to resume.

    Wave 4-B seam: from Wave 4-B on, the pipeline calls
    `FsmEngine.freeze_for_interrupt()` *before* this handler runs, so by the time
    we get here `fsm_state` is `PRICE_INTERRUPT` / `CANCEL_INTERRUPT` — never a
    member of `FROZEN_STATES`. The frozen main state lives in `fsm_prev_state`,
    and reading it here is what keeps the caller from hearing a price quote with
    no word about where the booking resumes. Without this fallback the bug is
    silent: `handled=True`, a non-empty reply, `made_progress` satisfied, and
    every Wave 3-B / 4-A test still green.

    The phrase itself always comes from `StateConfig.resume_phrase`. This
    module never hardcodes one.
    """
    state = FsmState.coerce(getattr(session, "fsm_state", None))
    if state is not None and state not in FROZEN_STATES:
        # Parked in a side-state: the resumable state is the frozen snapshot.
        # A terminal state (DONE/TRANSFER) has no snapshot, so this resolves to
        # None and the handler stays silent about resuming — as before.
        state = FsmState.coerce(getattr(session, "fsm_prev_state", None))
    if state is None or state not in FROZEN_STATES:
        return None, ""
    return state.value, STATES[state].resume_phrase


def _join(*parts: str) -> str:
    """Join non-empty sentence fragments with single spaces."""
    return " ".join(part.strip() for part in parts if part and part.strip())


# --- Tool invocation --------------------------------------------------------


async def _call_tool(
    tool_router: Any,
    name: str,
    args: dict[str, Any],
) -> tuple[Any, str | None]:
    """Invoke a tool, returning `(result, error)`.

    Supports both shapes the router is used in: a bound coroutine attribute
    (`tool_router.get_fitting_price(...)`) and the registry
    (`tool_router.execute("get_fitting_price", args)`), preferring the former.

    Never swallows: an exception, a non-dict result and an `{"error": ...}`
    payload are all reported at ERROR and counted, so a broken 1C integration
    shows up in Grafana instead of turning into a silent transfer.
    """
    handler = getattr(tool_router, name, None)
    try:
        if callable(handler):
            result = await handler(**args)
        else:
            result = await tool_router.execute(name, args)
    except Exception as exc:
        logger.error("interrupts: tool %s raised args=%s err=%s", name, args, exc, exc_info=True)
        _count_tool_error(name, "exception")
        return None, str(exc)
    if not isinstance(result, dict):
        logger.error("interrupts: tool %s returned %s, expected dict", name, type(result).__name__)
        _count_tool_error(name, "bad_result")
        return None, "bad_result"
    if result.get("error"):
        logger.error("interrupts: tool %s returned an error payload: %s", name, result)
        _count_tool_error(name, "tool_error")
        return None, str(result.get("error"))
    return result, None


def _count_tool_error(name: str, error_type: str) -> None:
    """Increment the shared tool-error counter, if metrics are available."""
    if not _METRICS_AVAILABLE:
        return
    try:
        tool_call_errors_total.labels(tool_name=name, error_type=error_type).inc()
    except Exception:
        # A broken counter is a real defect; log it loudly rather than at DEBUG.
        logger.exception("interrupts: failed to count tool error for %s", name)


# --- PRICE ------------------------------------------------------------------


def _station_id(session: CallSession) -> str:
    """The station the caller is currently being booked at, if pinned."""
    station_id = getattr(session, "last_fitting_station_id", None)
    return str(station_id) if station_id else ""


def _resolve_city(session: CallSession) -> str | None:
    """City for the price quote: pinned station first, then collected fields.

    Only used to phrase the answer («Шиномонтаж R17 у Києві …»). A missing city
    is not fatal: the handler quotes the station's (or the network's) prices
    without naming a city rather than asking a question it has no session field
    to remember asking.
    """
    station_id = _station_id(session)
    stations = getattr(session, "fitting_stations_seen", None) or []
    if station_id:
        for station in stations:
            if not isinstance(station, dict):
                continue
            if str(station.get("id") or station.get("station_id") or "") == station_id:
                city = station.get("city")
                if city:
                    return str(city)
    filled = getattr(session, "fsm_filled_fields", None) or {}
    city = filled.get("city") if isinstance(filled, dict) else None
    if city:
        return str(city)
    extracted = getattr(session, "extracted_fields", None) or {}
    city = extracted.get("city") if isinstance(extracted, dict) else None
    return str(city) if city else None


#: Category → spoken label for the price breakdown returned by
#: `_get_fitting_price` (it tags each item with `category`).
_CATEGORY_LABELS: tuple[tuple[str, str], ...] = (
    ("car", "легкові"),
    ("suv", "позашляховики"),
    ("microcar", "мікроавтобуси"),
    ("gazelle", "Газель"),
    ("tavriya", "Таврія"),
    ("atv", "квадроцикли"),
)


def _format_prices(prices: list[dict[str, Any]]) -> str:
    """Render 1C price rows as a short spoken fragment.

    Returns an empty string when nothing usable is present — the caller then
    treats it as "no price found" instead of speaking «None грн».
    """
    by_category: dict[str, list[dict[str, Any]]] = {}
    for item in prices:
        if isinstance(item, dict):
            by_category.setdefault(str(item.get("category") or "other"), []).append(item)

    fragments: list[str] = []
    for category, label in _CATEGORY_LABELS:
        for item in by_category.get(category, []):
            value = item.get("price")
            if value not in (None, ""):
                fragments.append(f"{label} — {value} грн")
                break
    if fragments:
        return ", ".join(fragments)

    for item in prices:
        if isinstance(item, dict) and item.get("price") not in (None, ""):
            return f"{item['price']} грн"
    return ""


async def handle_price_interrupt(
    customer_text: str,
    session: CallSession,
    tool_router: Any,
) -> InterruptResult:
    """Answer a mid-booking price question, then hand the flow back.

    Fires only when the caller's own words ask about price, or when the handler
    asked for a diameter on the previous turn and this turn supplies one.
    Everything else returns `handled=False` and the LLM takes the turn.

    Args:
        customer_text: Verbatim STT transcript of this turn.
        session: Live `CallSession`; the handler writes its own fields on it.
        tool_router: Router exposing `get_fitting_price`.

    Returns:
        `InterruptResult`. `handled=False` is always a valid, safe outcome.
    """
    text = customer_text or ""
    awaiting_diameter = bool(getattr(session, "pending_price_interrupt_needs_diameter", False))
    diameter_in_text = _extract_diameter(text)

    # --- Default-deny gate -------------------------------------------------
    if awaiting_diameter:
        # Continuation: accept a number, or the caller repeating the question.
        if diameter_in_text is None and not _mentions_price(text):
            logger.info(
                "price_interrupt: awaiting diameter but turn %r carries neither a "
                "diameter nor a price question — disarming, outcome=skipped",
                text[:60],
            )
            return _result(
                False,
                session_updates=_apply(session, {"pending_price_interrupt_needs_diameter": False}),
            )
        entry = False
    else:
        if not _mentions_price(text):
            return _result(False)
        entry = True

    # --- Loop-breaker (before any tool call) --------------------------------
    if _is_capped(session, PRICE_HANDLER, entry=entry):
        logger.info(
            "price_interrupt: cap reached (entry=%s, counts=%s) — outcome=capped",
            entry,
            _counts(session),
        )
        updates: dict[str, Any] = {}
        if awaiting_diameter:
            updates["pending_price_interrupt_needs_diameter"] = False
        return _result(False, session_updates=_apply(session, updates))

    counts = _bump(session, PRICE_HANDLER, entry=entry)
    city = _resolve_city(session)
    resume_state, resume_phrase = _resume(session)

    # --- Diameter -----------------------------------------------------------
    # The session value wins (it is what `_get_fitting_price`'s Wave 12 guard
    # trusts), except on the turn where the caller is answering our question.
    pinned = getattr(session, "fitting_diameter_client", None)
    diameter = diameter_in_text if (awaiting_diameter and diameter_in_text) else pinned
    if diameter is None:
        diameter = diameter_in_text

    if diameter is None:
        config = STATES[FsmState.PRICE_INTERRUPT]
        question = config.silence_reprompt if awaiting_diameter else config.question_template
        logger.info(
            "price_interrupt: city=%s diameter=None fires=%d outcome=asked_diameter",
            city,
            counts.get(PRICE_HANDLER, 0),
        )
        return _result(
            True,
            reply=question or config.question_template,
            resume_state=resume_state,
            session_updates=_apply(
                session,
                {
                    "pending_price_interrupt_needs_diameter": True,
                    "interrupt_counts": counts,
                },
            ),
            advanced=True,
        )

    # Pin the diameter *before* the tool call: `_get_fitting_price` hard-rejects
    # a quote when `session.fitting_diameter_client` is None (Wave 12 guard).
    _apply(session, {"fitting_diameter_client": int(diameter)})

    args: dict[str, Any] = {"tire_diameter": int(diameter)}
    station_id = _station_id(session)
    if station_id:
        args["station_id"] = station_id
    result, error = await _call_tool(tool_router, "get_fitting_price", args)
    if error is not None:
        logger.error(
            "price_interrupt: city=%s diameter=%s fires=%d outcome=tool_error (%s)",
            city,
            diameter,
            counts.get(PRICE_HANDLER, 0),
            error,
        )
        return _result(
            False,
            session_updates=_apply(
                session,
                {
                    "pending_price_interrupt_needs_diameter": False,
                    "interrupt_counts": _reset_followups(session, PRICE_HANDLER),
                },
            ),
        )

    prices = result.get("prices") or []
    body = _format_prices(prices if isinstance(prices, list) else [])
    closing_updates = _apply(
        session,
        {
            "pending_price_interrupt_needs_diameter": False,
            "fitting_diameter_client": int(diameter),
            "interrupt_counts": _reset_followups(session, PRICE_HANDLER),
        },
    )

    if not body:
        logger.info(
            "price_interrupt: city=%s diameter=%s fires=%d outcome=no_prices",
            city,
            diameter,
            counts.get(PRICE_HANDLER, 0),
        )
        return _result(
            True,
            reply=_join(f"Наразі не бачу ціни для R{diameter}.", resume_phrase),
            resume_state=resume_state,
            session_updates=closing_updates,
            advanced=True,
        )

    intro = f"Шиномонтаж R{diameter}"
    if city:
        intro += f" у місті {city}"
    logger.info(
        "price_interrupt: city=%s diameter=%s fires=%d outcome=quoted",
        city,
        diameter,
        counts.get(PRICE_HANDLER, 0),
    )
    return _result(
        True,
        reply=_join(f"{intro}: {body}.", resume_phrase),
        resume_state=resume_state,
        session_updates=closing_updates,
        advanced=True,
    )


# --- CANCEL -----------------------------------------------------------------


def _parse_cancel_action(raw: Any) -> tuple[str | None, str | None]:
    """Split `pending_cancel_action` into `(action, booking_id)`."""
    if not isinstance(raw, str) or not raw.strip():
        return None, None
    action, _, booking_id = raw.strip().partition(":")
    if action == CANCEL_AWAITING_CONFIRMATION:
        return CANCEL_AWAITING_CONFIRMATION, booking_id or None
    if action == CANCEL_AWAITING_SELECTION:
        return CANCEL_AWAITING_SELECTION, None
    logger.warning("cancel_interrupt: unknown pending_cancel_action %r — treating as fresh", raw)
    return None, None


def _describe_booking(booking: dict[str, Any]) -> str:
    """Human-readable «10.09.2026 о 14:00, Київ, вул. Прикладна 1»."""
    date = str(booking.get("date") or "").strip()
    time_ = str(booking.get("time") or booking.get("period") or "").strip()
    city = str(booking.get("city") or "").strip()
    address = str(booking.get("address") or booking.get("station_name") or "").strip()
    when = f"{date} о {time_}" if date and time_ else (date or time_)
    where = ", ".join(part for part in (city, address) if part)
    return ", ".join(part for part in (when, where) if part) or "запис без деталей"


#: Ordinal words/digits → zero-based index, for «скасуйте другий».
_ORDINALS: dict[str, int] = {
    "перший": 0,
    "перше": 0,
    "першу": 0,
    "первый": 0,
    "1": 0,
    "другий": 1,
    "друге": 1,
    "другу": 1,
    "второй": 1,
    "2": 1,
    "третій": 2,
    "третє": 2,
    "третю": 2,
    "третий": 2,
    "3": 2,
    "четвертий": 3,
    "четвертый": 3,
    "4": 3,
}


def _pick_booking(text: str, bookings: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Choose one booking from a list given the caller's answer.

    Ordinals win over dates; a date fragment must match exactly one booking.
    Returns None when the answer is ambiguous — the handler re-asks rather than
    cancelling the wrong appointment.
    """
    lowered = _norm(text)
    if not lowered or not bookings:
        return None
    for word, index in _ORDINALS.items():
        if _whole_word(lowered, word) and index < len(bookings):
            return bookings[index]
    for fragment in re.findall(r"\d{1,2}", lowered):
        matches = [
            b
            for b in bookings
            if re.search(
                rf"(?<![0-9]){re.escape(fragment)}(?![0-9])",
                f"{b.get('date') or ''} {b.get('time') or ''}",
            )
        ]
        if len(matches) == 1:
            return matches[0]
    return None


async def handle_cancel_interrupt(
    customer_text: str,
    session: CallSession,
    tool_router: Any,
) -> InterruptResult:
    """Cancel an existing fitting booking, over as many turns as it takes.

    Sub-flow, tracked in `session.pending_cancel_action`:

    1. fresh turn — fetch the caller's bookings; 0 → say so, 1 → ask to
       confirm, 2+ → read the list and ask which one;
    2. `awaiting_selection` — map the answer onto a booking;
    3. `awaiting_confirmation:<id>` — yes cancels, no keeps the booking.

    The booking list is re-fetched every turn instead of cached. That keeps the
    session contract to one string field *and* gives the guard for free: an id
    that is not in the freshly returned list is never passed to
    `cancel_fitting`.

    Args:
        customer_text: Verbatim STT transcript of this turn.
        session: Live `CallSession`; the handler writes its own fields on it.
        tool_router: Router exposing `get_customer_bookings` and
            `cancel_fitting`.

    Returns:
        `InterruptResult`. `handled=False` is always a valid, safe outcome.
    """
    text = customer_text or ""
    phone = getattr(session, "caller_phone", None)
    if not phone:
        logger.info("cancel_interrupt: caller_phone missing — outcome=skipped")
        return _result(False)

    action, target_id = _parse_cancel_action(getattr(session, "pending_cancel_action", None))
    entry = action is None

    # --- Default-deny gate -------------------------------------------------
    if entry and not _mentions_cancel(text):
        return _result(False)

    # --- Loop-breaker (before any tool call) --------------------------------
    if _is_capped(session, CANCEL_HANDLER, entry=entry):
        logger.info(
            "cancel_interrupt: cap reached (entry=%s, counts=%s) — outcome=capped",
            entry,
            _counts(session),
        )
        return _result(False, session_updates=_apply(session, {"pending_cancel_action": None}))

    counts = _bump(session, CANCEL_HANDLER, entry=entry)
    resume_state, resume_phrase = _resume(session)

    result, error = await _call_tool(tool_router, "get_customer_bookings", {"phone": str(phone)})
    if error is not None:
        logger.error(
            "cancel_interrupt: get_customer_bookings failed (%s) — outcome=tool_error", error
        )
        return _result(
            False,
            session_updates=_apply(
                session,
                {
                    "pending_cancel_action": None,
                    "interrupt_counts": _reset_followups(session, CANCEL_HANDLER),
                },
            ),
        )

    # Mirrors the pipeline's execute hook: `_cancel_fitting` refuses to run
    # unless `get_customer_bookings` has been recorded for this call.
    tools_called = getattr(session, "tools_called", None)
    if isinstance(tools_called, set):
        tools_called.add("get_customer_bookings")

    raw = result.get("bookings")
    bookings = [b for b in raw if isinstance(b, dict)] if isinstance(raw, list) else []

    # --- Step 3: yes/no on a chosen booking ---------------------------------
    if action == CANCEL_AWAITING_CONFIRMATION:
        return await _confirm_cancel(
            text=text,
            session=session,
            tool_router=tool_router,
            target_id=target_id,
            bookings=bookings,
            counts=counts,
            resume_state=resume_state,
            resume_phrase=resume_phrase,
        )

    # --- Step 2: pick one of several ----------------------------------------
    if action == CANCEL_AWAITING_SELECTION:
        picked = _pick_booking(text, bookings)
        if picked is None:
            logger.info("cancel_interrupt: selection unclear — outcome=reask_selection")
            return _result(
                True,
                reply=STATES[FsmState.CANCEL_INTERRUPT].silence_reprompt
                or "Скажіть, будь ласка, який запис скасувати.",
                resume_state=resume_state,
                session_updates=_apply(session, {"interrupt_counts": counts}),
            )
        return _ask_confirmation(session, picked, counts, resume_state)

    # --- Step 1: fresh entry -------------------------------------------------
    if not bookings:
        logger.info("cancel_interrupt: zero bookings — outcome=none_found")
        return _result(
            True,
            reply="У вас немає активних записів на шиномонтаж.",
            resume_state=None,
            session_updates=_apply(
                session,
                {
                    "pending_cancel_action": None,
                    "interrupt_counts": _reset_followups(session, CANCEL_HANDLER),
                },
            ),
            advanced=True,
        )

    if len(bookings) == 1:
        return _ask_confirmation(session, bookings[0], counts, resume_state)

    lines = [f"{i}) {_describe_booking(b)}" for i, b in enumerate(bookings, start=1)]
    logger.info("cancel_interrupt: %d bookings — outcome=listed", len(bookings))
    return _result(
        True,
        reply=f"У вас {len(bookings)} записи: " + "; ".join(lines) + ". Який скасовуємо?",
        resume_state=resume_state,
        session_updates=_apply(
            session,
            {
                "pending_cancel_action": CANCEL_AWAITING_SELECTION,
                "interrupt_counts": counts,
            },
        ),
        advanced=True,
    )


def _ask_confirmation(
    session: CallSession,
    booking: dict[str, Any],
    counts: dict[str, int],
    resume_state: str | None,
) -> InterruptResult:
    """Read a booking back and ask for a yes/no before cancelling it."""
    booking_id = booking.get("booking_id") or booking.get("id")
    if not _is_valid_booking_id(booking_id):
        logger.error(
            "cancel_interrupt: booking from get_customer_bookings has an unusable id %r "
            "— outcome=invalid_id",
            booking_id,
        )
        return _result(
            False,
            session_updates=_apply(
                session,
                {
                    "pending_cancel_action": None,
                    "interrupt_counts": _reset_followups(session, CANCEL_HANDLER),
                },
            ),
        )
    logger.info("cancel_interrupt: booking=%s — outcome=await_confirmation", booking_id)
    return _result(
        True,
        reply=f"У вас запис на {_describe_booking(booking)}. Скасувати? Скажіть «так» або «ні».",
        resume_state=resume_state,
        session_updates=_apply(
            session,
            {
                "pending_cancel_action": f"{CANCEL_AWAITING_CONFIRMATION}:{booking_id}",
                "interrupt_counts": counts,
            },
        ),
        advanced=True,
    )


async def _confirm_cancel(
    *,
    text: str,
    session: CallSession,
    tool_router: Any,
    target_id: str | None,
    bookings: list[dict[str, Any]],
    counts: dict[str, int],
    resume_state: str | None,
    resume_phrase: str,
) -> InterruptResult:
    """Handle the yes/no turn of the cancel sub-flow."""
    known_ids = {str(b.get("booking_id") or b.get("id") or "") for b in bookings}
    if not _is_valid_booking_id(target_id) or target_id not in known_ids:
        # Either the id never came from `get_customer_bookings`, or the booking
        # is gone. Refuse rather than send a guessed id to 1C.
        logger.error(
            "cancel_interrupt: pending booking id %r is not among the caller's current "
            "bookings — outcome=guard_blocked",
            target_id,
        )
        return _result(
            False,
            session_updates=_apply(
                session,
                {
                    "pending_cancel_action": None,
                    "interrupt_counts": _reset_followups(session, CANCEL_HANDLER),
                },
            ),
        )

    decision = _yes_no(text)
    if decision == "no":
        logger.info("cancel_interrupt: booking=%s — outcome=declined", target_id)
        return _result(
            True,
            reply=_join("Добре, запис залишаємо.", resume_phrase),
            resume_state=resume_state,
            session_updates=_apply(
                session,
                {
                    "pending_cancel_action": None,
                    "interrupt_counts": _reset_followups(session, CANCEL_HANDLER),
                },
            ),
            advanced=True,
        )

    if decision == "unclear":
        logger.info("cancel_interrupt: booking=%s — outcome=reask_confirmation", target_id)
        return _result(
            True,
            reply="Скажіть «так», щоб скасувати запис, або «ні», щоб залишити.",
            resume_state=resume_state,
            session_updates=_apply(session, {"interrupt_counts": counts}),
        )

    result, error = await _call_tool(tool_router, "cancel_fitting", {"booking_id": target_id})
    if error is not None or (result or {}).get("status") != "cancelled":
        logger.error(
            "cancel_interrupt: cancel_fitting did not confirm booking=%s (error=%s result=%s) "
            "— outcome=cancel_failed",
            target_id,
            error,
            result,
        )
        return _result(
            False,
            session_updates=_apply(
                session,
                {
                    "pending_cancel_action": None,
                    "interrupt_counts": _reset_followups(session, CANCEL_HANDLER),
                },
            ),
        )

    logger.info("cancel_interrupt: booking=%s — outcome=cancelled", target_id)
    return _result(
        True,
        reply="Готово, запис скасовано. Дякую за дзвінок!",
        resume_state=None,
        session_updates=_apply(
            session,
            {
                "pending_cancel_action": None,
                "fitting_booked": False,
                "interrupt_counts": _reset_followups(session, CANCEL_HANDLER),
            },
        ),
        advanced=True,
    )
