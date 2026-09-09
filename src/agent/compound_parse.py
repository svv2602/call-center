"""Compound pre-parse — pull every field out of a single customer utterance.

Wave 2-B of the FSM refactor (`development-checklists/fsm-refactor-2026-09-07/
wave-2-B-compound-parse/`).

Why this module exists
----------------------
The FSM (`src/agent/fitting_fsm.py`) walks the caller through one field per
state: city → station → storage → date → time → colour → brand. Verbose callers
routinely answer three of those in their opening line — «шиномонтаж R18 в
Дніпрі», «мій білий Toyota на Оболоні в понеділок». Without a compound parse the
machine would still ask «У якому місті вам зручніше?», which is exactly the kind
of re-asking that made testers hang up (calls 18e96042, dda83de3).

`compound_parse()` returns the fields it can prove; `FsmEngine.apply_field()`
pins the ones above the caller's confidence threshold and the auto-skip chain
walks past those states.

Reuse, not reimplementation
---------------------------
Every field detector here already exists and has been tuned against weeks of
production traffic. This module **calls** them:

* :mod:`src.agent.diameter_detect` — `detect_diameter`
* :mod:`src.agent.color_detect`    — `detect_color`
* :mod:`src.agent.name_detect`     — `detect_name`
* :mod:`src.agent.preparse`        — `preparse_fitting` (brand)
* :mod:`src.agent.date_detect`     — `mentions_date` (gate for `date_hint`)

:mod:`src.agent.time_detect` is the one detector this module does *not* call.
`detect_time_choice` matches a spoken time against `fitting_slots_offered`, and
on the opening turn that list is empty — by design, since its whole point is
that it "can pin an existing slot but never invent one". It stays the authority
once slots exist; `_detect_time_hint` below only reads the caller's initial
wish, which is a different question.

Only `_detect_city` and `_detect_station_hint` are genuinely new, plus the thin
`date_hint` / `time_hint` extractors — `date_detect` answers *whether* a date
was mentioned, not which one.

Returned field keys (deliberately different from the FSM's own field names for
the three that still need resolving against the store API):

    city, diameter, color, brand, name, station_hint, date_hint, time_hint

`station_hint` still needs `get_fitting_stations(query=…)` to become a
`station_id`; `date_hint` / `time_hint` still need calendar arithmetic. Doing
either here would duplicate logic that lives in the tool layer.

Confidence
----------
Downstream applies a field only at ``>= 0.7`` (see the checklist contract), so
the numbers are a routing decision, not a vibe:

* **1.0** — the utterance is unambiguous on its own: an explicit city name, a
  diameter carrying an «R»/«діаметр»/«дюйм» marker, a literal ``HH:MM``,
  «завтра»/«сьогодні»/«післязавтра».
* **0.8–0.9** — pattern-based and solid but inferred: a colour root, a car
  brand, a weekday, an hour given as a feminine ordinal, a city derived from a
  landmark, a name from an explicit «мене звати …».
* **0.5–0.6** — ambiguous or STT-mangled, deliberately *below* the apply
  threshold so the FSM still asks: a bare number 13-24 (diameter or hour or day
  of month?), a part-of-day preference, a mangled city variant.

The bare-number case is the important one. «на 16» after «шиномонтажу» is a
diameter, after «на» + «годину» it is a time — Wave 12's diameter guard exists
because getting this wrong quoted a price for a size the caller never named. If
the utterance does not disambiguate, this module refuses to.

No LLM, no I/O, no network. Pure regex; the whole call is well under 1 ms.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.agent.color_detect import detect_color
from src.agent.date_detect import mentions_date
from src.agent.diameter_detect import detect_diameter
from src.agent.name_detect import detect_name
from src.agent.preparse import preparse_fitting

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

#: Fields at or above this confidence are safe for `FsmEngine.apply_field`.
APPLY_THRESHOLD = 0.7

#: Every key `compound_parse` may put into `CompoundParseResult.fields`.
FIELD_KEYS: tuple[str, ...] = (
    "city",
    "station_hint",
    "date_hint",
    "time_hint",
    "diameter",
    "color",
    "brand",
    "name",
)


@dataclass
class CompoundParseResult:
    """Everything a single utterance yielded.

    `fields` holds *all* detections regardless of confidence — a caller that
    only wants the actionable ones uses :meth:`confident_fields`. Keeping the
    weak matches visible is deliberate: they are the difference between "the
    caller said nothing about a date" and "the caller said something we could
    not pin down", and the FSM phrases its re-ask differently for each.
    """

    fields: dict[str, Any] = field(default_factory=dict)
    fields_confidence: dict[str, float] = field(default_factory=dict)
    intent_hint: str | None = None  # BOOK/PRICE/CANCEL/… — filled by Wave 3-A

    def confident_fields(self, threshold: float = APPLY_THRESHOLD) -> dict[str, Any]:
        """Fields whose confidence reaches `threshold`."""
        return {
            name: value
            for name, value in self.fields.items()
            if self.fields_confidence.get(name, 0.0) >= threshold
        }

    def _put(self, name: str, value: Any, confidence: float) -> None:
        """Record a detection. First writer wins — detectors run in priority order."""
        if value is None or value == "" or name in self.fields:
            return
        self.fields[name] = value
        self.fields_confidence[name] = confidence


# ═══════════════════════════════════════════════════════════
#  Text normalisation
# ═══════════════════════════════════════════════════════════

_APOSTROPHES = {"ʼ": "'", "’": "'", "`": "'", "‘": "'", "´": "'"}


def _normalize(text: str) -> str:
    """Lowercase + fold the five apostrophe glyphs STT emits into one."""
    low = text.lower()
    for raw, canonical in _APOSTROPHES.items():
        low = low.replace(raw, canonical)
    return low


def _blank(text: str, spans: list[tuple[int, int]]) -> str:
    """Replace `spans` with spaces, keeping every other offset intact.

    Used to hide date/time matches from `detect_diameter`: «на 18 вересня» and
    «о 14 годині» both contain a number in the 13-24 diameter window, and
    without blanking the caller would be booked for tyres they never mentioned.
    """
    if not spans:
        return text
    chars = list(text)
    for start, end in spans:
        for i in range(max(0, start), min(len(chars), end)):
            chars[i] = " "
    return "".join(chars)


@dataclass(frozen=True)
class _Hit:
    """One detector's answer plus the text it consumed."""

    value: Any
    confidence: float
    spans: tuple[tuple[int, int], ...] = ()


