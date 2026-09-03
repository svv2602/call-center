"""Ukrainian TTS-friendly date/time word forms.

Converts ISO date/time strings into Ukrainian phrases so the LLM cites a
canonical form in confirmations and Google Neural2 reads them naturally.

Motivation: state-guard checklist previously exposed raw ISO ("07" / "10:30")
which gave the LLM freedom to reword ("десяте вересня" instead of "сьоме" —
Wave 3 #5 date drift). Server-side normalization eliminates that freedom.
"""

from __future__ import annotations

from datetime import date as _date

_MONTH_GENITIVE_UA: dict[int, str] = {
    1: "січня", 2: "лютого", 3: "березня", 4: "квітня",
    5: "травня", 6: "червня", 7: "липня", 8: "серпня",
    9: "вересня", 10: "жовтня", 11: "листопада", 12: "грудня",
}

_DAY_ORDINAL_NEUTER_UA: dict[int, str] = {
    1: "перше", 2: "друге", 3: "третє", 4: "четверте", 5: "пʼяте",
    6: "шосте", 7: "сьоме", 8: "восьме", 9: "девʼяте", 10: "десяте",
    11: "одинадцяте", 12: "дванадцяте", 13: "тринадцяте", 14: "чотирнадцяте",
    15: "пʼятнадцяте", 16: "шістнадцяте", 17: "сімнадцяте", 18: "вісімнадцяте",
    19: "девʼятнадцяте", 20: "двадцяте",
    21: "двадцять перше", 22: "двадцять друге", 23: "двадцять третє",
    24: "двадцять четверте", 25: "двадцять пʼяте", 26: "двадцять шосте",
    27: "двадцять сьоме", 28: "двадцять восьме", 29: "двадцять девʼяте",
    30: "тридцяте", 31: "тридцять перше",
}

# Hour ordinal, feminine nominative (agrees with implicit "година").
# Only fitting hours 8..20 covered — outside that range TTS gets ISO.
_HOUR_ORDINAL_FEM_UA: dict[int, str] = {
    8: "восьма", 9: "девʼята", 10: "десята", 11: "одинадцята",
    12: "дванадцята", 13: "тринадцята", 14: "чотирнадцята",
    15: "пʼятнадцята", 16: "шістнадцята", 17: "сімнадцята",
    18: "вісімнадцята", 19: "девʼятнадцята", 20: "двадцята",
}

# Minute cardinal. Common voice-friendly steps.
_MINUTE_CARDINAL_UA: dict[int, str] = {
    5: "пʼять", 10: "десять", 15: "пʼятнадцять", 20: "двадцять",
    25: "двадцять пʼять", 30: "тридцять", 35: "тридцять пʼять",
    40: "сорок", 45: "сорок пʼять", 50: "пʼятдесят", 55: "пʼятдесят пʼять",
}


def date_to_words(iso_date: str | None) -> str | None:
    """Convert ISO ``YYYY-MM-DD`` into ``«сьоме вересня»``.

    Returns ``None`` when input is unusable so the caller can keep the
    ISO value as-is without a parenthetical phrase.
    """
    if not iso_date:
        return None
    try:
        d = _date.fromisoformat(iso_date)
    except (ValueError, TypeError):
        return None
    day_word = _DAY_ORDINAL_NEUTER_UA.get(d.day)
    month_word = _MONTH_GENITIVE_UA.get(d.month)
    if not day_word or not month_word:
        return None
    return f"{day_word} {month_word}"


def time_to_words(hhmm: str | None) -> str | None:
    """Convert ``HH:MM`` into ``«десята тридцять»`` / ``«рівно десята»``.

    Returns ``None`` for unsupported hours (outside 8..20) or minute
    values not in the canonical 5-min grid.
    """
    if not hhmm or ":" not in hhmm:
        return None
    hh_s, mm_s = hhmm.split(":", 1)
    try:
        hh, mm = int(hh_s), int(mm_s)
    except ValueError:
        return None
    hour_word = _HOUR_ORDINAL_FEM_UA.get(hh)
    if not hour_word:
        return None
    if mm == 0:
        return f"рівно {hour_word}"
    minute_word = _MINUTE_CARDINAL_UA.get(mm)
    if not minute_word:
        return None
    return f"{hour_word} {minute_word}"
