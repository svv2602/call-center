"""Side-door interrupt handlers for mid-booking client turns.

Wave 1-C (2026-09-07) — FSM refactor T4 + T5.

The client can inject two off-flow intents in the middle of a fitting
booking dialog:

- PRICE: «А скільки коштує шиномонтаж?» — quote price, then resume.
- CANCEL: «Скасуйте мій запис» — list bookings, confirm, cancel, close.

Both handlers are **stateless** (they read/write `CallSession` fields) so
they can be called from the pipeline without any FSM in place. Multi-turn
state is threaded through session flags:

- `pending_price_interrupt_needs_diameter` — set when the price handler
  asked for a diameter and needs the next turn to supply a number.
- `pending_cancel_action` — enum in {"awaiting_selection",
  "awaiting_confirmation"} tracking cancel-flow multi-turn state.
- `pending_cancel_bookings` — list of booking dicts served last turn (so
  the selection turn can look them up by index/date without re-querying).
- `pending_cancel_target_id` — booking_id selected, waiting for
  yes/no confirmation.

These fields are NOT stored in `CallSession.to_dict()` — they are
transient turn-to-turn (session dict-attrs added on the fly). The
handlers use `getattr(session, name, default)` and `setattr(...)` so
sessions without them (fresh from Redis) simply start with defaults.

Contract: both handlers return `InterruptResult(handled, reply,
resume_state, session_updates)`.

- `handled=False` → downstream falls back to the LLM agent (safety-first
  when we lack the data we need, e.g. `caller_phone is None`).
- `session_updates` — dict the pipeline is expected to write back on the
  session object (kept explicit for testability rather than mutating in
  place).

Not in scope of Wave 1-C:

- Pipeline integration (Wave 2-A / T6).
- Real FSM state resume phrasing (Wave 4-A / T13). Currently a fallback
  «Продовжуємо запис.» string is used — the TODO in `_resume_message`
  marks where the FSM-aware version will slot in.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# --- Constants --------------------------------------------------------------

# Placeholder booking IDs — mirrors _cancel_fitting guard in src/main.py.
_PLACEHOLDER_BOOKING_IDS: frozenset[str] = frozenset(
    {
        "",
        "0",
        "000000000",
        "00000000-0000-0000-0000-000000000000",
        "unknown",
        "none",
        "null",
    }
)

# Legal diameters for fitting (R13..R24 inclusive).
_MIN_DIAMETER = 13
_MAX_DIAMETER = 24

# Word-form diameters (mixed uk/ru + digits) → int. Only the small set the
# customer is likely to say when asked «Який діаметр?».
_DIAMETER_WORDS: dict[str, int] = {
    "тринадцять": 13,
    "чотирнадцять": 14,
    "п'ятнадцять": 15,
    "пятнадцять": 15,
    "шістнадцять": 16,
    "шестнадцять": 16,
    "сімнадцять": 17,
    "семнадцять": 17,
    "вісімнадцять": 18,
    "восемнадцать": 18,
    "дев'ятнадцять": 19,
    "девятнадцять": 19,
    "двадцять": 20,
    "двадцять один": 21,
    "двадцять два": 22,
    "двадцять три": 23,
    "двадцять чотири": 24,
}


# --- Result dataclass -------------------------------------------------------


@dataclass
class InterruptResult:
    """Result of an interrupt-handler invocation.

    Attributes:
        handled: True if the handler produced a customer-facing reply. False
            means the caller should fall through to normal LLM processing.
        reply_to_customer: What to say (will be spoken via TTS). Empty when
            handled=False.
        resume_state: The FSM state to resume in after the interrupt closes
            (for Wave 5-A integration). None means the interrupt closed the
            flow entirely (cancel confirmed) or handler didn't run.
        session_updates: Fields the pipeline must write back onto the
            session. Kept explicit so tests can assert without inspecting
            hidden mutations.
    """

    handled: bool
    reply_to_customer: str = ""
    resume_state: str | None = None
    session_updates: dict[str, Any] = field(default_factory=dict)


# --- Helpers ----------------------------------------------------------------


def _resume_message(session: Any) -> str:
    """Produce a «Продовжуємо запис. Зараз на кроці X. [Питання]» phrase.

    TODO (Wave 4-A / T13): when `session.fsm_state` becomes populated by
    the new FSM, render a full state-aware prompt («Зараз на кроці 4:
    сховище. Ви привезете свої шини чи маєте контракт зі зберігання?»).

    Until then, fallback: bare «Продовжуємо запис.» — enough to signal
    the client that we're back on the main track and enough for the
    downstream LLM to re-ask whatever it was asking last turn.
    """
    fsm_state = getattr(session, "fsm_state", None)
    if fsm_state:
        # Wave 4-A will replace with an FSM-driven prompt.
        return f"Продовжуємо запис. Зараз на кроці {fsm_state}."
    return "Продовжуємо запис."


def _lookup_city_from_station(session: Any) -> str | None:
    """Resolve city name from `session.last_fitting_station_id`.

    Reads `session.fitting_stations_seen` (list of dicts populated by
    `_get_fitting_stations`). Returns None if station_id is not pinned
    or the lookup misses.
    """
    station_id = getattr(session, "last_fitting_station_id", None)
    if not station_id:
        return None
    stations = getattr(session, "fitting_stations_seen", None) or []
    for st in stations:
        if str(st.get("id") or st.get("station_id") or "") == str(station_id):
            city = st.get("city")
            if city:
                return str(city)
    return None


def _extract_diameter(text: str) -> int | None:
    """Extract a valid tire diameter (13..24) from customer text.

    Accepts bare number ("16", "R17"), R-prefix, or Ukrainian word forms.
    Returns None if nothing plausible is found.
    """
    if not text:
        return None
    lowered = text.lower().strip()

    # Word forms first (longer keys first to prefer «двадцять чотири» over
    # «двадцять»).
    for word in sorted(_DIAMETER_WORDS, key=len, reverse=True):
        if word in lowered:
            candidate = _DIAMETER_WORDS[word]
            if _MIN_DIAMETER <= candidate <= _MAX_DIAMETER:
                return candidate

    # Numeric match — R16 / r16 / just «16».
    m = re.search(r"(?:^|[^0-9])(?:[RrРр]\s*)?(\d{2})(?![0-9])", lowered)
    if m:
        try:
            candidate = int(m.group(1))
        except ValueError:
            return None
        if _MIN_DIAMETER <= candidate <= _MAX_DIAMETER:
            return candidate
    return None


def _is_valid_booking_id(booking_id: str) -> bool:
    """Guard mirroring `_cancel_fitting` in src/main.py.

    Rejects empty, placeholder, and non-UUID booking IDs. The 1C REST
    API returns real UUIDs; anything else is either LLM confabulation
    or a stale test string.
    """
    if not booking_id:
        return False
    trimmed = booking_id.strip()
    if not trimmed:
        return False
    if trimmed in _PLACEHOLDER_BOOKING_IDS:
        return False
    if all(c in "0-" for c in trimmed):
        return False
    # UUID validity (canonical or hex form).
    try:
        uuid.UUID(trimmed)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def _confirmation_from_text(text: str) -> str:
    """Classify a yes/no answer.

    Returns "yes", "no", or "unclear".
    """
    if not text:
        return "unclear"
    lowered = text.lower().strip()
    yes_markers = (
        "так",
        "да",
        "скасуйте",
        "скасуй",
        "підтверджу",
        "підтверджуй",
        "підтверджую",
        "підтверджу",
        "ага",
        "звичайно",
        "звісно",
        "yes",
    )
    no_markers = (
        "ні",
        "нет",
        "не треба",
        "не потрібно",
        "не хочу",
        "не скасовуйте",
        "залиш",
        "no",
    )
    for m in no_markers:
        if m in lowered:
            return "no"
    for m in yes_markers:
        if m in lowered:
            return "yes"
    return "unclear"


def _format_booking_line(idx: int, booking: dict[str, Any]) -> str:
    """Format a single booking as a spoken list item."""
    date = booking.get("date") or "невідома дата"
    time_ = booking.get("time") or booking.get("period") or ""
    city = booking.get("city") or ""
    address = booking.get("address") or booking.get("station_name") or ""
    location = " ".join(part for part in (city, address) if part).strip()
    when = f"{date} {time_}".strip()
    if location:
        return f"{idx}) {when} — {location}"
    return f"{idx}) {when}"


def _pick_booking_by_text(text: str, bookings: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick a booking from a list given a customer's selection text.

    Priorities:
      1. Ordinal ("перший" / "другий" / "1"..).
      2. Date substring match ("на десяте" → look for "10" in date).
    Returns None if ambiguous.
    """
    if not bookings:
        return None
    lowered = (text or "").lower().strip()
    ordinal_map = {
        "перший": 0,
        "первый": 0,
        "1": 0,
        "один": 0,
        "другий": 1,
        "второй": 1,
        "2": 1,
        "два": 1,
        "третій": 2,
        "третий": 2,
        "3": 2,
        "три": 2,
        "четвертий": 3,
        "четвертый": 3,
        "4": 3,
        "чотири": 3,
    }
    for word, idx in ordinal_map.items():
        # Word-boundary check to avoid matching "13" inside a date.
        if (
            re.search(rf"(?:^|\b|\s){re.escape(word)}(?:\b|\s|$)", lowered)
            and 0 <= idx < len(bookings)
        ):
            return bookings[idx]

    # Date fragment match — look for any 1-2 digit day mentioned in text.
    digits_in_text = re.findall(r"\d{1,2}", lowered)
    if digits_in_text:
        for d in digits_in_text:
            matches = [
                b for b in bookings
                if d in str(b.get("date") or "") or d in str(b.get("time") or "")
            ]
            if len(matches) == 1:
                return matches[0]
    return None