# ═══════════════════════════════════════════════════════════
#  Cities
# ═══════════════════════════════════════════════════════════
#
# Only the five cities the network actually serves (the fallback list the FSM
# reads out in `STATES[CITY].silence_reprompt`). Stems are prefix-matched after
# a `\b`, so every UA/RU case form of the name is covered by one entry:
# «Києві» / «Киеве» / «Київ» all start with «киї»/«кие»/«київ».
_CITY_STEMS: tuple[tuple[str, str], ...] = (
    # Дніпро — «дніпр» also covers Придніпровськ/Дніпрошина, both of which are
    # Dnipro landmarks anyway, and the old Дніпропетровськ forms.
    ("дніпр", "Дніпро"),
    ("днипр", "Дніпро"),
    ("днепр", "Дніпро"),
    ("dnipr", "Дніпро"),
    # Київ
    ("київ", "Київ"),
    ("києв", "Київ"),
    ("киев", "Київ"),
    ("кieв", "Київ"),
    ("кыев", "Київ"),
    ("kyiv", "Київ"),
    ("kiev", "Київ"),
    # Харків — the oblique cases swap і→о («у Харкові», «з Харкова»), so the
    # nominative stem alone misses the form callers actually use.
    # NB «харківськ»/«харьковск» is Харківське шосе *in Kyiv* and is stripped
    # by the landmark pass before we ever get here (call 1b6721a4).
    ("харків", "Харків"),
    ("харкiв", "Харків"),
    ("харков", "Харків"),
    ("харьков", "Харків"),
    ("kharkiv", "Харків"),
    # Запоріжжя — «запорізьке шосе» is a Dnipro street, so the adjective stem
    # «запорізьк» is intentionally NOT here.
    ("запоріж", "Запоріжжя"),
    ("запорiж", "Запоріжжя"),
    ("запорож", "Запоріжжя"),
    ("zapor", "Запоріжжя"),
    # Черкаси
    ("черкас", "Черкаси"),
    ("cherkas", "Черкаси"),
)

