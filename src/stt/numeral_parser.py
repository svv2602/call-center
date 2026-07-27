"""Ukrainian numeral-word → digit converter for plate/phone contexts.

Real problem (call 934b9d31, 2026-07-27): customer says the plate number
using Ukrainian numeral words («два одинадцять» for "2-11"), Google STT
outputs them as text tokens («одинадцять», «сімнадцятий», «двадцяту»),
and the LLM has to guess-convert. It often loses digits or misreads case
forms. This module deterministically maps word forms → digits BEFORE the
LLM sees the transcript.

Scope: 0–999, including all six grammatical cases and ordinal forms
(«одинадцятий/-ому/-ою…»). Applied only in `plate` / `phone` contexts
(see `_apply_stt_corrections` in `src/core/pipeline.py`) so we don't
mangle dates ("двадцять восьме липня") or amounts ("сто гривень").

Public API:
  * ``words_to_digits(text: str) -> tuple[str, int]`` — returns the
    normalized text plus the count of replacements made. Non-numeral
    tokens pass through untouched.
"""

from __future__ import annotations

import re

# ─── Vocabulary ────────────────────────────────────────────────
# Each key maps every case/gender form we've seen in real Ukrainian
# STT output to the digit value. Keys are lowercased pre-lookup.
# NOTE: Include both apostrophe styles (' and ʼ) because STT sometimes
# outputs one, sometimes the other, sometimes drops it entirely.

_UNITS: dict[str, int] = {
    # 0
    "нуль": 0, "нуля": 0, "нулю": 0, "нулем": 0, "нулі": 0,
    "ноль": 0,
    # 1 — masc/fem/neut + all cases + ordinal
    "один": 1, "одна": 1, "одне": 1, "одно": 1,
    "одного": 1, "одному": 1, "одним": 1, "одній": 1, "одну": 1, "одної": 1,
    "перший": 1, "першу": 1, "першого": 1, "першому": 1, "першою": 1, "перше": 1, "перша": 1,
    # 2
    "два": 2, "дві": 2, "двох": 2, "двом": 2, "двома": 2,
    "другий": 2, "другу": 2, "другого": 2, "другому": 2, "другою": 2, "друге": 2, "друга": 2,
    # 3
    "три": 3, "трьох": 3, "трьом": 3, "трьома": 3,
    "третій": 3, "третю": 3, "третього": 3, "третьому": 3, "третьою": 3, "третє": 3, "третя": 3,
    # 4
    "чотири": 4, "чотирьох": 4, "чотирьом": 4, "чотирма": 4, "чотирьома": 4,
    "четвертий": 4, "четверту": 4, "четвертого": 4, "четвертому": 4, "четвертою": 4, "четверте": 4, "четверта": 4,
    # 5
    "п'ять": 5, "пʼять": 5, "пять": 5,
    "п'яти": 5, "пʼяти": 5, "пяти": 5, "п'ятьох": 5, "пʼятьох": 5,
    "п'ятьма": 5, "пʼятьма": 5, "п'ятьома": 5, "пʼятьома": 5,
    "п'ятий": 5, "пʼятий": 5, "п'яту": 5, "пʼяту": 5,
    "п'ятого": 5, "пʼятого": 5, "п'ятому": 5, "пʼятому": 5,
    "п'ятою": 5, "пʼятою": 5, "п'яте": 5, "пʼяте": 5, "п'ята": 5, "пʼята": 5,
    # 6
    "шість": 6, "шести": 6, "шістьох": 6, "шістьма": 6, "шістьома": 6,
    "шостий": 6, "шосту": 6, "шостого": 6, "шостому": 6, "шостою": 6, "шосте": 6, "шоста": 6,
    # 7
    "сім": 7, "семи": 7, "сімох": 7, "сімома": 7, "сьома": 7,
    "сьомий": 7, "сьому": 7, "сьомого": 7, "сьомому": 7, "сьомою": 7, "сьоме": 7,
    # 8
    "вісім": 8, "восьми": 8, "вісьмох": 8, "вісьмома": 8, "вісьма": 8,
    "восьмий": 8, "восьму": 8, "восьмого": 8, "восьмому": 8, "восьмою": 8, "восьме": 8, "восьма": 8,
    # 9
    "дев'ять": 9, "девʼять": 9, "девять": 9,
    "дев'яти": 9, "девʼяти": 9, "девяти": 9, "дев'ятьох": 9, "девʼятьох": 9,
    "дев'ятьма": 9, "девʼятьма": 9, "дев'ятьома": 9, "девʼятьома": 9,
    "дев'ятий": 9, "девʼятий": 9, "дев'яту": 9, "девʼяту": 9,
    "дев'ятого": 9, "девʼятого": 9, "дев'ятому": 9, "девʼятому": 9,
    "дев'ятою": 9, "девʼятою": 9, "дев'яте": 9, "девʼяте": 9, "дев'ята": 9, "девʼята": 9,
}

