"""Vehicle model name as heard by STT → forms the catalogue can match.

The catalogue (``vehicle_models``) stores letter-code models in Latin with a
suffix — ``GLA-Class``, ``3 Series``, ``X5`` — while a caller says «Мерседес
ГЛА», «джі ел ей», «гла клас», «GLA 200», «ікс п'ять», «Ку5». None of those
match exactly, by alias or by trigram (Cyrillic never scores against Latin),
so the lookup used to return "not found" or a sibling (``GLE`` → ``GLE AMG``).

Pure functions only — no I/O. ``StoreClient._find_vehicle_model`` calls
:func:`model_query_variants` after the as-is lookup, then
:func:`pick_prefix_model` over the rows of one ``LIKE '<key>%'`` query.

Rules
-----
- letters spelled one by one (ua + ru letter names: «джі/джи»=G, «ел/эл»=L,
  «ей/эй»=A, «сі/си/це»=C, «ікс/икс»=X, «ку/кью»=Q …) are joined into a code
  when two or more stand in a row, or when one is followed by a number
  («ікс п'ять» → ``x5``);
- a short Cyrillic abbreviation («ГЛА», «Х5», «ц») is transliterated by
  letter sound (``ц`` → ``c``, ``х`` → ``x``) — never ``c`` → ``k``;
- «клас/класс/class/серія/серия/series» are dropped (the fallback adds the
  suffix back), and so is the engine: displacement («2.0», «2,0») anywhere,
  a three-digit engine number («200», «300d», «220cdi») after a letter code.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# Latin letter → how it is pronounced when spelled out (ua + ru forms).
_LETTER_NAMES: dict[str, tuple[str, ...]] = {
    "a": ("ей", "эй", "а"),
    "b": ("бі", "би", "бе", "бэ"),
    "c": ("сі", "си", "це", "ці", "цэ"),
    "d": ("ді", "ди", "де", "дэ"),
    "e": ("і", "и", "е", "э"),
    "f": ("еф", "эф"),
    "g": ("джі", "джи", "же", "ге", "гэ"),
    "h": ("ейч", "эйч", "аш", "ха"),
    "i": ("ай",),
    "j": ("джей",),
    "k": ("кей", "ка"),
    "l": ("ел", "эл", "ель", "эль"),
    "m": ("ем", "эм"),
    "n": ("ен", "эн"),
    "o": ("о",),
    "p": ("пі", "пи", "пе", "пэ"),
    "q": ("ку", "кью"),
    "r": ("ар", "ер", "эр"),
    "s": ("ес", "эс"),
    "t": ("ті", "ти", "те", "тэ"),
    "u": ("ю",),
    "v": ("ві", "ви", "ве", "вэ"),
    "x": ("ікс", "икс", "екс", "экс"),
    "y": ("вай", "ігрек", "игрек"),
    "z": ("зет", "зед", "зі", "зи"),
}
LETTER_BY_NAME: dict[str, str] = {
    name: letter for letter, names in _LETTER_NAMES.items() for name in names
}

NUMBER_WORDS: dict[str, str] = {
    "нуль": "0", "ноль": "0",
    "один": "1", "одна": "1", "одно": "1", "раз": "1",
    "два": "2", "дві": "2", "две": "2",
    "три": "3",
    "чотири": "4", "четыре": "4",
    "п'ять": "5", "пять": "5",
    "шість": "6", "шесть": "6",
    "сім": "7", "семь": "7",
    "вісім": "8", "восемь": "8",
    "дев'ять": "9", "девять": "9",
}  # fmt: skip

# Cyrillic letter → Latin letter by *sound* of a model code («ГЛЦ» = GLC).
CYR_TO_LAT: dict[str, str] = {
    "а": "a", "б": "b", "в": "v", "г": "g", "ґ": "g", "д": "d", "е": "e",
    "є": "e", "э": "e", "ж": "zh", "з": "z", "и": "i", "і": "i", "ї": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p",
    "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "x", "ц": "c",
    "ч": "ch", "ш": "sh", "ы": "y", "ю": "yu", "я": "ya", "ь": "",
}  # fmt: skip

CLASS_WORDS: frozenset[str] = frozenset({
    "клас", "класс", "класу", "класа", "класса", "классе", "класі",
    "class", "klasse",
    "серія", "серії", "серію", "серия", "серии", "серию", "series", "серіес", "сериес",
    "модель", "моделі", "модели",
})  # fmt: skip

ENGINE_WORDS: frozenset[str] = frozenset({
    "cdi", "tdi", "tsi", "fsi", "crdi", "tfsi", "hdi", "dci",
    "дизель", "бензин", "л", "літра", "литра", "літри", "литров", "літрів",
})  # fmt: skip

# Suffixes the prefix fallback puts back after a bare code ("gle" → "gle-class").
BASE_SUFFIXES: tuple[str, ...] = ("-class", " class", "-series", " series")

# A name carrying one of these is a variant of the base model, not the base.
MODIFIER_WORDS: frozenset[str] = frozenset({
    "amg", "coupe", "cabrio", "cabriolet", "sportback", "roadster", "convertible",
    "all-terrain", "allroad", "gran", "turismo", "tourer", "variant", "long",
})  # fmt: skip

_DISPLACEMENT_RE = re.compile(r"^\d[.,]\d$")
_ENGINE_NUMBER_RE = re.compile(r"^\d{3}[a-zа-яіїє]{0,3}$")
_CODE_RE = re.compile(r"^[a-z]{1,4}\d{0,2}$")
_CYR_ABBR_RE = re.compile(r"^[а-яіїєґ]{1,3}\d{0,2}$")
_GLUED_LETTER_NUM_RE = re.compile(
    r"^(" + "|".join(sorted(LETTER_BY_NAME, key=len, reverse=True)) + r")(\d{1,2})$"
)
_BOUNDARY = (" ", "-", "(")


def normalize_model_text(text: str) -> str:
    """Lowercase, ё→е, one apostrophe, whitespace collapsed."""
    if not text:
        return ""
    s = text.strip().lower().replace("ё", "е")
    s = re.sub(r"[’ʼ`´]", "'", s)
    return re.sub(r"\s+", " ", s)


def _tokens(text: str) -> list[str]:
    # hyphens split «гле-клас», «x-5»; a comma/dot inside a number stays («2,0»)
    raw = re.split(r"[\s\-/]+", text)
    out: list[str] = []
    for tok in raw:
        tok = tok.strip('.,!?;:"«»()')
        if tok:
            out.append(tok)
    return out


def _cyr_abbr_to_lat(tok: str) -> str:
    return "".join(CYR_TO_LAT.get(ch, ch) for ch in tok)


def _to_latin_tokens(tokens: list[str]) -> list[str]:
    """Spelled letters / number words / short Cyrillic codes → Latin tokens."""
    # 1. classify each token: ("L", letter) spelled letter, ("N", digits), ("W", word)
    kinds: list[tuple[str, str]] = []
    for tok in tokens:
        if tok in NUMBER_WORDS:
            kinds.append(("N", NUMBER_WORDS[tok]))
        elif tok.isdigit():
            kinds.append(("N", tok))
        elif tok in LETTER_BY_NAME:
            kinds.append(("L", LETTER_BY_NAME[tok]))
        else:
            kinds.append(("W", tok))

    out: list[str] = []
    i = 0
    while i < len(kinds):
        kind, val = kinds[i]
        if kind == "L":
            j = i
            letters = ""
            while j < len(kinds) and kinds[j][0] == "L":
                letters += kinds[j][1]
                j += 1
            digits = ""
            if j < len(kinds) and kinds[j][0] == "N" and len(kinds[j][1]) <= 2:
                digits = kinds[j][1]
                j += 1
            if len(letters) >= 2 or digits:
                out.append(letters + digits)
                i = j
                continue
            # a lone letter: a spelled name («джі клас», «ес клас») is the
            # letter; a one-char token («е», «ц») reads as an abbreviation
            tok = tokens[i]
            if len(tok) > 1:
                out.append(letters)
            else:
                out.append(_cyr_abbr_to_lat(tok))
            i += 1
            continue
        if kind == "N":
            out.append(val)
            i += 1
            continue
        tok = val
        m = _GLUED_LETTER_NUM_RE.match(tok)
        if m:
            out.append(LETTER_BY_NAME[m.group(1)] + m.group(2))
        elif _CYR_ABBR_RE.match(tok):
            out.append(_cyr_abbr_to_lat(tok))
        else:
            out.append(tok)
        i += 1
    return out


def _strip_noise(tokens: list[str]) -> list[str]:
    """Drop class words, displacement and an engine number after a code."""
    out: list[str] = []
    for tok in tokens:
        if tok in CLASS_WORDS or tok in ENGINE_WORDS or _DISPLACEMENT_RE.match(tok):
            continue
        if (
            _ENGINE_NUMBER_RE.match(tok)
            and out
            and (_CODE_RE.match(out[-1]) or _CYR_ABBR_RE.match(out[-1]))
            and not out[-1].isdigit()
        ):
            continue
        out.append(tok)
    return out


def _glue_code_digits(tokens: list[str]) -> list[str]:
    """«x 5» → «x5», «rav 4» → «rav4»: a short code followed by one or two digits."""
    out: list[str] = []
    for tok in tokens:
        if out and tok.isdigit() and len(tok) <= 2 and re.fullmatch(r"[a-z]{1,3}", out[-1]):
            out[-1] += tok
            continue
        out.append(tok)
    return out


def model_query_variants(raw: str) -> list[str]:
    """Ordered, de-duplicated lookup keys for a heard model name.

    The as-is form (``normalize_model_text(raw)``) is never included — the
    caller already tried it. Order: the stripped original script first (a
    Cyrillic alias like «ленд крузер» still matches), then the Latin form.
    """
    base = normalize_model_text(raw)
    if not base:
        return []
    tokens = _tokens(base)
    stripped = _strip_noise(tokens)
    latin = _glue_code_digits(_strip_noise(_to_latin_tokens(stripped)))

    variants: list[str] = []
    for cand in (" ".join(stripped), " ".join(latin)):
        cand = cand.strip()
        if cand and cand != base and cand not in variants:
            variants.append(cand)
    return variants


def _name(row: Mapping[str, Any]) -> str:
    return str(row["name"])


def is_base_variant(key: str, name: str) -> bool:
    """``name`` is ``key`` itself or ``key`` + a class/series suffix."""
    low = normalize_model_text(name)
    return low == key or any(low == key + suf for suf in BASE_SUFFIXES)


def _is_plain(key: str, name: str) -> bool:
    """Starts with ``key`` on a word boundary, no body code, no modifier."""
    low = normalize_model_text(name)
    if not low.startswith(key):
        return False
    rest = low[len(key) :]
    if rest and not rest.startswith(_BOUNDARY):
        return False
    if "(" in low:
        return False
    words = set(re.split(r"[\s\-]+", rest))
    return not (words & MODIFIER_WORDS)


def pick_prefix_model(
    key: str, rows: Sequence[Mapping[str, Any]]
) -> tuple[Mapping[str, Any] | None, bool]:
    """Pick the base model among ``rows`` (``name LIKE key%`` of one brand).

    Returns ``(row, ambiguous)``. ``rows`` must be ordered best-first for
    same-name duplicates (the caller orders by number of kits), so the first
    row of the chosen name is the one to use.

    1. ``<key>`` / ``<key>-class`` / ``<key> series`` — the base by name;
    2. otherwise names starting with ``<key>`` on a word boundary, without a
       body code in brackets and without AMG/Coupe/…; exactly one distinct
       name → it;
    3. anything else with candidates → ambiguous (``None, True``): not
       guessing is better than a sibling model.
    """
    key = normalize_model_text(key)
    if not key or not rows:
        return None, False
    base = [r for r in rows if is_base_variant(key, _name(r))]
    if base:
        names = {normalize_model_text(_name(r)) for r in base}
        if len(names) == 1:
            return base[0], False
        # «gle» and «gle-class» both present: the bare code is the base
        exact = [r for r in base if normalize_model_text(_name(r)) == key]
        return (exact[0], False) if exact else (None, True)
    bounded = [r for r in rows if _is_plain(key, _name(r))]
    names = {normalize_model_text(_name(r)) for r in bounded}
    if len(names) == 1:
        return bounded[0], False
    starts = [
        r
        for r in rows
        if normalize_model_text(_name(r)).startswith(key)
        and normalize_model_text(_name(r))[len(key) : len(key) + 1] in ("", *_BOUNDARY)
    ]
    return None, bool(starts)


def prefix_like_pattern(key: str) -> str:
    """``LIKE`` pattern for ``key`` with ``%``/``_``/``\\`` escaped."""
    esc = key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return esc + "%"