# STT mutations seen in production. Confidence stays below the apply threshold —
# these are guesses, and the FSM should confirm rather than pin.
# Sources: prompts.py §«ВИНЯТОК ДО STICKY CITY» (calls 2026-07-31, 2026-08-28).
_CITY_FUZZY: tuple[tuple[str, str], ...] = (
    ("запорище", "Запоріжжя"),
    ("затурищ", "Запоріжжя"),
    ("за париже", "Запоріжжя"),
    ("запорить", "Запоріжжя"),
    ("черкассы", "Черкаси"),
    ("черкаси", "Черкаси"),
)

_CITY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(r"\b" + re.escape(stem)), city)
    for stem, city in sorted(_CITY_STEMS, key=lambda kv: -len(kv[0]))
)

_CITY_FUZZY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(re.escape(stem)), city)
    for stem, city in sorted(_CITY_FUZZY, key=lambda kv: -len(kv[0]))
)


# ═══════════════════════════════════════════════════════════
#  Station landmarks
# ═══════════════════════════════════════════════════════════
#
# Callers name a district or a nearby object, never a station_id. The mapping
# below is the same routing knowledge that lives in the prompt's «ЛАНДМАРКИ»
# block (`prompts.py` §Krok 1b) — the ones with a fixed city are the ones the
# prompt calls out explicitly as overriding the profile city.
#
# Ordered longest-stem-first at build time so «жм перемог» wins over «перемог»
# and «харківськ» is tried before the bare city stem «харків».
_LANDMARKS: tuple[tuple[str, str, str | None], ...] = (
    # --- Київ ---
    # Харківське шосе is a Kyiv street, NOT the city of Kharkiv (call 1b6721a4).
    ("харківськ", "Харківське шосе", "Київ"),
    ("харьковск", "Харківське шосе", "Київ"),
    ("харковск", "Харківське шосе", "Київ"),
    ("оболон", "Оболонь", "Київ"),
    ("лукьяненк", "Лукʼяненка", "Київ"),
    ("лукяненк", "Лукʼяненка", "Київ"),
    ("лук'яненк", "Лукʼяненка", "Київ"),
    ("тимошенк", "Тимошенка", "Київ"),
    # --- City-agnostic: named a city the catalog does not back ---
    # «Героїв Дніпра» sat here under Київ, and no station in Київ carries it:
    # the only one that does is 000000007, «м. Черкаси, вул. Героїв Дніпра, 7».
    # The row was broken in both directions — a Kyiv caller (the metro on
    # Оболонь) got a station that does not exist, and a Cherkasy caller got the
    # city overwritten with Київ. Черкаси is not the fix either: pinning a city
    # off a landmark that is a metro station in another one is how b394f6c1
    # happened. With no city the resolver still finds the Cherkasy point on its
    # own whenever the snapshot holds it, and finds nothing in Київ, which is
    # the truth. Prod agrees: every «Героїв Дніпра» in `call_turns` is the bot
    # naming that address — no caller has ever used it as a landmark.
    ("героїв дніпра", "Героїв Дніпра", None),
    # --- Харків ---
    ("холодногірськ", "Холодногірська", "Харків"),
    ("холодногорск", "Холодногірська", "Харків"),
    ("холодна гора", "Холодногірська", "Харків"),
    # --- Дніпро ---
    ("жм перемог", "ЖМ Перемога", "Дніпро"),
    ("речпорт", "Речпорт", "Дніпро"),
    ("донецьке шосе", "Донецьке шосе", "Дніпро"),
    ("донецкое шоссе", "Донецьке шосе", "Дніпро"),
    ("запорізьке шосе", "Запорізьке шосе", "Дніпро"),
    ("запорожское шоссе", "Запорізьке шосе", "Дніпро"),
    ("добровольц", "Добровольців", "Дніпро"),
    ("княгині ольги", "Княгині Ольги", "Дніпро"),
    ("кротов", "Бориса Кротова", "Дніпро"),
    ("придніпровськ", "Придніпровськ", "Дніпро"),
    ("тополь", "Тополь", "Дніпро"),
    ("караван", "Караван", "Дніпро"),
    ("дніпрошин", "Дніпрошина", "Дніпро"),
    # --- Ambivalent: a landmark that exists in more than one city ---
    # «Перемоги» is a street in Запоріжжя AND a whole residential district in
    # Дніпро, and the bare word decides neither. `prompts.py:460` already says
    # so and orders the bot to ASK — three prod regressions (32c14b01,
    # 9c82ce3d, c3c54280) paid for that rule. Pinning Запоріжжя here at 0.9,
    # over a threshold of 0.7, contradicted it: on b394f6c1 the caller went on
    # to say «Днепро» explicitly and lost, because the FSM writes filled fields
    # with `setdefault` (pipeline.py:1227) and the first pin is permanent.
    # The label is kept — it is still the search key for
    # `get_fitting_stations(query=…)`; only the unfounded city is dropped.
    # «жм перемог» above keeps Дніпро and still wins, because patterns are
    # ordered longest-stem-first: a qualifier removes the ambivalence.
    #
    # The prompt reads this landmark three ways, and only the third is settled
    # here: «шосе/вулиця/проспект/набережна Перемоги» is Запоріжжя
    # (`prompts.py:491`, regression 2026-08-05), «Победа-N»/«шоста Перемога» is
    # Дніпро (`:492`), and the bare word is ambivalent (`:493`). No stem row
    # ever encoded the qualifiers, so all three used to collapse onto Запоріжжя
    # and two of them were wrong. They now ask instead. Encoding them needs the
    # whole table matched against the station catalog — see phase 02.
    ("перемог", "Перемоги", None),
    ("перемоз", "Перемоги", None),
    # --- City-agnostic districts (present in more than one city) ---
    ("лівий берег", "Лівий берег", None),
    ("правий берег", "Правий берег", None),
    ("автовокзал", "Автовокзал", None),
)


