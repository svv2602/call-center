"""Detect which of the offered fitting slots the customer just picked.

Wave 14 (2026-09-07). The session already snapshots every slot returned by
``get_fitting_slots`` into ``fitting_slots_offered``, but nothing recorded the
customer's verbal pick until ``book_fitting`` fired. In that gap the LLM was
free to contradict its own offer — call 2026-09-07: bot read a truncated list,
client answered «14 это 20», bot said «Слота на чотирнадцяту двадцять немає»
and then listed the full set *including* 14:20.

A match is only ever returned when the parsed time is present in the offered
list, so this can pin an existing slot but never invent one.
"""

from __future__ import annotations

import re

_HOUR_MIN = 8
_HOUR_MAX = 20

# Word stems → number. Stems are matched case-insensitively after apostrophe
# normalisation; the alternation is built longest-first so that «пʼятнадцят»
# wins over «пʼят» and «двадцят» over «два».
_NUM_WORDS: dict[str, int] = {
    "нуль": 0, "ноль": 0, "рівно": 0, "ровно": 0,
    "один": 1, "одна": 1,
    "два": 2, "дві": 2, "две": 2,
    "три": 3,
    "чотири": 4, "четыре": 4,
    "п'ят": 5, "пят": 5,
    "шість": 6, "шесть": 6,
    "сім": 7, "семь": 7,
    "восьм": 8, "вісім": 8, "восім": 8, "восем": 8,
    "дев'ят": 9, "девят": 9, "девьят": 9,
    "десят": 10,
    "одинадцят": 11, "одиннадцат": 11,
    "дванадцят": 12, "двенадцат": 12,
    "тринадцят": 13, "тринадцат": 13,
    "чотирнадцят": 14, "чотирнадцат": 14, "четырнадцат": 14,
    "п'ятнадцят": 15, "пятнадцят": 15, "пятнадцат": 15,
    "шістнадцят": 16, "шіснадцят": 16, "шестнадцат": 16,
    "сімнадцят": 17, "семнадцат": 17,
    "вісімнадцят": 18, "восімнадцят": 18, "восемнадцат": 18,
    "дев'ятнадцят": 19, "девятнадцят": 19, "девятнадцат": 19,
    "двадцят": 20, "двадцать": 20,
    "тридцят": 30, "тридцать": 30,
    "сорок": 40,
    "п'ятдесят": 50, "пятдесят": 50, "пятьдесят": 50,
}

_WORD_RE = re.compile(
    "(?<![а-яіїєґёa-z])("
    + "|".join(
        re.escape(w)
        for w in sorted(_NUM_WORDS, key=len, reverse=True)
    )
    + r")|(\d{1,4})",
    re.IGNORECASE,
)

# Diameter markers — «R17» / «ер 17» must never be read as 17:00.
_DIAMETER_PREFIX_RE = re.compile(
    r"(?:\bR|\bэр|\bер|\bар)\s*\d{1,2}", re.IGNORECASE
)

_TIME_TOKEN_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")

#: The bot announcing what is free. Anchors the slot scan below: hours named
#: *after* one of these are an offer, while hours named before it are as
#: likely a price («для сімнадцятого діаметра… коштує») or a Krok 8 summary
#: («перевіримо: дев'ятого вересня о 11:00…»). Counting hours anywhere in the
#: utterance turns `allow_hour_only` on for 37 of the corpus's 479 bot turns,
#: most of them exactly those two shapes — which is what the flag exists to
#: prevent.
_AVAILABILITY_RE = re.compile(
    r"вільн\w*\s+час\w*|вільні|зі списку|з переліку|доступн\w*\s+час\w*",
    re.IGNORECASE,
)

#: «12 вересня» is a date, not a slot. Without this the rule below would fire
#: on the day number whenever it happened to be ≤ 20 — behaviour that depends
#: on the calendar rather than on what the bot said. Deliberately a local copy
#: rather than an import of `compound_parse._DAY_MONTH_RE`: this module is the
#: one detector `compound_parse` does not call (see its docstring), and the
#: dependency runs that way round on purpose.
_DAY_MONTH_RE = re.compile(
    r"\b\d{1,2}\s+(?:січня|лютого|березня|квітня|травня|червня|липня|серпня|"
    r"вересня|жовтня|листопада|грудня|января|февраля|марта|апреля|мая|июня|"
    r"июля|августа|сентября|октября|ноября|декабря)\b",
    re.IGNORECASE,
)


