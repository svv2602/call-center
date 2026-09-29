"""Two tyres compared by the code, from their EU labels (sales).

Probe 2026-09-29 (`camry_compare_taurus_bridgestone`): «таурус лучше
бриджестоуна?» got a general brand answer from the knowledge base — Taurus
was 4th in the result and the model sees only the first three; «а чем
Туранза 6 лучше Т005?» got «в базі знань немає порівняння» or an invented
«новіша модель з можливими покращеннями». There was nothing to compare by;
now there is ``tire_eu_labels`` (migration 064), which the search puts on
every item as ``eu_label`` {fuel, wet, noise_db}.

``TyreCompare`` is one per call (like `vehicle_lookup_gate`): it keeps the
last ``search_tires`` result of the call — every item found, not only the
three the model is shown. ``plan(text)`` answers a compare question that
names at least two of those tyres with a phrase built from their labels
only: «<Бренд Модель>: мокра дорога B, економія палива C, шум 71 дБ; …» and
one sentence of which is better by each class — letters and dB as they are,
no word the data does not carry. A tyre without a label gets «по <X> даних
етикетки ЄС немає». Both loops say the phrase before the first LLM round
(the voice loop before the stream, the text loop first in the reply) and put
``compare_said_note`` at the end of the system prompt.

Default-deny: the phrase is said only when ALL hold —

- the utterance asks to compare (``is_compare_question``: «краща / лучше /
  гірша / хуже / порівняй / сравни / чим відрізняється / чем отличается /
  різниця / разница» or «X чи / или / або Y») and does not take a tyre
  («беру», «візьму», «оформлюйте»…) or ask about the price («скільки»,
  «ціна», «дешевше»…);
- it names ≥2 distinct tyres of the call's last result (``named_items``: a
  brand names all its tyres in the result unless a model of it is named too;
  Cyrillic sounding is read — «таурус», «бриджстоун», «туранза шість»,
  «т нуль нуль п'ять»), and at most ``MAX_COMPARED``;
- sales is on and a search ran in the call.

A tyre not in the last result is never compared — the model answers as
before. Loop-breaker: one phrase per turn (``plan`` is called once per
turn); a comparison is said again only when the caller asks again.
"""

from __future__ import annotations

import logging
import re
from itertools import pairwise
from typing import Any

logger = logging.getLogger(__name__)

#: More tyres than this in one question → the code stays silent (the phrase
#: would be a list nobody follows by ear).
MAX_COMPARED = 4

NO_LABEL = "даних етикетки ЄС немає"

_B = r"(?<![\w'])"

_COMPARE = re.compile(
    _B + r"(?:кращ\w*|лучш\w*|гірш\w*|хуже|хуж[еи]\w*|порівня\w*|порівнн\w*|сравн\w*"
    r"|відрізня\w*|відрізн\w*|отлича\w*|отличи[ея]\w*|різниц\w*|разниц\w*|vs)(?![\w'])"
)
#: «Туранза 6 чи Т005?» — a choice between two things: the word stands
#: between two words. «а чи є Туранза?» / «чи можна…» is the question
#: particle, not a choice.
_OR = re.compile(
    r"[\w'][\s,]+(?:чи|или|або|проти|против)\s+"
    r"(?!(?:є|есть|можна|можно|ви|вы|у|в|буде|будет|маєте|имеете|знаєте|знаете)(?![\w']))"
    r"[\w']"
)
_OR_PARTICLE_BEFORE = re.compile(
    _B + r"(?:а|і|и|й|так|ну|от|скажіть|скажите|підкажіть|подскажите)[\s,]+(?:чи|или|або)\s"
)
#: Taking a tyre is not a question about which is better.
_TAKES = re.compile(
    _B + r"(?:беру|берем\w*|беремо|візьм\w*|возьм\w*|оформ\w*|замов\w*|заказ\w*|куплю"
    r"|давайте|броню\w*|резерву\w*)"
)
#: A price question is the model's (the price corridor, «а є дешевше?»).
_PRICE = re.compile(
    _B + r"(?:скільк\w*|скольк\w*|цін[аиуіо]\w*|цен[аыуео]\w*|кошту\w*|стоит\w*|стоят\w*"
    r"|вартіст\w*|вартує\w*|дешев\w*|дорож\w*|дорог\w*|грн|гривн\w*|гривен\w*|бюджет\w*)"
)

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e", "є": "ye",
    "ж": "zh", "з": "z", "и": "y", "і": "i", "ї": "yi", "й": "y", "к": "k", "л": "l",
    "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ь": "", "ю": "yu",
    "я": "ya", "ы": "y", "э": "e", "ъ": "", "'": "",
}  # fmt: skip

