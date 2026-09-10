"""Backend guards against LLM checklist regressions in the fitting flow.

Extracted from src/main.py to keep the guard logic unit-testable — the
in-line closures in main.py's setup function can't be imported directly.

Pattern history:
- Wave 4C: false transfer_to_operator guard (_should_block_false_transfer)
- Wave 7: Krok 3/4 book_fitting date/time confabulation guard (inline in main)
- Wave 9: Krok 1 regression guard when LLM calls get_fitting_stations after
  station is already pinned + Krok 2+ signals present (this module)
- Wave 17: an empty book_fitting `date` slipped past every date check (this
  module)
- Wave 18: the profile city outranked the city the caller said out loud (this
  module)
"""

from __future__ import annotations

import re
from datetime import date as date_type
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from src.agent.compound_parse import named_cities

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence


def effective_booking_date(resolved_date: str, offered_dates: Collection[str]) -> str:
    """Pick the date ``book_fitting`` should actually use.

    ``resolve_tool_date("")`` returns ``""`` and every date check in
    ``main.py`` is written ``if booking_date_str and …`` — so an empty ``date``
    skipped the offered-slots check, the offered-time check, the
    ``selected_fitting_*`` pin and the today/+21 bound, and reached 1С
    unvalidated. Call ``97ddfd87`` (2026-09-08) sent exactly that.

    ``get_fitting_slots`` rebuilds ``fitting_slots_offered`` from a single
    ``date_from``, so while slots exist there is exactly one date the client
    can mean: adopt it. Adopting rather than bouncing an error back to the LLM
    is deliberate — a corrective message here would be a short-circuiting
    guard with no loop-breaker, the Wave 13 PRICE-handler shape.

    Returns ``""`` when there is nothing unambiguous to adopt, which leaves the
    caller's existing guards to refuse the booking.
    """
    if resolved_date:
        return resolved_date
    unique = set(offered_dates)
    if len(unique) == 1:
        return next(iter(unique))
    return ""


def caller_named_city(customer_utterances: Sequence[str]) -> str | None:
    """The city the caller said out loud, or ``None`` if they never did.

    Exists because the *profile* city outranks it otherwise. `customers.city`
    is injected into the system prompt (`prompts.format_customer_profile`) and
    that block ends with «Місто з профілю — використовуй ЗА ЗАМОВЧУВАННЯМ»,
    whose only stated exception is the caller naming a district or landmark.
    A caller naming a different *city* is not on that list, so the LLM
    following the prompt correctly still gets it wrong. Call ``cbb41e0d``
    (2026-09-10): caller opened with «вартість монтажу в Дніпрі», profile said
    Запоріжжя, and `get_fitting_stations(city='Запоріжжя')` went out on a
    hundredth call from a customer who was standing in Dnipro.

    **The latest mention wins**, so a caller who changes their mind is
    followed rather than pinned. That is the ``bd95036c`` shape from the other
    side: there the caller asked for Черкаси four times against a Дніпро
    snapshot and nothing recorded it.

    **An utterance naming two cities returns ``None``, and stops the walk.**
    «я з Києва, але треба в Дніпрі» is not something to guess at, and reaching
    further back would answer with a city that this very sentence may have
    just superseded. Silence is the safe answer: the caller keeps whatever the
    LLM chose, which is today's behaviour.

    Only cities named in words count — `named_cities` is the 1.0 tier alone.
    A landmark («на Оболоні») deliberately does not qualify: it is an
    inference, the prompt already routes it through a `query=` search, and
    ``63d11ab4`` is what happens when landmark text is allowed to read as a
    city.
    """
    for text in reversed(list(customer_utterances)):
        named = named_cities(text)
        if len(named) == 1:
            return next(iter(named))
        if named:
            return None
    return None


#: Weekday keywords the caller may use, mapped to ``date.weekday()``. Ukrainian
#: and Russian both, because the STT returns whichever the caller spoke.
_WEEKDAY_KEYWORDS: dict[str, int] = {
    "понеділок": 0,
    "понеділка": 0,
    "пн": 0,
    "monday": 0,
    "понедельник": 0,
    "вівторок": 1,
    "вівторка": 1,
    "вт": 1,
    "tuesday": 1,
    "вторник": 1,
    "серед": 2,
    "середу": 2,
    "wednesday": 2,
    "среду": 2,
    "среды": 2,
    "среда": 2,
    "четвер": 3,
    "четвр": 3,
    "чт": 3,
    "thursday": 3,
    "четверг": 3,
    "п'ятниц": 4,
    "пʼятниц": 4,
    "пятниц": 4,
    "пт": 4,
    "friday": 4,
    "субот": 5,
    "сб": 5,
    "saturday": 5,
    "суббот": 5,
    "неділ": 6,
    "нд": 6,
    "sunday": 6,
    "воскресен": 6,
}

#: Accusative forms — «Клієнт просив суботу», not «субота».
_WEEKDAY_ACCUSATIVE = [
    "понеділок",
    "вівторок",
    "середу",
    "четвер",
    "пʼятницю",
    "суботу",
    "неділю",
]


