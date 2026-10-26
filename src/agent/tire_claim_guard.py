"""Tyre claim guard — what the model says about the offered tyres, before TTS.

A port of the tshina web consultant's answer guards (bef4f5d66, 2026-10-26):
the reply is judged against what ``search_tires`` really showed in this call.

Rules (`RULES`, first match names the refusal):

- ``cheapest`` (`PriceClaimGuard.php`) — «найдешевша / найдоступніша / самая
  дешёвая / мінімальна ціна» about a shown tyre whose price is above the
  lowest shown price. The tyre is the one model the sentence names
  (`tyre_compare.named_items`), or — for «Вона / Ця модель …» — the one model
  the previous sentence named. Silent when it cannot tell: two models named,
  no model at all, a comparison («дешевша, ніж …»), a qualified claim
  («серед преміальних»), the named tyre has no shown price.
- ``novelty`` (`AnswerTextGuards::stripNoveltyClaim`) — «новинка / щойно
  з'явилась / нова модель сезону»: nothing in the result says a model is new.
  Silent when the caller's words this turn mention new tyres («нов…»).
- ``unbacked_rating`` (`AnswerTextGuards::stripUnbackedClaims`) — «переможець
  тестів / лідер рейтингу / №1 / рейтинг 4.7 / у всіх високий рейтинг» with
  no data behind it: no shown tyre carries ``tests`` (filled by the tyre-tests
  checklist) and no shown ``rating`` equals the number said.

- ``eu_class`` (`Dialog/EuClassClaimGuard.php`) — «клас A по мокрій /
  категорія B економії палива / клас шуму B / 71 дБ» about the one named
  tyre, against its ``eu_label`` (``wet`` / ``fuel`` / ``noise_class`` /
  ``noise_db``). Unlike tshina, a tyre without the value is not a reason for
  silence: the class said is made up.
  Silent: no axis named with the letter («має клас C»), a dB range, two
  axes and the letter matches either.
- ``stud`` (`Dialog/StudClaimGuard.php`) — «шипована / з шипами» unless
  ``studded == "studded"``; «липучка / без шипів / фрикційна» about a
  ``"studded"`` tyre; «під шип» unless ``"studdable"``. An item without
  ``studded`` (summer, unknown) is never judged. «Під шип» is cut out before
  «шип» is looked for; two kinds in one sentence («шиповані чи липучка») —
  silent. Stud *questions* out of season are `drop_stud_questions`' job.

``eu_class`` and ``stud`` are silent on any ambiguity, as in tshina: no tyre
or two named, another tyre or a comparison in the sentence («ніж у …», «у
першої»), a question or condition, a negation not part of a form.

- ``restated_verdict`` (wave 8-U) — the model says again the label verdict
  the code has just spoken this turn (`tyre_compare`, «Найкраще на мокрій
  дорозі — Bridgestone Turanza 6 і Firestone Roadhawk 2: клас A; …» followed
  by «Найкраще на мокрій дорозі гальмують Bridgestone Turanza 6…»). The loops
  hand the phrase over with `TireClaimState.note_code_phrase` right where they
  write `compare_said_note`; `begin_turn` forgets it. A restatement is a
  superlative / comparative on an axis the code's phrase covers (wet
  «найкращ / краще» + «мокр / гальм», noise «найтихіш / тихіша», fuel
  «найекономн / економніш», or «клас A» when the phrase has one axis) that
  names a tyre the phrase names. Silent: no phrase this turn, no such word,
  no axis in common (a tests phrase), no tyre of the phrase, a price,
  availability or advice in the sentence («за 3700 гривень», «є в
  наявності», «раджу»), a negation or a question.

``novelty`` and ``unbacked_rating`` are silent in a turn whose knowledge
base result itself backs the claim: an article that says «новинка / нова
модель / з'явилась» backs novelty, one that says «тест / рейтинг / ADAC»
backs a test or rating claim (it does say «один із лідерів тестів ADAC»).
A knowledge base result without those words backs nothing.

Negations and questions pass («не найдешевша», «чи є новіші?»): a negation,
question or condition word before the claim, or a sentence ending in «?».

A refused sentence is *replaced*, never dropped (as in `network_claim_guard`:
an emptied turn is not silence here). ``novelty`` in a cause clause («…, бо
вона щойно з'явилась») loses only that clause; otherwise every rule speaks its
neutral sentence, which carries no new fact. A rule's fixed sentence is said
once per filter — a second copy is dropped.

The one exception is ``restated_verdict``: its sentence is *dropped*, with
no replacement. The rule fires only when the code's phrase was spoken this
turn, so the turn is never empty — the caller has already heard the very
fact the dropped sentence repeats; a replacement would be a third copy.

More rules join `RULES` without touching the loops. The code's own
comparison phrase (`tyre_compare`) never passes the guard: it is spoken
before the stream and added after `guard_tire_claims_text`.

Sales scope only: the loops note results and wrap the stream only under
``sales_enabled``. Switch: env ``TIRE_CLAIM_GUARD_ENABLED`` (default on;
``0/false/no/off`` disables), read at call time.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.agent.tyre_compare import named_items
from src.core.sentence_buffer import SentenceReady
from src.monitoring.metrics import tire_claim_refused_total

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from src.core.sentence_buffer import BufferEvent

logger = logging.getLogger(__name__)

ENV_FLAG = "TIRE_CLAIM_GUARD_ENABLED"

SEARCH_TOOL = "search_tires"
KNOWLEDGE_TOOL = "search_knowledge_base"

RULE_CHEAPEST = "cheapest"
RULE_NOVELTY = "novelty"
RULE_UNBACKED_RATING = "unbacked_rating"
RULE_EU_CLASS = "eu_class"
RULE_STUD = "stud"
RULE_RESTATED_VERDICT = "restated_verdict"

#: Neutral sentences — no fact the code has not checked. ``restated_verdict``
#: has none: its sentence is dropped (the code's phrase was already said).
REPLACEMENTS: dict[str, str] = {
    RULE_CHEAPEST: "Ціни у варіантів різні, можу назвати кожну.",
    RULE_NOVELTY: "Даних про те, коли модель з'явилась, у мене немає.",
    RULE_UNBACKED_RATING: "Даних тестів і рейтингів для цих шин у мене немає.",
    RULE_EU_CLASS: "Класи EU-етикетки цієї шини краще звірити з даними магазину.",
    RULE_STUD: "Чи шипована ця модель, краще звірити з даними магазину.",
}

_L = r"[^\W\d_]"

_CHEAPEST = re.compile(
    rf"(?<!\w)(?:сам{_L}*|наиболее|найбільш)\s+(?:(?:по\s+цен{_L}*|за\s+цін{_L}*)\s+)?"
    rf"(?:доступн|деш[её]в|бюджетн|недорог){_L}*"
    rf"|(?<!\w)(?:сам{_L}*\s+низк|найнижч|найменш){_L}*\s+(?:цен|цін|стоимост|вартіст){_L}*"
    rf"|(?<!\w)наи(?:деш[её]вш|доступнейш|бюджетнейш){_L}*"
    rf"|(?<!\w)най(?:дешевш|доступніш|бюджетніш){_L}*"
    rf"|(?<!\w)(?:минимальн|мінімальн){_L}*\s+(?:цен|цін|стоимост|вартіст){_L}*"
    r"|(?<!\w)(?:дешевле|дешевше)\s+(?:всех|остальных|за\s+(?:всі|усі|інші|решту))(?!\w)",
    re.IGNORECASE,
)
#: Not about all shown tyres («серед преміальних», «у своєму класі»), or a
#: comparison with one of them («дешевша, ніж …»).
_QUALIFIED = re.compile(
    rf"(?<!\w)(?:среди|серед|из|із|з|в|у)\s+(?:сво{_L}*\s+)?"
    rf"(?:премиальн|преміальн|брендов|брендових|топов|сегмент|класс|клас|категор|линейк|лінійк){_L}*"
    r"|(?<!\w)(?:чем|ніж|в\s+отличие|на\s+відміну|по\s+сравнению|порівняно)(?!\w)",
    re.IGNORECASE,
)
#: «Вона …», «Ця модель …» — the tyre the previous sentence named.
_SUBJECT_REFERENCE = re.compile(
    r"^\s*(?:(?:\d{1,2}[.)]|[-*•])[ \t]+)?"
    r"(?:(?:также|тоже|кроме\s+того|також|теж|крім\s+того)\s*,?\s+)?"
    r"(?:она|оно|он|вона|воно|він|"
    rf"(?:эта|этот|данная|данный|ця|цей|дана|даний)\s+(?:модел|шин|вариант|варіант|резин){_L}*)"
    r"(?!\w)",
    re.IGNORECASE,
)

_NOVELTY = re.compile(
    r"(?:только|лишь|недавно|нещодавно|тільки|лише|щойно)(?:-но)?\s+(?:что\s+)?"
    rf"(?:появил|вышл|вышел|поступил|з'явил|вийшл|надійшл){_L}*"
    rf"|(?<!\w)новинк{_L}*"
    rf"|(?<!\w)нов{_L}*\s+(?:модел{_L}*\s+)?на\s+(?:рынке|ринку)"
    rf"|(?:появил|з'явил|вышл|вийшл){_L}*\s+(?:на\s+(?:рынке|ринку|рынок|ринок)\s+)?"
    r"(?:недавно|нещодавно)"
    rf"|(?<!\w)нов(?:а|ая|ий|ый|у|ую)\s+модел{_L}*\s+"
    r"(?:сезону|сезона|цього\s+року|этого\s+года|\d{4}\s+(?:року|года))"
    # «це новіша модель», «новейшая модель», «нова модель» (wave 8-U): a
    # «новіша» about a batch or a date («новіша партія») is not a model's age.
    rf"|(?<!\w)новіш{_L}*+(?!\s+(?:парті|виробн|дат|рок|dot))"
    rf"|(?<!\w)новейш{_L}*\s+модел{_L}*"
    rf"|(?<!\w)нов(?:а|ая|ий|ый|у|ую|ої|ой)\s+модел{_L}*",
    re.IGNORECASE,
)
#: A knowledge base result that itself says a model is new.
_KB_NOVELTY = re.compile(rf"новинк|нов{_L}*\s+модел|з'явил|появил", re.IGNORECASE)
#: A knowledge base result that itself speaks of tests or ratings.
_KB_RATING = re.compile(
    r"тест|рейтинг|переможц|победител|лідер|лидер|adac|auto\s*bild|оцінк|оценк", re.IGNORECASE
)
#: The novelty given as a reason: only that clause goes.
_NOVELTY_CAUSE = re.compile(
    r"\s*(?:,|—|-)?\s*(?:так\s+как|поскольку|потому\s+что|ведь|оскільки|бо|тому\s+що|адже)\s+"
    rf"[^.!?]*?(?:{_NOVELTY.pattern})[^.!?]*",
    re.IGNORECASE,
)
#: The caller asked about new tyres this turn.
_ASKED_NEW = re.compile(
    rf"(?<!\w)нов|(?<!\w)(?:свіж|свеж){_L}*|(?:останн|последн){_L}*\s+(?:модел|випуск|выпуск)",
    re.IGNORECASE,
)

_TEST_CLAIM = re.compile(
    rf"(?<!\w)(?:переможц|переможец|победител){_L}*\s+(?:{_L}+\s+){{0,2}}"
    rf"(?:тест|рейтинг|порівнян|сравнен){_L}*"
    rf"|(?<!\w)(?:виграл|выиграл|перемогл|победил){_L}*\s+(?:{_L}+\s+){{0,2}}(?:тест|рейтинг){_L}*"
    rf"|(?<!\w)(?:найкращ|лучш){_L}*\s+(?:{_L}+\s+)?(?:за|по)\s+(?:результатами\s+)?"
    rf"(?:тест|рейтинг){_L}*"
    rf"|(?<!\w)(?:лідер|лидер){_L}*\s+(?:{_L}+\s+)?(?:тест|рейтинг|продаж){_L}*"
    rf"|(?<!\w)(?:перш|перв){_L}*\s+(?:місц|мест){_L}*\s+(?:у|в|на)\s+(?:{_L}+\s+){{0,2}}"
    rf"(?:тест|рейтинг){_L}*"
    rf"|(?:за|по)\s+(?:результатами|результатам|даними|данным)\s+(?:{_L}+\s+){{0,2}}"
    rf"(?:тест|рейтинг){_L}*"
    # «№1 / топ-3» only as a ranking: «Варіант номер один — …», «ось топ-3
    # варіанти» list the offer, and replacing them loses the tyre and price.
    r"|(?:№|номер)\s*(?:1|один|одна|одне)(?![\w\d])\s+(?:серед|среди|"
    rf"(?:у|в|на|по)\s+(?:{_L}+\s+)?(?:тест|рейтинг|ринк|рынк|продаж){_L}*)"
    r"|(?<!\w)топ[-\s]?(?:1|3|5|10|один|три|п'ять|десять)(?![\w\d])\s+"
    rf"(?:серед|среди|(?:{_L}+\s+){{0,2}}(?:тест|рейтинг|продаж){_L}*)"
    rf"|(?<!\w)(?:у|в)\s+(?:топі|топе)\s+(?:{_L}+\s+)?(?:тест|рейтинг){_L}*",
    # A bare «ADAC» is no claim («ADAC щороку тестує зимові шини»); a claim
    # about it is caught by the forms above («переможець тестів ADAC»).
    re.IGNORECASE,
)
_RATING_NUMBER = re.compile(
    rf"(?:рейтинг|оцінк|оценк){_L}*[^.!?\d]{{0,30}}?(\d[.,]\d{{1,2}})"
    r"|(\d[.,]\d{1,2})\s*(?:з|із|из)\s*(?:5|п'яти|пяти)(?!\d)",
    re.IGNORECASE,
)
_RATING_FIVE_WORDS = re.compile(
    rf"(?:рейтинг|оцінк|оценк){_L}*[^.!?]{{0,40}}(?:з|із|из)\s+(?:п'яти|пяти)(?!\w)",
    re.IGNORECASE,
)
_RATING_ALL = re.compile(
    rf"(?:середн|средн){_L}*\s+(?:рейтинг|оцінк|оценк)"
    rf"|(?<!\w)(?:все|усі|всі)\s+(?:{_L}+\s+){{0,2}}(?:модел|шин|варіант|вариант){_L}*\s+"
    rf"(?:мають|имеют)\s+(?:висок|высок|хорош|добр){_L}*\s+(?:рейтинг|оцінк|оценк|відгук|отзыв)"
    rf"|(?<!\w)у\s+(?:всех|усіх|всіх)\s+(?:моделей\s+)?(?:хорош|висок|высок|добр){_L}*\s+"
    r"(?:оценк|оцінк|рейтинг)",
    re.IGNORECASE,
)

#: A negation, question or condition word before the claim, in its clause,
#: turns it into a denial, a question or a hypothesis («не найдешевша»).
_NOT_A_CLAIM = re.compile(
    r"(?<!\w)(?:не|ні|немає|нема|нет|ни|чи|ли|якщо|если|хіба|разве)(?!\w)", re.IGNORECASE
)
#: Where the claim's own clause starts: a negation in an earlier clause
#: («відгуків немає, бо вона щойно з'явилась») is not the claim's.
_CLAUSE_BREAK = re.compile(
    r"[,;:—–]|\s-\s|(?<!\w)(?:бо|але|проте|оскільки|адже|но|поскольку|ведь)(?!\w)",
    re.IGNORECASE,
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def guard_enabled() -> bool:
    raw = os.environ.get(ENV_FLAG)
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _normalize(text: str) -> str:
    return text.replace("ʼ", "'").replace("’", "'").replace("ё", "е")


def _price(item: dict[str, Any]) -> float | None:
    try:
        value = float(item.get("price"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _key(item: dict[str, Any]) -> tuple[str, str]:
    return (
        str(item.get("brand") or "").strip().lower(),
        str(item.get("model") or "").strip().lower(),
    )


# ── State ───────────────────────────────────────────────────────────────


class TireClaimState:
    """The call's last ``search_tires`` result, whole, and this turn's tools.

    One per loop (one loop per call). `begin_turn` clears the per-turn part:
    the knowledge base flags and the code's phrase of the turn.
    """

    def __init__(self, *, sales_enabled: bool) -> None:
        self._sales_enabled = sales_enabled
        self._items: list[dict[str, Any]] = []
        self.knowledge_used = False
        #: This turn's knowledge base result says a model is new / speaks of tests.
        self.knowledge_novelty = False
        self.knowledge_rating = False
        #: The code's comparison phrase spoken this turn (`tyre_compare`).
        self.code_phrase: str | None = None

    @property
    def items(self) -> list[dict[str, Any]]:
        return list(self._items)

    def begin_turn(self) -> None:
        self.knowledge_used = False
        self.knowledge_novelty = False
        self.knowledge_rating = False
        self.code_phrase = None

    def note_code_phrase(self, phrase: str | None) -> None:
        """The code's own comparison phrase, said this turn before the model."""
        if not self._sales_enabled or not phrase:
            return
        self.code_phrase = phrase

    def note(self, tool: str, raw: Any) -> None:
        """A tool result of this turn (forced or the model's); others are ignored."""
        if not self._sales_enabled:
            return
        if tool == KNOWLEDGE_TOOL:
            self.knowledge_used = True
            text = _normalize(raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False))
            self.knowledge_novelty = self.knowledge_novelty or bool(_KB_NOVELTY.search(text))
            self.knowledge_rating = self.knowledge_rating or bool(_KB_RATING.search(text))
            return
        if tool != SEARCH_TOOL:
            return
        # A refusal or a failure carries no ``items`` and leaves the last one.
        items = raw.get("items") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            return
        self._items = [dict(i) for i in items if isinstance(i, dict) and i.get("brand")]