# --- Public handlers --------------------------------------------------------


async def handle_price_interrupt(
    customer_text: str,
    session: Any,
    tool_router: Any,
) -> InterruptResult:
    """Handle PRICE-intent mid-booking.

    Flow:
        1. Resolve city from `session.last_fitting_station_id` via
           `session.fitting_stations_seen`. If missing, `handled=False`
           (fallback to LLM which can ask for the city naturally).
        2. Resolve diameter from `session.fitting_diameter_client`
           (Wave 12B guard) or from `customer_text` when a multi-turn
           follow-up came in with a number.
        3. If diameter still unknown, ask once and set
           `pending_price_interrupt_needs_diameter=True`.
        4. Call `tool_router.execute("get_fitting_price", ...)` and
           format prices for the customer.
        5. Reply ends with `_resume_message(session)`.

    Args:
        customer_text: Verbatim STT transcript for this turn.
        session: `CallSession` instance (read/write).
        tool_router: `ToolRouter` (must have `get_fitting_price`
            registered).

    Returns:
        `InterruptResult`.
    """
    # 1. City lookup.
    city = _lookup_city_from_station(session)

    # 2. Diameter lookup — prefer session-pinned Wave 12B value.
    diameter = getattr(session, "fitting_diameter_client", None)

    pending_flag = getattr(
        session, "pending_price_interrupt_needs_diameter", False
    )

    if diameter is None:
        # If we asked last turn, try to extract from this turn's text.
        if pending_flag:
            diameter = _extract_diameter(customer_text)
        else:
            # First interrupt turn — also try text (client may say
            # «А скільки за R17?» in one go).
            diameter = _extract_diameter(customer_text)

    if diameter is None:
        # Ask once, remember we're waiting.
        logger.info(
            "price_interrupt: needs diameter city=%s outcome=asked",
            city,
        )
        return InterruptResult(
            handled=True,
            reply_to_customer=(
                "Скажіть, будь ласка, який діаметр коліс — "
                "щоб я назвала точну ціну."
            ),
            resume_state="price_interrupt_awaiting_diameter",
            session_updates={
                "pending_price_interrupt_needs_diameter": True,
            },
        )

    # 3. Call the price tool.
    station_id = getattr(session, "last_fitting_station_id", None) or ""
    call_args: dict[str, Any] = {"tire_diameter": diameter}
    if station_id:
        call_args["station_id"] = station_id

    try:
        price_result = await tool_router.execute("get_fitting_price", call_args)
    except Exception as exc:  # pragma: no cover — defensive.
        logger.warning(
            "price_interrupt: tool_router raised city=%s diameter=%s err=%s",
            city, diameter, exc,
        )
        return InterruptResult(handled=False)

    if not isinstance(price_result, dict) or price_result.get("error"):
        logger.info(
            "price_interrupt: tool_router error city=%s diameter=%s result=%s",
            city, diameter, price_result,
        )
        return InterruptResult(handled=False)

    prices = price_result.get("prices") or []
    if not prices:
        logger.info(
            "price_interrupt: no prices city=%s diameter=%s",
            city, diameter,
        )
        return InterruptResult(
            handled=True,
            reply_to_customer=(
                f"Наразі не бачу ціни для R{diameter}. "
                + _resume_message(session)
            ),
            resume_state="price_interrupt_returned",
            session_updates={
                "pending_price_interrupt_needs_diameter": False,
                "fitting_diameter_client": diameter,
            },
        )

    # 4. Format prices — group by category.
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for p in prices:
        cat = str(p.get("category") or "other")
        by_cat.setdefault(cat, []).append(p)

    fragments: list[str] = []

    def _price_for(cat: str, label: str) -> str | None:
        entries = by_cat.get(cat) or []
        if not entries:
            return None
        # Pick the first price for this category (already filtered by
        # diameter in _get_fitting_price).
        price = entries[0].get("price") or entries[0].get("Price")
        if price is None:
            return None
        return f"{label} — {price} грн"

    car_frag = _price_for("car", "легкові")
    suv_frag = _price_for("suv", "SUV")
    for frag in (car_frag, suv_frag):
        if frag:
            fragments.append(frag)

    # If neither car nor SUV bucket matched, fall back to the first entry.
    if not fragments:
        first = prices[0]
        price = first.get("price") or first.get("Price")
        if price is not None:
            fragments.append(f"{price} грн за колесо")

    body_intro = f"Шиномонтаж R{diameter}"
    if city:
        body_intro += f" у {city}"
    price_line = ", ".join(fragments) if fragments else "ціни уточнюємо"
    reply = f"{body_intro}: {price_line}. {_resume_message(session)}"

    logger.info(
        "price_interrupt: city=%s diameter=%s outcome=priced",
        city, diameter,
    )
    return InterruptResult(
        handled=True,
        reply_to_customer=reply,
        resume_state="price_interrupt_returned",
        session_updates={
            "pending_price_interrupt_needs_diameter": False,
            "fitting_diameter_client": diameter,
        },
    )


