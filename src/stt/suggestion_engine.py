"""Auto-suggest new STT correction rules from historical call_turns.

Approach
--------
1. Find pairs where the bot's next reply was a re-ask ("повторіть",
   "не розчула", "уточніть", "не зрозум"). The preceding customer turn is
   treated as a confirmed STT failure — the LLM couldn't make sense of it.

2. Tokenise those failed transcripts and drop tokens that are known-good:
   base + auto phrase hints, curated Ukrainian stopwords, digits, and very
   short tokens. What's left is a candidate — a token the STT keeps
   producing that isn't in any vocabulary.

3. Group candidates by (token, detected_context). The context is inferred
   from the bot's *question* that preceded the failed turn ("держномер"
   → plate, "яка дата" → date, ...). Rules in stt:corrections are already
   context-scoped, so keeping the same axis avoids over-firing.

4. For each cluster, try a Damerau-Levenshtein match against a
   context-specific candidate list (days for date, cities for address,
   brands for tire). If a close match is found, pre-populate the suggested
   replacement — otherwise leave it null for the content-manager to fill.

5. Upsert into stt_correction_suggestions. On repeated scans the
   occurrence_count grows and sample_transcripts is refreshed. Already
   promoted / rejected entries are left alone.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# ─── Trigger patterns: bot signals it didn't understand ────
_REASK_PATTERNS = (
    "%повторіть%",
    "%не розчула%",
    "%уточніть%",
    "%не зрозум%",
)

# ─── Context classifier: bot's question → domain ──────────
_CONTEXT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "plate",
        re.compile(r"держ.?номер|номер\s+авт|номер\s+машин", re.IGNORECASE),
    ),
    (
        "date",
        re.compile(
            r"\bдат[ауи]|числ[оа]|\bдень\b|коли\s+запис|який\s+день|"
            r"якого\s+числ",
            re.IGNORECASE,
        ),
    ),
    (
        "time",
        re.compile(
            r"\bчас\b|о\s+котр|о\s+якій\s+годин|\d{1,2}:\d{2}", re.IGNORECASE
        ),
    ),
    (
        "address",
        re.compile(
            r"район|адрес|вулиц|місто|звідки|де\s+ви|орієнтир|яхтклуб",
            re.IGNORECASE,
        ),
    ),
    (
        "tire_size",
        re.compile(r"розмір|шин[ауи]|\bR\d{2}|модель\s+шин", re.IGNORECASE),
    ),
    (
        "name",
        re.compile(r"як\s+вас\s+звати|ваше\s+ім|представт", re.IGNORECASE),
    ),
)


def detect_context(bot_question: str | None) -> str:
    """Classify the bot's question into a context tag.

    Falls back to 'any' when the question is missing or matches nothing —
    'any' means the resulting rule will apply regardless of context.
    """
    if not bot_question:
        return "any"
    for tag, pattern in _CONTEXT_RULES:
        if pattern.search(bot_question):
            return tag
    return "any"


# ─── Tokeniser ────────────────────────────────────────────
# Keep letters and apostrophes; treat everything else as a separator.
_TOKEN_SPLIT = re.compile(r"[^а-яА-ЯіїєґІЇЄҐа-яa-zA-Zʼʹ'’]+")
_HAS_DIGIT = re.compile(r"\d")
_MIN_TOKEN_LEN = 4


def tokenize(text: str) -> list[str]:
    """Split into lowercase word tokens suitable for vocabulary lookup.

    Filters: length < MIN, contains digit, or leading/trailing apostrophes
    only. Deduplicates while preserving order.
    """
    if not text:
        return []
    tokens: list[str] = []
    seen: set[str] = set()
    for raw in _TOKEN_SPLIT.split(text.lower()):
        t = raw.strip("'’ʼʹ-")
        if len(t) < _MIN_TOKEN_LEN or _HAS_DIGIT.search(t):
            continue
        if t in seen:
            continue
        seen.add(t)
        tokens.append(t)
    return tokens


# ─── Known-good vocabulary ────────────────────────────────
# Common Ukrainian / Russian dialogue words. Any token in here is treated
# as legitimate and never suggested as a correction candidate. Kept small
# and hand-picked — the risk of a false negative (missing a real error) is
# low because the same error tends to recur across many calls; the risk of
# a false positive (surfacing a normal word as a suggestion) is annoying
# for the content-manager, so we err on the side of over-filtering here.
_UA_STOPWORDS: frozenset[str] = frozenset(
    """
    алло також так само тоді треба буде добре ласка дякую вибачте
    перепрошую доброго вечора ранку потрібно потребую хочу можу можна
    тобто коли куди звідки чому який яка яке які цей ця той тая
    вам вас мене мною ним нею вами добрий день щось нічого ніякий
    приблизно приблизна нормально нормальна
    можливо ймовірно напевно навіть тільки взагалі наприклад справді
    зараз пізніше раніше сьогодні завтра післязавтра вчора позавчора
    ранок вечір якийсь якась
    записати запишіть запис запису замовити замовлення замовлюю
    підбір купити купую придбати монтаж шиномонтаж переобути
    можете хочете знаєте розумієте почули чули говорите слухайте
    автомобіль автомобіля машина машину машині номер номером
    марка модель розмір розміру діаметр висота ширина
    точно звичайно
    гаразд чудово прекрасно згоден згодна погоджуюсь
    зателефонуйте передзвоніть зв'яжіться напишіть надішліть
    оператор оператора менеджер менеджера консультант
    """.split()  # noqa: SIM905
)

# Russian equivalents seen in real transcripts (~50% of calls are ru-RU).
_RU_STOPWORDS: frozenset[str] = frozenset(
    """
    алло здравствуйте добрый день вечер утро ночь спасибо пожалуйста
    извините хорошо ладно понятно давайте нужно нужен нужна нужны
    хочу хочет хотим можно могу может можем нельзя надо
    есть нету имеется имеются найдётся найдется скажите узнать
    записать запишите запись запиши заказать заказ подобрать выбрать
    сегодня завтра послезавтра вчера позавчера утром вечером днём
    приблизительно точно около примерно наверное возможно конечно
    машина машину машины автомобиль автомобилем номер номера
    марка модель размер диаметр ширина высота
    оператор менеджер консультант перезвоните позвоните
    """.split()  # noqa: SIM905
)


async def build_known_vocab(redis: Redis) -> frozenset[str]:
    """Union of phrase hints + curated stopwords, all lower-case."""
    from src.stt.phrase_hints import get_phrase_hints

    vocab: set[str] = set()
    try:
        data = await get_phrase_hints(redis)
        for bucket in ("base", "auto", "custom"):
            for phrase in data.get(bucket, []):
                for tok in tokenize(phrase):
                    vocab.add(tok)
    except Exception:
        logger.warning("suggestion_engine: failed to load phrase hints", exc_info=True)

    vocab.update(_UA_STOPWORDS)
    vocab.update(_RU_STOPWORDS)
    return frozenset(vocab)


# ─── Damerau-Levenshtein for replacement suggestion ───────


def _dam_lev(a: str, b: str) -> int:
    """Damerau-Levenshtein distance (with adjacent transpositions).

    Rolling three-row implementation:
      d0 — row i-1 (previous)
      d1 — row i   (current, being filled)
      d2 — row i-2 (only used for the transposition step; unused at i=1)
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    la, lb = len(a), len(b)
    d0 = list(range(lb + 1))  # base row (i=0)
    d1 = [0] * (lb + 1)
    d2 = [0] * (lb + 1)

    for i in range(1, la + 1):
        d1[0] = i
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d1[j] = min(
                d1[j - 1] + 1,        # insertion
                d0[j] + 1,             # deletion
                d0[j - 1] + cost,      # substitution
            )
            if (
                i > 1
                and j > 1
                and a[i - 1] == b[j - 2]
                and a[i - 2] == b[j - 1]
            ):
                d1[j] = min(d1[j], d2[j - 2] + cost)  # transposition
        # Rotate: the old d0 becomes d2, d1 becomes new d0, old d2 is scratch d1
        d2, d0, d1 = d0, d1, d2

    return d0[lb]