#: Number words, UA + RU, as STT writes a model's digits.
_NUMBER_WORDS = {
    "нуль": "0", "ноль": "0", "один": "1", "одна": "1", "одну": "1", "два": "2",
    "дві": "2", "две": "2", "три": "3", "чотири": "4", "четыре": "4", "п'ять": "5",
    "пять": "5", "шість": "6", "шесть": "6", "сім": "7", "семь": "7", "вісім": "8",
    "восемь": "8", "дев'ять": "9", "девять": "9",
}  # fmt: skip

#: Brands whose Cyrillic sounding the transliteration does not reach.
_BRAND_SOUNDS: dict[str, re.Pattern[str]] = {
    brand: re.compile(_B + r"(?:" + stems + r")\w*")
    for brand, stems in {
        "bridgestone": r"бр[иі]дж[еє]?сто[уў]?н|бр[иі]дгесто[уў]?н",
        "firestone": r"фа[йєе]р[еє]?сто[уў]?н",
        "michelin": r"м[иі]шл[еі]н",
        "goodyear": r"гуд[ьй]?[іиїя][еєр]?р|гуд[ьй]?[іиї]р",
        "hankook": r"ханкук|хенкок|ханкок",
        "pirelli": r"п[иі]рел",
        "yokohama": r"[йи]око[гх]ам",
        "continental": r"конт[иі]нентал",
        "nokian": r"нок[иі]ан",
        "vredestein": r"вред[еі]шта[йи]н|вред[еі]ста[йи]н",
        "laufenn": r"лауфен",
        "kleber": r"клебер",
        "falken": r"фал[ьк]?кен",
        "gislaved": r"г[иі]славед",
        "semperit": r"семпер[иі]т",
        "debica": r"деб[иі]ц",
        "premiorri": r"прем[иі]ор",
        "bfgoodrich": r"б\s?ф\s?гудр[иі]ч",
    }.items()
}


def _normalize(text: str) -> str:
    return text.lower().replace("ё", "е").replace("’", "'").replace("ʼ", "'").replace("`", "'")


def _skeleton(word: str) -> str:
    """A spelling-free key: «туранза» and «Turanza», «гекторра» and «Hectorra»."""
    latin = "".join(_TRANSLIT.get(ch, ch) for ch in word)
    for a, b in (("kh", "h"), ("ck", "k"), ("ph", "f"), ("g", "h"), ("c", "k"), ("q", "k")):
        latin = latin.replace(a, b)
    for a, b in (("x", "ks"), ("w", "v"), ("y", "i"), ("j", "i")):
        latin = latin.replace(a, b)
    return re.sub(r"([a-z])\1+", r"\1", latin)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-zа-яіїєґ0-9']+", _normalize(text))


def _text_keys(text: str) -> tuple[list[str], set[str]]:
    """The utterance's words (skeletons) and its version tokens («6», «t005»)."""
    words: list[str] = []
    versions: set[str] = set()
    raw = _tokens(text)
    i = 0
    while i < len(raw):
        tok = raw[i]
        if tok in _NUMBER_WORDS:
            digits = ""
            while i < len(raw) and raw[i] in _NUMBER_WORDS:
                digits += _NUMBER_WORDS[raw[i]]
                i += 1
            versions.add(digits)
            words.append(digits)
            continue
        key = _skeleton(tok.strip("'"))
        if key:
            words.append(key)
            if any(ch.isdigit() for ch in key):
                versions.add(key)
        i += 1
    # «т 005», «т нуль нуль п'ять» reach «T005» by its digits (`_version_said`).
    return words, versions


def _word_matches(word: str, token: str) -> bool:
    """A word of the utterance is ``token`` — or ``token`` in a case form."""
    if word == token:
        return True
    return len(token) >= 5 and word.startswith(token[:-1]) and -1 <= len(word) - len(token) <= 2