def requested_weekday(customer_utterances: Sequence[str], *, depth: int = 3) -> int | None:
    """The weekday the caller last named, or ``None``.

    Walks backwards over the customer's turns and stops after ``depth`` of them
    without a hit. Anything older is not what the current turn is about.
    """
    scanned = 0
    for text in reversed(list(customer_utterances)):
        if not text:
            continue
        lowered = text.lower()
        for keyword, weekday in _WEEKDAY_KEYWORDS.items():
            if re.search(r"\b" + re.escape(keyword), lowered):
                return weekday
        scanned += 1
        if scanned >= depth:
            break
    return None


def check_weekday_mismatch(
    date_from: str,
    customer_utterances: Sequence[str],
    today: date_type,
    *,
    dates_with_no_slots: Collection[str] = (),
    bounced_weekday_since_lookup: int | None = None,
) -> dict[str, Any] | None:
    """Refuse a ``get_fitting_slots`` date that is not the weekday the caller named.

    Call 2026-08-03: the caller said «п'ятницю» (7 серпня) and the LLM queried
    6 серпня — a Thursday — so the bot read out Thursday's slots as if they were
    the Friday the caller asked for.

    Returns the tool-result body to hand back to the LLM, plus a
    ``requested_weekday`` key for the caller to record as the loop-breaker, or
    ``None`` when the date is fine — or when refusing it would be worse:

    **The named weekday has already come back empty.** Call ``b034315e``
    (2026-09-10) is the shape. Caller: «на неділю». ``get_fitting_slots`` for
    that Sunday returned `total=0, available=0`. The bot offered Monday
    instead, the caller said «так», the LLM duly queried Monday — and this
    guard sent it back to the Sunday it had just been told was empty. Twice.
    The bot then told the caller there were no evening slots, reading an empty
    list this guard had caused, and the FSM ran out of TIME attempts. A date
    this call has already queried to zero is an objective signal that the
    caller's weekday is unavailable, and it outranks the words.

    Call ``ab39e3c6`` (2026-07-27) is the same defect with the caller naming a
    replacement date instead of agreeing to one: «в неділю» → 2026-08-02 empty
    → «1 серпня» → slots. Without this rule the guard sends that back to the
    Sunday it was already told was closed. That call went on to book 11:40.

    **It has already bounced for this weekday and nothing happened since.**
    ``bounced_weekday_since_lookup`` is the loop-breaker
    (``feedback_guard_needs_loop_breaker.md``). The caller must clear it after
    any lookup that reaches the backend, which is what splits a repeat from a
    continuation:

    - A **repeat** is the LLM re-sending the same wrong date with no lookup in
      between. Call ``56aea836`` (2026-08-04) sent ``2026-08-05`` three times
      in 29 seconds and was refused three times identically; the bounce was
      not going to start working on the fourth try.
    - A **continuation** is the LLM taking the correction, querying the right
      day, and only later drifting back. Calls ``dce9f6af`` and ``dac6df27``
      (2026-08-05) both did exactly that — bounce, correct date four seconds
      later with real slots, then a regression to the wrong date a turn on.
      There the second bounce is the guard doing its job, so the intervening
      lookup rearms it.

    Keying on the weekday rather than a bare flag is the same split from the
    other side: a caller who names a *new* day is making a new request, not
    repeating the old one.
    """
    try:
        requested = date_type.fromisoformat(date_from)
    except (ValueError, AttributeError, TypeError):
        return None

    weekday = requested_weekday(customer_utterances)
    if weekday is None or requested.weekday() == weekday:
        return None

    if bounced_weekday_since_lookup == weekday:
        return None

    earliest = today + timedelta(days=1)
    correct = earliest + timedelta(days=(weekday - earliest.weekday()) % 7)

    if correct.isoformat() in set(dates_with_no_slots):
        return None

    return {
        "error": True,
        "reason": "weekday_mismatch",
        "requested_weekday": weekday,
        "message": (
            f"Дата {date_from} — це {_WEEKDAY_ACCUSATIVE[requested.weekday()]}, "
            f"а не {_WEEKDAY_ACCUSATIVE[weekday]}. Клієнт просив "
            f"{_WEEKDAY_ACCUSATIVE[weekday]} — це {correct.isoformat()}. "
            f"Виклич ЩЕ РАЗ get_fitting_slots з date_from='{correct.isoformat()}'."
        ),
        "slots": [],
    }


