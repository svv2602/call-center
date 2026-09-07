"""Tire-diameter detection from customer transcripts.

Wave 12 (2026-09-07): backend guard for Wave 3 P0 regression. Prompt
HARD GUARD at `prompts.py:670` forbids the LLM from quoting diameter/
price/address before the customer has named the diameter. It regressed
under attention dilution (calls f70deab5 2026-09-01, ebe7dfcb 2026-09-07)
— bot output "R16 у Києві коштує 354 грн" without ever asking the
customer's diameter, then adjusted after a client correction. Backend
now hard-rejects `get_fitting_price` calls where no diameter has been
mentioned by the customer yet, forcing the LLM to ask first.

Detection scope:
- The customer said a number 13-24 (with or without «R»/«диаметр»/«дюймів»)
- The customer said a Ukrainian ordinal for 13-24 («шіснадцять», «сімнадцять»,
  «двадцять», etc.)
- Or a Russian variant («шестнадцать», «двадцать», etc.)

We must NOT trigger on time slots («на 14:30» → hour=14, NOT diameter),
so integration in pipeline.py should gate this by "did the bot just ask
for diameter?" — same pattern as color_detect / name_detect.

Returns the matched integer (13-24) or None.
"""

from __future__ import annotations

import re

_DIGIT_RE = re.compile(r"(?<![0-9])(1[3-9]|2[0-4])(?![0-9:])")
_R_PREFIX_RE = re.compile(r"(?:\bR|\bэр|\bер|\bар)\s*(1[3-9]|2[0-4])\b", re.IGNORECASE)

_WORD_TO_NUM: dict[str, int] = {
    "тринадцять": 13, "тринадцати": 13, "тринадцати́": 13, "тринадцать": 13,
    "чотирнадцять": 14, "чотирнадцяти": 14, "четырнадцать": 14, "четырнадцати": 14,
    "п'ятнадцять": 15, "пятнадцять": 15, "п'ятнадцяти": 15,
    "пятнадцать": 15, "пятнадцати": 15,
    "шістнадцять": 16, "шіснадцять": 16, "шістнадцяти": 16, "шіснадцяти": 16,
    "шестнадцать": 16, "шестнадцати": 16,
    "сімнадцять": 17, "сімнадцяти": 17,
    "семнадцать": 17, "семнадцати": 17,
    "вісімнадцять": 18, "вісімнадцяти": 18,
    "восемнадцать": 18, "восемнадцати": 18,
    "дев'ятнадцять": 19, "девятнадцять": 19, "дев'ятнадцяти": 19,
    "девятнадцать": 19, "девятнадцати": 19,
    "двадцять": 20, "двадцяти": 20,
    "двадцать": 20, "двадцати": 20,
    "двадцять один": 21, "двадцятого першого": 21,
    "двадцать один": 21, "двадцатого первого": 21,
    "двадцять два": 22, "двадцятого другого": 22,
    "двадцать два": 22, "двадцатого второго": 22,
    "двадцять три": 23, "двадцятого третього": 23,
    "двадцать три": 23,
    "двадцять чотири": 24,
    "двадцать четыре": 24,
}

_DIAMETER_QUESTION_KEYWORDS = (
    "діаметр", "діамет",  # UA
    "диаметр", "диамет",  # RU
    "розмір", "размер",
    "радіус", "радиус",
    "дюйм",  # «на скільки дюймів»
)


def is_diameter_question(bot_utterance: str) -> bool:
    """True if the bot's last question was about diameter/size."""
    if not bot_utterance:
        return False
    low = bot_utterance.lower()
    return any(kw in low for kw in _DIAMETER_QUESTION_KEYWORDS)


def detect_diameter(customer_text: str) -> int | None:
    """Extract a tire diameter 13-24 from a customer utterance.

    Returns the matched integer or None if no diameter mention.

    Priority:
        1. Explicit «R17» / «эр 17» prefix.
        2. Ukrainian/Russian word ordinal (checked as substring so
           «на п'ятнадцять» matches «п'ятнадцять»). Multi-word forms
           («двадцять один») checked before single-word to avoid
           «двадцять» winning over «двадцять один».
        3. Bare number 13-24 (with lookaround so «14:30», «140»,
           «2024» are excluded).
    """
    if not customer_text:
        return None
    txt = customer_text.strip()
    if not txt:
        return None
    txt_low = txt.lower()

    # R-prefix takes priority (unambiguous).
    m = _R_PREFIX_RE.search(txt)
    if m:
        return int(m.group(1))

    # Multi-word forms first (longest match wins over single-word).
    for word, num in sorted(
        _WORD_TO_NUM.items(), key=lambda kv: -len(kv[0])
    ):
        if word in txt_low:
            return num

    # Bare number 13-24.
    m = _DIGIT_RE.search(txt)
    if m:
        return int(m.group(1))

    return None