# Context-specific candidate lists for fuzzy replacement matching.
_CANDIDATES_BY_CONTEXT: dict[str, tuple[str, ...]] = {
    "date": (
        "понеділок", "вівторок", "середу", "четвер", "пʼятницю", "суботу", "неділю",
        "січня", "лютого", "березня", "квітня", "травня", "червня",
        "липня", "серпня", "вересня", "жовтня", "листопада", "грудня",
        "завтра", "післязавтра", "сьогодні",
    ),
    "time": (
        "восьма", "девʼята", "десята", "одинадцята", "дванадцята",
        "тринадцята", "чотирнадцята", "пʼятнадцята", "шістнадцята",
        "сімнадцята", "вісімнадцята", "ранку", "обід", "вечора",
    ),
}


def _lazy_candidates_for(context: str) -> tuple[str, ...]:
    """Build candidates lazily to avoid circular imports at module load."""
    if context in _CANDIDATES_BY_CONTEXT:
        return _CANDIDATES_BY_CONTEXT[context]
    from src.stt.phrase_hints import (
        BASE_CITIES,
        BRAND_PRONUNCIATIONS,
    )
    if context == "address":
        return tuple(BASE_CITIES)
    if context == "tire_size":
        out: list[str] = []
        for pronunciations in BRAND_PRONUNCIATIONS.values():
            out.extend(pronunciations)
        return tuple(out)
    return ()


