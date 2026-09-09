"""Wave 17 (2026-09-09) — a refused book_fitting is not a booking.

The result shapes below are copied from prod `call_tool_calls.tool_result`
rows in the 2026-09-09 tester window, where every single one of them — the
refusals included — was stored with `success = true`.
"""

from __future__ import annotations

import pytest

from src.agent.booking_result import is_booking_confirmed

# Every rejection branch of `_book_fitting_with_metric` returns this shape.
REJECTIONS: list[dict] = [
    {
        "error": True,
        "message": "Неможливо записати без: auto_number (колір авто), "
        "vehicle_info (марка авто). Поверніся до чеклісту.",
    },
    {
        "error": True,
        "message": "⛔ auto_number='колір не назвали' — це escape-hatch.",
    },
    {
        "error": True,
        "action_required": "call_get_fitting_slots",
        "message": "⛔ Спочатку виклич get_fitting_slots.",
    },
    # ToolRouter's own exception path.
    {"error": "AttributeError: 'UUID' object has no attribute 'replace'"},
]

CONFIRMATIONS: list[dict] = [
    # 1C path (main.py) — booking_id is deliberately withheld from the LLM.
    {"status": "confirmed", "message": "Запис створено. Клієнту скажи: «Готово…»"},
    # Store API fallback (mock/legacy).
    {"id": "bk-4471", "station_id": "12", "date": "2026-09-10"},
]


class TestRejectionsAreNotBookings:
    @pytest.mark.parametrize("result", REJECTIONS)
    def test_rejection_is_not_confirmed(self, result: dict) -> None:
        assert is_booking_confirmed(result) is False

    def test_error_outranks_a_marker_beside_it(self) -> None:
        """An explicit error wins even if a success marker is present."""
        assert is_booking_confirmed({"error": True, "status": "confirmed"}) is False
        assert is_booking_confirmed({"error": True, "id": "bk-1"}) is False


class TestConfirmationsAreBookings:
    @pytest.mark.parametrize("result", CONFIRMATIONS)
    def test_confirmation_is_confirmed(self, result: dict) -> None:
        assert is_booking_confirmed(result) is True


class TestDefaultDeny:
    @pytest.mark.parametrize(
        "result",
        [
            None,
            "Запис створено",
            ["confirmed"],
            {},
            # A message alone is what a refusal looks like minus the flag —
            # it must not be read as a booking.
            {"message": "Запис створено. Клієнту скажи: «Готово…»"},
            {"status": "pending"},
            {"id": ""},
            {"id": None},
        ],
    )
    def test_anything_without_a_positive_marker_is_denied(self, result: object) -> None:
        assert is_booking_confirmed(result) is False
