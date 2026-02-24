"""Knowledge base deduplication checks.

Provides exact title matching and semantic (pgvector cosine) similarity checks
to prevent duplicate articles in the knowledge base.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from src.config import get_settings
from src.knowledge.embeddings import EmbeddingGenerator

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# Similarity thresholds (same as scraper pipeline)
DUPLICATE_THRESHOLD = 0.90
SUSPECT_THRESHOLD = 0.80


async def check_title_exists(
    engine: AsyncEngine,
    title: str,
    *,
    exclude_id: str | None = None,
) -> dict[str, Any] | None:
    """Check if an active article with the same title (case-insensitive) exists.

    Returns:
        {"id": ..., "title": ...} if a match is found, None otherwise.
    """
    params: dict[str, Any] = {"title": title.lower()}
    exclude_clause = ""
    if exclude_id:
        exclude_clause = " AND id != CAST(:exclude_id AS uuid)"
        params["exclude_id"] = exclude_id

    async with engine.begin() as conn:
        result = await conn.execute(
            text(f"""
                SELECT id, title FROM knowledge_articles
                WHERE lower(title) = :title AND active = true{exclude_clause}
                LIMIT 1
            """),
            params,
        )
        row = result.first()

    if row:
        return {"id": str(row.id), "title": row.title}
    return None


async def check_semantic_duplicate(
    engine: AsyncEngine,
    content: str,
    *,
    exclude_id: str | None = None,
) -> dict[str, Any]:
    """Check if content is a semantic duplicate using pgvector cosine similarity.

    Returns:
        {"status": "new"} — no similar articles
        {"status": "duplicate", "similar_title": ..., "similarity": ...} — above 0.90
        {"status": "suspect", "similar_title": ..., "similarity": ...} — 0.80-0.90
    """
    try:
        settings = get_settings()
        api_key = settings.openai.api_key
        if not api_key:
            return {"status": "new"}

        model = settings.openai.embedding_model
        dimensions = settings.openai.embedding_dimensions

        generator = EmbeddingGenerator(api_key=api_key, model=model, dimensions=dimensions)
        await generator.open()
        try:
            vectors = await generator.generate([content[:2000]])
            if not vectors or not vectors[0]:
                return {"status": "new"}
            embedding = vectors[0]
        finally:
            await generator.close()

        vec_str = "[" + ",".join(str(v) for v in embedding) + "]"

        params: dict[str, Any] = {"vec": vec_str}
        exclude_clause = ""
        if exclude_id:
            exclude_clause = " AND ka.id != CAST(:exclude_id AS uuid)"
            params["exclude_id"] = exclude_id

        async with engine.begin() as conn:
            result = await conn.execute(
                text(f"""
                    SELECT ka.title, 1 - (ke.embedding <=> CAST(:vec AS vector)) AS similarity
                    FROM knowledge_embeddings ke
                    JOIN knowledge_articles ka ON ka.id = ke.article_id
                    WHERE ka.active = true{exclude_clause}
                    ORDER BY ke.embedding <=> CAST(:vec AS vector)
                    LIMIT 1
                """),
                params,
            )
            row = result.first()

        if not row:
            return {"status": "new"}

        sim = float(row.similarity)
        similar_title = row.title

        if sim > DUPLICATE_THRESHOLD:
            return {"status": "duplicate", "similar_title": similar_title, "similarity": sim}
        if sim >= SUSPECT_THRESHOLD:
            return {"status": "suspect", "similar_title": similar_title, "similarity": sim}

        return {"status": "new"}

    except Exception:
        logger.warning("Semantic dedup check failed, treating as new article", exc_info=True)
        return {"status": "new"}