_TEENS: dict[str, int] = {
    # 10
    "десять": 10, "десяти": 10, "десятьох": 10, "десятьма": 10, "десятьома": 10,
    "десятий": 10, "десяту": 10, "десятого": 10, "десятому": 10, "десятою": 10, "десяте": 10, "десята": 10,
    # 11
    "одинадцять": 11, "одинадцяти": 11, "одинадцятьох": 11, "одинадцятьма": 11, "одинадцятьома": 11,
    "одинадцятий": 11, "одинадцяту": 11, "одинадцятого": 11, "одинадцятому": 11, "одинадцятою": 11, "одинадцяте": 11, "одинадцята": 11,
    # 12
    "дванадцять": 12, "дванадцяти": 12, "дванадцятьох": 12, "дванадцятьма": 12, "дванадцятьома": 12,
    "дванадцятий": 12, "дванадцяту": 12, "дванадцятого": 12, "дванадцятому": 12, "дванадцятою": 12, "дванадцяте": 12, "дванадцята": 12,
    # 13
    "тринадцять": 13, "тринадцяти": 13, "тринадцятьох": 13, "тринадцятьма": 13, "тринадцятьома": 13,
    "тринадцятий": 13, "тринадцяту": 13, "тринадцятого": 13, "тринадцятому": 13, "тринадцятою": 13, "тринадцяте": 13, "тринадцята": 13,
    # 14
    "чотирнадцять": 14, "чотирнадцяти": 14, "чотирнадцятьох": 14, "чотирнадцятьма": 14, "чотирнадцятьома": 14,
    "чотирнадцятий": 14, "чотирнадцяту": 14, "чотирнадцятого": 14, "чотирнадцятому": 14, "чотирнадцятою": 14, "чотирнадцяте": 14, "чотирнадцята": 14,
    # 15
    "п'ятнадцять": 15, "пʼятнадцять": 15, "пятнадцять": 15,
    "п'ятнадцяти": 15, "пʼятнадцяти": 15, "п'ятнадцятьох": 15, "пʼятнадцятьох": 15,
    "п'ятнадцятий": 15, "пʼятнадцятий": 15, "п'ятнадцяту": 15, "пʼятнадцяту": 15,
    "п'ятнадцятого": 15, "пʼятнадцятого": 15, "п'ятнадцятому": 15, "пʼятнадцятому": 15,
    "п'ятнадцятою": 15, "пʼятнадцятою": 15, "п'ятнадцяте": 15, "пʼятнадцяте": 15, "п'ятнадцята": 15, "пʼятнадцята": 15,
    # 16
    "шістнадцять": 16, "шістнадцяти": 16, "шістнадцятьох": 16, "шістнадцятьма": 16, "шістнадцятьома": 16,
    "шістнадцятий": 16, "шістнадцяту": 16, "шістнадцятого": 16, "шістнадцятому": 16, "шістнадцятою": 16, "шістнадцяте": 16, "шістнадцята": 16,
    # 17
    "сімнадцять": 17, "сімнадцяти": 17, "сімнадцятьох": 17, "сімнадцятьма": 17, "сімнадцятьома": 17,
    "сімнадцятий": 17, "сімнадцяту": 17, "сімнадцятого": 17, "сімнадцятому": 17, "сімнадцятою": 17, "сімнадцяте": 17, "сімнадцята": 17,
    # 18
    "вісімнадцять": 18, "вісімнадцяти": 18, "вісімнадцятьох": 18, "вісімнадцятьма": 18, "вісімнадцятьома": 18,
    "вісімнадцятий": 18, "вісімнадцяту": 18, "вісімнадцятого": 18, "вісімнадцятому": 18, "вісімнадцятою": 18, "вісімнадцяте": 18, "вісімнадцята": 18,
    # 19
    "дев'ятнадцять": 19, "девʼятнадцять": 19, "девятнадцять": 19,
    "дев'ятнадцяти": 19, "девʼятнадцяти": 19,
    "дев'ятнадцятий": 19, "девʼятнадцятий": 19, "дев'ятнадцяту": 19, "девʼятнадцяту": 19,
    "дев'ятнадцятого": 19, "девʼятнадцятого": 19, "дев'ятнадцятому": 19, "девʼятнадцятому": 19,
    "дев'ятнадцятою": 19, "девʼятнадцятою": 19, "дев'ятнадцяте": 19, "девʼятнадцяте": 19, "дев'ятнадцята": 19, "девʼятнадцята": 19,
}