# ── Rules ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Refusal:
    rule: str
    #: The sentence said instead.
    replacement: str
    #: True for the rule's fixed sentence (said once per filter).
    fixed: bool = True


@dataclass
class ClaimContext:
    """What a rule may read: the shown tyres, the caller's words, the reply so far."""

    items: list[dict[str, Any]]
    user_text: str
    #: This turn's knowledge base result backs a novelty / a test or rating claim.
    knowledge_novelty: bool = False
    knowledge_rating: bool = False
    #: The code's comparison phrase spoken this turn, if any.
    code_phrase: str | None = None
    #: The one model the previous sentence named («Вона …» refers to it).
    previous: dict[str, Any] | None = None
    #: Sentences of this reply already judged (for rules that need them).
    seen: list[str] = field(default_factory=list)


Rule = Callable[[str, ClaimContext], Refusal | None]


def _claim_at(pattern: re.Pattern[str], sentence: str) -> re.Match[str] | None:
    """The first affirmed match: no negation/question word before it, no «?»."""
    if sentence.rstrip().endswith("?"):
        return None
    match = pattern.search(sentence)
    if match is None:
        return None
    head = sentence[: match.start()]
    breaks = list(_CLAUSE_BREAK.finditer(head))
    clause = head[breaks[-1].end() :] if breaks else head
    if _NOT_A_CLAIM.search(clause):
        return None
    return match


