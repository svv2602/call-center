"""Preview a candidate correction rule against recent call transcripts.

Before a manager promotes a suggestion into a live rule, we run the
proposed pattern against the last ~200 customer turns and split matches
into two buckets:

  * expected   — the match occurred in a turn that preceded a bot
                 re-ask ("повторіть/не розчула"). These are the
                 turns the rule was designed to fix. Green.
  * unexpected — the match occurred in an otherwise successful turn.
                 These are potential false positives — the rule may
                 change the meaning of a legitimate transcript.
                 Yellow, needs manager review.

If unexpected count is non-zero the UI shows a warning; the manager can
still promote but they've been told.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


# Same trigger set as suggestion_engine — keep in sync.
_REASK_PATTERNS = ("%повторіть%", "%не розчула%", "%уточніть%", "%не зрозум%")

# Pull recent customer turns; join to next bot turn to detect re-ask.
# Range predicate on created_at gives partition pruning on call_turns.
_SQL_RECENT_TURNS = """
    WITH recent_customer AS (
        SELECT
            id, call_id, turn_number, content, created_at
        FROM call_turns
        WHERE speaker = 'customer'
          AND created_at > NOW() - make_interval(days => :days)
          AND content IS NOT NULL AND LENGTH(content) > 0
        ORDER BY created_at DESC
        LIMIT :limit
    )
    SELECT
        c.call_id::text AS call_id,
        c.turn_number,
        c.content AS text,
        c.created_at,
        CASE
            WHEN b.content ILIKE :p1
              OR b.content ILIKE :p2
              OR b.content ILIKE :p3
              OR b.content ILIKE :p4
            THEN TRUE ELSE FALSE
        END AS was_followed_by_reask
    FROM recent_customer c
    LEFT JOIN call_turns b
      ON b.call_id = c.call_id
     AND b.turn_number = c.turn_number + 1
     AND b.speaker = 'bot'
     AND b.created_at > NOW() - make_interval(days => :days)
"""


_MAX_MATCHES_PER_BUCKET = 20


async def preview_correction(
    engine: AsyncEngine,
    *,
    pattern: str,
    replacement: str,
    context_hint: str = "",
    days: int = 7,
    limit: int = 500,
) -> dict[str, Any]:
    """Apply pattern to recent customer turns; return match statistics.

    Return shape::

        {
          "scanned": <int>,           # total customer turns scanned
          "expected_count": <int>,    # matches in re-ask preceding turns
          "unexpected_count": <int>,  # matches in successful turns
          "expected": [{call_id, turn_number, before, after, created_at}, ...],
          "unexpected": [...],
          "regex_ok": True,
        }

    On invalid regex returns {"regex_ok": False, "error": "..."}
    without touching the DB.
    """
    from sqlalchemy import text

    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        return {"regex_ok": False, "error": f"invalid regex: {exc}"}

    async with engine.begin() as conn:
        result = await conn.execute(
            text(_SQL_RECENT_TURNS),
            {
                "days": days,
                "limit": limit,
                "p1": _REASK_PATTERNS[0],
                "p2": _REASK_PATTERNS[1],
                "p3": _REASK_PATTERNS[2],
                "p4": _REASK_PATTERNS[3],
            },
        )
        rows = list(result)

    expected: list[dict[str, Any]] = []
    unexpected: list[dict[str, Any]] = []

    for row in rows:
        original = row.text or ""
        # subn returns (new_string, num_replacements)
        replaced, count = compiled.subn(replacement, original)
        if count == 0:
            continue

        entry: dict[str, Any] = {
            "call_id": row.call_id,
            "turn_number": row.turn_number,
            "before": original[:200],
            "after": replaced[:200],
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

        if row.was_followed_by_reask:
            if len(expected) < _MAX_MATCHES_PER_BUCKET:
                expected.append(entry)
        else:
            if len(unexpected) < _MAX_MATCHES_PER_BUCKET:
                unexpected.append(entry)

    total_expected = sum(
        1
        for row in rows
        if row.was_followed_by_reask and compiled.search(row.text or "")
    )
    total_unexpected = sum(
        1
        for row in rows
        if (not row.was_followed_by_reask) and compiled.search(row.text or "")
    )

    logger.info(
        "correction_preview: pattern=%r expected=%d unexpected=%d scanned=%d",
        pattern,
        total_expected,
        total_unexpected,
        len(rows),
    )

    return {
        "regex_ok": True,
        "scanned": len(rows),
        "expected_count": total_expected,
        "unexpected_count": total_unexpected,
        "expected": expected,
        "unexpected": unexpected,
        "context_hint": context_hint,
    }