def _build_landmark_keys() -> dict[str, tuple[str, ...]]:
    """Label → every string worth searching a station's own text for.

    The detector matches a *stem* and reports a canonical *label*, and the two
    are not interchangeable against the catalog. Matched over all 30 rows of
    `_LANDMARKS`, searching by the label alone loses «Бориса Кротова» — every
    field but the unsearched `name` abbreviates it to «вул. Б.Кротова», so only
    the stem «кротов» reaches it — and loses «Лукʼяненка», whose label carries a
    U+02BC apostrophe that appears nowhere.

    **All** stems of a label, not just the one that fired. «лукяненк» is a
    plausible STT form and matches nothing; its sibling «лукьяненк» matches
    `000000006`. Keying the search off whichever stem the caller happened to
    produce would leave that form unresolvable for no reason, and is the naive
    fix this shape exists to avoid.

    The label is kept even though no *current* catalog row needs it: it is the
    only key for 9 of the 21 labels, and dropping it for the 12 that do have
    stems would make the rule depend on hand-edited 1C text staying as it is
    today. `district` is edited in prod — `000000003` has five revisions — so a
    key set tuned to one snapshot of that text rots by construction.
    """
    keys: dict[str, list[str]] = {}
    for stem, label, _city in _LANDMARKS:
        bucket = keys.setdefault(label, [label.lower()])
        if stem not in bucket:
            bucket.append(stem)
    return {label: tuple(bucket) for label, bucket in keys.items()}


_LANDMARK_KEYS: dict[str, tuple[str, ...]] = _build_landmark_keys()

_LANDMARK_PATTERNS: tuple[tuple[re.Pattern[str], str, str | None], ...] = tuple(
    (re.compile(r"\b" + re.escape(stem)), label, city)
    for stem, label, city in sorted(_LANDMARKS, key=lambda t: -len(t[0]))
)


def _detect_station_hint(text: str) -> _Hit | None:
    """Match a district / landmark the caller used to name a station.

    Returns the canonical landmark label — `get_fitting_stations(query=…)`
    resolves it to a `station_id`. Never 1.0: a landmark is a search key, and
    «Перемоги» exists in two cities.
    """
    for pattern, label, _city in _LANDMARK_PATTERNS:
        match = pattern.search(text)
        if match:
            return _Hit(label, 0.9, (match.span(),))
    return None