def _normalize(text: str) -> str:
    return text.lower().replace("ʼ", "'").replace("’", "'").replace("`", "'")


def _extract_numbers(text: str) -> list[int]:
    """Return every number in the utterance, in spoken order."""
    nums: list[int] = []
    for m in _WORD_RE.finditer(text):
        if m.group(1) is not None:
            nums.append(_NUM_WORDS[m.group(1)])
        else:
            nums.append(int(m.group(2)))
    return _merge_composites(nums)


def _merge_composites(nums: list[int]) -> list[int]:
    """Collapse «двадцять п'ять» → 25 so it is not read as 20 then 5."""
    merged: list[int] = []
    i = 0
    while i < len(nums):
        if (
            i + 1 < len(nums)
            and nums[i] in (20, 30, 40, 50)
            and 1 <= nums[i + 1] <= 9
        ):
            merged.append(nums[i] + nums[i + 1])
            i += 2
        else:
            merged.append(nums[i])
            i += 1
    return merged


def bot_listed_slots(bot_utterance: str) -> bool:
    """True when the bot's last turn read out the available times.

    Two ways to qualify, the second strictly widening the first:

    * two or more `HH:MM` literals — the original rule, unchanged;
    * an availability marker followed by at least one hour. This is the TTS
      path: the bot reads slots out in words («З переліку вільні: дев'ять,
      десять двадцять, одинадцять сорок, тринадцять»), which contains no
      `HH:MM` token at all, so the original rule scored zero and
      `allow_hour_only` stayed off — leaving `detect_time_choice` unable to
      match the caller's «давайте на 13» against a slot the bot had just
      offered. Call `f2bec2d6` died in TIME on exactly that turn. It also
      covers a one-slot day, which is a list of length one.

    Missed 9 of the 33 slot announcements in the 2026-09-07..09 corpus before
    this; 0 after, with no turn losing its previous verdict.
    """
    if not bot_utterance:
        return False
    if len(_TIME_TOKEN_RE.findall(bot_utterance)) >= 2:
        return True
    normalized = _normalize(bot_utterance)
    marker = _AVAILABILITY_RE.search(normalized)
    if not marker:
        return False
    tail = _DAY_MONTH_RE.sub(" ", normalized[marker.end() :])
    return any(_HOUR_MIN <= n <= _HOUR_MAX for n in _extract_numbers(tail))


def detect_time_choice(
    customer_text: str,
    offered_times: list[str],
    *,
    allow_hour_only: bool = False,
) -> str | None:
    """Return the offered ``HH:MM`` the customer picked, or ``None``.

    ``allow_hour_only`` widens matching to a bare hour («давайте на десяту»)
    and should only be enabled when the bot has just read out the slot list —
    otherwise a bare «17» is far more likely to be a wheel diameter.
    """
    if not customer_text or not offered_times:
        return None

    offered = set(offered_times)
    text = _normalize(customer_text)
    # Drop «R17»-style diameters before parsing numbers.
    text = _DIAMETER_PREFIX_RE.sub(" ", text)
    nums = _extract_numbers(text)

    # A dictated phone number produces a long digit run in which some
    # neighbouring pair will eventually look like a valid slot («нуль дев'ять
    # нуль…» → 09:00). Slot picks are short, so bail out on long sequences.
    if len(nums) > 5 or re.search(r"\d{6,}", text):
        return None

    for i, hour in enumerate(nums):
        if not _HOUR_MIN <= hour <= _HOUR_MAX:
            # «1420» spoken as one token.
            if 800 <= hour <= 2059:
                candidate = f"{hour // 100:02d}:{hour % 100:02d}"
                if candidate in offered:
                    return candidate
            continue
        if i + 1 < len(nums) and 0 <= nums[i + 1] <= 59:
            candidate = f"{hour:02d}:{nums[i + 1]:02d}"
            if candidate in offered:
                return candidate

    if allow_hour_only:
        for hour in nums:
            if not _HOUR_MIN <= hour <= _HOUR_MAX:
                continue
            same_hour = [t for t in offered if t.startswith(f"{hour:02d}:")]
            if len(same_hour) == 1:
                return same_hour[0]

    return None
