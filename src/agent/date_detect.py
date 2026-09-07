"""Did the customer name a booking date at all?

Wave 14 (2026-09-07). The LLM was calling ``get_fitting_slots`` with a date it
picked itself — call add8354b went straight to
``get_fitting_slots(date_from="2026-09-08", station_id="000000009")`` without
ever asking, and the client only found out which day they had been booked on
during the final confirmation.

Deliberately generous: a false ``True`` merely lets the flow continue as it
does today, while a false ``False`` would make the bot re-ask a question the
client already answered. When in doubt, say the date was mentioned.
"""

from __future__ import annotations

import re

_KEYWORDS: tuple[str, ...] = (
    # Relative days
    "завтра", "післязавтра", "послезавтра", "после завтра",
    "сьогодні", "сегодня", "нині", "ныне",
    # Weekdays (UA + RU, stem form)
    "понеділ", "понедельник", "вівтор", "вторник", "серед", "среду", "среды",
    "среда", "четвер", "четвр", "четверг", "п'ятниц", "пятниц", "субот",
    "суббот", "неділ", "воскресен", "вихідн", "выходн",
    # Months
    "січня", "лютого", "березня", "квітня", "травня", "червня", "липня",
    "серпня", "вересня", "жовтня", "листопада", "грудня",
    "января", "февраля", "марта", "апреля", "мая", "июня", "июля",
    "августа", "сентября", "октября", "ноября", "декабря",
    # Explicit date talk
    "числа", "число", "дату", "дата", "дати",
    # Client delegated the choice — treat as answered, not as silence.
    "найближч", "ближайш", "будь-коли", "будь коли", "коли завгодно",
    "все одно", "всё равно", "на ваш розсуд", "як вам зручно",
    "наступного тижня", "на следующей неделе", "цього тижня",
)

_NUMERIC_DATE_RE = re.compile(r"\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b")


def mentions_date(text: str) -> bool:
    """True when the utterance carries any hint of a booking date."""
    if not text:
        return False
    low = text.lower().replace("ʼ", "'").replace("’", "'")
    if any(kw in low for kw in _KEYWORDS):
        return True
    return bool(_NUMERIC_DATE_RE.search(low))
