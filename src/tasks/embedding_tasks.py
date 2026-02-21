"""Celery tasks for knowledge base embedding generation.

Generates embeddings when articles are created/updated,
and supports bulk reindexing of all articles.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.config import get_settings
from src.knowledge.embeddings import EmbeddingGenerator, process_article
from src.tasks.celery_app import app

logger = logging.getLogger(__name__)


@app.task(
    name="src.tasks.embedding_tasks.generate_article_embeddings",
    bind=True,
    max_retries=3,
    soft_time_limit=120,
    time_limit=150,
)  # type: ignore[untyped-decorator]
def generate_article_embeddings(self: Any, article_id: str) -> dict[str, Any]:
    """Generate embeddings for a single article.

    Args:
        article_id: UUID of the article to process.

    Returns:
        Result dict with article_id, chunks count, and status.
    """
    import asyncio

    return asyncio.run(
        _generate_article_embeddings_async(self, article_id)
    )


async def _generate_article_embeddings_async(task: Any, article_id: str) -> dict[str, Any]:
    """Async implementation of embedding generation."""
    settings = get_settings()
    engine = create_async_engine(settings.database.url, pool_pre_ping=True)

    try:
        # Set status to processing
        async with engine.begin() as conn:
            await conn.execute(
                text("""
                    UPDATE knowledge_articles
                    SET embedding_status = 'processing'
                    WHERE id = :id
                """),
                {"id": article_id},
            )

        # Fetch article content
        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    SELECT id, title, content
                    FROM knowledge_articles
                    WHERE id = :id AND active = true
                """),
                {"id": article_id},
            )
            row = result.first()

        if not row:
            logger.warning("Article %s not found or inactive, skipping embedding", article_id)
            return {"article_id": article_id, "error": "not_found"}

        # Get embedding config
        api_key = settings.openai.api_key
        model = settings.openai.embedding_model
        dimensions = settings.openai.embedding_dimensions

        # Generate embeddings
        generator = EmbeddingGenerator(api_key=api_key, model=model, dimensions=dimensions)
        await generator.open()

        try:
            # Use asyncpg pool directly for process_article (it expects asyncpg, not SQLAlchemy)
            import asyncpg

            db_url = settings.database.url.replace("+asyncpg", "")
            pool = await asyncpg.create_pool(db_url)
            try:
                chunks_count = await process_article(
                    article_id=str(row.id),
                    title=row.title,
                    content=row.content,
                    pool=pool,
                    generator=generator,
                )
            finally:
                await pool.close()
        finally:
            await generator.close()

        # Set status to indexed
        async with engine.begin() as conn:
            await conn.execute(
                text("""
                    UPDATE knowledge_articles
                    SET embedding_status = 'indexed'
                    WHERE id = :id
                """),
                {"id": article_id},
            )

        logger.info(
            "Embeddings generated for article %s: %d chunks",
            article_id,
            chunks_count,
        )
        return {
            "article_id": article_id,
            "chunks": chunks_count,
            "status": "indexed",
        }

    except Exception as exc:
        # Set status to error
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text("""
                        UPDATE knowledge_articles
                        SET embedding_status = 'error'
                        WHERE id = :id
                    """),
                    {"id": article_id},
                )
        except Exception:
            logger.exception("Failed to set error status for article %s", article_id)

        logger.exception("Embedding generation failed for article %s", article_id)
        raise task.retry(countdown=60) from exc
    finally:
        await engine.dispose()


@app.task(
    name="src.tasks.embedding_tasks.reindex_all_articles",
    bind=True,
    soft_time_limit=1800,
    time_limit=1860,
)  # type: ignore[untyped-decorator]
def reindex_all_articles(self: Any) -> dict[str, Any]:
    """Reindex all active articles by dispatching individual tasks.

    Returns:
        Result dict with count of dispatched tasks.
    """
    import asyncio

    return asyncio.run(_reindex_all_articles_async())


async def _reindex_all_articles_async() -> dict[str, Any]:
    """Async implementation of reindex-all."""
    settings = get_settings()
    engine = create_async_engine(settings.database.url, pool_pre_ping=True)

    try:
        async with engine.begin() as conn:
            # Set all active articles to pending
            await conn.execute(
                text("""
                    UPDATE knowledge_articles
                    SET embedding_status = 'pending'
                    WHERE active = true
                """)
            )

            # Get all active article IDs
            result = await conn.execute(
                text("SELECT id FROM knowledge_articles WHERE active = true")
            )
            article_ids = [str(row.id) for row in result]

        # Dispatch individual tasks
        for article_id in article_ids:
            generate_article_embeddings.delay(article_id)

        logger.info("Reindex-all dispatched %d embedding tasks", len(article_ids))
        return {"dispatched": len(article_ids)}
    finally:
        await engine.dispose()