def _subject(sentence: str, ctx: ClaimContext) -> dict[str, Any] | None:
    named = named_items(sentence, ctx.items)
    if len(named) == 1:
        return named[0]
    if not named and _SUBJECT_REFERENCE.search(sentence):
        return ctx.previous
    return None


def rule_cheapest(sentence: str, ctx: ClaimContext) -> Refusal | None:
    if _claim_at(_CHEAPEST, sentence) is None or _QUALIFIED.search(sentence):
        return None
    # One shown price is the lowest itself: the comparison never fires.
    prices = [p for p in (_price(i) for i in ctx.items) if p is not None]
    subject = _subject(sentence, ctx)
    if subject is None:
        return None
    own = [p for i in ctx.items if _key(i) == _key(subject) and (p := _price(i)) is not None]
    if not own:
        return None
    if min(own) > min(prices):
        return Refusal(RULE_CHEAPEST, REPLACEMENTS[RULE_CHEAPEST])
    return None


def rule_novelty(sentence: str, ctx: ClaimContext) -> Refusal | None:
    if ctx.knowledge_novelty or _ASKED_NEW.search(_normalize(ctx.user_text)):
        return None
    if _claim_at(_NOVELTY, sentence) is None:
        return None
    cut, count = _NOVELTY_CAUSE.subn("", sentence, count=1)
    if count and re.search(rf"{_L}{{3,}}", cut):
        trimmed = cut.rstrip(" ,—-.!")
        return Refusal(RULE_NOVELTY, trimmed + ".", fixed=False)
    return Refusal(RULE_NOVELTY, REPLACEMENTS[RULE_NOVELTY])