def _detect_city(text: str) -> _Hit | None:
    """Resolve the city the caller is asking about.

    Order matters and is not arbitrary:

    1. Landmarks first, recording the span they occupy.
    2. Explicit city stems are searched in the text with those spans blanked —
       otherwise «на харьковскому» (Харківське шосе, Kyiv) matches the Kharkiv
       stem and the bot confirms the wrong city, which is exactly what call
       1b6721a4 did.
    3. An explicitly named city outranks a landmark-derived one: if the caller
       says «в Дніпрі на Оболоні» they told us the city, and the cross-city
       guard deals with the mismatch.
    4. Only then do the known STT mutations get a look, at low confidence.
    """
    landmark_city: str | None = None
    landmark_spans: list[tuple[int, int]] = []
    for pattern, _label, city in _LANDMARK_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        landmark_spans.append(match.span())
        if landmark_city is None and city is not None:
            landmark_city = city

    residual = _blank(text, landmark_spans)

    for pattern, city in _CITY_PATTERNS:
        if pattern.search(residual):
            return _Hit(city, 1.0)

    if landmark_city is not None:
        return _Hit(landmark_city, 0.9)

    for pattern, city in _CITY_FUZZY_PATTERNS:
        if pattern.search(residual):
            return _Hit(city, 0.6)

    return None


# ═══════════════════════════════════════════════════════════
#  Date hint
# ═══════════════════════════════════════════════════════════
#
# `date_detect.mentions_date` is the gate — it is deliberately generous and
# already carries the Wave 14 keyword list. This adds the "which day, then?"
# half that it does not do.

# Weekday stems → canonical Ukrainian nominative. Same map the
# `get_fitting_slots` weekday guard uses (`src/main.py`, «_weekday_map»,
# extended with Russian names in Wave 12 after call ebe7dfcb turn 20).
_WEEKDAYS: tuple[tuple[str, str], ...] = (
    ("понеділ", "понеділок"), ("понедельник", "понеділок"),
    ("вівтор", "вівторок"), ("вторник", "вівторок"),
    ("серед", "середа"), ("сред", "середа"),
    ("четвер", "четвер"), ("четвр", "четвер"),
    ("п'ятниц", "п'ятниця"), ("пятниц", "п'ятниця"),
    ("субот", "субота"), ("суббот", "субота"),
    ("неділ", "неділя"), ("воскресен", "неділя"),
)
_WEEKDAY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(r"\b" + re.escape(stem)), label)
    for stem, label in sorted(_WEEKDAYS, key=lambda kv: -len(kv[0]))
)

_RELATIVE_DAYS: tuple[tuple[re.Pattern[str], str], ...] = (
    # «післязавтра» before «завтра» — the former contains the latter.
    (re.compile(r"\b(?:після\s*завтра|післязавтра|после\s*завтра|послезавтра)"), "післязавтра"),
    (re.compile(r"\bзавтра"), "завтра"),
    (re.compile(r"\b(?:сьогодні|сегодня)"), "сьогодні"),
)

_MONTHS_UA = (
    "січня|лютого|березня|квітня|травня|червня|липня|серпня|вересня|жовтня|"
    "листопада|грудня"
)
_MONTHS_RU = (
    "января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|"
    "ноября|декабря"
)
_DAY_MONTH_RE = re.compile(rf"\b(\d{{1,2}})\s+({_MONTHS_UA}|{_MONTHS_RU})\b")
_NUMERIC_DATE_RE = re.compile(r"\b(\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?)\b")

# The caller handing the choice back to us. Real answers («будь-коли», «все
# одно») but not a date — kept below the threshold so DATE still asks.
_VAGUE_DATE_RE = re.compile(
    r"\b(?:найближч|ближайш|будь[-\s]?коли|коли завгодно|все одно|всё равно|"
    r"на ваш розсуд|як вам зручно)"
)


def _detect_date_hint(text: str) -> _Hit | None:
    """Extract the day the caller named, as a hint — no calendar arithmetic.

    Resolution to an ISO date needs "today", the +3-working-day storage rule
    and the 21-day booking window; all three live in the tool layer. Returning
    a raw hint keeps this module a pure function.
    """
    if not mentions_date(text):
        return None

    for pattern, label in _RELATIVE_DAYS:
        match = pattern.search(text)
        if match:
            return _Hit(label, 1.0, (match.span(),))

    match = _DAY_MONTH_RE.search(text)
    if match:
        return _Hit(f"{int(match.group(1))} {match.group(2)}", 0.9, (match.span(),))

    match = _NUMERIC_DATE_RE.search(text)
    if match:
        return _Hit(match.group(1), 0.9, (match.span(),))

    for pattern, label in _WEEKDAY_PATTERNS:
        match = pattern.search(text)
        if match:
            return _Hit(label, 0.9, (match.span(),))

    match = _VAGUE_DATE_RE.search(text)
    if match:
        return _Hit("найближча", 0.6, (match.span(),))

    return None


