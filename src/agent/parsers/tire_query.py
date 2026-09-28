"""`tire_query` — deterministic predicates for a spoken tire request.

Port of the dictionaries in tshina `TireConsultant/Services/Dialog/
DeterministicIntentParser.php` (`SIZE_REGEX`, `NUMBER_WORDS`,
`NAIL_TYPE_KEYWORDS`, `QUANTITY_KEYWORDS`, `BUDGET_SCOPE_KEYWORDS`,
`BRAND_CYRILLIC_ALIASES`, `TECH_REQUIREMENT_KEYWORDS`,
`extractIsolatedDiameter`, `parseStaggeredSizes`) and of the size grammar in
`Modules/Search/Support/TireSizeNotation.php` — adapted to what reaches us:
Google STT output in Ukrainian or Russian, numbers very often as words.

What is reused rather than re-declared:

* numeral vocabulary (all cases, UA + RU) — `src.stt.numeral_parser._lookup`.
  `words_to_digits` itself is not used: it glues every numeral run into one
  digit string (right for a plate, wrong for «двісті п'ять п'ятдесят п'ять
  шістнадцять», where the group boundaries are the size);
* isolated diameter — `DiameterParser` (which is `detect_diameter` plus the
  date/time masking of the broad pass);
* phonetic brand table — `src.stt.entity_normalizer.normalize_brand_names`
  runs first; the table here only adds the tshina forms and the inflected
  forms it does not know.

Nothing here is wired into a call: the module is pure, synchronous, with no
I/O. Every list-shaped result comes back as ``None`` when empty — an empty
list passes the `not in (None, '', False)` filter the progress block uses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from src.agent.parsers.base import ParseContext
from src.agent.parsers.diameter_parser import DiameterParser
from src.stt.entity_normalizer import normalize_brand_names
from src.stt.numeral_parser import _lookup as _numeral_value

__all__ = [
    "Budget",
    "NailType",
    "QuantityParse",
    "TechRequirement",
    "TireSize",
    "extract_tire_brands",
    "normalize_spoken_numbers",
    "normalize_tire_brand",
    "parse_budget",
    "parse_nail_type",
    "parse_quantity",
    "parse_tech_refusals",
    "parse_tech_requirements",
    "parse_tire_size",
]

NailType = Literal["studdable", "studless", "studded"]
TechRequirement = Literal["runflat", "xl", "commercial"]
BudgetScope = Literal["per_tire", "per_set"]

# ═══════════════════════════════════════════════════════════
#  Text normalisation
# ═══════════════════════════════════════════════════════════

_APOSTROPHES = str.maketrans({"ʼ": "'", "’": "'", "`": "'", "‘": "'", "´": "'"})

#: RU forms `numeral_parser` does not carry (its RU table stops at the tens).
#: Mapped onto a UA form it does carry, so the value comes from one table.
_RU_EXTRA_NUMERALS: dict[str, int] = {
    "две": 2,
    "двести": 200,
    "четыреста": 400,
    "пятьсот": 500,
    "шестьсот": 600,
    "семьсот": 700,
    "восемьсот": 800,
    "девятьсот": 900,
    "тринадцатый": 13,
    "четырнадцатый": 14,
    "пятнадцатый": 15,
    "шестнадцатый": 16,
    "семнадцатый": 17,
    "восемнадцатый": 18,
    "девятнадцатый": 19,
    "восемнадцатого": 18,
    "девятнадцатого": 19,
    "семнадцатого": 17,
    "шестнадцатого": 16,
}

#: «друга шина» is «the other tyre», «перший раз» is not a number of anything.
_ORDINAL_NOISE_PREFIXES = ("друг", "перш", "перв")

_TOKEN_RE = re.compile(r"[\w']+|[^\w'\s]+|\s+", re.UNICODE)


def _word_value(token: str) -> int | None:
    if token.isdigit():
        return None  # digit tokens are kept verbatim, never folded
    if token.startswith(_ORDINAL_NOISE_PREFIXES):
        return None
    if token in _RU_EXTRA_NUMERALS:
        return _RU_EXTRA_NUMERALS[token]
    return _numeral_value(token)


def _fold_run(values: list[int]) -> list[str]:
    """Fold one run of numeral words into space-separated groups.

    Same additive rule as `numeral_parser._combine_run` («двісті» + «п'ять» =
    205, «п'ятдесят» + «п'ять» = 55), but the group boundaries are kept:
    «двісті п'ять п'ятдесят п'ять шістнадцять» → ``205 55 16``.

    Consecutive single digits are dictation («два нуль п'ять») and are joined:
    «два нуль п'ять п'ять п'ять» → ``20555``.
    """
    groups: list[int] = []
    i = 0
    while i < len(values):
        acc = values[i]
        while i + 1 < len(values):
            nxt = values[i + 1]
            text = str(acc)
            zeros = len(text) - len(text.rstrip("0"))
            if zeros == 0 or nxt == 0 or nxt >= acc or len(str(nxt)) > zeros:
                break
            acc += nxt
            i += 1
        groups.append(acc)
        i += 1

    out: list[str] = []
    digits = ""
    for g in groups:
        if g < 10:
            digits += str(g)
            continue
        if digits:
            out.append(digits)
            digits = ""
        out.append(str(g))
    if digits:
        out.append(digits)
    return out


_THOUSANDS_RE = re.compile(
    r"(?<![\d.,])(\d{1,3})\s+(?:тисяч\w*|тысяч\w*|тис\b\.?|тыс\b\.?)(?:\s+(\d{1,3})(?![\d:]))?"
)
_BARE_THOUSAND_RE = re.compile(r"(?<!\d\s)\b(?:тисяч[уаі]|тысяч[уа])\b")
#: «20 18 року» — a year said in two halves.
_SPLIT_YEAR_RE = re.compile(r"\b(19|20)\s+(\d{2})\s+(?=(?:рік|року|роки|год\b|года|г\.|р\.))")


def normalize_spoken_numbers(text: str) -> str:
    """Lower-case, fold apostrophes, and turn numeral words into digit groups.

    «двісті п'ять п'ятдесят п'ять р шістнадцять» → ``205 55 р 16``;
    «дві тисячі вісімнадцятого року» → ``2018 року``;
    «три тисячі п'ятсот гривень» → ``3500 гривень``.
    """
    if not text:
        return ""
    low = text.lower().translate(_APOSTROPHES)
    out: list[str] = []
    run: list[int] = []
    pending_ws = ""

    def flush() -> None:
        if run:
            out.append(" ".join(_fold_run(run)))
            run.clear()

    for tok in _TOKEN_RE.findall(low):
        if tok.isspace():
            if run:
                pending_ws = tok
            else:
                out.append(tok)
            continue
        val = _word_value(tok)
        if val is not None:
            pending_ws = ""
            run.append(val)
            continue
        flush()
        if pending_ws:
            out.append(pending_ws)
            pending_ws = ""
        out.append(tok)
    flush()
    result = "".join(out)

    result = _THOUSANDS_RE.sub(lambda m: str(int(m.group(1)) * 1000 + int(m.group(2) or 0)), result)
    result = _BARE_THOUSAND_RE.sub("1000", result)
    return _SPLIT_YEAR_RE.sub(lambda m: m.group(1) + m.group(2) + " ", result)


# ═══════════════════════════════════════════════════════════
#  Size
# ═══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class TireSize:
    """One tyre size. `width`/`aspect` are ``None`` for a bare «R18»."""

    width: int | None
    aspect: int | None
    diameter: int
    #: ``"C"`` (commercial) / ``"LT"`` as said after the diameter.
    suffix: Literal["C", "LT"] | None = None
    #: Set only for a staggered pair: front is the narrower one.
    axle: Literal["front", "rear"] | None = None

    @property
    def is_full(self) -> bool:
        return self.width is not None and self.aspect is not None


_R_MARK = r"(?:радіус\w*|радиус\w*|эр|ер|zr|зр|r|р)"
_SEP = r"[/x×хХ\-.,\\]"
_SUFFIX = r"(?:(c|с|lt)(?![^\W\d_]))?"

#: `22555 r17` — glued width+aspect, only with an explicit R.
_GLUED_RE = re.compile(rf"(?<![\d.,])(\d{{3}})(\d{{2}})\s*{_R_MARK}\s*(\d{{2}}){_SUFFIX}(?!\d)")
#: `2055516` — seven digits, the STT/keyboard form with nothing between.
_COMPACT_RE = re.compile(rf"(?<![\d.,])(\d{{3}})(\d{{2}})(\d{{2}}){_SUFFIX}(?!\d)")
#: `205/55R16`, `205 55 16`, `225 на 45 р 17`, `225х45х17`, `205/55/16`.
_LOOSE_RE = re.compile(
    rf"(?<![\d.,])(\d{{3}})(?:\s*{_SEP}\s*|\s+на\s+|\s+)(\d{{2}})"
    rf"(?:\s*{_SEP}?\s*{_R_MARK}\s*|\s*{_SEP}\s*|\s+на\s+|\s+)"
    rf"(\d{{2}}){_SUFFIX}(?!\d)"
)
#: `315/35` right after a full size — the rear of a staggered pair.
_PARTIAL_RE = re.compile(r"(?<![\d.,])(\d{3})\s*(?:/|\s+на\s+)\s*(\d{2})(?![\d])")

_ALTERNATIVE_RE = re.compile(
    r"(?<![^\W\d_])(?:или|либо|або|чи|вместо|замість|заместо|or|vs)(?![^\W\d_])"
)
_AXLE_RE = re.compile(
    r"разношир|різношир|staggered|перед|задн|(?<![^\W\d_])зад(?![^\W\d_])"
    r"|(?<![^\W\d_])(?:ось|оси|осі|вісь)(?![^\W\d_])|front|rear"
)


def _plausible(width: int, aspect: int, diameter: int) -> bool:
    """Catalogue ranges (tshina `TireSizeNotation::isPlausible`, 2026-09-25)."""
    return 125 <= width <= 455 and 25 <= aspect <= 95 and 10 <= diameter <= 24


def _suffix(raw: str | None) -> Literal["C", "LT"] | None:
    if not raw:
        return None
    return "LT" if raw == "lt" else "C"


def _full_sizes(norm: str) -> list[tuple[tuple[int, int], TireSize]]:
    found: list[tuple[tuple[int, int], TireSize]] = []
    taken: list[tuple[int, int]] = []
    for regex in (_GLUED_RE, _COMPACT_RE, _LOOSE_RE):
        for m in regex.finditer(norm):
            span = m.span()
            if any(span[0] < e and s < span[1] for s, e in taken):
                continue
            w, a, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if not _plausible(w, a, d):
                continue
            taken.append(span)
            found.append((span, TireSize(w, a, d, _suffix(m.group(4)))))
    found.sort(key=lambda item: item[0][0])
    return found


def _size_spans(norm: str) -> list[tuple[int, int]]:
    return [span for span, _ in _full_sizes(norm)]


def _blank(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for s, e in spans:
        chars[s:e] = " " * (e - s)
    return "".join(chars)


#: What looks like a diameter but is not one.
_YEAR_RE = re.compile(r"(?<!\d)(?:19[5-9]\d|20[0-4]\d)(?!\d)")
_BUDGET_CAP_NUMBER_RE = re.compile(r"(?:до|под|під|not\s+more|until|<=?)\s*\d+")
_QUANTITY_NUMBER_RE = re.compile(
    r"(?<![\d/rр])(?<![rр]\s)\d{1,3}\s*(?:штук\w*|шт\b\.?|коробок|шин\w*|колес\w*|коліс\w*|покришк\w*|покрышк\w*)"
)


def _isolated_diameter(norm: str) -> int | None:
    """«Tiguan 18» → 18; «Tiguan 2018» → nothing. A year is only four digits."""
    masked = _YEAR_RE.sub(" ", norm)
    masked = _BUDGET_CAP_NUMBER_RE.sub(" ", masked)
    masked = _QUANTITY_NUMBER_RE.sub(" ", masked)
    outcome = DiameterParser().parse(ParseContext(customer_text=masked))
    return outcome.value if isinstance(outcome.value, int) else None


def _staggered(norm: str, sizes: list[TireSize]) -> list[TireSize] | None:
    """tshina `parseStaggeredSizes`: two widths → front (narrower) + rear."""
    if len(sizes) != 2 or sizes[0].width == sizes[1].width:
        return None
    if _ALTERNATIVE_RE.search(norm):
        return None
    if not _AXLE_RE.search(norm) and sizes[0].diameter != sizes[1].diameter:
        return None
    front, rear = sorted(sizes, key=lambda s: s.width or 0)
    return [
        TireSize(front.width, front.aspect, front.diameter, front.suffix, "front"),
        TireSize(rear.width, rear.aspect, rear.diameter, rear.suffix, "rear"),
    ]


def parse_tire_size(text: str) -> list[TireSize] | None:
    """Every tyre size in an utterance, or ``None``.

    * full sizes in any spoken/typed notation, in order of appearance;
    * two sizes of different width (axle words, or one diameter, and no
      «або/или/замість») → a staggered pair, ``axle`` set, front = narrower;
      the rear may omit its diameter («275/40 R20 і 315/35»);
    * no full size → the isolated diameter («Tiguan 18», «р шістнадцять»)
      as ``TireSize(None, None, d)``. A year is four digits and never a
      diameter: «Tiguan 2018», «дві тисячі вісімнадцятого року».
    """
    norm = normalize_spoken_numbers(text)
    if not norm.strip():
        return None

    hits = _full_sizes(norm)
    sizes: list[TireSize] = []
    for _, size in hits:
        if size not in sizes:
            sizes.append(size)

    if sizes:
        # A partial `WWW/AA` only counts as the second half of a staggered pair.
        rest = _blank(norm, [span for span, _ in hits])
        for m in _PARTIAL_RE.finditer(rest):
            w, a = int(m.group(1)), int(m.group(2))
            candidate = TireSize(w, a, sizes[0].diameter)
            if _plausible(w, a, sizes[0].diameter) and candidate not in sizes:
                staggered = _staggered(norm, [*sizes, candidate])
                if staggered is not None:
                    return staggered
        return _staggered(norm, sizes) or sizes

    diameter = _isolated_diameter(norm)
    if diameter is None:
        return None
    return [TireSize(None, None, diameter)]


# ═══════════════════════════════════════════════════════════
#  Nail type
# ═══════════════════════════════════════════════════════════

#: Order matters (tshina): «під шип» and «нешиповані» both contain «шип».
#: Every entry has UA and RU forms; stems cover the oblique cases
#: («шипованих», «шипованные», «липучку»).
_NAIL_PATTERNS: tuple[tuple[NailType, re.Pattern[str]], ...] = (
    (
        "studdable",
        re.compile(
            r"під\s+шип|підшип(?!ник)"  # ua
            r"|под\s+шип|подшип(?!ник)"  # ru
        ),
    ),
    (
        "studless",
        re.compile(
            r"липучк\w*"  # ua + ru
            r"|не\s*шипован\w*"  # ua + ru («нешиповані», «не шипованные»)
            r"|без\s+шипів"  # ua
            r"|без\s+шипов"  # ru
            r"|фрикційн\w*|фрикцийн\w*"  # ua (+ STT spelling)
            r"|фрикцион\w*"  # ru
        ),
    ),
    (
        "studded",
        re.compile(
            r"шипован\w*"  # ua «шиповані», ru «шипованные»
            r"|шиповк\w*"  # ua + ru
            r"|(?:з|із|зі)\s+шипами"  # ua
            r"|с\s+шипами|со\s+шипами"  # ru
        ),
    ),
)


def parse_nail_type(text: str) -> NailType | None:
    """«під шип» / «липучка» / «шиповані» — or ``None``.

    Two different kinds in one utterance («шиповані чи липучка?») is a
    question, not an answer → ``None``.
    """
    norm = normalize_spoken_numbers(text)
    found: list[NailType] = []
    for kind, pattern in _NAIL_PATTERNS:
        spans = [m.span() for m in pattern.finditer(norm)]
        if spans:
            found.append(kind)
            norm = _blank(norm, spans)
    return found[0] if len(found) == 1 else None


# ═══════════════════════════════════════════════════════════
#  Quantity
# ═══════════════════════════════════════════════════════════

#: Tyres are bought 1..8 at a time (tshina `QUANTITY_REGEX`).
_MIN_QUANTITY = 1
_MAX_QUANTITY = 8


@dataclass(frozen=True)
class QuantityParse:
    """`count` is ``None`` when a number was said but is out of 1..8."""

    count: int | None
    out_of_range: bool = False


_TIRE_NOUN = r"(?:штук\w*|шт\b\.?|шин\w*|колес\w*|коліс\w*|покришк\w*|покрышк\w*|скат\w*)"
_QUANTITY_RE = re.compile(rf"(?<![\d/.,rр])(?<![rр]\s)(\d{{1,3}})\s*{_TIRE_NOUN}")
_SET_RE = re.compile(
    r"комплект(?!ац)\w*|набір|набор"
    r"|(?:всі|усі|все)\s+4|на\s+(?:обидві|обидва|обе|оба|дві|две|2)\s+(?:осі|оси)"
)
_PAIR_RE = re.compile(
    rf"пар[аиуы]\s+{_TIRE_NOUN}|(?:1|одну)\s+пар[уа]"
    r"|на\s+(?:1|одну)\s+(?:вісь|ось)"
)
_ONLY_ONE_RE = re.compile(r"(?:тільки|лише|только|лишь)\s+1(?![\d:])")


def parse_quantity(text: str) -> QuantityParse | None:
    """How many tyres: explicit «4 шини», «комплект» = 4, «пару шин» = 2.

    A number said with a tyre noun but outside 1..8 («30 шин») → ``count=None``
    with ``out_of_range=True`` — never clamped into range.
    """
    norm = normalize_spoken_numbers(text)
    norm = _blank(norm, _size_spans(norm))
    m = _QUANTITY_RE.search(norm)
    if m:
        value = int(m.group(1))
        if _MIN_QUANTITY <= value <= _MAX_QUANTITY:
            return QuantityParse(value)
        return QuantityParse(None, out_of_range=True)
    if _SET_RE.search(norm):
        return QuantityParse(4)
    if _PAIR_RE.search(norm):
        return QuantityParse(2)
    if _ONLY_ONE_RE.search(norm):
        return QuantityParse(1)
    return None


# ═══════════════════════════════════════════════════════════
#  Budget
# ═══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Budget:
    amount: int
    scope: BudgetScope | None = None
    #: «до 6000» / «не дорожче 3000» — an upper bound, not a target.
    is_cap: bool = False


_MONEY_SUFFIX = r"(?:грн\b\.?|гривен\w*|гривн\w*|гривень|₴|uah\b|hrn\b)"
_CAP_PREFIX = (
    r"(?:до|не\s+більше|не\s+больше|не\s+дорожче|не\s+дороже|максимум|макс\.?"
    r"|в\s+межах|у\s+межах|в\s+пределах)"
)
_TARGET_PREFIX = r"(?:бюджет\w*|за|по|приблизно|близько|около|десь|где-то|в\s+районі|в\s+районе)"
_AMOUNT = r"(\d{3,6})(?![\d:/])"
_CAP_AMOUNT_RE = re.compile(rf"{_CAP_PREFIX}\s+{_AMOUNT}")
_SUFFIX_AMOUNT_RE = re.compile(rf"(?<![\d/.,]){_AMOUNT}\s*{_MONEY_SUFFIX}")
_TARGET_AMOUNT_RE = re.compile(rf"{_TARGET_PREFIX}\s+{_AMOUNT}(?!\s*(?:рік|року|год\b|года|р\.))")
#: «3000 за шину» — the scope phrase itself marks the number as money.
_SCOPED_AMOUNT_RE = re.compile(
    rf"(?<![\d/.,]){_AMOUNT}\s+(?:за|на)\s+(?:1\s+|одну\s+|одне\s+|всі\s+|все\s+)?"
    r"(?:шину|колесо|штуку|шт\b|комплект|4(?![\d:]))"
)

_PER_TIRE_RE = re.compile(
    r"за\s+(?:1\s+|одну\s+)?шину|за\s+(?:1\s+|одне\s+|одно\s+)?колесо|за\s+штуку|за\s+шт\b"
    r"|за\s+1(?![\d:])|за\s+одиницю|за\s+единицу|per\s+(?:tire|wheel)"
)
_PER_SET_RE = re.compile(
    r"за\s+комплект|на\s+комплект|за\s+(?:всі|усі|все)(?:\s+4)?|за\s+4(?![\d:])|на\s+(?:всі|все)\s+4"
    r"|загальн\w*\s+бюджет|общий\s+бюджет|per\s+set|total"
)


def parse_budget(text: str) -> Budget | None:
    """«до шести тисяч» → Budget(6000, is_cap=True); «3000 за шину» → per tire.

    A bare number is not a budget — only one with «грн/гривень», with a cap
    («до», «не дорожче») or a target word («бюджет», «за», «десь»). Size
    digits are blanked first, so «205» is never read as money.
    """
    norm = normalize_spoken_numbers(text)
    norm = _blank(norm, _size_spans(norm))

    cap = _CAP_AMOUNT_RE.search(norm)
    if cap:
        amount, is_cap = int(cap.group(1)), True
    else:
        hit = (
            _SUFFIX_AMOUNT_RE.search(norm)
            or _SCOPED_AMOUNT_RE.search(norm)
            or _TARGET_AMOUNT_RE.search(norm)
        )
        if hit is None:
            return None
        amount, is_cap = int(hit.group(1)), False

    scope: BudgetScope | None = None
    if _PER_TIRE_RE.search(norm):
        scope = "per_tire"
    elif _PER_SET_RE.search(norm):
        scope = "per_set"
    return Budget(amount, scope, is_cap)


# ═══════════════════════════════════════════════════════════
#  Brand
# ═══════════════════════════════════════════════════════════

#: Case endings a Cyrillic brand stem takes in UA/RU speech («мішлена»,
#: «бриджстоуном», «континенталі»). Empty ending is the nominative.
_ENDING = r"(?:а|у|ом|ем|ові|еві|і|и|ы|е|ю|я|ах|ів|ов)?"

#: tshina `BRAND_CYRILLIC_ALIASES` + UA spellings from the owner's list.
#: stem → slug. `entity_normalizer` already rewrites some nominatives to
#: Latin; these stems add the forms and cases it does not know.
_CYRILLIC_BRANDS: dict[str, str] = {
    "мішлен": "michelin",
    "мишлен": "michelin",
    "бріджстоун": "bridgestone",
    "бриджстоун": "bridgestone",
    "континенталь": "continental",
    "континентал": "continental",
    "конті": "continental",
    "конти": "continental",
    "піреллі": "pirelli",
    "пирелли": "pirelli",
    "гудієр": "goodyear",
    "гудиер": "goodyear",
    "гудьир": "goodyear",
    "гудєр": "goodyear",
    "данлоп": "dunlop",
    "ханкук": "hankook",
    "хенкук": "hankook",
    "кумхо": "kumho",
    "нокіан": "nokian",
    "нокиан": "nokian",
    "тойо": "toyo",
    "йокогама": "yokohama",
    "йокохама": "yokohama",
    "фалкен": "falken",
    "нексен": "nexen",
    "барум": "barum",
    "дебіка": "debica",
    "дебика": "debica",
    "матадор": "matador",
    "росава": "rosava",
    "преміоррі": "premiorri",
    "премиорри": "premiorri",
    "вредестайн": "vredestein",
    "вредештайн": "vredestein",
    "фаєрстоун": "firestone",
    "фаерстоун": "firestone",
    "файрстоун": "firestone",
    "кама": "kama",
    "віатті": "viatti",
    "виатти": "viatti",
    "тигар": "tigar",
    "семперит": "semperit",
    "семперіт": "semperit",
    "лауфен": "laufenn",
    "лауфенн": "laufenn",
    "максіс": "maxxis",
    "максис": "maxxis",
    "нітто": "nitto",
    "нитто": "nitto",
    "сейлун": "sailun",
    "саилун": "sailun",
    "тріангл": "triangle",
    "триангл": "triangle",
    "кордіант": "cordiant",
    "кордиант": "cordiant",
    "белшина": "kama",
}

#: tshina `COMPARABLE_BRAND_SLUGS` minus `SIZE_BRANCH_AMBIGUOUS_BRANDS`
#: (car models / plain words: sunny, mirage, taurus, federal, …).
_LATIN_SLUGS: frozenset[str] = frozenset(
    [
        "accelera",
        "achilles",
        "aeolus",
        "amtel",
        "antares",
        "apollo",
        "aplus",
        "arivo",
        "atturo",
        "austone",
        "avon",
        "barum",
        "bfgoodrich",
        "blacklion",
        "bridgestone",
        "ceat",
        "continental",
        "cooper",
        "cordiant",
        "cst",
        "davanti",
        "dayton",
        "debica",
        "delinte",
        "doublestar",
        "dunlop",
        "evergreen",
        "falken",
        "firemax",
        "firestone",
        "fronway",
        "fulda",
        "gislaved",
        "giti",
        "goform",
        "goodride",
        "goodyear",
        "grenlander",
        "gripmax",
        "gtradial",
        "habilead",
        "haida",
        "hankook",
        "hifly",
        "ilink",
        "joyroad",
        "kama",
        "kapsen",
        "kinforest",
        "kleber",
        "kormoran",
        "kumho",
        "landsail",
        "lanvigator",
        "lassa",
        "laufenn",
        "leao",
        "linglong",
        "mabor",
        "matador",
        "maxxis",
        "mazzini",
        "michelin",
        "minerva",
        "mitas",
        "nankang",
        "nexen",
        "nitto",
        "nokian",
        "nordexx",
        "orium",
        "ovation",
        "petlas",
        "pirelli",
        "premiorri",
        "riken",
        "roadcruza",
        "roadstone",
        "rosava",
        "rotalla",
        "rovelo",
        "royalblack",
        "sailun",
        "sava",
        "semperit",
        "silverstone",
        "starmaxx",
        "sumitomo",
        "sunfull",
        "syron",
        "tigar",
        "toyo",
        "tracmax",
        "trazano",
        "triangle",
        "tristar",
        "tunga",
        "uniroyal",
        "viatti",
        "vredestein",
        "wanli",
        "westlake",
        "windforce",
        "winrun",
        "yokohama",
        "zeetex",
        "zmax",
    ]
)

_CYRILLIC_BRAND_RE = re.compile(
    r"\b("
    + "|".join(sorted(map(re.escape, _CYRILLIC_BRANDS), key=len, reverse=True))
    + rf"){_ENDING}\b"
)
_LATIN_BRAND_RE = re.compile(
    r"\b(" + "|".join(sorted(_LATIN_SLUGS, key=len, reverse=True)) + r")\b"
)


def extract_tire_brands(text: str) -> list[str] | None:
    """Every tyre brand slug named, in order of first mention, or ``None``."""
    if not text:
        return None
    latinised, _ = normalize_brand_names(text.translate(_APOSTROPHES))
    low = latinised.lower()
    hits: list[tuple[int, str]] = [
        (m.start(), _CYRILLIC_BRANDS[m.group(1)]) for m in _CYRILLIC_BRAND_RE.finditer(low)
    ]
    hits += [(m.start(), m.group(1)) for m in _LATIN_BRAND_RE.finditer(low)]
    slugs: list[str] = []
    for _, slug in sorted(hits):
        if slug not in slugs:
            slugs.append(slug)
    return slugs or None


def normalize_tire_brand(text: str) -> str | None:
    """The first tyre brand named, as a `brands.slug` («мішлена» → ``michelin``)."""
    brands = extract_tire_brands(text)
    return brands[0] if brands else None


# ═══════════════════════════════════════════════════════════
#  Tech requirements
# ═══════════════════════════════════════════════════════════

#: «посилені / усиленные» is XL (tshina `a1d7882c2`): the catalogue has one
#: load facet, `is_xl`; a separate «reinforced» key had no reader but the label.
_TECH_PATTERNS: tuple[tuple[TechRequirement, re.Pattern[str]], ...] = (
    ("runflat", re.compile(r"run[\s-]?flat|\brft\b|ран\s?фл[еэєя]т\w*")),
    (
        "xl",
        re.compile(
            r"\bxl\b|extra\s*load|[еэ]кстра\s*ло[ау]д|ікс\s*ель|икс\s*эль"
            r"|посилен\w*|усилен\w*|reinforced"
        ),
    ),
    (
        "commercial",
        re.compile(
            r"комерц\w*|коммерч\w*|\bбус\w*|фургон\w*|мікроавтобус\w*|микроавтобус\w*"
            r"|вантажопас\w*|грузопас\w*|цешк\w*|газел\w*"
        ),
    ),
)

#: «без ранфлета», «не потрібен ранфлет», «не треба мені XL», «ніяких
#: ранфлетів», «no runflat» — a refusal, not a requirement (tshina
#: `14f13515f`). The negation is the word right before the key, optionally
#: followed by a modal («потрібен / нужны / хочу») and one filler word from a
#: closed list — never an arbitrary word: «без шипів ранфлет» wants runflat.
_NEG_WORD = r"(?:без|не|no|not|without|ніяких|никаких|жодних|жодного)"
_NEG_MODAL = (
    r"(?:потріб\w*|треба|хочу|хочемо|бажано"  # ua
    r"|нужн\w*|нужен|надо|хотим"  # ru
    r"|need\w*|want)"  # en
)
_NEG_FILLER = r"(?:мені|мне|нам|ці|цих|эти|этих|ваш\w*|всяк\w*|any|the)"
_NEG_BEFORE_RE = re.compile(rf"(?<![\w']){_NEG_WORD}(?:\s+{_NEG_MODAL})?(?:\s+{_NEG_FILLER})?\s+$")
#: «ранфлет не потрібен», «XL не нужно» — after the key only with a modal:
#: «ранфлет не дорогий» is not a refusal.
_NEG_AFTER_RE = re.compile(rf"^[\w']*\s+(?:мені\s+|мне\s+|нам\s+)?не\s+{_NEG_MODAL}(?![\w'])")


def _tech_mentions(text: str) -> tuple[list[TechRequirement], list[TechRequirement]]:
    """(wanted, refused) — a key is refused only when every mention is negated."""
    norm = normalize_spoken_numbers(text)
    wanted: list[TechRequirement] = []
    refused: list[TechRequirement] = []
    for kind, pattern in _TECH_PATTERNS:
        negated = affirmed = False
        for m in pattern.finditer(norm):
            if _NEG_BEFORE_RE.search(norm[: m.start()]) or _NEG_AFTER_RE.search(norm[m.end() :]):
                negated = True
            else:
                affirmed = True
        if affirmed:
            wanted.append(kind)
        elif negated:
            refused.append(kind)
    if "commercial" not in wanted and "commercial" not in refused:
        sizes = parse_tire_size(text) or []
        if any(s.suffix is not None for s in sizes):
            wanted.append("commercial")
    return wanted, refused


def parse_tech_requirements(text: str) -> list[TechRequirement] | None:
    """runflat / XL (посилені) / C (комерційні, «на бус») — or ``None``.

    A size said with a C/LT suffix («215/75 R16C») counts as commercial.
    A negated key («без ранфлета», «не потрібен XL») is not a requirement —
    see `parse_tech_refusals`.
    """
    wanted, _ = _tech_mentions(text)
    return wanted or None


def parse_tech_refusals(text: str) -> list[TechRequirement] | None:
    """Keys the caller turned down («без ранфлета», «XL не треба») — or ``None``.

    Separate from `parse_tech_requirements` so a caller that merges turns can
    drop a requirement said earlier. A key both refused and asked for in one
    utterance counts as asked for.
    """
    _, refused = _tech_mentions(text)
    return refused or None
