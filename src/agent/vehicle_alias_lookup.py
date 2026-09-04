"""Runtime reverse-lookup: customer utterance → (brand_id, model_id?).

Consumes the vehicle_aliases table populated by scripts/generate_aliases.py
(Wave 8 Phase 2). Called from src/store_client/client.py before pg_trgm
fuzzy fallback so exact hits on hand-curated Cyrillic variants ("Дастер",
"Фольксваген Тігуан") short-circuit the more expensive similarity search.

Ambiguity handling
------------------
If an alias resolves to rows spanning >1 distinct brand (e.g. "500" → Fiat
500 and someone else's 500), ``ResolveResult.ambiguous`` is True and
brand_id is None. The caller must then ask the LLM/customer to
disambiguate — typically by falling back to the Krok 6 vehicle-type prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from src.agent.vehicle_translit import normalize_alias


@dataclass
class ResolveResult:
    """Result of an alias lookup."""

    brand_id: int | None = None
    brand_name: str | None = None
    model_id: int | None = None
    model_name: str | None = None
    ambiguous: bool = False
    ambiguous_matches: list[dict[str, Any]] | None = None
    source: str | None = None  # 'auto_import' | 'auto_translit' | 'auto_model_name' | 'manual'


async def resolve_by_alias(conn: Any, utterance: str) -> ResolveResult:
    """Try to resolve a single customer word/phrase to (brand, model?).

    Query strategy:
    1. Normalize the utterance (lower, ё→е, strip accents, collapse spaces).
    2. SELECT all rows matching alias_normalized exactly, joining brands + models.
    3. If 0 rows → return empty ResolveResult (caller falls back to fuzzy/prompt).
    4. If all rows share the same brand_id:
       - Same model_id (or all model_id NULL) → unambiguous → return.
       - Multiple distinct model_ids under one brand → return brand only,
         model_id=None (still useful — narrows LLM to correct brand).
    5. If rows span multiple brand_ids → ambiguous=True, matches listed for
       optional UI/logging use.
    """
    normalized = normalize_alias(utterance)
    if not normalized:
        return ResolveResult()

    result = await conn.execute(
        text("""
            SELECT
                va.brand_id,
                b.name AS brand_name,
                va.model_id,
                m.name AS model_name,
                va.source
            FROM vehicle_aliases va
            JOIN vehicle_brands b ON b.id = va.brand_id
            LEFT JOIN vehicle_models m ON m.id = va.model_id
            WHERE va.alias_normalized = :norm
        """),
        {"norm": normalized},
    )
    rows = [dict(row._mapping) for row in result]

    if not rows:
        return ResolveResult()

    distinct_brands = {r["brand_id"] for r in rows}

    if len(distinct_brands) > 1:
        return ResolveResult(
            ambiguous=True,
            ambiguous_matches=rows,
        )

    # Single brand. Check model distribution.
    brand_id = rows[0]["brand_id"]
    brand_name = rows[0]["brand_name"]
    distinct_models = {r["model_id"] for r in rows if r["model_id"] is not None}

    if len(distinct_models) == 0:
        # All brand-only aliases (or an alias matched exactly to a brand row).
        return ResolveResult(
            brand_id=brand_id,
            brand_name=brand_name,
            source=rows[0]["source"],
        )

    if len(distinct_models) == 1:
        model_row = next(r for r in rows if r["model_id"] is not None)
        return ResolveResult(
            brand_id=brand_id,
            brand_name=brand_name,
            model_id=model_row["model_id"],
            model_name=model_row["model_name"],
            source=model_row["source"],
        )

    # Same brand but multiple candidate models — hand back brand only so the
    # LLM can ask a follow-up "яка саме модель?" question.
    return ResolveResult(
        brand_id=brand_id,
        brand_name=brand_name,
        ambiguous=True,
        ambiguous_matches=rows,
    )


async def find_brand_by_alias(conn: Any, name: str) -> dict[str, Any] | None:
    """Alias-based brand lookup returning the same shape as _find_vehicle_brand.

    Returns ``{"id": ..., "name": ...}`` on unambiguous hit; None otherwise
    (including ambiguous). Callers should fall back to pg_trgm fuzzy match
    when None is returned.
    """
    res = await resolve_by_alias(conn, name)
    if res.brand_id is None or res.ambiguous:
        return None
    return {"id": res.brand_id, "name": res.brand_name}


async def find_model_by_alias(
    conn: Any, brand_id: int, name: str
) -> dict[str, Any] | None:
    """Alias-based model lookup constrained to a specific brand.

    Same shape as _find_vehicle_model. Returns None when the alias resolves
    to a different brand or no brand — the caller should fall back to
    pg_trgm on vehicle_models filtered by brand_id.
    """
    normalized = normalize_alias(name)
    if not normalized:
        return None

    result = await conn.execute(
        text("""
            SELECT va.model_id, m.name
            FROM vehicle_aliases va
            JOIN vehicle_models m ON m.id = va.model_id
            WHERE va.alias_normalized = :norm
              AND va.brand_id = :bid
              AND va.model_id IS NOT NULL
            LIMIT 2
        """),
        {"norm": normalized, "bid": brand_id},
    )
    rows = [dict(row._mapping) for row in result]

    if len(rows) != 1:
        # 0 = no alias hit; 2 = ambiguous within the brand → let fuzzy try
        return None
    return {"id": rows[0]["model_id"], "name": rows[0]["name"]}
