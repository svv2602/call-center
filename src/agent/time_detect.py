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

#: The six ordinal stems `compound_parse._HOUR_ORDINALS` knows and the cardinal
#: table above does not. `_mentions_time` has understood «на другу» since Wave
#: 6-A while the vocabulary that actually pins a slot did not, so a caller who
#: named the hour as an ordinal was heard talking about time and then matched
#: against nothing. The other fourteen ordinals need no entry — «п'яту» already
#: matches the cardinal stem «п'ят», «восьму» matches «восьм», «десяту»
#: matches «десят».
_ORDINAL_ONLY_STEMS: dict[str, int] = {
    "перш": 1, "друг": 2, "трет": 3, "четверт": 4, "шост": 6, "сьом": 7,
}

#: Feminine endings only, copied from `compound_parse._HOUR_ORDINAL_ENDING`.
#: These are the forms that agree with «годину»/«годині». The stems are *not*
#: admitted bare, because the masculine genitive — «сьомого», «третього» — is a
#: day of the month: «сьомого вересня» would otherwise read as 19:00. None of
#: the generated forms is a prefix of a genitive one, which is what keeps that
#: separation. Some combinations («трета») are not words; they simply never
#: match.
_NUM_WORDS.update(
    {
        stem + ending: hour
        for stem, hour in _ORDINAL_ONLY_STEMS.items()
        for ending in ("а", "у", "ої", "ьої", "ій", "ю")
    }
)

_NUM_ALT = "|".join(re.escape(w) for w in sorted(_NUM_WORDS, key=len, reverse=True))

_WORD_RE = re.compile(
    "(?<![а-яіїєґёa-z])(" + _NUM_ALT + r")|(\d{1,4})",
    re.IGNORECASE,
)