def _brand_named(brand: str, text_norm: str, words: list[str]) -> bool:
    key = _skeleton(re.sub(r"[^a-zа-яіїєґ0-9]", "", _normalize(brand)))
    if not key:
        return False
    sound = _BRAND_SOUNDS.get(re.sub(r"[^a-z]", "", brand.lower()))
    if sound is not None and sound.search(text_norm):
        return True
    joined = [a + b for a, b in pairwise(words)]
    return any(_word_matches(w, key) for w in (*words, *joined))


#: Model words that are not a family name: «спорт» or «зима» in an utterance
#: must not pick «Sport Maxx» or «Winter Contact».
_GENERIC_MODEL_WORDS = frozenset(
    _skeleton(w)
    for w in (
        "summer", "winter", "sport", "season", "all", "premium", "comfort", "plus",
        "pro", "snow", "ice", "extra", "eco", "suv", "van", "max", "touring",
    )
)  # fmt: skip


def _model_parts(model: str) -> tuple[list[str], list[str]]:
    """A model's family words («turanza») and its version tokens («6», «t005»)."""
    family: list[str] = []
    versions: list[str] = []
    for tok in re.split(r"[\s\-/]+", _normalize(model)):
        key = _skeleton(tok)
        if not key:
            continue
        if any(ch.isdigit() for ch in key):
            versions.append(key)
        elif len(key) >= 3 and key not in _GENERIC_MODEL_WORDS:
            family.append(key)
    return family, versions


def _version_said(version: str, said: set[str]) -> bool:
    if version in said:
        return True
    digits = re.sub(r"\D", "", version)
    return version != digits and len(digits) >= 2 and digits in said


def _version_follows(family: tuple[str, ...], words: list[str]) -> bool:
    """A version is said right after a family word: «туранза 5», «туранза т 005»."""
    for pos, word in enumerate(words[:-1]):
        if not any(_word_matches(word, f) for f in family):
            continue
        nxt = words[pos + 1]
        if any(ch.isdigit() for ch in nxt):
            return True
        if len(nxt) == 1 and pos + 2 < len(words) and words[pos + 2].isdigit():
            return True
    return False


def _name(item: dict[str, Any]) -> str:
    return " ".join(str(item.get(k) or "").strip() for k in ("brand", "model") if item.get(k))