def suggest_replacement(bad_token: str, context: str) -> tuple[str | None, int | None]:
    """Return the best fuzzy candidate for `bad_token` in `context`, or None.

    A candidate is accepted when distance <= max(2, 30% of shorter length).
    Returns (replacement, distance) or (None, None) when no candidate is
    close enough or the context has no dictionary.
    """
    candidates = _lazy_candidates_for(context)
    if not candidates:
        return None, None

    bad_lower = bad_token.lower()
    best: tuple[int, str] | None = None
    for cand in candidates:
        cand_lower = cand.lower()
        dist = _dam_lev(bad_lower, cand_lower)
        if best is None or dist < best[0]:
            best = (dist, cand)

    if best is None:
        return None, None

    dist, replacement = best
    threshold = max(2, int(0.3 * min(len(bad_token), len(replacement))))
    if dist > threshold:
        return None, None
    return replacement, dist


# ─── Main scan ────────────────────────────────────────────
# call_turns is partitioned by created_at; the scan only ever looks back a
# short window so a range predicate on created_at gives partition pruning.

_SQL_FETCH_PAIRS = """
    WITH bot_reasks AS (
        SELECT call_id, turn_number, content AS bot_reask_text, created_at
        FROM call_turns
        WHERE speaker = 'bot'
          AND created_at > NOW() - make_interval(days => :days)
          AND (
              content ILIKE :p1 OR content ILIKE :p2
              OR content ILIKE :p3 OR content ILIKE :p4
          )
    )
    SELECT
        c.call_id::text,
        c.turn_number,
        c.content AS customer_text,
        c.language AS customer_lang,
        c.stt_confidence AS customer_conf,
        c.created_at,
        prev_bot.content AS bot_question_text
    FROM bot_reasks b
    JOIN call_turns c
      ON c.call_id = b.call_id
     AND c.turn_number = b.turn_number - 1
     AND c.speaker = 'customer'
     AND c.created_at > NOW() - make_interval(days => :days)
    LEFT JOIN call_turns prev_bot
      ON prev_bot.call_id = b.call_id
     AND prev_bot.turn_number = c.turn_number - 1
     AND prev_bot.speaker = 'bot'
     AND prev_bot.created_at > NOW() - make_interval(days => :days)
    WHERE c.content IS NOT NULL AND LENGTH(c.content) > 0
"""


async def _fetch_re_ask_pairs(engine: AsyncEngine, days: int) -> list[dict[str, Any]]:
    from sqlalchemy import text

    async with engine.begin() as conn:
        result = await conn.execute(
            text(_SQL_FETCH_PAIRS),
            {
                "days": days,
                "p1": _REASK_PATTERNS[0],
                "p2": _REASK_PATTERNS[1],
                "p3": _REASK_PATTERNS[2],
                "p4": _REASK_PATTERNS[3],
            },
        )
        return [dict(row._mapping) for row in result]