async def handle_cancel_interrupt(
    customer_text: str,
    session: Any,
    tool_router: Any,
) -> InterruptResult:
    """Handle CANCEL-intent (side-door or primary).

    Multi-turn state via `session.pending_cancel_action`:
      None → initial turn: fetch bookings, present list / prompt confirm.
      "awaiting_selection" → client picks from multiple bookings.
      "awaiting_confirmation" → client says yes/no.

    Guards:
      - `session.caller_phone is None` → `handled=False`.
      - Booking IDs pass `_is_valid_booking_id` (UUID, no placeholders).

    Args:
        customer_text: Verbatim STT transcript for this turn.
        session: `CallSession` instance.
        tool_router: `ToolRouter` with `get_customer_bookings` +
            `cancel_fitting` registered.

    Returns:
        `InterruptResult`.
    """
    if not getattr(session, "caller_phone", None):
        logger.info("cancel_interrupt: caller_phone missing → fallback")
        return InterruptResult(handled=False)

    state = getattr(session, "pending_cancel_action", None)

    # --- Sub-flow: awaiting yes/no confirmation ---
    if state == "awaiting_confirmation":
        target_id = getattr(session, "pending_cancel_target_id", None)
        decision = _confirmation_from_text(customer_text)
        if decision == "no":
            logger.info(
                "cancel_interrupt: user declined cancellation target=%s",
                target_id,
            )
            return InterruptResult(
                handled=True,
                reply_to_customer=(
                    "Добре, запис залишаємо. " + _resume_message(session)
                ),
                resume_state=None,
                session_updates={
                    "pending_cancel_action": None,
                    "pending_cancel_target_id": None,
                    "pending_cancel_bookings": None,
                },
            )
        if decision == "unclear":
            return InterruptResult(
                handled=True,
                reply_to_customer=(
                    "Скажіть «так» щоб скасувати або «ні» щоб залишити запис."
                ),
                resume_state="cancel_awaiting_confirmation",
                session_updates={},
            )
        # decision == "yes"
        if not _is_valid_booking_id(str(target_id or "")):
            logger.warning(
                "cancel_interrupt: pending target_id invalid=%r",
                target_id,
            )
            return InterruptResult(
                handled=True,
                reply_to_customer=(
                    "Виникла помилка з ідентифікатором запису. Продовжуємо запис."
                ),
                resume_state=None,
                session_updates={
                    "pending_cancel_action": None,
                    "pending_cancel_target_id": None,
                    "pending_cancel_bookings": None,
                },
            )
        try:
            cancel_result = await tool_router.execute(
                "cancel_fitting", {"booking_id": target_id}
            )
        except Exception as exc:  # pragma: no cover — defensive.
            logger.warning(
                "cancel_interrupt: cancel_fitting raised id=%s err=%s",
                target_id, exc,
            )
            return InterruptResult(handled=False)

        status = (cancel_result or {}).get("status") if isinstance(cancel_result, dict) else None
        if status == "cancelled":
            logger.info(
                "cancel_interrupt: cancelled booking=%s", target_id,
            )
            return InterruptResult(
                handled=True,
                reply_to_customer=(
                    "Готово, запис скасовано. Дякуємо, гарного дня!"
                ),
                resume_state=None,
                session_updates={
                    "pending_cancel_action": None,
                    "pending_cancel_target_id": None,
                    "pending_cancel_bookings": None,
                    "fitting_booked": False,
                },
            )
        # Error from 1C — degrade gracefully to LLM path.
        logger.info(
            "cancel_interrupt: cancel_fitting failed id=%s result=%s",
            target_id, cancel_result,
        )
        return InterruptResult(handled=False)

    # --- Sub-flow: awaiting selection from a multi-booking list ---
    if state == "awaiting_selection":
        cached = getattr(session, "pending_cancel_bookings", None) or []
        picked = _pick_booking_by_text(customer_text, cached)
        if picked is None:
            # Ambiguous — re-ask.
            return InterruptResult(
                handled=True,
                reply_to_customer=(
                    "Не зрозуміла, який саме запис. Скажіть номер зі списку "
                    "або дату."
                ),
                resume_state="cancel_awaiting_selection",
                session_updates={},
            )
        booking_id = str(picked.get("booking_id") or "")
        if not _is_valid_booking_id(booking_id):
            logger.warning(
                "cancel_interrupt: picked booking has invalid id=%r",
                booking_id,
            )
            return InterruptResult(handled=False)
        date = picked.get("date") or ""
        time_ = picked.get("time") or picked.get("period") or ""
        addr = picked.get("address") or picked.get("station_name") or ""
        return InterruptResult(
            handled=True,
            reply_to_customer=(
                f"Скасувати запис на {date} {time_} за адресою {addr}? "
                "Скажіть «так» або «ні»."
            ),
            resume_state="cancel_awaiting_confirmation",
            session_updates={
                "pending_cancel_action": "awaiting_confirmation",
                "pending_cancel_target_id": booking_id,
            },
        )

    # --- Sub-flow: initial turn — fetch bookings ---
    try:
        bookings_result = await tool_router.execute(
            "get_customer_bookings", {"phone": session.caller_phone}
        )
    except Exception as exc:  # pragma: no cover — defensive.
        logger.warning(
            "cancel_interrupt: get_customer_bookings raised err=%s", exc,
        )
        return InterruptResult(handled=False)

    if not isinstance(bookings_result, dict):
        return InterruptResult(handled=False)

    bookings = bookings_result.get("bookings") or []
    total = len(bookings)

    if total == 0:
        logger.info("cancel_interrupt: zero bookings for phone=%s", session.caller_phone)
        return InterruptResult(
            handled=True,
            reply_to_customer=(
                "У вас немає активних записів на шиномонтаж. "
                + _resume_message(session)
            ),
            resume_state=None,
            session_updates={
                "pending_cancel_action": None,
                "pending_cancel_target_id": None,
                "pending_cancel_bookings": None,
            },
        )

    if total == 1:
        only = bookings[0]
        booking_id = str(only.get("booking_id") or "")
        if not _is_valid_booking_id(booking_id):
            logger.warning(
                "cancel_interrupt: single booking has invalid id=%r",
                booking_id,
            )
            return InterruptResult(handled=False)
        date = only.get("date") or ""
        time_ = only.get("time") or only.get("period") or ""
        addr = only.get("address") or only.get("station_name") or ""
        return InterruptResult(
            handled=True,
            reply_to_customer=(
                f"У вас запис на {date} {time_} за адресою {addr}. "
                "Скасувати? Скажіть «так» або «ні»."
            ),
            resume_state="cancel_awaiting_confirmation",
            session_updates={
                "pending_cancel_action": "awaiting_confirmation",
                "pending_cancel_target_id": booking_id,
                "pending_cancel_bookings": bookings,
            },
        )

    # total >= 2 — list all and ask which.
    lines = [_format_booking_line(i + 1, b) for i, b in enumerate(bookings)]
    listing = "; ".join(lines)
    return InterruptResult(
        handled=True,
        reply_to_customer=(
            f"У вас {total} активні записи: {listing}. Який саме скасувати?"
        ),
        resume_state="cancel_awaiting_selection",
        session_updates={
            "pending_cancel_action": "awaiting_selection",
            "pending_cancel_bookings": bookings,
        },
    )