def named_items(text: str | None, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The tyres of ``items`` the utterance names, in the result's order, once each.

    A named model picks its tyres; a named brand picks all its tyres unless a
    model of that brand is named too. A family named with a version picks the
    version («Туранза 6»); a family alone picks every model of it.
    """
    if not text or not items:
        return []
    text_norm = _normalize(text)
    words, said_versions = _text_keys(text)

    family_hit: list[int] = []
    version_hit: set[int] = set()
    for idx, item in enumerate(items):
        family, versions = _model_parts(str(item.get("model") or ""))
        fam = any(_word_matches(w, f) for f in family for w in words)
        ver = any(_version_said(v, said_versions) for v in versions)
        lettered = any(_version_said(v, said_versions) for v in versions if re.search(r"[a-z]", v))
        if fam:
            family_hit.append(idx)
            if ver:
                version_hit.add(idx)
        elif lettered:
            version_hit.add(idx)
    # A family named with a version of it: only the versions named. A family
    # named with a version the result does not have («туранза п'ять») is a
    # tyre not in the result — none of its family is picked.
    model_hit: set[int] = set(version_hit)
    by_family: dict[tuple[str, ...], list[int]] = {}
    for idx in family_hit:
        family, _ = _model_parts(str(items[idx].get("model") or ""))
        by_family.setdefault(tuple(family), []).append(idx)
    for family, group in by_family.items():
        if any(i in version_hit for i in group):
            continue
        if not _version_follows(family, words):
            model_hit.update(group)

    picked: set[int] = set()
    brands = {str(i.get("brand") or "").strip().lower() for i in items if i.get("brand")}
    for brand in brands:
        own = [
            i for i, it in enumerate(items) if str(it.get("brand") or "").strip().lower() == brand
        ]
        if any(i in model_hit for i in own):
            continue
        if _brand_named(brand, text_norm, words):
            picked.update(own)
    picked.update(model_hit)

    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for idx, item in enumerate(items):
        key = (str(item.get("brand") or "").lower(), str(item.get("model") or "").lower())
        if idx in picked and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def is_compare_question(text: str | None) -> bool:
    """The utterance asks which of the tyres is better — and nothing else."""
    if not text:
        return False
    low = _normalize(text)
    if _TAKES.search(low) or _PRICE.search(low):
        return False
    if _COMPARE.search(low):
        return True
    return bool(_OR.search(low)) and not _OR_PARTICLE_BEFORE.search(low)


def _label(item: dict[str, Any]) -> dict[str, Any]:
    label = item.get("eu_label")
    return label if isinstance(label, dict) else {}


def _item_clause(item: dict[str, Any]) -> str:
    label = _label(item)
    parts: list[str] = []
    if label.get("wet"):
        parts.append(f"мокра дорога {label['wet']}")
    if label.get("fuel"):
        parts.append(f"економія палива {label['fuel']}")
    if label.get("noise_db") is not None:
        parts.append(f"шум {label['noise_db']} дБ")
    if not parts:
        return f"по {_name(item)} {NO_LABEL}"
    return f"{_name(item)}: " + ", ".join(parts)


def _best(items: list[dict[str, Any]], key: str) -> tuple[Any, list[str], bool] | None:
    """The best value of ``key`` (lowest letter / fewest dB), who has it, all equal?"""
    have = [(it, _label(it)[key]) for it in items if _label(it).get(key) not in (None, "")]
    if len(have) < 2:
        return None
    best = min(v for _, v in have)
    winners = [_name(it) for it, v in have if v == best]
    return best, winners, len(winners) == len(have)


def _verdict(items: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    wet = _best(items, "wet")
    if wet is not None:
        best, winners, same = wet
        parts.append(
            f"на мокрій дорозі однаково — {best}"
            if same
            else f"на мокрій дорозі краще {' і '.join(winners)} ({best})"
        )
    fuel = _best(items, "fuel")
    if fuel is not None:
        best, winners, same = fuel
        parts.append(
            f"економія палива однакова — {best}"
            if same
            else f"економніша {' і '.join(winners)} ({best})"
        )
    noise = _best(items, "noise_db")
    if noise is not None:
        best, winners, same = noise
        parts.append(
            f"шум однаковий — {best} дБ" if same else f"тихіша {' і '.join(winners)} ({best} дБ)"
        )
    if not parts:
        return ""
    return "За етикеткою: " + "; ".join(parts) + "."


def compare_phrase(items: list[dict[str, Any]]) -> str:
    """The code's comparison of ``items`` — their label classes and nothing else."""
    body = "; ".join(_item_clause(it) for it in items)
    body = body[:1].upper() + body[1:] + "."
    verdict = _verdict(items)
    return f"{body} {verdict}" if verdict else body


def compare_said_note(phrase: str | None) -> str:
    """The system-prompt tail telling the model the comparison was said."""
    if not phrase:
        return ""
    return (
        "\n\n## Вже сказано клієнту в цьому ході (порівняння шин)\n"
        f"Код уже сказав клієнту порівняння за етикеткою ЄС: «{phrase}» "
        "Не повторюй цього, не додавай характеристик, яких немає в даних "
        "етикетки; відповідай далі по суті."
    )


def _brief(item: dict[str, Any]) -> dict[str, Any]:
    keep = ("brand", "model", "size", "price", "eu_label")
    return {k: item[k] for k in keep if k in item}


class TyreCompare:
    """The call's last tyre search and the comparison of tyres named from it."""

    def __init__(self, *, sales_enabled: bool) -> None:
        self._sales_enabled = sales_enabled
        self._items: list[dict[str, Any]] = []

    @property
    def last_items(self) -> list[dict[str, Any]]:
        return list(self._items)

    def note_search(self, raw: Any) -> None:
        """A ``search_tires`` result (forced or the model's) — the call's last one."""
        if not self._sales_enabled:
            return
        # A refusal or a failure carries no ``items`` and leaves the last one.
        items = raw.get("items") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            return
        self._items = [_brief(i) for i in items if isinstance(i, dict) and i.get("brand")]

    def plan(self, user_text: str | None) -> str | None:
        """The comparison the code says first on this turn, or ``None``."""
        # Sales off → ``note_search`` never keeps a result (the one sales check).
        if not self._items:
            return None
        if not is_compare_question(user_text):
            return None
        named = named_items(user_text, self._items)
        if not 2 <= len(named) <= MAX_COMPARED:
            return None
        phrase = compare_phrase(named)
        logger.warning(
            "tyre_compare items=%s last_customer_text=%r",
            ",".join(_name(i) for i in named),
            (user_text or "")[:120],
        )
        return phrase