_TENS: dict[str, int] = {
    # 20
    "двадцять": 20, "двадцяти": 20, "двадцятьох": 20, "двадцятьма": 20, "двадцятьома": 20,
    "двадцятий": 20, "двадцяту": 20, "двадцятого": 20, "двадцятому": 20, "двадцятою": 20, "двадцяте": 20, "двадцята": 20,
    # 30
    "тридцять": 30, "тридцяти": 30, "тридцятьох": 30, "тридцятьма": 30, "тридцятьома": 30,
    "тридцятий": 30, "тридцяту": 30, "тридцятого": 30, "тридцятому": 30, "тридцятою": 30, "тридцяте": 30, "тридцята": 30,
    # 40
    "сорок": 40, "сорока": 40,
    "сороковий": 40, "сорокову": 40, "сорокового": 40, "сороковому": 40, "сороковою": 40, "сорокове": 40, "сорокова": 40,
    # 50
    "п'ятдесят": 50, "пʼятдесят": 50, "пятдесят": 50,
    "п'ятдесяти": 50, "пʼятдесяти": 50, "п'ятдесятьох": 50, "пʼятдесятьох": 50,
    "п'ятдесятий": 50, "пʼятдесятий": 50, "п'ятдесяту": 50, "пʼятдесяту": 50,
    # 60
    "шістдесят": 60, "шістдесяти": 60, "шістдесятьох": 60,
    "шістдесятий": 60, "шістдесяту": 60,
    # 70
    "сімдесят": 70, "сімдесяти": 70, "сімдесятьох": 70,
    "сімдесятий": 70, "сімдесяту": 70,
    # 80
    "вісімдесят": 80, "вісімдесяти": 80, "вісімдесятьох": 80,
    "вісімдесятий": 80, "вісімдесяту": 80,
    # 90
    "дев'яносто": 90, "девʼяносто": 90, "девяносто": 90,
    "дев'яноста": 90, "девʼяноста": 90,
    "дев'яностий": 90, "девʼяностий": 90, "дев'яносту": 90, "девʼяносту": 90,
}

_HUNDREDS: dict[str, int] = {
    "сто": 100, "ста": 100, "стом": 100,
    "сотий": 100, "соту": 100, "сотого": 100, "сотому": 100, "сотою": 100, "соте": 100, "сота": 100,
    "двісті": 200, "двохсот": 200, "двомстам": 200, "двомастами": 200,
    "триста": 300, "трьохсот": 300, "тристами": 300,
    "чотириста": 400, "чотирьохсот": 400,
    "п'ятсот": 500, "пʼятсот": 500, "пятсот": 500,
    "шістсот": 600, "шестисот": 600,
    "сімсот": 700, "семисот": 700,
    "вісімсот": 800, "восьмисот": 800,
    "дев'ятсот": 900, "девʼятсот": 900, "девятсот": 900,
}


# Russian numeral spellings — Redis RU→UA correction rules normally
# handle these, but STT sometimes emits hybrid forms («одиннадцять»
# — RU double-n + UA -ять ending) that no rule catches. Also acts
# as a safety net when a rule is disabled or removed.
_RU_NUMERALS: dict[str, int] = {
    # 10
    "десять": 10,  # same as UA — no-op mapping
    # 11–19 (RU forms) + hybrid variants (double n, either -ать/-ять)
    "одиннадцать": 11, "одиннадцять": 11, "одинадцать": 11,
    "двенадцать": 12, "двенадцять": 12,
    "тринадцать": 13,  # UA form already in _TEENS
    "четырнадцать": 14, "четырнадцять": 14,
    "пятнадцать": 15, "пятнадцять": 15,
    "шестнадцать": 16, "шестнадцять": 16,
    "семнадцать": 17, "семнадцять": 17,
    "восемнадцать": 18, "восемнадцять": 18,
    "девятнадцать": 19, "девятнадцять": 19,
    # 20
    "двадцать": 20,
    # 30
    "тридцать": 30,
    # 40 same in both (сорок)
    # 50–90 — RU forms
    "пятьдесят": 50, "пятдесят": 50,  # written with ь or without
    "шестьдесят": 60,
    "семьдесят": 70,
    "восемьдесят": 80,
    # 90 same (девяносто variants already in _TENS)
    # RU units (as spoken by Russian-speaking callers)
    "один": 1,  # already in _UNITS but repeat for symmetry
    "два": 2,
    "три": 3,
    "четыре": 4, "четырех": 4,
    "пять": 5, "пяти": 5,
    "шесть": 6, "шести": 6,
    "семь": 7,
    "восемь": 8, "восьмь": 8,
    "девять": 9,
}