# ═══════════════════════════════════════════════════════════
#  Time hint
# ═══════════════════════════════════════════════════════════
#
# `time_detect.detect_time_choice` needs `fitting_slots_offered`, which on the
# opening turn is empty — it can pin a slot but not read a bare wish. This
# extractor covers the wish; the slot list still gates the actual booking.

_TIME_TOKEN_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")

# «на 14 годину», «14 годин», «о 9 годині».
_HOUR_DIGIT_WORD_RE = re.compile(r"\b(\d{1,2})\s*(?:годин\w*|год\b)")

# «о 14», «об 11» — the preposition «о/об» before a bare number is a time in
# Ukrainian and never a diameter.
_HOUR_PREP_DIGIT_RE = re.compile(r"\bоб?\s+(\d{1,2})\b")

# «на 1100», «о 1420» — STT collapses «одинадцята нуль-нуль» into one token.
_HOUR_RUN_RE = re.compile(r"\b(?:на|о|об)\s+(\d{3,4})\b")

# Hour spoken as a feminine ordinal: «о шостій», «на чотирнадцяту», «до третьої».
# The endings are the discriminator against the diameter forms — «п'ятнадцять»
# / «п'ятнадцяти» (R15) end in ь/и and are excluded on purpose.
_HOUR_ORDINALS: tuple[tuple[str, int], ...] = (
    ("перш", 1), ("друг", 2), ("трет", 3), ("четверт", 4), ("п'ят", 5),
    ("шост", 6), ("сьом", 7), ("восьм", 8), ("дев'ят", 9), ("десят", 10),
    ("одинадцят", 11), ("дванадцят", 12), ("тринадцят", 13),
    ("чотирнадцят", 14), ("п'ятнадцят", 15), ("шістнадцят", 16),
    ("сімнадцят", 17), ("вісімнадцят", 18), ("дев'ятнадцят", 19),
    ("двадцят", 20),
)
_HOUR_ORDINAL_ALT = "|".join(
    re.escape(stem) for stem, _ in sorted(_HOUR_ORDINALS, key=lambda kv: -len(kv[0]))
)
_HOUR_ORDINAL_ENDING = r"(?:а|у|ої|ьої|ій|ю)"
# «о шостій» / «на чотирнадцяту» — preposition in front …
_HOUR_ORDINAL_PREP_RE = re.compile(
    rf"\b(?:о|об|на|до|після)\s+({_HOUR_ORDINAL_ALT}){_HOUR_ORDINAL_ENDING}\b"
)
# … or «шоста година» — the noun behind.
_HOUR_ORDINAL_NOUN_RE = re.compile(
    rf"\b({_HOUR_ORDINAL_ALT}){_HOUR_ORDINAL_ENDING}\s+годин"
)
_HOUR_ORDINAL_LOOKUP: dict[str, int] = dict(_HOUR_ORDINALS)

_PARTS_OF_DAY: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:після\s*обід|пообід|после\s*обед|в\s*обід|на\s*обід)"), "обід"),
    (re.compile(r"\b(?:зранку|вранці|ранков|на\s*ранок|утром|с\s*утра|ранку)"), "ранок"),
    (re.compile(r"\b(?:ввечері|увечері|вечір|вечор|на\s*вечір|вечером)"), "вечір"),
)

_FITTING_HOUR_MIN = 8
_FITTING_HOUR_MAX = 20