def _rating(item: dict[str, Any]) -> float | None:
    try:
        value = float(item.get("rating"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _rating_backed(value: float, items: list[dict[str, Any]]) -> bool:
    return any(r is not None and abs(r - value) < 0.051 for r in map(_rating, items))


def rule_unbacked_rating(sentence: str, ctx: ClaimContext) -> Refusal | None:
    if ctx.knowledge_rating:
        return None
    refusal = Refusal(RULE_UNBACKED_RATING, REPLACEMENTS[RULE_UNBACKED_RATING])
    if _claim_at(_TEST_CLAIM, sentence) is not None and not any(i.get("tests") for i in ctx.items):
        return refusal
    if _claim_at(_RATING_FIVE_WORDS, sentence) is not None:
        return refusal
    number = _claim_at(_RATING_NUMBER, sentence)
    if number is not None:
        value = float((number.group(1) or number.group(2)).replace(",", "."))
        if not _rating_backed(value, ctx.items):
            return refusal
    all_rated = bool(ctx.items) and all(_rating(i) is not None for i in ctx.items)
    if _claim_at(_RATING_ALL, sentence) is not None and not all_rated:
        return refusal
    return None


def _subject_masked(sentence: str, ctx: ClaimContext) -> tuple[dict[str, Any] | None, str]:
    """The one tyre the sentence is about, and the sentence without its name.

    The brand and model words are blanked so a model letter («Aplus A702»,
    «… S») is not read as a class letter and a brand is not an ordinal.
    """
    subject = _subject(sentence, ctx)
    if subject is None:
        return None, sentence
    masked = sentence
    words = re.split(r"[\s\-‐–]+", f"{subject.get('brand') or ''} {subject.get('model') or ''}")
    for word in sorted({w for w in words if w}, key=len, reverse=True):
        masked = re.sub(rf"(?<![\w]){re.escape(word)}(?![\w])", " ", masked, flags=re.IGNORECASE)
    return subject, masked


#: Another tyre or a comparison in the sentence («ніж у …», «у першої»,
#: «на відміну від …»), or a negation / question / condition anywhere — the
#: claim may not be about the named tyre, or not a claim (`EuClassClaimGuard`
#: and `StudClaimGuard` ``OTHER_REFERENT``). The guard is silent then.
_OTHER_REFERENT = re.compile(
    r"(?<!\w)(?:чем|ніж|против|проти|вместо|замість|заместо)(?!\w)"
    r"|альтернатив|в\s+отличие|на\s+відміну|по\s+сравнению|порівнян"
    r"|(?<!\w)(?:как|як)\s+(?:и\s+|і\s+)?у(?!\w)|тогда\s+как|тоді\s+як"
    r"|в\s+то\s+время|у\s+той\s+час"
    r"|(?<!\w)(?:перв|перш|втор|трет|четв[её]рт|четверт)"
    r"|(?<!\w)друг(?:ая|ой|ую|ий|а|у|ої)(?!\w)"
    r"|(?<!\w)п'?ят(?:а|у|ий|ая|ую|ый|ой)(?!\w)"
    r"|последн|останн|предыдущ|попередн|остальн|(?<!\w)решт"
    r"|(?<!\w)(?:других|інших|другой|іншої|другим|іншим)(?!\w)"
    r"|(?<!\w)(?:обе|обеих|обидв\w*|обох)(?!\w)"
    r"|(?<!\w)(?:чи|ли|якщо|если|хіба|разве)(?!\w)",
    re.IGNORECASE,
)
#: A negation anywhere in the EU sentence («не має класу A») — silent.
_EU_NEGATION = re.compile(r"(?<!\w)(?:не|ни|ні|нет|немає|нема|без)(?!\w)", re.IGNORECASE)

#: The axes of the label, ``eu_label`` key → ua/ru word roots.
_EU_AXES: dict[str, re.Pattern[str]] = {
    "wet": re.compile(r"мокр|сцеплен|зчеплен|тормож|гальм", re.IGNORECASE),
    "fuel": re.compile(
        rf"эконом|економ|расход|витрат|топлив|пальн|паливн|энергоэфф|енергоефект"
        rf"|сопротивлен{_L}*\s+качен|опір{_L}*\s+кочен",
        re.IGNORECASE,
    ),
    # The noise letter (A/B/C) is checked against ``noise_class``
    # (``tire_eu_labels.noise_class``); a label without it backs no letter.
    "noise_class": re.compile(r"(?<!\w)шум|шумн|гучн", re.IGNORECASE),
}
#: A class letter, Latin, standalone and upper case: «клас C», «(C)», not «+7°C».
_EU_LETTER = re.compile(r"(?<![^\W\d_]|[\d\-+°º])([A-G])(?![^\W_]|-)")
#: A Cyrillic twin right after «клас / категорія»: «клас С», «категорія В».
_EU_CYRILLIC_LETTER = re.compile(r"(?<!\w)(?:клас|категор)\w*\s+([АВСЕ])(?!\w|-)", re.IGNORECASE)
_CYRILLIC_TO_LATIN = {"А": "A", "В": "B", "С": "C", "Е": "E"}
#: «72 дБ», «69дБ», «71.5 dB», «71 децибел».
_DB_VALUE = re.compile(
    r"(?<![\d.,\-–])(\d{2}(?:[.,]\d)?)\s?(?:дб|db|децибел\w*)(?!\w)", re.IGNORECASE
)
#: «70–72 дБ» — a range is not checked.
_DB_RANGE = re.compile(r"\d\s?[-–—]\s?\d{2}(?:[.,]\d)?\s?(?:дб|db|децибел)", re.IGNORECASE)


def _eu_letters(text: str) -> set[str]:
    letters = set(_EU_LETTER.findall(text))
    letters.update(_CYRILLIC_TO_LATIN[c] for c in _EU_CYRILLIC_LETTER.findall(text))
    return letters


def rule_eu_class(sentence: str, ctx: ClaimContext) -> Refusal | None:
    """A wet / fuel / noise class or a dB figure the named tyre's label lacks.

    No ``eu_label`` (or no value on that axis) → the value said is made up.
    A letter is refused only when it matches *none* of the axes the sentence
    names (two axes, one letter — no guessing which one it is).
    """
    if sentence.rstrip().endswith("?"):
        return None
    subject, masked = _subject_masked(sentence, ctx)
    if subject is None or _OTHER_REFERENT.search(masked) or _EU_NEGATION.search(masked):
        return None
    label = subject.get("eu_label")
    label = label if isinstance(label, dict) else {}
    refusal = Refusal(RULE_EU_CLASS, REPLACEMENTS[RULE_EU_CLASS])

    said_db = {round(float(v.replace(",", "."))) for v in _DB_VALUE.findall(masked)}
    if len(said_db) == 1 and not _DB_RANGE.search(masked):
        actual = label.get("noise_db")
        if not isinstance(actual, int | float) or round(actual) not in said_db:
            return refusal

    without_db = _DB_VALUE.sub(" ", masked)
    letters = _eu_letters(without_db)
    axes = [axis for axis, pattern in _EU_AXES.items() if pattern.search(without_db)]
    if not letters or not axes:
        return None
    known = {str(label[axis]).strip().upper() for axis in axes if label.get(axis)}
    if letters - known:
        return refusal
    return None


#: ``studded`` of a winter item → what the sentence may say.
_STUDS_STUDDED = "studded"
_STUDS_STUDDABLE = "studdable"
_STUDS_FRICTION = "friction"
#: «під шип», «под шип», «підшип», «під ошиповку».
_CLAIM_STUDDABLE = re.compile(
    rf"(?<!\w)(?:под|під)[\s\-]*(?:шип(?:ы|и)?|ошиповк{_L}*)(?!\w)|(?<!\w)(?:подшип|підшип){_L}*",
    re.IGNORECASE,
)
#: «без шипів», «нешипована», «шипів немає», «липучка», «фрикційна».
_CLAIM_FRICTION = re.compile(
    rf"(?<!\w)без\s+шип{_L}*|(?<!\w)не\s*шип{_L}*|(?<!\w)шип{_L}*\s+(?:нет|немає|нема)(?!\w)"
    rf"|(?<!\w)(?:нет|немає|нема)\s+шип{_L}*|(?<!\w)липучк{_L}*|(?<!\w)фрикц{_L}*",
    re.IGNORECASE,
)
#: «шипи», «шипы», «з шипами», «шипована», «ошипована»; not «шини».
_CLAIM_STUDDED = re.compile(
    rf"(?<!\w)шип(?:ы|и|ов|ів|ам|ами|ах|ован{_L}*|овк{_L}*)?(?!\w)|(?<!\w)ошипован{_L}*",
    re.IGNORECASE,
)
#: A negation left after the forms above are cut — the meaning is unclear.
_STUD_NEGATION = re.compile(r"(?<!\w)(?:не|ни|ні|нет|немає|нема|без)(?!\w)", re.IGNORECASE)


def _stud_claim(masked: str) -> str | None:
    """The one kind of stud claim in the sentence, or ``None`` (none, mixed)."""
    kinds: list[str] = []
    rest = masked
    for kind, pattern in (
        (_STUDS_STUDDABLE, _CLAIM_STUDDABLE),
        (_STUDS_FRICTION, _CLAIM_FRICTION),
        (_STUDS_STUDDED, _CLAIM_STUDDED),
    ):
        rest, count = pattern.subn(" ", rest)
        if count:
            kinds.append(kind)
    if len(kinds) != 1 or _STUD_NEGATION.search(rest):
        return None
    return kinds[0]


def rule_stud(sentence: str, ctx: ClaimContext) -> Refusal | None:
    """«шипована / липучка / під шип» against the named tyre's ``studded``.

    «Шипована» is refused unless ``studded``; «липучка / без шипів» only
    for ``studded`` (a studdable tyre is sold without studs); «під шип»
    unless ``studdable``. No ``studded`` (a summer tyre, unknown) — silent.
    """
    if "шип" not in sentence.lower() and not re.search(r"липучк|фрикц", sentence, re.IGNORECASE):
        return None
    if sentence.rstrip().endswith("?"):
        return None
    subject, masked = _subject_masked(sentence, ctx)
    if subject is None or _OTHER_REFERENT.search(masked):
        return None
    actual = subject.get("studded")
    if actual not in (_STUDS_STUDDED, _STUDS_STUDDABLE, _STUDS_FRICTION):
        return None
    claimed = _stud_claim(masked)
    if claimed is None:
        return None
    # «Без шипів» is true of a studdable tyre too: it is sold without studs.
    wrong = actual == _STUDS_STUDDED if claimed == _STUDS_FRICTION else claimed != actual
    return Refusal(RULE_STUD, REPLACEMENTS[RULE_STUD]) if wrong else None


#: The label axes of a verdict, as the code's phrase and the model word them.
_VERDICT_AXIS_WORDS: dict[str, re.Pattern[str]] = {
    "wet": re.compile(r"мокр|гальм|тормоз|тормож|зчеплен|сцеплен", re.IGNORECASE),
    "fuel": re.compile(r"економ|эконом|палив|пальн|топлив|витрат|расход", re.IGNORECASE),
    "noise": re.compile(
        r"(?<!\w)шум|шумн|(?<!\w)тих(?=[іиоа])|(?<!\w)тиш|(?<!\w)дб(?!\w)|децибел", re.IGNORECASE
    ),
}
#: Words that are a verdict on one axis by themselves.
_VERDICT_OWN_AXIS: dict[str, re.Pattern[str]] = {
    "noise": re.compile(
        rf"(?<!\w)(?:найтих|тихіш|тиш(?:е|ий|ая|ое|і)|самая\s+тих|сам{_L}*\s+тих)"
        rf"|(?<!\w)(?:менш|менее|найменш|наименее)\s+шумн",
        re.IGNORECASE,
    ),
    "fuel": re.compile(
        rf"(?<!\w)(?:найекономн|економніш|экономичне|наиболее\s+экономичн|сам{_L}*\s+экономичн)",
        re.IGNORECASE,
    ),
}
#: «найкраще / краще / лучше» — a verdict on whichever axis the sentence names.
_VERDICT_GENERAL = re.compile(rf"(?<!\w)(?:найкращ|кращ|лучш|наилучш){_L}*", re.IGNORECASE)
#: «клас A» — a verdict on the one axis of the code's phrase.
_VERDICT_CLASS = re.compile(
    r"(?<!\w)(?:клас|класс|категор)\w*\s+(?-i:[A-GАВСЕ])(?!\w)", re.IGNORECASE
)
#: A price, availability or advice: the sentence says more than the verdict.
_VERDICT_MORE = re.compile(
    r"\d{3,}|(?<!\w)(?:грн|гривн\w*|гривень|гривні|гривня)(?!\w)|₴"
    r"|наявн|наличи|в\s+наличии|склад|дешев|дорожч|дороже|найдорож|ціна|ціну|цін[іи]|цена|цену|стоит|коштує"
    r"|раджу|рекоменд|совету|пропоную|предлагаю|оформ|замов|заказ",
    re.IGNORECASE,
)


def _axes(text: str) -> set[str]:
    return {axis for axis, pattern in _VERDICT_AXIS_WORDS.items() if pattern.search(text)}


def _verdict_axes(sentence: str, phrase_axes: set[str]) -> set[str]:
    """The axes ``sentence`` gives a verdict on (affirmed, not a question)."""
    axes = {axis for axis, pattern in _VERDICT_OWN_AXIS.items() if _claim_at(pattern, sentence)}
    if _claim_at(_VERDICT_GENERAL, sentence) is not None:
        axes |= _axes(sentence)
    if _claim_at(_VERDICT_CLASS, sentence) is not None:
        named_axes = _axes(sentence)
        axes |= named_axes or (phrase_axes if len(phrase_axes) == 1 else set())
    return axes


def rule_restated_verdict(sentence: str, ctx: ClaimContext) -> Refusal | None:
    """The code's label verdict of this turn, said again by the model — dropped.

    The one rule whose refusal has no replacement: it fires only when
    ``ctx.code_phrase`` (spoken this turn, before the model) exists, so the
    turn still says the fact. Without the phrase it is silent.
    """
    phrase = ctx.code_phrase
    if not phrase or sentence.rstrip().endswith("?") or _VERDICT_MORE.search(sentence):
        return None
    phrase_axes = _axes(_normalize(phrase))
    if not phrase_axes or not (_verdict_axes(sentence, phrase_axes) & phrase_axes):
        return None
    said = {_key(i) for i in named_items(_normalize(phrase), ctx.items)}
    if not any(_key(i) in said for i in named_items(sentence, ctx.items)):
        return None
    return Refusal(RULE_RESTATED_VERDICT, "", fixed=False)


#: The rule registry: first match wins and names the refusal. New rules
#: are appended here; the loops never change.
RULES: list[tuple[str, Rule]] = [
    (RULE_CHEAPEST, rule_cheapest),
    (RULE_NOVELTY, rule_novelty),
    (RULE_UNBACKED_RATING, rule_unbacked_rating),
    (RULE_EU_CLASS, rule_eu_class),
    (RULE_STUD, rule_stud),
    (RULE_RESTATED_VERDICT, rule_restated_verdict),
]


def check_sentence(sentence: str, ctx: ClaimContext) -> Refusal | None:
    """The first rule ``sentence`` breaks, or ``None``."""
    text = _normalize(sentence).strip()
    if not text:
        return None
    for _name, rule in RULES:
        refusal = rule(text, ctx)
        if refusal is not None:
            return refusal
    return None


def _advance(sentence: str, ctx: ClaimContext) -> None:
    """After a sentence: remember the one model it named (for «Вона …»)."""
    named = named_items(sentence, ctx.items)
    if named:
        ctx.previous = named[0] if len(named) == 1 else None
    ctx.seen.append(sentence)


def _refused(refusal: Refusal, call_id: str, site: str, text: str) -> None:
    tire_claim_refused_total.labels(rule=refusal.rule).inc()
    logger.info(
        "tire_claim_refused: call=%s, site=%s, rule=%s, text=%r",
        call_id,
        site,
        refusal.rule,
        text[:200],
    )


def _context(state: TireClaimState, user_text: str | None) -> ClaimContext:
    ctx = ClaimContext(items=state.items, user_text=user_text or "")
    _refresh(ctx, state)
    return ctx


def _refresh(ctx: ClaimContext, state: TireClaimState) -> None:
    """The state's current result, knowledge base flags and code phrase."""
    ctx.items = state.items
    ctx.knowledge_novelty = state.knowledge_novelty
    ctx.knowledge_rating = state.knowledge_rating
    ctx.code_phrase = state.code_phrase


# ── The text path ───────────────────────────────────────────────────────


def guard_tire_claims_text(
    text: str,
    state: TireClaimState,
    user_text: str | None,
    call_id: str = "unknown",
    site: str = "text_path",
) -> str:
    """The same verdicts on a whole reply (the text loop)."""
    if not guard_enabled() or not text or not text.strip():
        return text
    ctx = _context(state, user_text)
    out: list[str] = []
    spoken: set[str] = set()
    changed = False
    for sentence in _SENTENCE_SPLIT.split(text.strip()):
        refusal = check_sentence(sentence, ctx)
        _advance(sentence, ctx)
        if refusal is None:
            out.append(sentence)
            continue
        changed = True
        _refused(refusal, call_id, site, sentence)
        # ``restated_verdict``: dropped, the code's phrase already said it.
        if not refusal.replacement:
            continue
        if refusal.fixed and refusal.replacement in spoken:
            continue
        spoken.add(refusal.replacement)
        out.append(refusal.replacement)
    return " ".join(out) if changed else text


# ── The stream filter ───────────────────────────────────────────────────


async def guard_tire_claims(
    stream: AsyncIterator[BufferEvent],
    state: TireClaimState,
    user_text: str | None,
    call_id: str = "unknown",
) -> AsyncIterator[BufferEvent]:
    """Replace refused sentences before they reach TTS.

    Fragments are held to the end of the sentence, as in `drop_fit_claims`:
    «Вона не,» and «найдешевша.» judged apart would lose the negation.
    ``state`` is read per sentence — the round's tools are back by then.
    """
    if not guard_enabled():
        async for event in stream:
            yield event
        return

    held: list[SentenceReady] = []
    spoken: set[str] = set()
    ctx: ClaimContext | None = None

    def settle() -> list[SentenceReady]:
        nonlocal held, ctx
        queued = held
        held = []
        if not queued:
            return []
        if ctx is None:
            ctx = _context(state, user_text)
        else:
            _refresh(ctx, state)
        text = " ".join(e.text for e in queued).strip()
        refusal = check_sentence(text, ctx)
        _advance(text, ctx)
        if refusal is None:
            return queued
        _refused(refusal, call_id, "stream", text)
        # ``restated_verdict``: dropped, the code's phrase already said it.
        if not refusal.replacement:
            return []
        if refusal.fixed and refusal.replacement in spoken:
            return []
        spoken.add(refusal.replacement)
        return [SentenceReady(text=refusal.replacement)]

    async for event in stream:
        if isinstance(event, SentenceReady):
            held.append(event)
            if event.text.rstrip().endswith((".", "!", "?")):
                for queued in settle():
                    yield queued
            continue
        for queued in settle():
            yield queued
        yield event

    for queued in settle():
        yield queued
