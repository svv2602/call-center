"""Wave 14: did the customer actually name a booking date?"""

import pytest

from src.agent.date_detect import mentions_date


@pytest.mark.parametrize(
    "text",
    [
        "давайте завтра",
        "можна післязавтра",
        "можно послезавтра",
        "запишіть на п'ятницю",
        "запишите на среду",
        "хочу в суботу",
        "восьмого вересня",
        "10 сентября",
        "на 12.09",
        "якого числа у вас є вікна?",
        # Client delegating the choice still counts as answered — the guard
        # must not re-ask someone who said «коли завгодно».
        "мені будь-коли зручно",
        "на найближчий день",
        "та все одно",
    ],
)
def test_date_mentioned(text):
    assert mentions_date(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "мені потрібен шиномонтаж",
        "у мене R17",
        "на Харківському шосе",
        "Renault Duster",
        "так, підтверджую",
    ],
)
def test_date_not_mentioned(text):
    assert mentions_date(text) is False