#: A count of wheels, not an hour. «два колеса» and «на 4 колеса» are the
#: price flow's multiplier triggers (`prompts.py:706`), and 2 and 4 are also
#: the two most-offered afternoon hours once the 12-hour clock is read. The
#: booking flow never asks for a count — it books a set of four
#: (`prompts.py:450`) — so any number that carries one of these nouns is
#: dropped before the slot scan rather than disambiguated after it.
_QUANTITY_RE = re.compile(
    r"(?:(?<![а-яіїєґёa-z])(?:" + _NUM_ALT + r")\w*|\d{1,2})\s*"
    r"(?:колес|коліс|шин|балон|диск|штук)\w*",
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

#: The bot asking which hour the caller wants. Deliberately interrogative — the
#: bare noun «час» is not enough, because «вільний час на 12 вересня уточнюю» and
#: «Приймаємо цю годину?» are not questions about which hour, and a bare number
#: after either of those is as likely a diameter. Narrower than the `time` entry
#: in `pipeline._CTX_KEYWORDS`, which serves STT correction, where a false
#: positive costs nothing.
#: The forms are the ones the bot actually used in the last 30 days, not a
#: guess: «О котрій зручніше?», «Який час зручний?», «на яку годину вам зручно»,
#: «На якій годині вам зручно?». Note «якийсь час» does not match «який час» —
#: the stem is followed by «сь», which is why the alternatives are spelled out
#: rather than written as `як\w+\s+час`.
_TIME_QUESTION_RE = re.compile(
    r"о котр\w*|котр\w+\s+годин\w*|який час|яку годину|якій годині|"
    r"во сколько|в котором часу",
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


def _hour_variants(n: int) -> list[int]:
    """Every hour a spoken number could name.

    Callers use the 12-hour clock — «на два», «на два часа дня», «на другу»
    all mean 14:00 — and the 24-hour reading threw those away before any slot
    was looked at. Call `8abd8557` (2026-09-10): the bot read out the 17
    September list, the caller said «давайте на два», this returned ``None``,
    the LLM answered «Час 10:20 прийнято», and the caller's correction («я
    хотел на два часа дня») ended in a transfer.

    Mapping 1-7 onto the afternoon takes no reading away: 45 days of prod
    ``get_fitting_slots`` results contain no slot outside 08:00-17:59, so a
    literal 02:00 could never have matched anything. The membership test in
    ``detect_time_choice`` remains the real authority — this only widens what
    is offered up to it.
    """
    if _HOUR_MIN <= n <= _HOUR_MAX:
        return [n]
    if 1 <= n <= 7:
        return [n + 12]
    return []


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


def bot_asked_for_time(bot_utterance: str) -> bool:
    """True when the bot's last turn asked the caller which hour they want.

    A second, independent reason a bare number is an hour. `bot_listed_slots`
    answers «did the bot read the times out», which is not the only turn on
    which «о 12» is a time: the bot routinely asks Krok 4 bare («О котрій
    зручніше?», `prompts.py:2016`) and then the caller's answer is *only* ever
    an hour. Call `431e60fb` (2026-09-10) died on exactly that turn.

    The flag this feeds exists to stop a bare «17» being read as an R17
    diameter. That risk is what makes the marker set narrow: it takes an
    explicit interrogative about the hour, so a price quote, a Krok 8 summary
    and a «Приймаємо цю годину?» confirmation all stay out.
    """
    if not bot_utterance:
        return False
    return bool(_TIME_QUESTION_RE.search(_normalize(bot_utterance)))


def hour_only_allowed(bot_utterance: str) -> bool:
    """Whether a bare hour may be matched against the offered slots.

    The single authority for that decision. It had been spelled out
    independently at both call sites (`parsers/time_parser.py`, the Wave 14 pin
    in `core/pipeline.py`), which is two copies of a rule that must not drift.
    """
    return bot_listed_slots(bot_utterance) or bot_asked_for_time(bot_utterance)


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
    # Drop «R17»-style diameters and «два колеса»-style counts before parsing.
    text = _DIAMETER_PREFIX_RE.sub(" ", text)
    text = _QUANTITY_RE.sub(" ", text)
    nums = _extract_numbers(text)

    # A dictated phone number produces a long digit run in which some
    # neighbouring pair will eventually look like a valid slot («нуль дев'ять
    # нуль…» → 09:00). Slot picks are short, so bail out on long sequences.
    if len(nums) > 5 or re.search(r"\d{6,}", text):
        return None

    for i, raw in enumerate(nums):
        # «1420» spoken as one token.
        if 800 <= raw <= 2059:
            candidate = f"{raw // 100:02d}:{raw % 100:02d}"
            if candidate in offered:
                return candidate
        if not (i + 1 < len(nums) and 0 <= nums[i + 1] <= 59):
            continue
        for hour in _hour_variants(raw):
            candidate = f"{hour:02d}:{nums[i + 1]:02d}"
            if candidate in offered:
                return candidate

    if allow_hour_only:
        for i, raw in enumerate(nums):
            # The caller named minutes, and the loop above already found that
            # `HH:MM` is not on offer. Widening here would answer a request for
            # 11:20 with 11:00 — a time nobody asked for, silently. Measured on
            # 30 days of prod turns: without this, «11:20», «15:20», «на 12-20»
            # and «9 20» all became the top of their hour.
            if i + 1 < len(nums) and 0 <= nums[i + 1] <= 59:
                continue
            # A bare hour names the slot on the hour when there is one. Treating
            # «о 12» as ambiguous because 12:30 also exists reads the caller as
            # vaguer than they were: a half-past pick is spoken as «дванадцять
            # тридцять». Call `431e60fb` (2026-09-10) offered the full 09:00-17:30
            # half-hour grid, the caller said «о 12», this returned None, and TIME
            # took a `parser_null` charge for the very turn that answered it — the
            # third charge escalated a fully collected, customer-confirmed booking
            # to an operator. The LLM had read the same turn as 12:00 («Дванадцята
            # прийнята»).
            for hour in _hour_variants(raw):
                same_hour = [t for t in offered if t.startswith(f"{hour:02d}:")]
                if not same_hour:
                    continue
                on_the_hour = f"{hour:02d}:00"
                if on_the_hour in offered:
                    return on_the_hour
                # Nothing on the hour: one slot in it is still an unambiguous
                # pick, several remain a genuine coin flip.
                if len(same_hour) == 1:
                    return same_hour[0]

    return None