def check_krok1_regression(
    last_fitting_station_id: str | None,
    storage_contract_guard_triggered: bool,
    fitting_storage_contract: str | None,
    selected_fitting_date: str | None,
    fitting_slots_offered_count: int,
    *,
    pinned_station_city: str | None = None,
    incoming_city: str = "",
    fitting_storage_choice: str | None = None,
    storage_contracts_found_count: int = 0,
    caller_city: str | None = None,
) -> dict[str, Any] | None:
    """Detect an LLM regression to Krok 1 (district selection) after the
    checklist has already advanced past Krok 1.

    Returns an error dict for the LLM with a strong recovery hint, or
    ``None`` when the call is legitimate (station not yet pinned, or the
    LLM is legitimately refining district within the same city).

    Two guard triggers (either one fires):

    (A) **Cross-city regression** — station is pinned in city X, but the
        LLM is now calling ``get_fitting_stations(city=Y)`` with Y ≠ X.
        The customer did not switch cities; the LLM lost context.

    (B) **Past-Krok-2 signal present** — station is pinned AND at least
        one signal indicates the flow moved past Krok 1:
          - storage question was asked (``storage_contract_guard_triggered``)
          - storage contract was actually selected (``fitting_storage_contract``)
          - storage choice recorded (``fitting_storage_choice`` in {"own","contract"})
          - ``storage_contracts_found`` non-empty (find_storage matched)
          - date was chosen (``selected_fitting_date``)
          - slots were offered / listed (``fitting_slots_offered_count > 0``)

    Root cases:

    - Call ``88dc3c77`` 2026-09-04 (Wave 9v1): customer picked Донецьке
      шосе → confirmed → storage 'свої' → date «завтра» → LLM invented all
      book_fitting fields → Wave 7 rejected → LLM called
      get_fitting_stations instead of get_fitting_slots → bot listed
      districts again.

    - Call ``8404d35b`` 2026-09-04 (Wave 10 addition): mid-flow — station
      pinned in Києві/Оболоні, storage discovered via find_storage — LLM
      called ``get_fitting_stations(city="Дніпро")`` (cross-city!). Wave 9v1
      didn't catch it because past-Krok-2 signals hadn't fully materialised
      yet at that exact turn.

    ``caller_city`` (Wave 18) is the escape hatch for the one case both
    triggers get wrong: the customer really did switch cities. Until it
    existed the guard had no way to tell an LLM losing context from a caller
    correcting the bot, and it resolved that in favour of the pin — answering
    a correction with «Клієнт не змінював місто», which is a statement of
    fact, and false. Pass `caller_named_city(...)` and a caller who said the
    new city in words is believed. It defeats **both** triggers on purpose:
    once the pin is in the wrong city, everything collected after it —
    storage, date, slots — belongs to the wrong city too, so «continue the
    checklist» is the wrong instruction, not just an impolite one.

    That is a narrow hatch, not a hole: the words have to have come from the
    caller (`named_cities`, 1.0 tier only), and they have to match the city
    the LLM is actually asking for. An LLM cannot manufacture either.

    Consequently «Клієнт не змінював місто» below is now true when it is
    printed — the only paths that reach it are a caller who named no city, or
    one who named the pinned city.

    Args mirror ``CallSession`` attributes. Passed explicitly so this helper
    stays a pure function easy to unit-test. Wave 10 args are keyword-only
    to keep backwards compatibility with earlier test call-sites.
    """
    if not last_fitting_station_id:
        return None

    # (A) Cross-city regression
    incoming_norm = (incoming_city or "").strip().lower()
    pinned_norm = (pinned_station_city or "").strip().lower()
    caller_norm = (caller_city or "").strip().lower()

    if caller_norm and caller_norm == incoming_norm and caller_norm != pinned_norm:
        return None

    cross_city = bool(pinned_norm and incoming_norm and pinned_norm != incoming_norm)

    # (B) Past-Krok-2 signals
    past_krok2 = (
        storage_contract_guard_triggered
        or fitting_storage_contract is not None
        or fitting_storage_choice is not None
        or storage_contracts_found_count > 0
        or bool(selected_fitting_date)
        or fitting_slots_offered_count > 0
    )

    if not (cross_city or past_krok2):
        return None

    date_hint = selected_fitting_date or "YYYY-MM-DD"

    if cross_city:
        return {
            "error": True,
            "action_required": "resume_checklist",
            "reason": "cross_city",
            "message": (
                f"⛔ Регресія Кроку 1 (cross-city). Станцію вже обрано у "
                f"місті '{pinned_station_city}' "
                f"(station_id='{last_fitting_station_id}'), а ти намагаєшся "
                f"шукати у місті '{incoming_city}'. Клієнт не змінював "
                f"місто — не повертайся до вибору. НАСТУПНИЙ КРОК — продовж "
                f"чекліст: Крок 2 (зберігання) → Крок 3 (дата) → Крок 4 "
                f"(час через `get_fitting_slots(station_id="
                f"'{last_fitting_station_id}', date_from='{date_hint}')`) → "
                f"book_fitting."
            ),
        }

    # past_krok2 branch (Wave 9v1)
    return {
        "error": True,
        "action_required": "call_get_fitting_slots",
        "reason": "past_krok_2",
        "message": (
            f"⛔ Регресія Кроку 1. Станцію вже обрано "
            f"(station_id='{last_fitting_station_id}') і чекліст пройшов "
            f"далі. Не повертайся до вибору міста/району. НАСТУПНИЙ КРОК — "
            f"виклич САМЕ `get_fitting_slots(station_id="
            f"'{last_fitting_station_id}', date_from='{date_hint}')` з "
            f"датою, яку назвав клієнт. Отримай список часів, озвуч "
            f"клієнту, дочекайся вибору → потім book_fitting."
        ),
    }