def _lookup(word: str) -> int | None:
    """Return digit value for a numeral word, or None if not a numeral.

    Bare digit tokens ("11", "1725") also qualify — in plate/phone
    contexts, spoken numbers routinely come as a mix of words and
    digits, and consecutive tokens of either kind should join a single
    run («два» + "11" spoken as one utterance → "211").
    """
    w = word.lower()
    # Also check Russian numeral spellings as a defensive layer — Redis
    # RU→UA rules cover the common cases but hybrids like «одиннадцять»
    # (RU double-n + UA -ять ending) still leak through, and we want the
    # parser to be resilient.
    if w in _RU_NUMERALS:
        return _RU_NUMERALS[w]
    if w in _UNITS:
        return _UNITS[w]
    if w in _TEENS:
        return _TEENS[w]
    if w in _TENS:
        return _TENS[w]
    if w in _HUNDREDS:
        return _HUNDREDS[w]
    if w.isdigit():
        return int(w)
    return None


# Word-boundary tokenizer that keeps apostrophes as word characters.
# Ukrainian numerals contain U+0027 (') and U+02BC (ʼ) both — either
# should attach to the neighboring letters, not split the token.
_TOKEN_RE = re.compile(r"[\w'ʼ]+|[^\w'ʼ\s]+|\s+", re.UNICODE)


def words_to_digits(text: str) -> tuple[str, int]:
    """Convert Ukrainian numeral words in *text* to digit strings.

    - Consecutive numeral words are treated as a single sequence
      («два одинадцять» → "211", «двадцять один» → "21",
      «сто одинадцять» → "111").
    - Non-numeral tokens pass through unchanged.
    - Composition rule: within a run, values ≤ 9 concatenate as digits
      («два одинадцять» → "2" + "11" → "211"), values that are tens/
      hundreds combine additively with the next unit («двадцять один»
      = 20+1 = "21", «сто одинадцять» = 100+11 = "111").
    - Case-insensitive matching.

    Returns (new_text, replacements_made).
    """
    if not text:
        return text, 0

    tokens = _TOKEN_RE.findall(text)
    result: list[str] = []
    # Accumulator for a run: each entry is (int_value, display_string).
    # Digit tokens keep their original spelling so leading zeros survive
    # ("0294" stays "0294" instead of becoming 294 → "294"). Numeral
    # words use str(int_value), which never has a leading zero.
    run: list[tuple[int, str]] = []
    # Was any word-form (not just bare digits) contributing to this run?
    # If the whole run was already digits, emitting the same digit string
    # is a passthrough and shouldn't count as a "replacement".
    run_has_word = False
    replacements = 0
    pending_ws = ""

    def _flush() -> None:
        nonlocal replacements, run_has_word
        if not run:
            return
        digits = _combine_run(run)
        result.append(digits)
        if run_has_word:
            replacements += 1
        run.clear()
        run_has_word = False

    for tok in tokens:
        if tok.isspace() or not tok.strip():
            if run:
                pending_ws = tok
            else:
                result.append(tok)
                pending_ws = ""
            continue
        val = _lookup(tok)
        if val is not None:
            pending_ws = ""
            if tok.isdigit():
                run.append((val, tok))
            else:
                run.append((val, str(val)))
                run_has_word = True
        else:
            _flush()
            if pending_ws:
                result.append(pending_ws)
                pending_ws = ""
            result.append(tok)
    _flush()

    return "".join(result), replacements


def _combine_run(values: list[tuple[int, str]]) -> str:
    """Merge a run of (int, display_str) tuples into a single digit string.

    Rules:
      * Greedily fold each value's successors into its trailing-zero
        slots, as long as the successor is strictly smaller than the
        accumulator and fits in the zero region. Handles cascading
        composition: «двісті двадцять п'ять» = 200 + 20 + 5 = 225.
      * A value that doesn't fit terminates the current accumulator
        and starts a new one — its display string is appended verbatim
        («два одинадцять» → "2" | "11" → "211").
      * Once we combine values additively, the combined value's display
        is str(int_sum) — leading zeros only survive on standalone tokens
        that never enter an additive fold.
    """
    if not values:
        return ""
    parts: list[str] = []
    i = 0
    while i < len(values):
        v_int, v_str = values[i]
        while i + 1 < len(values):
            nxt_int, _ = values[i + 1]
            trailing_zeros = len(v_str) - len(v_str.rstrip("0"))
            if trailing_zeros == 0 or nxt_int >= v_int:
                break
            if len(str(nxt_int)) > trailing_zeros:
                break
            v_int = v_int + nxt_int
            v_str = str(v_int)
            i += 1
        parts.append(v_str)
        i += 1
    return "".join(parts)
