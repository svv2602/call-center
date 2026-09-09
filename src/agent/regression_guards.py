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
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Collection


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

    Args mirror ``CallSession`` attributes. Passed explicitly so this helper
    stays a pure function easy to unit-test. Wave 10 args are keyword-only
    to keep backwards compatibility with earlier test call-sites.
    """
    if not last_fitting_station_id:
        return None

    # (A) Cross-city regression
    incoming_norm = (incoming_city or "").strip().lower()
    pinned_norm = (pinned_station_city or "").strip().lower()
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
