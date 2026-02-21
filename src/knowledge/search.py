"""Vector search for knowledge base articles.

Uses pgvector cosine similarity for semantic search,
with text fallback when pgvector is unavailable.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class KnowledgeSearch:
    """Semantic search over knowledge base using pgvector.

    Falls back to text search (ILIKE) if pgvector is unavailable.
    """

    def __init__(self, pool: Any, embedding_generator: Any) -> None:
        """Initialize knowledge search.

        Args:
            pool: asyncpg connection pool.
            embedding_generator: EmbeddingGenerator instance for query embedding.
        """
        self._pool = pool
        self._generator = embedding_generator

    async def search(
        self,
        query: str,
        category: str = "",
        limit: int = 5,
        tenant_id: str = "",
    ) -> list[dict[str, Any]]:
        """Search knowledge base by semantic similarity.

        When category is specified, performs a dual search: within the category
        AND across all categories. Results are merged and deduplicated so that
        highly relevant articles from other categories are not missed
        (e.g. free delivery promotions when searching in "delivery").

        Args:
            query: User's question or search query.
            category: Optional category hint. Used for focused search but does
                not exclude results from other categories.
            limit: Maximum number of results (default 5).
            tenant_id: Optional tenant UUID. When set, returns only shared
                (tenant_id IS NULL) and tenant-specific articles.

        Returns:
            List of matching articles with relevance scores.
        """
        if self._generator is None:
            logger.warning("Knowledge search unavailable: embedding generator not configured")
            return []

        try:
            return await self._vector_search_merged(query, category, limit, tenant_id)
        except Exception as exc:
            logger.warning("Vector search failed, falling back to text search: %s", exc)
            return await self._text_search(query, category, limit, tenant_id)

    async def _vector_search_merged(
        self,
        query: str,
        category: str,
        limit: int,
        tenant_id: str = "",
    ) -> list[dict[str, Any]]:
        """Dual vector search: category-focused + cross-category, merged.

        When category is specified, runs two queries in one SQL using UNION:
        one filtered by category and one without. Deduplicates and returns
        top results by relevance, ensuring relevant articles from other
        categories are not missed.
        """
        query_embedding = await self._generator.generate_single(query)
        embedding_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

        if not category:
            return await self._vector_search(query_embedding, embedding_str, "", limit, tenant_id)

        # Dual search: category-focused (3 results) + cross-category (3 results)
        cat_limit = min(limit, 3)
        broad_limit = min(limit, 3)
        return await self._vector_search_dual(
            embedding_str, category, cat_limit, broad_limit, limit, tenant_id
        )

    async def _vector_search_dual(
        self,
        embedding_str: str,
        category: str,
        cat_limit: int,
        broad_limit: int,
        final_limit: int,
        tenant_id: str = "",
    ) -> list[dict[str, Any]]:
        """Execute dual vector search with UNION and deduplication."""
        tenant_filter = ""
        params: list[Any] = [embedding_str, cat_limit, category, broad_limit]
        param_idx = 5

        if tenant_id:
            tenant_filter = f" AND (a.tenant_id IS NULL OR a.tenant_id = ${param_idx}::uuid)"
            params.append(tenant_id)
            param_idx += 1

        sql = f"""
            WITH category_results AS (
                SELECT
                    a.id AS article_id, a.title, a.category,
                    e.chunk_text, e.chunk_index,
                    1 - (e.embedding <=> $1::vector) AS relevance
                FROM knowledge_embeddings e
                JOIN knowledge_articles a ON a.id = e.article_id
                WHERE a.active = true AND a.category = $3{tenant_filter}
                ORDER BY e.embedding <=> $1::vector
                LIMIT $2
            ),
            broad_results AS (
                SELECT
                    a.id AS article_id, a.title, a.category,
                    e.chunk_text, e.chunk_index,
                    1 - (e.embedding <=> $1::vector) AS relevance
                FROM knowledge_embeddings e
                JOIN knowledge_articles a ON a.id = e.article_id
                WHERE a.active = true{tenant_filter}
                ORDER BY e.embedding <=> $1::vector
                LIMIT $4
            ),
            merged AS (
                SELECT * FROM category_results
                UNION
                SELECT * FROM broad_results
            )
            SELECT DISTINCT ON (article_id, chunk_index) *
            FROM merged
            ORDER BY article_id, chunk_index, relevance DESC
        """

        # Re-sort by relevance and apply final limit in Python
        # (DISTINCT ON requires matching ORDER BY prefix)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)

        results = [
            {
                "article_id": str(row["article_id"]),
                "title": row["title"],
                "category": row["category"],
                "content": row["chunk_text"],
                "relevance": round(float(row["relevance"]), 4),
            }
            for row in rows
        ]
        results.sort(key=lambda r: r["relevance"], reverse=True)
        return results[:final_limit]

    async def _vector_search(
        self,
        query_embedding: list[float],
        embedding_str: str,
        category: str,
        limit: int,
        tenant_id: str = "",
    ) -> list[dict[str, Any]]:
        """Perform single vector similarity search using pgvector."""
        extra_filters = ""
        params: list[Any] = [embedding_str, limit]
        param_idx = 3

        if category:
            extra_filters += f" AND a.category = ${param_idx}"
            params.append(category)
            param_idx += 1

        if tenant_id:
            extra_filters += f" AND (a.tenant_id IS NULL OR a.tenant_id = ${param_idx}::uuid)"
            params.append(tenant_id)
            param_idx += 1

        sql = f"""
            SELECT
                a.id AS article_id,
                a.title,
                a.category,
                e.chunk_text,
                e.chunk_index,
                1 - (e.embedding <=> $1::vector) AS relevance
            FROM knowledge_embeddings e
            JOIN knowledge_articles a ON a.id = e.article_id
            WHERE a.active = true{extra_filters}
            ORDER BY e.embedding <=> $1::vector
            LIMIT $2
        """

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)

        return [
            {
                "article_id": str(row["article_id"]),
                "title": row["title"],
                "category": row["category"],
                "content": row["chunk_text"],
                "relevance": round(float(row["relevance"]), 4),
            }
            for row in rows
        ]

    async def _text_search(
        self,
        query: str,
        category: str,
        limit: int,
        tenant_id: str = "",
    ) -> list[dict[str, Any]]:
        """Fallback text search using ILIKE."""
        search_term = f"%{query}%"

        extra_filters = ""
        params: list[Any] = [search_term, limit]
        param_idx = 3

        if category:
            extra_filters += f" AND a.category = ${param_idx}"
            params.append(category)
            param_idx += 1

        if tenant_id:
            extra_filters += f" AND (a.tenant_id IS NULL OR a.tenant_id = ${param_idx}::uuid)"
            params.append(tenant_id)
            param_idx += 1

        sql = f"""
            SELECT
                a.id AS article_id,
                a.title,
                a.category,
                e.chunk_text
            FROM knowledge_embeddings e
            JOIN knowledge_articles a ON a.id = e.article_id
            WHERE a.active = true
              AND (e.chunk_text ILIKE $1 OR a.title ILIKE $1)
              {extra_filters}
            LIMIT $2
        """

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)

        return [
            {
                "article_id": str(row["article_id"]),
                "title": row["title"],
                "category": row["category"],
                "content": row["chunk_text"],
                "relevance": 0.5,  # No relevance score for text search
            }
            for row in rows
        ]