def _detect_time_hint(text: str) -> _Hit | None:
    """Extract the hour (or part of day) the caller asked for.

    Only forms that cannot also be a wheel diameter are trusted above the apply
    threshold. A bare «на 16» is left to the FSM's TIME/PRICE states, which know
    which question they just asked.
    """
    match = _TIME_TOKEN_RE.search(text)
    if match:
        return _Hit(f"{int(match.group(1)):02d}:{match.group(2)}", 1.0, (match.span(),))

    for pattern in (_HOUR_ORDINAL_PREP_RE, _HOUR_ORDINAL_NOUN_RE):
        match = pattern.search(text)
        if not match:
            continue
        hour = _HOUR_ORDINAL_LOOKUP[match.group(1)]
        if hour < _FITTING_HOUR_MIN:
            # «на другу» / «о шостій» — a shop open 08:00-20:00 means 14:00 and
            # 18:00. Callers use the 12-hour form for afternoon slots almost
            # exclusively; the morning reading («о шостій» = 06:00) is outside
            # working hours entirely. Slightly lower confidence for the
            # inference — the TIME state still validates against real slots.
            return _Hit(f"{hour + 12:02d}:00", 0.8, (match.span(),))
        if hour <= _FITTING_HOUR_MAX:
            return _Hit(f"{hour:02d}:00", 0.9, (match.span(),))

    for pattern in (_HOUR_DIGIT_WORD_RE, _HOUR_PREP_DIGIT_RE):
        match = pattern.search(text)
        if match:
            hour = int(match.group(1))
            if _FITTING_HOUR_MIN <= hour <= _FITTING_HOUR_MAX:
                return _Hit(f"{hour:02d}:00", 0.9, (match.span(),))

    match = _HOUR_RUN_RE.search(text)
    if match:
        raw = int(match.group(1))
        hour, minute = divmod(raw, 100)
        if _FITTING_HOUR_MIN <= hour <= _FITTING_HOUR_MAX and minute <= 59:
            return _Hit(f"{hour:02d}:{minute:02d}", 0.8, (match.span(),))

    for pattern, label in _PARTS_OF_DAY:
        match = pattern.search(text)
        if match:
            # A preference, not a time. Below the threshold on purpose.
            return _Hit(label, 0.6, (match.span(),))

    return None


# ═══════════════════════════════════════════════════════════
#  Diameter
# ═══════════════════════════════════════════════════════════

# Marker words that make a number unambiguously a wheel size. Same vocabulary
# `diameter_detect.is_diameter_question` uses on the bot side, plus the «R»
# prefix its `_R_PREFIX_RE` accepts.
_DIAMETER_MARKER_RE = re.compile(
    r"(?:\bR\s*\d|\bэр\s*\d|\bер\s*\d|\bар\s*\d|"
    r"діамет|диамет|розмір|размер|радіус|радиус|дюйм|колес\w*\s+\d)",
    re.IGNORECASE,
)


def _diameter_confidence(text: str) -> float:
    """1.0 with an explicit size marker, 0.6 for a bare number.

    A bare 13-24 is genuinely ambiguous — it is a diameter, an hour and a day of
    the month all at once. Wave 12 added the backend diameter guard precisely
    because the bot quoted «R16 у Києві коштує 354 грн» off such a number
    (calls f70deab5, ebe7dfcb); this module refuses to make the same leap.
    """
    return 1.0 if _DIAMETER_MARKER_RE.search(text) else 0.6


# ═══════════════════════════════════════════════════════════
#  Name
# ═══════════════════════════════════════════════════════════

# ONLY an explicit self-introduction is reported. `name_detect.detect_name` is
# built for the Krok 0 answer — a bare one-word turn right after «Як до вас
# звертатися?» — and the pipeline gates it on `is_name_question(bot_utterance)`
# for exactly that reason. Ungated it accepts «Ммм», «Що?», «Завтра» and
# «Оболонь» as names, and a wrong name is what put «Марина» (the bot's own
# name) into book_fitting in call fcfb26a9. compound_parse has no bot-utterance
# context, so it does not run the ungated path at all; the FSM's own NAME state
# keeps the gated one.
#
# Matched against the ORIGINAL text (case-insensitively) rather than the
# lowercased copy, so the captured slice keeps the capital `detect_name`
# prefers. The name runs to the next punctuation mark — «мене звати Олена,
# білий Nissan, Запоріжжя» must hand `detect_name` "Олена", not the whole
# remaining sentence (which it would reject as "more than two words").
_NAME_INTRO_RE = re.compile(
    r"(?:мене\s+(?:звати|звуть)|мене\s+зовут|меня\s+зовут|"
    r"моє\s+ім['ʼ’`]я|моё\s+имя|мое\s+имя|я\s+[—–-])"
    r"\s+([^,.!?;:\n]{2,40})",
    re.IGNORECASE,
)


