"""Wave 17: an empty `book_fitting` date used to skip every date check.

Found auditing the 7 calls of 2026-09-07..09 that booked a slot
`get_fitting_slots` never returned. The confabulation itself turned out to be
covered — all 7 had an empty `fitting_slots_offered`, so the Krok 3/4 guard
would have refused them had the colour guard not fired first. What was *not*
covered: `resolve_tool_date("") == ""` and every date check in `main.py` reads
`if booking_date_str and …`, so `book_fitting(date="")` after a successful
`get_fitting_slots` skipped the offered-date check, the offered-time check, the
`selected_fitting_*` pin and the today/+21 bound. Call `97ddfd87` sent an empty
date in prod.
"""

from __future__ import annotations

import pytest

from src.agent.regression_guards import effective_booking_date

OFFERED = "2026-09-10"


class TestAdoptsTheOnlyOfferedDate:
    """`get_fitting_slots` snapshots one `date_from`, so there is one candidate."""

    def test_empty_date_adopts_it(self) -> None:
        assert effective_booking_date("", [OFFERED, OFFERED]) == OFFERED

    def test_adopts_regardless_of_slot_count(self) -> None:
        assert effective_booking_date("", [OFFERED] * 13) == OFFERED


class TestLeavesEverythingElseAlone:
    """The helper only fills a gap; it must never override or invent."""

    def test_explicit_date_is_returned_unchanged(self) -> None:
        assert effective_booking_date("2026-09-11", [OFFERED]) == "2026-09-11"

    def test_explicit_date_wins_even_when_unoffered(self) -> None:
        """Refusing it is the caller's offered-dates guard, not this one's."""
        assert effective_booking_date("2026-12-31", [OFFERED]) == "2026-12-31"

    def test_no_offered_dates_adopts_nothing(self) -> None:
        assert effective_booking_date("", []) == ""

    def test_ambiguous_offered_dates_adopt_nothing(self) -> None:
        """Two dates → no single thing the client can have meant."""
        assert effective_booking_date("", [OFFERED, "2026-09-11"]) == ""


@pytest.mark.parametrize("empty", ["", None])
def test_falsy_resolved_date_is_treated_as_absent(empty: str | None) -> None:
    assert effective_booking_date(empty or "", [OFFERED]) == OFFERED
