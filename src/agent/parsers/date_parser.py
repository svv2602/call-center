"""`date_parser` — DATE. Turns the hint into an ISO date, or into nothing.

The latent P0 this closes
-------------------------
`compound_parse._detect_date_hint` returns a **raw label** on purpose —
«завтра», «п'ятниця», «15 березня» — because calendar arithmetic needs a
«today» the module does not have. The seam in `src/core/pipeline.py` then maps
`date_hint → "date"` with the label's own confidence, which for «завтра» is
`1.0`: above the apply threshold. `session.fsm_filled_fields["date"]` becomes
the string «завтра», DATE's `auto_skip_if` sees a filled field, and the state
is skipped on a value nothing downstream can book. In `shadow` that is
harmless — the fields only reach a log. In `live` it is the same defect class
as `c8c6601`: the bot believes it knows the date and never asks.

This parser is the guard. **A raw label never leaves it.** Either the hint
resolves to `YYYY-MM-DD`, or the outcome is `unresolved` with no value at all.

Time comes from `ctx.now` and from nowhere else
-----------------------------------------------
Without `ctx.now` the answer is `unresolved`, not «probably today». A parser
that reaches for the process clock stops being a function of its context: the
same utterance would resolve differently depending on when the test ran, and
the shadow-mode comparison against the live flow would drift with the date.

Input vocabulary
----------------
Exactly what `_detect_date_hint` emits, and nothing else:

===========================  ==========  ==============================
hint                         confidence  resolved to
===========================  ==========  ==============================
`сьогодні`/`завтра`/         `1.0`       `ctx.now` + 0/1/2 days
`післязавтра`
`«15 березня»`               `0.9`       next occurrence, 12 UA + 12 RU
                                         month names
`dd.mm[.yyyy]` via `. / -`   `0.9`       as written; bare `dd.mm` takes
                                         the next occurrence
weekday, canonicalised       `0.9`       next **future** occurrence
`найближча`                  `0.6`       nothing — below the threshold
===========================  ==========  ==============================

`найближча` stays unresolved by design. The caller handed the choice back to
the bot, and a date still has to be said out loud before it is booked; raising
its confidence to make it «work» would book a day nobody named.

Weekday resolution: «сьогодні п'ятниця» + «п'ятниця» → **next** Friday
--------------------------------------------------------------------
Strictly future, never today. A caller who means today has an unambiguous word
for it («сьогодні») and uses it; a weekday name is a recurring label. The
asymmetry decides it: booking a caller for next week when they meant today
costs one correction at CONFIRM, where the date is read back in words
(`ua_datetime.date_to_words`), while booking them for today when they meant
next week has them miss a slot they never agreed to.

An explicit «15 березня» is different and *does* accept today: it names one
specific day, not a recurring label.

Relationship to `main.py:_resolve_date`
---------------------------------------
`src/main.py:_resolve_date` covered сьогодні/завтра/післязавтра only, had no
weekday or day-month handling, and read the wall clock itself, so it could not
be imported into a parser that must stay a function of `ctx`. Wave 6-B removed
it, together with the third copy nobody had noticed in
`src/sandbox/agent_runner.py`, and pointed both call sites at
:func:`resolve_tool_date` below. One calendar implementation, three callers.

`resolve_tool_date` is **not** `parse()` with a different name
--------------------------------------------------------------
It is a thin tool-layer adapter, and the ISO passthrough in it is load-bearing
rather than an optimisation. `_detect_date_hint` scans for substrings, so on
the ISO string the LLM normally passes it matches the *tail*:
`"2026-03-01"` → hint `"03-01"` → day 3, month 1 → **2027-01-03**. Handing a
date the LLM already resolved to the hint detector silently books a different
year. So: ISO first, then the two English aliases `_resolve_date` accepted and
`_detect_date_hint` does not know, and only then the parser.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from datetime import date as _date

from src.agent.compound_parse import (
    _MONTHS_RU,
    _MONTHS_UA,
    _detect_date_hint,
    _detect_time_hint,
    _normalize,
)
from src.agent.fitting_fsm import FsmState, bot_is_asking
from src.agent.parsers.base import (
    APPLY_THRESHOLD,
    NOT_MENTIONED,
    ParseContext,
    ParseOutcome,
    graded,
    unresolved,
)

logger = logging.getLogger(__name__)

#: Labels `_RELATIVE_DAYS` canonicalises to, and their offset in days.
_RELATIVE_OFFSETS: dict[str, int] = {
    "сьогодні": 0,
    "завтра": 1,
    "післязавтра": 2,
}

#: The canonical forms `_WEEKDAYS` maps its stems to, in `weekday()` order.
_WEEKDAY_INDEX: dict[str, int] = {
    "понеділок": 0,
    "вівторок": 1,
    "середа": 2,
    "четвер": 3,
    "п'ятниця": 4,
    "субота": 5,
    "неділя": 6,
}

#: Built from the same alternations `_DAY_MONTH_RE` is built from, so the two
#: can not drift apart: genitive month names, UA and RU, January first.
_MONTH_NUMBERS: dict[str, int] = {
    name: number for number, name in enumerate(_MONTHS_UA.split("|"), start=1)
}
_MONTH_NUMBERS.update(
    {name: number for number, name in enumerate(_MONTHS_RU.split("|"), start=1)}
)

_DAY_MONTH_HINT_RE = re.compile(r"^(\d{1,2})\s+([а-яіїєґ']+)$")
_NUMERIC_HINT_RE = re.compile(r"^(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?$")

#: How far forward a bare day/month is allowed to roll while looking for a
#: valid calendar date. Four years is enough to clear a 29 February.
_MAX_YEAR_ROLL = 4

#: A day of the month standing on its own. The lookbehind and the lookahead do
#: all the work: they keep the match off anything that merely *contains* a
#: small number. «900» is not day 9 (a digit follows), «15:40» is not day 15,
#: «11.09» is not day 11 — that shape belongs to `_NUMERIC_HINT_RE`. An
#: ordinal suffix is allowed through («11-е»), because what follows the dash
#: there is a letter, not a digit.
_BARE_DAY_RE = re.compile(r"(?<![\d:.,/-])(3[01]|[12]\d|0?[1-9])(?!\d|[:.,/-]\s*\d)")

_DIGIT_RUN_RE = re.compile(r"\d+")

#: Below the `0.9` an explicit «11 вересня» earns and above the threshold. The
#: month is inferred rather than heard, so this is a weaker reading of the same
#: utterance — but it is still a specific day the caller named in answer to the
#: question, which is what `APPLY_THRESHOLD` separates.
_BARE_DAY_CONFIDENCE = 0.8

#: Months a bare day may roll forward through. Four is enough for a 31st asked
#: in February to land on 31 March rather than nowhere.
_MAX_MONTH_ROLL = 4

#: A day of the month spoken as a word, UA and RU. Stems only — the **endings**
#: are the discriminator, exactly as in `compound_parse._HOUR_ORDINALS`. The
#: three readings a number can have in this dialogue take three disjoint sets
#: of endings, so the ending alone decides which one it is:
#:
#:   date (neuter)     «третє», «третього», «третье», «третьего»
#:   hour (feminine)   «третю», «третьої», «третій»   → `_HOUR_ORDINALS`
#:   diameter          «п'ятнадцять», «п'ятнадцяти»   → `detect_diameter`
#:
#: That is why «на другу» stays an hour and «на друге» becomes the 2nd.
# fmt: off
_DAY_ORDINAL_STEMS: tuple[tuple[str, int], ...] = (
    ("перш", 1), ("перв", 1),
    ("друг", 2), ("втор", 2),
    ("трет", 3),
    ("четверт", 4),
    ("п'ят", 5), ("пят", 5),
    ("шост", 6), ("шест", 6),
    ("сьом", 7), ("седьм", 7),
    ("восьм", 8),
    ("дев'ят", 9), ("девят", 9),
    ("десят", 10),
    ("одинадцят", 11), ("одиннадцат", 11),
    ("дванадцят", 12), ("двенадцат", 12),
    ("тринадцят", 13), ("тринадцат", 13),
    ("чотирнадцят", 14), ("четырнадцат", 14),
    ("п'ятнадцят", 15), ("пятнадцат", 15),
    ("шістнадцят", 16), ("шестнадцат", 16),
    ("сімнадцят", 17), ("семнадцат", 17),
    ("вісімнадцят", 18), ("восемнадцат", 18),
    ("дев'ятнадцят", 19), ("девятнадцат", 19),
    ("двадцят", 20), ("двадцат", 20),
    ("тридцят", 30), ("тридцат", 30),
)

#: 21..31 are two words, and the tens half stays **cardinal**: «двадцять
#: перше», never «двадцяте перше». So a bare «двадцять» cannot be mistaken for
#: the 20th — it carries no ordinal ending and `_DAY_ORDINAL_RE` will not match
#: it on its own.
_DAY_TENS: dict[str, int] = {
    "двадцять": 20, "двадцать": 20, "тридцять": 30, "тридцать": 30,
}
# fmt: on

_DAY_ORDINAL_LOOKUP: dict[str, int] = dict(_DAY_ORDINAL_STEMS)
#: Alternation order does not decide «п'ятнадцяте», the mandatory ending does:
#: «п'ят» matches first, «надцяте» is not an ending, and the engine backtracks
#: into «п'ятнадцят». This holds only while no stem can *complete* inside a
#: longer one, which `test_no_stem_can_complete_inside_another` enforces —
#: sorting longest-first here looked like the safeguard but changed no outcome.
_DAY_ORDINAL_ALT = "|".join(re.escape(stem) for stem, _ in _DAY_ORDINAL_STEMS)
#: Neuter and genitive only. Adding a feminine ending here («у», «ій») is what
#: would turn «на другу» into the 2nd, so this tuple is the whole discriminator.
_DAY_ORDINAL_ENDINGS: tuple[str, ...] = ("ього", "ьего", "ого", "ье", "ое", "е", "є")
_DAY_ORDINAL_RE = re.compile(
    rf"\b(?:({'|'.join(_DAY_TENS)})\s+)?({_DAY_ORDINAL_ALT})"
    rf"(?:{'|'.join(_DAY_ORDINAL_ENDINGS)})\b"
)


def _ordinals_to_digits(text: str) -> tuple[str, tuple[tuple[int, int], ...]]:
    """«на чотирнадцяте вересня» → «на 14 вересня», plus the spans it rewrote.

    The bot reads every date back in exactly this form —
    `ua_datetime.date_to_words` turns `2026-09-14` into «чотирнадцяте
    вересня» — so a caller who repeats the bot's own wording was, until this
    rewrite, not understood. Over the 30 days to 2026-09-11 prod carried five
    such answers («на третє», «на третій вересня», «над пятого», «давайте
    другого начнем», «давайте на третьего разням»), every one of them read as
    «no date».

    Rewriting to digits rather than teaching `_detect_date_hint` a second
    vocabulary keeps one calendar implementation: «чотирнадцяте вересня»
    becomes the «14 вересня» the detector already resolves, and a lone
    «чотирнадцяте» becomes the «14» that `_bare_day` already gates behind the
    bot's question.

    The spans are returned in the **input**'s coordinates, not the rewritten
    string's — a substitution changes every offset after it, and a span that
    silently points at the wrong characters is worse than none.
    """
    spans: list[tuple[int, int]] = []

    def _replace(match: re.Match[str]) -> str:
        tens = _DAY_TENS.get(match.group(1) or "", 0)
        unit = _DAY_ORDINAL_LOOKUP[match.group(2)]
        day = tens + unit
        # «двадцять одинадцяте» is not a date, and neither is a 32nd.
        if tens and unit > 9:
            return match.group(0)
        if not 1 <= day <= 31:
            return match.group(0)
        spans.append(match.span())
        return str(day)

    return _DAY_ORDINAL_RE.sub(_replace, text), tuple(spans)


def _next_day_of_month(day: int, today: _date) -> _date | None:
    """First calendar date with this day number, today included.

    Rolls the **month**, where :func:`_next_occurrence` rolls the year. A
    caller who says «на 11» on the 20th means next month's 11th; one who says
    it on the 5th means this month's.
    """
    year, month = today.year, today.month
    for _ in range(_MAX_MONTH_ROLL):
        try:
            candidate = _date(year, month, day)
        except ValueError:
            candidate = None  # 31 in a 30-day month — try the next one.
        if candidate is not None and candidate >= today:
            return candidate
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return None


def _next_occurrence(day: int, month: int, today: _date) -> _date | None:
    """First calendar date with this day and month, today included."""
    for offset in range(_MAX_YEAR_ROLL):
        try:
            candidate = _date(today.year + offset, month, day)
        except ValueError:
            continue  # 29 February in a non-leap year — try the next one.
        if candidate >= today:
            return candidate
    return None


def _resolve_hint(hint: str, today: _date) -> _date | None:
    """Hint → calendar date. `None` means «we could not pin it down»."""
    offset = _RELATIVE_OFFSETS.get(hint)
    if offset is not None:
        return today + timedelta(days=offset)

    weekday = _WEEKDAY_INDEX.get(hint)
    if weekday is not None:
        ahead = (weekday - today.weekday()) % 7
        return today + timedelta(days=ahead or 7)

    match = _DAY_MONTH_HINT_RE.match(hint)
    if match:
        month = _MONTH_NUMBERS.get(match.group(2))
        if month is None:
            return None
        return _next_occurrence(int(match.group(1)), month, today)

    match = _NUMERIC_HINT_RE.match(hint)
    if match:
        day, month = int(match.group(1)), int(match.group(2))
        if not 1 <= month <= 12:
            return None
        raw_year = match.group(3)
        if raw_year is None:
            return _next_occurrence(day, month, today)
        year = int(raw_year)
        if year < 100:
            year += 2000
        try:
            return _date(year, month, day)
        except ValueError:
            return None

    # «найближча» and anything else the detector may grow later.
    return None


class DateParser:
    """DATE. ISO or nothing."""

    name = "date_parser"
    field_name = "date"
    aresolve = None

    def parse(self, ctx: ParseContext) -> ParseOutcome:
        text = (ctx.customer_text or "").strip()
        if not text:
            return NOT_MENTIONED

        normalized = _normalize(text)
        rewritten, ordinal_spans = _ordinals_to_digits(normalized)

        hit = _detect_date_hint(rewritten)
        if hit is None:
            return self._bare_day(ctx, rewritten, ordinal_spans)

        if hit.confidence < APPLY_THRESHOLD:
            # «найближча» — the caller spoke about the date without naming one.
            logger.debug("date_parser: hint %r is below the apply threshold", hit.value)
            return unresolved(confidence=hit.confidence, spans=hit.spans)

        if ctx.now is None:
            logger.debug(
                "date_parser: hint %r needs a reference date and ctx.now is None "
                "— unresolved",
                hit.value,
            )
            return unresolved(confidence=hit.confidence, spans=hit.spans)

        resolved = _resolve_hint(str(hit.value), ctx.now.date())
        if resolved is None:
            logger.debug("date_parser: hint %r did not resolve to a calendar date", hit.value)
            return unresolved(confidence=hit.confidence, spans=hit.spans)

        # Business rules — the 21-day window, +3 working days on a storage
        # contract — stay in the tool layer (`src/main.py`). This parser
        # normalises the form; it does not decide whether the day is bookable.
        return graded(resolved.isoformat(), hit.confidence, ordinal_spans or hit.spans)

    def _bare_day(
        self,
        ctx: ParseContext,
        rewritten: str,
        ordinal_spans: tuple[tuple[int, int], ...],
    ) -> ParseOutcome:
        """«на 11» — a day with no month, and only while the bot asked for one.

        `_detect_date_hint` refuses this shape on purpose, and it is right to:
        a bare number is whatever the last question made it. Across the whole
        2026-09-10 prod window the same shape carried a wheel diameter («19»),
        a time («900») and three chunks of a phone number. So the fix is not a
        sharper regex, it is the question — this reads a number as a date only
        while the bot's previous turn was DATE's own question, the gate
        `storage_choice_parser._bot_is_asking_storage` and
        `diameter_detect.is_diameter_question` already stand on. `a83655c5`
        was transferred on «на 11» said straight after «На яку дату
        записуємо?», and it is the one call in that window where the caller
        was answering the state's real question.

        Two narrowings on top of the gate, because the gate can only tell what
        the *bot* said and STT decides what the caller said:

        * **One number in the turn, or nothing.** A phone number and a card
          number are several runs of digits, and the last run of «095 9362 18»
          is a legal day. The bot asking for a phone while the FSM sits in
          DATE is exactly what happened in `30dd42fa`.
        * **Nothing the time detector claims.** «на 6 вечера» is 18:00, not
          the 6th, and a suffix list would have had to grow «вечора»,
          «ранку», «дня», «о шостій» and «на другу» one regression at a time.
          `_detect_time_hint` already knows all of them and draws the line in
          the same place this parser needs it: it returns nothing for a bare
          «на 16», on the stated grounds that the bare form belongs to
          «the FSM's TIME/PRICE states, which know which question they just
          asked» — which is this gate.
        * **A word ordinal counts as a digit.** `_ordinals_to_digits` has
          already turned «одинадцяте» into «11» by the time this runs, so the
          gap the first version of this docstring left open knowingly is
          closed — but only through the same gate, never around it.

          The time detector is asked about the **rewritten** text, and the
          rewrite is what makes that the right side to ask about. An hour
          ending is never rewritten, so «на другу» arrives at the detector
          verbatim; a date ending that collapses to a bare digit can *newly*
          read as an hour — «о першого» becomes «о 1», which is 13:00 — and
          that reading only exists after the rewrite. An earlier version also
          checked the pre-rewrite spelling; a sweep of 204k generated
          utterances found no input where it changed the outcome, so it went.
        """
        if not bot_is_asking(FsmState.DATE, ctx.last_bot_utterance):
            return NOT_MENTIONED

        if len(_DIGIT_RUN_RE.findall(rewritten)) != 1:
            return NOT_MENTIONED

        if _detect_time_hint(rewritten) is not None:
            return NOT_MENTIONED

        match = _BARE_DAY_RE.search(rewritten)
        if match is None:
            return NOT_MENTIONED

        spans = ordinal_spans or (match.span(),)
        if ctx.now is None:
            logger.debug("date_parser: bare day %r needs ctx.now — unresolved", match.group(1))
            return unresolved(confidence=_BARE_DAY_CONFIDENCE, spans=spans)

        resolved = _next_day_of_month(int(match.group(1)), ctx.now.date())
        if resolved is None:
            return unresolved(confidence=_BARE_DAY_CONFIDENCE, spans=spans)

        logger.info(
            "date_parser: bare day %r read as %s (bot asked for a date)",
            match.group(1),
            resolved.isoformat(),
        )
        return graded(resolved.isoformat(), _BARE_DAY_CONFIDENCE, spans)


PARSER = DateParser()


#: Aliases `main.py:_resolve_date` accepted and `_detect_date_hint` does not
#: know. Dropping them would be a silent behaviour change on the tool layer:
#: the LLM does emit bare English `today` / `tomorrow`.
_TOOL_ALIASES: dict[str, str] = {
    "today": "сьогодні",
    "сегодня": "сьогодні",
    "tomorrow": "завтра",
    "aftertomorrow": "післязавтра",
    "послезавтра": "післязавтра",
    "після завтра": "післязавтра",
}


def resolve_tool_date(value: str, now: datetime | None = None) -> str:
    """Normalise a date argument coming from an LLM tool call to `YYYY-MM-DD`.

    Replaces `main.py:_resolve_date` and its copy in
    `sandbox/agent_runner.py`. Same contract as the original: `""` in, `""`
    out; anything it cannot resolve is returned stripped rather than dropped,
    because the SOAP layer's own validation is the one that should reject it,
    and swallowing the value here would turn a bad date into no date.

    Wider than the original by design — weekdays, `«15 березня»` and `dd.mm`
    now resolve too, through the same parser the FSM uses. The wall clock is
    read **here**, once, when the caller does not supply `now`; the parser
    itself stays a function of its context.
    """
    if not value:
        return ""
    text = value.strip()

    # ISO first. See the module docstring: feeding an already-resolved date to
    # the hint detector matches its tail and moves it to another year.
    try:
        return _date.fromisoformat(text).isoformat()
    except ValueError:
        pass

    hint = _TOOL_ALIASES.get(text.lower(), text)
    if now is None:
        now = datetime.now(tz=UTC)

    outcome = PARSER.parse(ParseContext(customer_text=hint, now=now))
    if outcome.status == "value" and outcome.value:
        return str(outcome.value)

    logger.debug("resolve_tool_date: %r did not resolve — passing through", value)
    return text
