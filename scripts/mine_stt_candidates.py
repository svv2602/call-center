"""Mine STT correction candidates from real call transcripts.

Reads recent ``call_turns`` and surfaces user utterances that look like STT
mishears. Two signals:

  1. **Confusion follow-up** — the bot's next reply matches a hedge phrase
     («не зрозумів / повторіть / перепрошую / уточніть / не почув»). The user
     turn right before is a strong mishear candidate.
  2. **Verbatim repetition** — a *user* utterance that shows up 3+ times
     across different calls, character-for-character, is almost always a
     consistent STT error (real speech would vary).

Output is a TSV a human curator can open in a spreadsheet, review, fill
the ``replacement`` column, and hand to ``scripts/apply_stt_corrections.py``
(which POSTs the reviewed rows to the ``/admin/stt/corrections/bulk`` API).

Usage (inside a container that has DATABASE_URL and asyncpg available)::

    python -m scripts.mine_stt_candidates \\
        --days 14 --min-freq 3 --out /tmp/stt_candidates.tsv

    # Filter by tenant slug
    python -m scripts.mine_stt_candidates --days 30 --tenant prokoleso

Columns in the TSV (tab-separated):
    signal, freq, user_text, preceding_bot, next_bot, ctx_guess,
    pattern, replacement, context_hint, enabled, note

The ``pattern`` column is pre-filled with an escaped ``\\bword\\b`` version
of the user text so the curator only needs to type the ``replacement``.
Leave ``replacement`` empty to drop the row on import.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import re
import sys
from collections import Counter, defaultdict
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.config import get_settings

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy.ext.asyncio import AsyncEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Bot hedges that signal the previous user turn was probably mis-heard.
# Kept as a single case-insensitive regex — feel free to expand.
_HEDGE_RE = re.compile(
    r"(не\s+зрозум|не\s+почу|повтор|перепрош|уточн|скажіть\s+ще\s+раз|"
    r"не\s+розчу|повторите|уточните|переспросить)",
    re.IGNORECASE,
)

# Rough hint at pipeline context based on the bot's question wording.
# Mirrors the map in ``src/core/pipeline.py::_infer_context_hint`` but only
# has to be good enough for a human curator to sanity-check.
_CTX_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("date", re.compile(r"(дат|числ|коли\b|день|місяц|липн|серпн|жовтн|листопад|грудн|січн|лютог|березн|квітн|травн|червн)", re.I)),
    ("time", re.compile(r"(годин|о\s+котр|час(и|у)?\b|котру\s+годину)", re.I)),
    ("phone", re.compile(r"(телефон|номер\s+телеф)", re.I)),
    ("plate", re.compile(r"(держном|номерн|автомобіл.*номер)", re.I)),
    ("city", re.compile(r"(місто|у\s+якому\s+місті|населен)", re.I)),
    ("station", re.compile(r"(шиномонтаж|станц|пункт|філі)", re.I)),
)


def _guess_ctx(bot_utterance: str) -> str:
    if not bot_utterance:
        return ""
    for name, pat in _CTX_PATTERNS:
        if pat.search(bot_utterance):
            return name
    return ""


def _escape_for_regex(text_: str) -> str:
    """Return ``\\bescaped\\b`` — sensible default pattern for the curator."""
    return r"\b" + re.escape(text_.strip()) + r"\b"


async def _fetch_turns(
    engine: AsyncEngine, days: int, tenant: str | None
) -> list[dict[str, object]]:
    """Fetch (call_id, turn_number, speaker, content) for the window.

    Ordered by (call_id, turn_number) so the caller can walk each call in
    sequence and find neighbour turns.
    """
    where = ["ct.created_at >= NOW() - make_interval(days => :days)"]
    params: dict[str, object] = {"days": days}

    if tenant:
        where.append("c.tenant_id = (SELECT id FROM tenants WHERE slug = :slug)")
        params["slug"] = tenant

    sql = f"""
        SELECT ct.call_id::text AS call_id,
               ct.turn_number,
               ct.speaker,
               ct.content,
               ct.language
          FROM call_turns ct
          JOIN calls c ON c.id = ct.call_id
         WHERE {" AND ".join(where)}
           AND ct.content IS NOT NULL
           AND ct.content <> ''
      ORDER BY ct.call_id, ct.turn_number
    """

    async with engine.connect() as conn:
        result = await conn.execute(text(sql), params)
        return [dict(r._mapping) for r in result]


def _mine_confusion_pairs(
    turns_by_call: dict[str, list[dict[str, object]]],
) -> list[dict[str, object]]:
    """For each call, find bot turns that match the hedge regex and grab
    the user turn immediately before them.
    """
    out: list[dict[str, object]] = []
    for turns in turns_by_call.values():
        for i, turn in enumerate(turns):
            if turn["speaker"] != "bot":
                continue
            content = str(turn["content"] or "")
            if not _HEDGE_RE.search(content):
                continue

            # Find the most recent user turn before this bot turn
            user_idx = None
            for j in range(i - 1, -1, -1):
                if turns[j]["speaker"] == "user":
                    user_idx = j
                    break
            if user_idx is None:
                continue

            preceding_bot = ""
            for j in range(user_idx - 1, -1, -1):
                if turns[j]["speaker"] == "bot":
                    preceding_bot = str(turns[j]["content"] or "")
                    break

            user_text = str(turns[user_idx]["content"] or "").strip()
            if not user_text:
                continue

            out.append(
                {
                    "signal": "confusion",
                    "user_text": user_text,
                    "preceding_bot": preceding_bot,
                    "next_bot": content,
                    "ctx_guess": _guess_ctx(preceding_bot),
                }
            )
    return out


def _mine_frequent_verbatims(
    turns_by_call: dict[str, list[dict[str, object]]],
    min_freq: int,
) -> list[dict[str, object]]:
    """Verbatim user utterances that appear ``min_freq``+ times across
    *different* calls. Same utterance repeated within one call counts once —
    otherwise a stuck-record caller pollutes the signal.
    """
    seen: defaultdict[str, set[str]] = defaultdict(set)
    context_examples: dict[str, tuple[str, str]] = {}

    for call_id, turns in turns_by_call.items():
        for i, turn in enumerate(turns):
            if turn["speaker"] != "user":
                continue
            content = str(turn["content"] or "").strip()
            if not content or len(content) > 120:
                continue

            seen[content].add(call_id)
            if content not in context_examples:
                preceding_bot = ""
                for j in range(i - 1, -1, -1):
                    if turns[j]["speaker"] == "bot":
                        preceding_bot = str(turns[j]["content"] or "")
                        break
                next_bot = ""
                for j in range(i + 1, len(turns)):
                    if turns[j]["speaker"] == "bot":
                        next_bot = str(turns[j]["content"] or "")
                        break
                context_examples[content] = (preceding_bot, next_bot)

    counter = Counter({text_: len(calls) for text_, calls in seen.items()})
    out: list[dict[str, object]] = []
    for user_text, freq in counter.most_common():
        if freq < min_freq:
            break
        preceding_bot, next_bot = context_examples.get(user_text, ("", ""))
        out.append(
            {
                "signal": "verbatim",
                "freq": freq,
                "user_text": user_text,
                "preceding_bot": preceding_bot,
                "next_bot": next_bot,
                "ctx_guess": _guess_ctx(preceding_bot),
            }
        )
    return out


def _merge_and_dedup(
    confusion: Iterable[dict[str, object]],
    verbatim: Iterable[dict[str, object]],
) -> list[dict[str, object]]:
    """Combine both signals, dedup by user_text.

    Verbatim wins as the primary source (has freq), but confusion is folded
    in — a row that appears in both is stronger, so freq gets a small boost.
    """
    by_text: dict[str, dict[str, object]] = {}
    for row in verbatim:
        by_text[str(row["user_text"])] = dict(row)

    for row in confusion:
        text_ = str(row["user_text"])
        if text_ in by_text:
            by_text[text_]["signal"] = "verbatim+confusion"
            by_text[text_]["freq"] = int(by_text[text_].get("freq", 0)) + 1
        else:
            row = dict(row)
            row.setdefault("freq", 1)
            by_text[text_] = row

    rows = sorted(
        by_text.values(),
        key=lambda r: (int(r.get("freq", 0)), str(r.get("user_text", ""))),
        reverse=True,
    )
    return rows


def _write_tsv(rows: list[dict[str, object]], out_path: str) -> None:
    fields = [
        "signal",
        "freq",
        "user_text",
        "preceding_bot",
        "next_bot",
        "ctx_guess",
        "pattern",
        "replacement",
        "context_hint",
        "enabled",
        "note",
    ]
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=fields, delimiter="\t", quoting=csv.QUOTE_MINIMAL
        )
        writer.writeheader()
        for row in rows:
            user_text = str(row.get("user_text", ""))
            writer.writerow(
                {
                    "signal": row.get("signal", ""),
                    "freq": row.get("freq", 1),
                    "user_text": user_text,
                    "preceding_bot": row.get("preceding_bot", ""),
                    "next_bot": row.get("next_bot", ""),
                    "ctx_guess": row.get("ctx_guess", ""),
                    "pattern": _escape_for_regex(user_text),
                    "replacement": "",
                    "context_hint": row.get("ctx_guess", ""),
                    "enabled": "true",
                    "note": f"mined {row.get('signal', '')} freq={row.get('freq', 1)}",
                }
            )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=14, help="lookback window")
    parser.add_argument(
        "--min-freq",
        type=int,
        default=3,
        help="minimum distinct-call count for the verbatim signal",
    )
    parser.add_argument(
        "--tenant", type=str, default=None, help="tenant slug filter (optional)"
    )
    parser.add_argument(
        "--out",
        type=str,
        default="stt_candidates.tsv",
        help="output TSV path",
    )
    args = parser.parse_args()

    settings = get_settings()
    engine = create_async_engine(settings.database.url)
    try:
        turns = await _fetch_turns(engine, args.days, args.tenant)
    finally:
        await engine.dispose()

    logger.info("fetched %d turns from last %d days", len(turns), args.days)
    if not turns:
        logger.warning("no turns found — nothing to mine")
        _write_tsv([], args.out)
        return

    by_call: defaultdict[str, list[dict[str, object]]] = defaultdict(list)
    for row in turns:
        by_call[str(row["call_id"])].append(row)

    confusion = _mine_confusion_pairs(by_call)
    verbatim = _mine_frequent_verbatims(by_call, args.min_freq)
    logger.info(
        "signals: confusion=%d, verbatim (freq>=%d)=%d",
        len(confusion),
        args.min_freq,
        len(verbatim),
    )

    rows = _merge_and_dedup(confusion, verbatim)
    _write_tsv(rows, args.out)
    logger.info("wrote %d candidates to %s", len(rows), args.out)
    logger.info(
        "next: open %s in a spreadsheet, fill the `replacement` column, "
        "then run: python -m scripts.apply_stt_corrections --file %s --url ... --token ...",
        args.out,
        args.out,
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
