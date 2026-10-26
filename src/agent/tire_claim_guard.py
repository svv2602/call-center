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

``novelty`` and ``unbacked_rating`` are silent in a turn that consulted the
knowledge base: its articles do say «один із лідерів тестів ADAC», and that
is a backed claim the guard cannot tell apart.

Negations and questions pass («не найдешевша», «чи є новіші?»): a negation,
question or condition word before the claim, or a sentence ending in «?».

A refused sentence is *replaced*, never dropped (as in `network_claim_guard`:
an emptied turn is not silence here). ``novelty`` in a cause clause («…, бо
вона щойно з'явилась») loses only that clause; otherwise every rule speaks its
neutral sentence, which carries no new fact. A rule's fixed sentence is said
once per filter — a second copy is dropped.

More rules (EU classes, studs) join `RULES` without touching the loops.

Sales scope only: the loops note results and wrap the stream only under
``sales_enabled``. Switch: env ``TIRE_CLAIM_GUARD_ENABLED`` (default on;
``0/false/no/off`` disables), read at call time.
"""

from __future__ import annotations

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

#: Neutral sentences — no fact the code has not checked.
REPLACEMENTS: dict[str, str] = {
    RULE_CHEAPEST: "Ціни у варіантів різні, можу назвати кожну.",
    RULE_NOVELTY: "Даних про те, коли модель з'явилась, у мене немає.",
    RULE_UNBACKED_RATING: "Даних тестів і рейтингів для цих шин у мене немає.",
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
    r"(?:сезону|сезона|цього\s+року|этого\s+года|\d{4}\s+(?:року|года))",
    re.IGNORECASE,
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

    One per loop (one loop per call). `begin_turn` clears the per-turn part.
    """

    def __init__(self, *, sales_enabled: bool) -> None:
        self._sales_enabled = sales_enabled
        self._items: list[dict[str, Any]] = []
        self.knowledge_used = False

    @property
    def items(self) -> list[dict[str, Any]]:
        return list(self._items)

    def begin_turn(self) -> None:
        self.knowledge_used = False

    def note(self, tool: str, raw: Any) -> None:
        """A tool result of this turn (forced or the model's); others are ignored."""
        if not self._sales_enabled:
            return
        if tool == KNOWLEDGE_TOOL:
            self.knowledge_used = True
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
    knowledge_used: bool = False
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
    if ctx.knowledge_used or _ASKED_NEW.search(_normalize(ctx.user_text)):
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
    if ctx.knowledge_used:
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


#: The rule registry: first match wins and names the refusal. New rules
#: (EU classes, studs) are appended here; the loops never change.
RULES: list[tuple[str, Rule]] = [
    (RULE_CHEAPEST, rule_cheapest),
    (RULE_NOVELTY, rule_novelty),
    (RULE_UNBACKED_RATING, rule_unbacked_rating),
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
    return ClaimContext(
        items=state.items, user_text=user_text or "", knowledge_used=state.knowledge_used
    )


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
            ctx.items = state.items
            ctx.knowledge_used = state.knowledge_used
        text = " ".join(e.text for e in queued).strip()
        refusal = check_sentence(text, ctx)
        _advance(text, ctx)
        if refusal is None:
            return queued
        _refused(refusal, call_id, "stream", text)
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
