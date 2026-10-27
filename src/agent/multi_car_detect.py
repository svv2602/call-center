"""Detect a caller who wants several cars booked in one call.

Owner request 2026-10-27 (multi-car booking): «Твоя шина» books several
cars one after another in a single call (up to three); a fourth car and
more is a fleet and goes to an operator. The question is never asked —
only a caller who says it himself («дві машини», after a booking «ще одну
запишіть») triggers the flow.

Pure function, zero I/O, no wiring: the network (ТШ/ПК) is not known
here, the caller decides what to do with the result.

Traps this module is built around:
- one-language lookup reads as silence: every number word exists in uk
  and ru, in oblique cases and with diminutive nouns («двома машинками»,
  «двух автомобилей», «пару машин»);
- tyre counts are not car counts: «два колеса», «дві шини», «4 колеса
  машини» — a tyre/wheel word between the number and the car noun
  blocks the match;
- «машина друга», «другий раз», «друга половина дня» carry «друг-» but are
  not a second car;
- an explicit limit («але запис на одну») turns the intent into count=1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_CARS_PER_CALL = 3

_B = r"(?<![\w'])"
_E = r"(?![\w'])"

_CAR = (
    r"(?:машин\w*|автомашин\w*|автомобіл\w*|автомобил\w*|автівк\w*|"
    r"тачк\w*|авто" + _E + r")"
)

# Only a plural adjective may stand between the number and the car noun
# («дві різні машини», «две легковые машины», «двох своїх машин»). The
# adjective-ending requirement is what keeps tyre counts out: «два колеса
# машини», «дві шини машини» — the tyre word is not an adjective, so the
# number never reaches the car noun. «дні/тижні» end like an adjective and
# are excluded by name («на два дні машину залишити»).
_ADJ = r"(?!(?:дн|тижн)[\w']*" + _E + r")[\w']+(?:і|ї|ые|ие|их|іх|ими|іми|ыми)"

_NUMBERS_UA: dict[str, int] = {
    "два": 2,
    "дві": 2,
    "двох": 2,
    "двома": 2,
    "двоє": 2,
    "обидві": 2,
    "обидва": 2,
    "обох": 2,
    "три": 3,
    "трьох": 3,
    "трьома": 3,
    "троє": 3,
    "чотири": 4,
    "чотирьох": 4,
    "чотирма": 4,
    "четверо": 4,
    "п'ять": 5,
    "п'яти": 5,
    "п'ятьох": 5,
    "п'ятеро": 5,
    "шість": 6,
    "шести": 6,
    "сім": 7,
    "вісім": 8,
    "дев'ять": 9,
    "десять": 10,
}

_NUMBERS_RU: dict[str, int] = {
    "две": 2,
    "двух": 2,
    "двумя": 2,
    "двое": 2,
    "оба": 2,
    "обе": 2,
    "обеих": 2,
    "обоих": 2,
    "пару": 2,
    "пара": 2,
    "парочку": 2,
    "трех": 3,
    "трёх": 3,
    "тремя": 3,
    "трое": 3,
    "четыре": 4,
    "четырех": 4,
    "четырёх": 4,
    "четырьмя": 4,
    "пять": 5,
    "пяти": 5,
    "пятеро": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
}

_NUMBERS: dict[str, int] = {**_NUMBERS_UA, **_NUMBERS_RU}

_NUMBER_WORD = "|".join(sorted((re.escape(w) for w in _NUMBERS), key=len, reverse=True))

_COUNT_RE = re.compile(
    _B + r"(?P<num>" + _NUMBER_WORD + r"|\d{1,2})" + _E + r"\s+(?:" + _ADJ + r"\s+)?" + _CAR
)

# «кілька машин» — several, number unknown.
_SEVERAL_RE = re.compile(_B + r"(?:кілька|декілька|несколько)\s+(?:" + _ADJ + r"\s+)?" + _CAR)

_FLEET_RE = re.compile(
    _B + r"(?:автопарк\w*|парк\s+(?:машин|автомобіл\w*|автомобил\w*|авто" + _E + r"))"
)

_MORE_WORD = r"(?:ще|щё|ещё|еще)"

# «ще одну машину», «ещё один автомобиль», «ще одне авто».
_MORE_WITH_CAR_RE = re.compile(
    _B + _MORE_WORD + r"\s+(?:одн\w*|один)\s+(?:" + _ADJ + r"\s+)?" + _CAR
)

# «ще одну запишіть», «ещё одну, пожалуйста», «і ще одну» (bare, end of phrase).
_MORE_BARE_RE = re.compile(
    _B
    + _MORE_WORD
    + r"\s+(?:одну|один)"
    + r"(?:\s*[,.!?]|\s*$|\s+(?:запи\w*|оформ\w*|теж|тоже|також|также|будь|пожал\w*|"
    r"можна|можно|хоч\w*|хот\w*|треба|надо|нужн\w*|потр\w*|давай\w*|прошу))"
)

# «ще дві машини» — more on top of a count.
_MORE_BEFORE_COUNT_RE = re.compile(_B + _MORE_WORD + r"\s+$")

# «другу машину», «друге авто», «вторую машину».
_ORDINAL_RE = re.compile(
    _B
    + r"(?:другу|друге|другий|друга|другої|вторую|второе|второй|вторая|второго)"
    + r"\s+(?:"
    + _ADJ
    + r"\s+)?"
    + _CAR
)
# «у друга машина», «для друга авто» — the friend's car, not a second one.
_FRIEND_PREP_RE = re.compile(
    _B + r"(?:у|в|для|від|од|до|с|со|з|із|мого|моего|мому|моему|мій|мой)\s+$"
)

# «і другу теж», «и вторую тоже».
_AND_SECOND_TOO_RE = re.compile(
    _B
    + r"(?:і|й|и|та|а)\s+(?:другу|друге|другий|вторую|второе|второй)\s+(?:теж|також|тоже|также)"
    + _E
)

# «але запис на одну», «но только одну», «тільки одну». A limit never
# overrides «ще одну» — detect_multi_car checks it only when more is False.
_ONE_WORD = r"(?:одну|одна|одне|одно|один|одной|однієї)" + _E
_LIMIT_RE = re.compile(
    _B
    + r"(?:(?:лише|тільки|только|лиш|всего|поки\s+що|пока\s+что)\s+(?:на\s+)?"
    + _ONE_WORD
    + r"|(?:але|но|однак|однако)\s+(?:[\w']+\s+){0,3}?(?:на\s+)?"
    + _ONE_WORD
    + r")"
)


@dataclass(frozen=True)
class MultiCarIntent:
    """What the caller said about booking several cars.

    count: how many cars (2, 3, 4…) or None when unknown («кілька машин»,
        «ще одну»); 1 when the caller explicitly limited the booking.
    more: a car on top of what is already booked («ще одну», «і другу теж»).
    over_cap: more than MAX_CARS_PER_CALL cars, or a fleet → operator.
    """

    count: int | None
    more: bool
    over_cap: bool


def _norm(text: str) -> str:
    return (text or "").lower().replace("’", "'").replace("ʼ", "'").replace("`", "'")


def _first_count(low: str) -> tuple[int, bool] | None:
    for m in _COUNT_RE.finditer(low):
        raw = m.group("num")
        n = int(raw) if raw.isdigit() else _NUMBERS[raw]
        if n < 2:
            continue
        more = bool(_MORE_BEFORE_COUNT_RE.search(low[: m.start()]))
        return n, more
    return None


def _ordinal_second(low: str) -> bool:
    return any(not _FRIEND_PREP_RE.search(low[: m.start()]) for m in _ORDINAL_RE.finditer(low))


def detect_multi_car(text: str) -> MultiCarIntent | None:
    """Return the caller's multi-car intent, or None when he did not say it."""
    low = _norm(text)
    if not low:
        return None

    count: int | None = None
    more = False
    found = False

    hit = _first_count(low)
    if hit is not None:
        count, more = hit
        found = True
    elif _SEVERAL_RE.search(low):
        found = True

    if _MORE_WITH_CAR_RE.search(low) or _MORE_BARE_RE.search(low):
        more = True
        found = True
    if _ordinal_second(low) or _AND_SECOND_TOO_RE.search(low):
        more = True
        found = True

    fleet = bool(_FLEET_RE.search(low))
    if fleet:
        found = True

    if not found:
        return None

    if not more and _LIMIT_RE.search(low):
        return MultiCarIntent(count=1, more=False, over_cap=False)

    over_cap = fleet or (count is not None and count > MAX_CARS_PER_CALL)
    return MultiCarIntent(count=count, more=more, over_cap=over_cap)
