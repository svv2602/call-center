"""Load knowledge base articles from markdown files into PostgreSQL.

Reads markdown files from knowledge_base/ directory, inserts them as
knowledge_articles, generates embeddings, and stores in knowledge_embeddings.

Usage:
    python -m scripts.load_knowledge_base [--dir knowledge_base/] [--dry-run]

Environment variables:
    DATABASE_URL  — PostgreSQL connection string
    OPENAI_API_KEY — OpenAI API key for embeddings
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import uuid
from pathlib import Path

import asyncpg

from src.knowledge.embeddings import EmbeddingGenerator, process_article

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Category mapping from directory name
_DIR_TO_CATEGORY = {
    "brands": "brands",
    "guides": "guides",
    "faq": "faq",
    "comparisons": "comparisons",
}


def parse_markdown(file_path: Path) -> tuple[str, str]:
    """Extract title and content from a markdown file.

    Title is taken from the first H1 heading (# Title).
    Content is everything after the title.
    """
    text = file_path.read_text(encoding="utf-8")
    lines = text.strip().split("\n")

    title = file_path.stem.replace("_", " ").title()
    content_start = 0

    for i, line in enumerate(lines):
        if line.startswith("# "):
            title = line[2:].strip()
            content_start = i + 1
            break

    content = "\n".join(lines[content_start:]).strip()
    return title, content


def discover_articles(base_dir: Path) -> list[dict[str, str]]:
    """Discover all markdown articles in the knowledge base directory."""
    articles = []

    for subdir, category in _DIR_TO_CATEGORY.items():
        dir_path = base_dir / subdir
        if not dir_path.is_dir():
            logger.warning("Directory not found: %s", dir_path)
            continue

        for md_file in sorted(dir_path.glob("*.md")):
            title, content = parse_markdown(md_file)
            if not content:
                logger.warning("Empty article: %s", md_file)
                continue

            articles.append({
                "file": str(md_file),
                "title": title,
                "category": category,
                "content": content,
            })

    return articles


async def load_articles(
    articles: list[dict[str, str]],
    pool: asyncpg.Pool,
    generator: EmbeddingGenerator,
    dry_run: bool = False,
) -> None:
    """Load articles into the database and generate embeddings."""
    logger.info("Found %d articles to load", len(articles))

    if dry_run:
        for a in articles:
            logger.info(
                "[DRY RUN] Would load: %s (%s) — %d chars",
                a["title"],
                a["category"],
                len(a["content"]),
            )
        return

    total_chunks = 0

    for article_data in articles:
        article_id = str(uuid.uuid4())

        # Insert or update article
        async with pool.acquire() as conn:
            # Check if article with same title exists
            existing = await conn.fetchrow(
                "SELECT id FROM knowledge_articles WHERE title = $1",
                article_data["title"],
            )

            if existing:
                article_id = str(existing["id"])
                await conn.execute(
                    "UPDATE knowledge_articles "
                    "SET content = $1, category = $2, updated_at = now() "
                    "WHERE id = $3",
                    article_data["content"],
                    article_data["category"],
                    existing["id"],
                )
                logger.info("Updated article: %s", article_data["title"])
            else:
                await conn.execute(
                    "INSERT INTO knowledge_articles "
                    "(id, title, category, content) "
                    "VALUES ($1, $2, $3, $4)",
                    uuid.UUID(article_id),
                    article_data["title"],
                    article_data["category"],
                    article_data["content"],
                )
                logger.info("Inserted article: %s", article_data["title"])

        # Process embeddings
        chunks = await process_article(
            article_id,
            article_data["title"],
            article_data["content"],
            pool,
            generator,
        )
        total_chunks += chunks

    logger.info(
        "Loading complete: %d articles, %d total chunks",
        len(articles),
        total_chunks,
    )


async def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Load knowledge base articles into PostgreSQL"
    )
    parser.add_argument(
        "--dir",
        default="knowledge_base",
        help="Path to knowledge base directory (default: knowledge_base/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be loaded without actually loading",
    )
    args = parser.parse_args()

    base_dir = Path(args.dir)
    if not base_dir.is_dir():
        logger.error("Knowledge base directory not found: %s", base_dir)
        sys.exit(1)

    # Discover articles
    articles = discover_articles(base_dir)
    if not articles:
        logger.error("No articles found in %s", base_dir)
        sys.exit(1)

    database_url = os.environ.get("DATABASE_URL", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")

    if args.dry_run:
        # Dry run doesn't need DB or API
        await load_articles(articles, None, None, dry_run=True)  # type: ignore[arg-type]
        return

    if not database_url:
        logger.error("DATABASE_URL environment variable is required")
        sys.exit(1)

    if not openai_key:
        logger.error("OPENAI_API_KEY environment variable is required")
        sys.exit(1)

    # Connect to database
    pool = await asyncpg.create_pool(database_url)
    assert pool is not None

    # Initialize embedding generator
    generator = EmbeddingGenerator(api_key=openai_key)
    await generator.open()

    try:
        await load_articles(articles, pool, generator)
    finally:
        await generator.close()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
