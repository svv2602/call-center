"""Embedding pipeline for knowledge base articles.

Handles chunking, embedding generation via OpenAI API,
and storage in PostgreSQL with pgvector.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# Chunking config
_MAX_CHUNK_TOKENS = 500
_APPROX_CHARS_PER_TOKEN = 4
_MAX_CHUNK_CHARS = _MAX_CHUNK_TOKENS * _APPROX_CHARS_PER_TOKEN

# Embedding defaults (overridden by config when available)
_EMBEDDING_MODEL = "text-embedding-3-small"
_EMBEDDING_DIMENSIONS = 1536
_BATCH_SIZE = 20


# Embedding config via settings (lazy import to avoid circular deps)
def _get_embedding_config() -> tuple[str, str, int]:
    """Get embedding config from settings. Returns (api_key, model, dimensions)."""
    try:
        from src.config import get_settings

        settings = get_settings()
        return (
            settings.openai.api_key,
            settings.openai.embedding_model,
            settings.openai.embedding_dimensions,
        )
    except Exception:
        return ("", _EMBEDDING_MODEL, _EMBEDDING_DIMENSIONS)


def chunk_text(text: str, max_chars: int = _MAX_CHUNK_CHARS) -> list[str]:
    """Split text into chunks by paragraphs, respecting max size.

    Strategy:
      1. Split by double newlines (paragraphs)
      2. If a paragraph exceeds max_chars, split by sentences
      3. Merge small paragraphs into chunks up to max_chars
    """
    paragraphs = re.split(r"\n\s*\n", text.strip())
    chunks: list[str] = []
    current_chunk = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # If paragraph itself exceeds max, split by sentences
        if len(para) > max_chars:
            sentences = re.split(r"(?<=[.!?])\s+", para)
            for sentence in sentences:
                if len(current_chunk) + len(sentence) + 1 > max_chars:
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                    current_chunk = sentence
                else:
                    current_chunk = f"{current_chunk} {sentence}" if current_chunk else sentence
        elif len(current_chunk) + len(para) + 2 > max_chars:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = para
        else:
            current_chunk = f"{current_chunk}\n\n{para}" if current_chunk else para

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    return chunks


class EmbeddingGenerator:
    """Generates embeddings using OpenAI API."""

    def __init__(
        self,
        api_key: str,
        model: str = _EMBEDDING_MODEL,
        dimensions: int = _EMBEDDING_DIMENSIONS,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._dimensions = dimensions
        self._session: aiohttp.ClientSession | None = None

    async def open(self) -> None:
        """Open the HTTP session."""
        self._session = aiohttp.ClientSession(
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def generate(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a list of texts.

        Args:
            texts: List of text strings to embed.

        Returns:
            List of embedding vectors (each is a list of floats).
        """
        if self._session is None:
            raise RuntimeError("EmbeddingGenerator not opened — call open() first")

        all_embeddings: list[list[float]] = []

        # Process in batches
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i : i + _BATCH_SIZE]
            embeddings = await self._embed_batch(batch)
            all_embeddings.extend(embeddings)

        return all_embeddings

    async def generate_single(self, text: str) -> list[float]:
        """Generate embedding for a single text string."""
        results = await self.generate([text])
        return results[0]

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Send a batch of texts to the OpenAI embeddings API."""
        assert self._session is not None

        body: dict[str, Any] = {
            "input": texts,
            "model": self._model,
            "dimensions": self._dimensions,
        }

        async with self._session.post(
            "https://api.openai.com/v1/embeddings",
            json=body,
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                logger.error("OpenAI embeddings API error %d: %s", resp.status, error_text[:200])
                raise RuntimeError(f"Embedding API error {resp.status}: {error_text[:200]}")

            data = await resp.json()

        # Sort by index to maintain order
        results = sorted(data["data"], key=lambda x: x["index"])
        return [r["embedding"] for r in results]


async def process_article(
    article_id: str,
    title: str,
    content: str,
    pool: Any,
    generator: EmbeddingGenerator,
) -> int:
    """Process a single article: chunk, embed, store.

    Args:
        article_id: UUID of the article.
        title: Article title (prepended to each chunk for context).
        content: Article body text.
        pool: asyncpg connection pool.
        generator: EmbeddingGenerator instance.

    Returns:
        Number of chunks created.
    """
    # Chunk the content
    chunks = chunk_text(content)
    if not chunks:
        logger.warning("Article %s has no content to chunk", article_id)
        return 0

    # Prepend title to each chunk for better context
    texts_for_embedding = [f"{title}\n\n{chunk}" for chunk in chunks]

    # Generate embeddings
    embeddings = await generator.generate(texts_for_embedding)

    # Delete old embeddings for this article
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM knowledge_embeddings WHERE article_id = $1",
            uuid.UUID(article_id),
        )

        # Insert new embeddings
        for idx, (chunk, embedding) in enumerate(zip(chunks, embeddings, strict=False)):
            embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"
            await conn.execute(
                "INSERT INTO knowledge_embeddings "
                "(id, article_id, chunk_text, chunk_index, embedding) "
                "VALUES ($1, $2, $3, $4, $5::vector)",
                uuid.uuid4(),
                uuid.UUID(article_id),
                chunk,
                idx,
                embedding_str,
            )

    logger.info(
        "Article %s processed: %d chunks, %d embeddings",
        article_id,
        len(chunks),
        len(embeddings),
    )
    return len(chunks)


async def generate_embeddings_inline(article_id: str) -> dict[str, Any]:
    """Generate embeddings for a single article inline (no Celery needed).

    Creates its own DB pool and embedding generator, suitable for calling
    from API endpoints or scripts.

    Returns:
        Result dict with article_id, chunks count, and status.
    """
    import asyncpg

    from src.config import get_settings

    settings = get_settings()
    api_key = settings.openai.api_key
    if not api_key:
        logger.warning("No OpenAI API key configured, skipping embeddings for %s", article_id)
        return {"article_id": article_id, "status": "skipped", "reason": "no_api_key"}

    model = settings.openai.embedding_model
    dimensions = settings.openai.embedding_dimensions

    db_url = settings.database.url.replace("+asyncpg", "")
    pool = await asyncpg.create_pool(db_url)

    try:
        # Fetch article
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, title, content FROM knowledge_articles WHERE id = $1 AND active = true",
                uuid.UUID(article_id),
            )

        if not row:
            logger.warning("Article %s not found or inactive, skipping embedding", article_id)
            return {"article_id": article_id, "status": "skipped", "reason": "not_found"}

        # Update status to processing
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE knowledge_articles SET embedding_status = 'processing' WHERE id = $1",
                uuid.UUID(article_id),
            )

        generator = EmbeddingGenerator(api_key=api_key, model=model, dimensions=dimensions)
        await generator.open()

        try:
            chunks_count = await process_article(
                article_id=str(row["id"]),
                title=row["title"],
                content=row["content"],
                pool=pool,
                generator=generator,
            )
        finally:
            await generator.close()

        # Set status to indexed
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE knowledge_articles SET embedding_status = 'indexed' WHERE id = $1",
                uuid.UUID(article_id),
            )

        logger.info("Inline embeddings for article %s: %d chunks", article_id, chunks_count)
        return {"article_id": article_id, "chunks": chunks_count, "status": "indexed"}

    except Exception:
        # Set status to error
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE knowledge_articles SET embedding_status = 'error' WHERE id = $1",
                    uuid.UUID(article_id),
                )
        except Exception:
            logger.exception("Failed to set error status for article %s", article_id)

        logger.exception("Inline embedding generation failed for article %s", article_id)
        return {"article_id": article_id, "status": "error"}
    finally:
        await pool.close()