_SQL_UPSERT = """
    INSERT INTO stt_correction_suggestions (
        bad_token, detected_context, occurrence_count,
        sample_transcripts, proposed_pattern, proposed_replacement,
        match_distance, first_seen_at, last_seen_at
    )
    VALUES (
        :bad_token, :context, :count,
        CAST(:samples AS jsonb), :pattern, :replacement,
        :distance, NOW(), NOW()
    )
    ON CONFLICT (bad_token, detected_context) DO UPDATE
    SET
        occurrence_count = stt_correction_suggestions.occurrence_count + EXCLUDED.occurrence_count,
        sample_transcripts = EXCLUDED.sample_transcripts,
        last_seen_at = NOW(),
        -- Refresh proposed_replacement only if we now have one and had none before
        proposed_replacement = COALESCE(
            stt_correction_suggestions.proposed_replacement,
            EXCLUDED.proposed_replacement
        ),
        match_distance = COALESCE(
            stt_correction_suggestions.match_distance,
            EXCLUDED.match_distance
        )
    WHERE stt_correction_suggestions.status = 'pending'
"""


async def _upsert_suggestion(
    engine: AsyncEngine, proposal: dict[str, Any]
) -> None:
    import json

    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.execute(
            text(_SQL_UPSERT),
            {
                "bad_token": proposal["bad_token"],
                "context": proposal["context"],
                "count": proposal["count"],
                "samples": json.dumps(proposal["samples"], ensure_ascii=False),
                "pattern": proposal["pattern"],
                "replacement": proposal["replacement"],
                "distance": proposal["distance"],
            },
        )


_MAX_SAMPLES_PER_CLUSTER = 5


async def scan_for_suggestions(
    engine: AsyncEngine,
    redis: Redis,
    *,
    days: int = 30,
    min_occurrences: int = 2,
) -> dict[str, Any]:
    """Scan recent call_turns and upsert new correction suggestions.

    Idempotent — running twice in a row bumps counters but doesn't create
    duplicates. Returns a stats dict useful for logs and API responses.
    """
    pairs = await _fetch_re_ask_pairs(engine, days=days)
    known_vocab = await build_known_vocab(redis)

    # Cluster: (token, context) → { count, samples[] }
    clusters: dict[tuple[str, str], dict[str, Any]] = {}

    for pair in pairs:
        customer_text = pair["customer_text"] or ""
        bot_question = pair["bot_question_text"]
        context = detect_context(bot_question)
        tokens = tokenize(customer_text)

        for token in tokens:
            if token in known_vocab:
                continue

            key = (token, context)
            entry = clusters.get(key)
            if entry is None:
                entry = {"count": 0, "samples": []}
                clusters[key] = entry
            entry["count"] += 1
            if len(entry["samples"]) < _MAX_SAMPLES_PER_CLUSTER:
                entry["samples"].append(
                    {
                        "call_id": pair["call_id"],
                        "turn_number": pair["turn_number"],
                        "text": customer_text[:200],
                        "bot_question": (bot_question or "")[:200],
                    }
                )

    # Build proposals for clusters that clear the threshold
    from src.monitoring.metrics import stt_correction_suggestions_upserted_total

    upserted = 0
    below_threshold = 0
    for (token, context), entry in clusters.items():
        if entry["count"] < min_occurrences:
            below_threshold += 1
            continue

        replacement, distance = suggest_replacement(token, context)
        pattern = rf"\b{re.escape(token)}\b"

        await _upsert_suggestion(
            engine,
            {
                "bad_token": token,
                "context": context,
                "count": entry["count"],
                "samples": entry["samples"],
                "pattern": pattern,
                "replacement": replacement,
                "distance": distance,
            },
        )
        upserted += 1
        stt_correction_suggestions_upserted_total.labels(context=context).inc()

    stats = {
        "pairs_scanned": len(pairs),
        "clusters_total": len(clusters),
        "clusters_upserted": upserted,
        "clusters_below_threshold": below_threshold,
        "known_vocab_size": len(known_vocab),
        "window_days": days,
        "min_occurrences": min_occurrences,
    }
    logger.info("stt suggestion scan complete: %s", stats)
    return stats
