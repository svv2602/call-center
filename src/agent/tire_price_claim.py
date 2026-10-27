"""The price the bot says for a shown tyre, checked against the search result.

Call 51f0652f (Про Колесо, 2026-10-27): Sportrak Snowtrek SP709 was shown at
3118 грн. The caller said «за 3000 с копейками»; at the order the bot asked
«…за три тисячі гривень за шину, вірно?» and then «…разом шість тисяч
гривень» for two — 6236. Nothing compared a spoken price with the result.

`price_corrections` finds each amount said before a currency word («три
тисячі сто вісімнадцять гривень», «3 118 грн», «6236 гривень») and returns the
ones that are wrong for the one tyre the sentence names: a per-tyre amount
must equal its price; an amount after «разом / всього / загалом / за
комплект …» must equal price × the count said in the sentence (none said →
not judged). Only the wrong amount is replaced, by digits — the rest of the
sentence (the size, «вірно?», the delivery question) is kept.

An amount in a delivery clause is not a tyre price: «SP709 — 3118 грн,
доставка Новою поштою — орієнтовно 810 грн» must keep its 810. A clause is cut
by commas, semicolons and «а / але / но / проте / однак» — never by a dash —
and a delivery / courier / cash-on-delivery-fee word in it before the amount
leaves the amount alone (`in_delivery_clause`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.stt.numeral_parser import _lookup as _numeral_value

_CURRENCY = r"(?:грн\.?|гривень|гривні|гривня|гривнях|гривен|гривны|гривна|гривнями)"
_THOUSAND = r"(?:тисяч[аіу]?|тисячами|тысяч[аиу]?)"
_WORD = r"[\w'ʼ]+"
#: A run of number words / digits / «тисяч» ending right before a currency word.
_AMOUNT_RUN = re.compile(rf"((?:{_WORD}\s+)*?{_WORD})\s+{_CURRENCY}(?!\w)", re.IGNORECASE)
_TOTAL_MARKER = re.compile(
    r"(?:разом|всього|усього|загалом|в\s+сумі|у\s+сумі|итого|всего|вместе|в\s+сумме"
    r"|за\s+комплект|за\s+(?:обидві|обидва|дві|два|три|чотири|\d)\s+(?:шин\w*|колес\w*))"
    r"[\s:,—-]*$",
    re.IGNORECASE,
)
_PER_SET_AFTER = re.compile(
    r"^\s*(?:за\s+комплект|за\s+(?:\d|дві|два|чотири)\s+шин)", re.IGNORECASE
)

#: Clause borders for a delivery sum — the same as network_claim_guard's: a dash
#: joins «доставка Новою поштою — орієнтовно 810 грн» into one clause.
_CLAUSE_SPLIT = re.compile(r"[,;]|\s(?:а|але|но|проте|однак)\s", re.IGNORECASE)
#: Delivery, courier and cash-on-delivery-fee words: a sum after one is not a tyre price.
_DELIVERY_WORD = re.compile(
    r"(?<!\w)(?:достав|відправк|отправк|пересил|кур['ʼ’]?[єе]р|курьер"
    r"|накладен|наложен|комісі|комисси)\w*",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Amount:
    value: int
    start: int
    end: int
    total: bool


def _group(tokens: list[str]) -> int | None:
    """0..999 from number words / one digit token, strictly composed, or ``None``."""
    if not tokens:
        return 0
    if len(tokens) == 1 and tokens[0].isdigit():
        return int(tokens[0])
    if len(tokens) == 2 and tokens[0].isdigit() and tokens[1].isdigit() and len(tokens[1]) == 3:
        return None  # «3 118» is a thousands separator, handled by the caller
    total, last = 0, 1000
    for tok in tokens:
        if tok.isdigit():
            return None
        v = _numeral_value(tok)
        if v is None or v == 0:
            return None
        # hundreds, then tens (or a teen), then units — each smaller than the last
        rank = 100 if v >= 100 else 10 if v >= 10 else 1
        if rank >= last or (v >= 10 and v < 20 and last <= 10):
            return None
        total += v
        last = rank if not (10 <= v < 20) else 1
    return total if total < 1000 else None


def _parse(tokens: list[str]) -> int | None:
    """An amount from tokens: «три тисячі сто вісімнадцять», «3 118», «6236»."""
    low = [t.lower().replace("ʼ", "'") for t in tokens]
    if len(low) == 1 and low[0].isdigit():
        return int(low[0])
    if len(low) == 2 and all(t.isdigit() for t in low) and len(low[1]) == 3:
        return int(low[0]) * 1000 + int(low[1])
    for i, tok in enumerate(low):
        if re.fullmatch(_THOUSAND, tok):
            head = _group(low[:i]) if i else 1
            tail = _group(low[i + 1 :])
            if head is None or tail is None or head == 0:
                return None
            return head * 1000 + tail
    value = _group(low)
    return value or None


def find_amounts(sentence: str) -> list[Amount]:
    """Amounts said before a currency word — the longest valid number suffix of each run."""
    out: list[Amount] = []
    for m in _AMOUNT_RUN.finditer(sentence):
        run_start = m.start(1)
        words = list(re.finditer(_WORD, m.group(1)))
        for i in range(len(words)):
            value = _parse([w.group(0) for w in words[i:]])
            if value is None:
                continue
            start = run_start + words[i].start()
            end = run_start + words[-1].end()
            before = sentence[:start]
            after = sentence[m.end() :]
            total = bool(_TOTAL_MARKER.search(before[-40:])) or bool(_PER_SET_AFTER.search(after))
            out.append(Amount(value, start, end, total))
            break
    return out


def in_delivery_clause(sentence: str, pos: int) -> bool:
    """A delivery / courier / COD-fee word in the clause of ``pos``, before ``pos``."""
    start = 0
    for border in _CLAUSE_SPLIT.finditer(sentence, 0, pos):
        start = border.end()
    return _DELIVERY_WORD.search(sentence, start, pos) is not None


def price_corrections(sentence: str, price: float, count: int | None) -> str | None:
    """``sentence`` with its wrong amounts replaced, or ``None`` when all are right."""
    unit = round(price)
    fixed = sentence
    changed = False
    for amount in reversed(find_amounts(sentence)):
        if in_delivery_clause(sentence, amount.start):
            continue
        if amount.total:
            if count is None or count < 2:
                continue
            truth = unit * count
        else:
            truth = unit
        if amount.value == truth:
            continue
        fixed = fixed[: amount.start] + str(truth) + fixed[amount.end :]
        changed = True
    return fixed if changed else None