def _detect_name(text_raw: str) -> _Hit | None:
    """Extract a customer name from an explicit self-introduction only.

    «мене звати X» / «меня зовут X» / «моє ім'я X» → 0.9. Anything else returns
    None — see the comment above `_NAME_INTRO_RE`.
    """
    match = _NAME_INTRO_RE.search(text_raw)
    if not match:
        return None
    # `detect_name` still applies its own stop-word list, so «мене звати
    # оператор» is rejected here too.
    name = detect_name(match.group(1).strip())
    return _Hit(name, 0.9) if name else None


# ═══════════════════════════════════════════════════════════
#  Public API
# ═══════════════════════════════════════════════════════════


def compound_parse(customer_text: str) -> CompoundParseResult:
    """Extract every field a single utterance carries.

    Sync, deterministic, no I/O. Detectors run in an order that lets the
    date/time matches mask themselves out of the diameter search — «мій білий
    Toyota R17 на Оболоні в понеділок» must yield 17, while «запишіть на 18
    вересня» must yield no diameter at all.

    `intent_hint` is always ``None`` here; classification is Wave 3-A and is
    reached through :func:`compound_parse_with_intent`.
    """
    result = CompoundParseResult()
    if not customer_text or not customer_text.strip():
        return result

    raw = customer_text.strip()
    low = _normalize(raw)

    # --- 1. Location -----------------------------------------------------
    city = _detect_city(low)
    if city:
        result._put("city", city.value, city.confidence)

    station = _detect_station_hint(low)
    if station:
        result._put("station_hint", station.value, station.confidence)

    # --- 2. When ---------------------------------------------------------
    # Both consume digits that would otherwise read as a diameter.
    consumed: list[tuple[int, int]] = []

    date_hit = _detect_date_hint(low)
    if date_hit:
        result._put("date_hint", date_hit.value, date_hit.confidence)
        consumed.extend(date_hit.spans)

    time_hit = _detect_time_hint(low)
    if time_hit:
        result._put("time_hint", time_hit.value, time_hit.confidence)
        consumed.extend(time_hit.spans)

    # --- 3. Vehicle ------------------------------------------------------
    masked = _blank(low, consumed)

    diameter = detect_diameter(masked)
    if diameter is not None:
        result._put("diameter", diameter, _diameter_confidence(masked))

    color = detect_color(low)
    if color:
        # Pattern-based: `color_detect` matches an inflected root, and its
        # false-positive filter (Черкаси, Біла Церква, Сергій…) already ran.
        result._put("color", color, 0.9)

    brand = preparse_fitting(raw).get("brand")
    if brand:
        # Curated whole-word list, but it deliberately contains STT mutations
        # («билл» → BYD, «лікер» → Zeekr), so this is not a 1.0.
        result._put("brand", brand, 0.9)

    # --- 4. Who ----------------------------------------------------------
    name_hit = _detect_name(raw)
    if name_hit:
        result._put("name", name_hit.value, name_hit.confidence)

    if result.fields:
        logger.debug(
            "compound_parse: %d field(s) from %r → %s",
            len(result.fields),
            raw[:80],
            result.fields,
        )
    return result


async def compound_parse_with_intent(
    text: str,
    llm_router: Any = None,
    *,
    classifier: Callable[[str, Any], Awaitable[str | None]] | None = None,
) -> CompoundParseResult:
    """`compound_parse` plus an intent hint from the Wave 3-A classifier.

    The classifier is injected rather than imported: `src/agent/intent_classifier.py`
    does not exist yet and its contract is still being designed, so hard-wiring
    a call here would either break the import or hide a stub behind a
    `try/except ImportError` — the silent-swallow pattern that cost three
    bookings in `37fb2d0`. Wave 4-B passes the real classifier in.

    A classifier failure degrades to the field-only result and is logged at
    WARNING with the traceback; intent is an optimisation, never a call-breaker.
    """
    result = compound_parse(text)

    if classifier is None:
        logger.debug(
            "compound_parse_with_intent: no classifier injected (Wave 3-A pending) "
            "— returning fields only"
        )
        return result

    try:
        result.intent_hint = await classifier(text, llm_router)
    except Exception:
        logger.warning(
            "compound_parse_with_intent: intent classifier failed for %r "
            "— continuing with fields only",
            text[:80],
            exc_info=True,
        )
    return result
