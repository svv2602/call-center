"""Backend guards against LLM checklist regressions in the fitting flow.

Extracted from src/main.py to keep the guard logic unit-testable — the
in-line closures in main.py's setup function can't be imported directly.

Pattern history:
- Wave 4C: false transfer_to_operator guard (_should_block_false_transfer)
- Wave 7: Krok 3/4 book_fitting date/time confabulation guard (inline in main)
- Wave 9: Krok 1 regression guard when LLM calls get_fitting_stations after
  station is already pinned + Krok 2+ signals present (this module)
"""

from __future__ import annotations

from typing import Any


def check_krok1_regression(
    last_fitting_station_id: str | None,
    storage_contract_guard_triggered: bool,
    fitting_storage_contract: str | None,
    selected_fitting_date: str | None,
    fitting_slots_offered_count: int,
) -> dict[str, Any] | None:
    """Detect an LLM regression to Krok 1 (district selection) after checklist
    has already advanced.

    Returns an error dict for the LLM (with a strong hint to call
    ``get_fitting_slots`` instead) when the guard fires, or ``None`` when the
    call is legitimate (e.g., the customer just picked a city and no station
    is pinned yet).

    Guard rule:
        Fires when a station is pinned (Krok 1 closed) AND at least one
        past-Krok-2 signal is present:
          - storage question was asked (``storage_contract_guard_triggered``)
          - storage contract was actually selected (``fitting_storage_contract``)
          - date was chosen (``selected_fitting_date``)
          - slots were offered / listed (``fitting_slots_offered_count > 0``)

    Root case (call ``88dc3c77`` 2026-09-04): customer picked Донецьке шосе
    (Turn 11), confirmed, storage 'свої' (Turn 14), date «завтра» (Turn 16),
    LLM invented all book_fitting fields → Wave 7 guard rejected →
    LLM called ``get_fitting_stations`` instead of ``get_fitting_slots`` →
    bot listed districts again, dropping the customer at Krok 1.

    Args mirror the corresponding attributes on ``CallSession``. Passed
    explicitly (not as a Session object) so the helper stays a pure function.
    """
    if not last_fitting_station_id:
        return None

    past_krok2 = (
        storage_contract_guard_triggered
        or fitting_storage_contract is not None
        or bool(selected_fitting_date)
        or fitting_slots_offered_count > 0
    )
    if not past_krok2:
        return None

    date_hint = selected_fitting_date or "YYYY-MM-DD"
    return {
        "error": True,
        "action_required": "call_get_fitting_slots",
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
