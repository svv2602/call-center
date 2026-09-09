"""Wave 18 (2026-09-09) — shadow measurement of Krok 8 before book_fitting.

Confirmation is the only checklist item with neither a rendered row in
`_render_fitting_progress` nor a server-side check: the block emits 8 rows, so
«усі поля ✅» goes true the moment the brand lands, and only prose withholds the
call. A transcript survey of 44 confirmed bookings over 14 days of prod put the
recap-then-«так» share at roughly half (23 of 44), so a hard gate built on that
estimate would refuse real bookings.

`booking_was_confirmed` re-measures with the exact predicates such a gate would
use. `_book_fitting_with_metric` only counts and logs — it does NOT reject.
Promote to default-deny only once the counter says the signal is reliable.

The wiring in main.py is not covered here: the local venv cannot import
`src.main` (no uvicorn), which is why 63 unit modules error out on collection.
"""

from __future__ import annotations

import pytest

from src.agent.confirm_detect import booking_was_confirmed

RECAP = (
    "Перевіримо: десяте вересня о девʼятій ранку, Київ, вулиця Хрещатик 1, "
    "червоний Renault Duster. Підтверджуєте?"
)
REASK = "Перепрошую, не розчула."
BRAND_Q = "Яка марка вашого авто?"


class TestConfirmedExchanges:
    @pytest.mark.parametrize(
        "answer",
        ["так", "Так.", "да", "так, вірно", "підтверджую", "ага", "добре, записуйте"],
    )
    def test_recap_then_agreement(self, answer: str) -> None:
        assert booking_was_confirmed([("assistant", RECAP), ("user", answer)]) is True

    def test_a_filler_reask_sits_between_question_and_answer(self) -> None:
        """asked_for_confirmation looks two bot turns back for exactly this."""
        assert (
            booking_was_confirmed(
                [("assistant", RECAP), ("assistant", REASK), ("user", "так")]
            )
            is True
        )

    def test_earlier_dialog_does_not_disturb_the_window(self) -> None:
        assert (
            booking_was_confirmed(
                [
                    ("assistant", "Як до вас звертатися?"),
                    ("user", "Олена"),
                    ("assistant", BRAND_Q),
                    ("user", "Дастер"),
                    ("assistant", RECAP),
                    ("user", "так"),
                ]
            )
            is True
        )


class TestUnconfirmedExchanges:
    def test_no_recap_at_all(self) -> None:
        assert booking_was_confirmed([("assistant", BRAND_Q), ("user", "Дастер")]) is False

    def test_recap_but_the_customer_pushed_back(self) -> None:
        assert (
            booking_was_confirmed([("assistant", RECAP), ("user", "а можна на іншу дату")])
            is False
        )

    def test_the_customer_never_spoke(self) -> None:
        assert booking_was_confirmed([("assistant", RECAP)]) is False

    def test_an_empty_dialog(self) -> None:
        assert booking_was_confirmed([]) is False

    def test_the_recap_is_three_bot_turns_back(self) -> None:
        """The window is two turns; anything older is a different exchange."""
        assert (
            booking_was_confirmed(
                [
                    ("assistant", RECAP),
                    ("assistant", REASK),
                    ("assistant", BRAND_Q),
                    ("user", "так"),
                ]
            )
            is False
        )

    def test_agreement_to_a_question_that_was_not_the_recap(self) -> None:
        assert (
            booking_was_confirmed(
                [("assistant", "Шини привозите свої з собою?"), ("user", "так")]
            )
            is False
        )


class TestKnownUndercount:
    """Real agreements the detector misses — the shadow counter will log these
    as `confirmed=no` even though the customer did confirm.

    Widening `is_confirmation` is out of scope here: the same predicate feeds
    the *live* Wave 15 EMERGENCY banner, so loosening it changes production
    behaviour. Pin the gap instead, and let the counter say how big it is.
    """

    @pytest.mark.parametrize("answer", ["все вірно", "все правильно", "усе так"])
    def test_agreement_prefixed_with_vse_is_not_recognised(self, answer: str) -> None:
        assert booking_was_confirmed([("assistant", RECAP), ("user", answer)]) is False


class TestWindowMechanics:
    def test_empty_turns_are_skipped_not_counted(self) -> None:
        """Two empties, because the window is two.

        With only one empty bot turn the recap still lands inside the window by
        accident, so such a case passes even when the skip is removed entirely.
        """
        assert (
            booking_was_confirmed(
                [
                    ("assistant", RECAP),
                    ("assistant", ""),
                    ("assistant", ""),
                    ("user", ""),
                    ("user", "так"),
                ]
            )
            is True
        )

    def test_only_the_newest_customer_turn_is_the_answer(self) -> None:
        """An older «так» must not be borrowed to close a later recap."""
        assert (
            booking_was_confirmed(
                [
                    ("assistant", RECAP),
                    ("user", "так"),
                    ("assistant", "Записала."),
                    ("user", "а ще питання"),
                ]
            )
            is False
        )
