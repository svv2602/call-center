"""Wave 15: detect a bot reply that promises a booking.

Paired with ``session.fitting_booked`` in ``Pipeline._flag_false_booking_claim``
— a match on its own is normal (it is what a successful Krok 9 sounds like);
a match while nothing was booked is the bug from call 7462c08b.
"""

import pytest

from src.core.pipeline import _claims_booking_done


@pytest.mark.parametrize(
    "text",
    [
        # Call 7462c08b 2026-09-07 turn 44 — spoken with no book_fitting at all.
        "Наталя, ви записані. СМС підтвердження надійде. Дякуємо!",
        # Krok 9 template after a real success — same wording, hence the
        # fitting_booked gate at the call site.
        "Готово, записала на девʼяте вересня о девʼятій двадцять на вулиці Перемоги.",
        "Вікторія, готово, записала на одинадцяте вересня о десятій двадцятій.",
        "Запис створено.",
        "Ви записані на завтра.",
        "Вас записано, чекаємо.",
    ],
)
def test_detects_booking_promise(text):
    assert _claims_booking_done(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "На яку дату записуємо?",
        "Записуємо туди?",
        "У нас записано Renault, колір чорний. Вірно?",
        "Наталя, перевіримо: 11 вересня о 11:00. Підтверджуєте?",
        "Шини привозите свої з собою чи ті, що у нас на зберіганні?",
        "Вільний час на пʼятницю 11 вересня: 9:00, 9:40. Який час зручніший?",
    ],
)
def test_ignores_the_rest_of_the_flow(text):
    assert not _claims_booking_done(text)


def test_mid_flow_questions_are_not_promises():
    """Krok 1-8 must stay silent — otherwise every booking call alerts."""
    assert not _claims_booking_done("Добре, 11:00 прийнято. Назвіть, будь ласка, колір автомобіля.")
    assert not _claims_booking_done("Перепрошую, не розчула. На яку дату записуємо?")
