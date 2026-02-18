"""Celery tasks for article scraping from prokoleso.ua.

Discovers new articles, processes them via LLM, and adds to knowledge base.
Supports both scheduled (Celery Beat) and manual trigger from admin UI.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.config import get_settings
from src.tasks.celery_app import app

logger = logging.getLogger(__name__)


@app.task(
    name="src.tasks.scraper_tasks.run_scraper",
    bind=True,
    max_retries=3,
)  # type: ignore[untyped-decorator]
def run_scraper(self: Any) -> dict[str, Any]:
    """Run the article scraper pipeline.

    Returns:
        Stats dict with processed, skipped, errors counts.
    """
    import asyncio

    return asyncio.get_event_loop().run_until_complete(_run_scraper_async(self))


async def _run_scraper_async(task: Any) -> dict[str, Any]:
    """Async implementation of the scraper pipeline."""
    import json

    from redis.asyncio import Redis

    from src.knowledge.article_processor import process_article
    from src.knowledge.scraper import ProKolesoScraper

    settings = get_settings()
    engine = create_async_engine(settings.database.url)

    # Read config from Redis (with env fallback)
    redis = Redis.from_url(settings.redis.url, decode_responses=True)
    try:
        config = await _get_scraper_config(redis, settings)
    finally:
        await redis.aclose()

    if not config["enabled"]:
        logger.info("Scraper is disabled, skipping run")
        return {"status": "disabled"}

    stats = {"processed": 0, "skipped": 0, "errors": 0}

    scraper = ProKolesoScraper(
        base_url=config["base_url"],
        request_delay=config["request_delay"],
    )
    await scraper.open()

    try:
        # 1. Discover article URLs
        discovered = await scraper.discover_article_urls(
            info_path=config["info_path"],
            max_pages=config["max_pages"],
        )
        logger.info("Discovered %d article URLs", len(discovered))

        if not discovered:
            await engine.dispose()
            return {"status": "ok", **stats, "discovered": 0}

        # 2. Filter against known URLs
        urls = [item["url"] for item in discovered]
        async with engine.begin() as conn:
            result = await conn.execute(
                text("SELECT url FROM knowledge_sources WHERE url = ANY(:urls)"),
                {"urls": urls},
            )
            known_urls = {row.url for row in result}

        new_articles = [item for item in discovered if item["url"] not in known_urls]
        logger.info(
            "New articles to process: %d (filtered %d known)",
            len(new_articles),
            len(known_urls),
        )

        # 3. Process each new article
        for item in new_articles:
            url = item["url"]
            try:
                # Insert source record
                async with engine.begin() as conn:
                    await conn.execute(
                        text("""
                            INSERT INTO knowledge_sources (url, source_site, original_title, status)
                            VALUES (:url, 'prokoleso.ua', :title, 'processing')
                            ON CONFLICT (url) DO NOTHING
                        """),
                        {"url": url, "title": item.get("title", "")},
                    )

                # Fetch article
                scraped = await scraper.fetch_article(url)
                if scraped is None:
                    await _update_source_status(engine, url, "error", skip_reason="Fetch failed")
                    stats["errors"] += 1
                    continue

                # Update fetched_at
                async with engine.begin() as conn:
                    await conn.execute(
                        text("""
                            UPDATE knowledge_sources
                            SET fetched_at = now()
                            WHERE url = :url
                        """),
                        {"url": url},
                    )

                # LLM processing
                processed = await process_article(
                    title=scraped.title,
                    content=scraped.content,
                    source_url=url,
                    api_key=settings.anthropic.api_key,
                    model=config["llm_model"],
                )

                if not processed.is_useful:
                    await _update_source_status(
                        engine,
                        url,
                        "skipped",
                        skip_reason=processed.skip_reason or "Not useful",
                    )
                    stats["skipped"] += 1
                    logger.info("Skipped article %s: %s", url, processed.skip_reason)
                    continue

                # Insert into knowledge_articles
                active = config["auto_approve"]
                embedding_status = "pending" if active else "none"

                async with engine.begin() as conn:
                    result = await conn.execute(
                        text("""
                            INSERT INTO knowledge_articles
                                (title, category, content, active, embedding_status)
                            VALUES (:title, :category, :content, :active, :embedding_status)
                            RETURNING id
                        """),
                        {
                            "title": processed.title,
                            "category": processed.category,
                            "content": processed.content,
                            "active": active,
                            "embedding_status": embedding_status,
                        },
                    )
                    article_row = result.first()
                    article_id = str(article_row.id) if article_row else None

                # Link source → article
                async with engine.begin() as conn:
                    await conn.execute(
                        text("""
                            UPDATE knowledge_sources
                            SET status = 'processed', article_id = :article_id, processed_at = now()
                            WHERE url = :url
                        """),
                        {"url": url, "article_id": article_id},
                    )

                # Dispatch embeddings if auto-approved
                if active and article_id:
                    _dispatch_embedding(article_id)

                stats["processed"] += 1
                logger.info("Processed article: %s → %s", url, article_id)

            except Exception:
                logger.exception("Error processing article %s", url)
                await _update_source_status(engine, url, "error", skip_reason="Processing error")
                stats["errors"] += 1

    except Exception as exc:
        logger.exception("Scraper pipeline failed")
        raise task.retry(countdown=300) from exc
    finally:
        await scraper.close()
        await engine.dispose()

    logger.info("Scraper finished: %s", json.dumps(stats))
    return {"status": "ok", **stats, "discovered": len(discovered)}


@app.task(
    name="src.tasks.scraper_tasks.scrape_single_url",
    bind=True,
    max_retries=3,
)  # type: ignore[untyped-decorator]
def scrape_single_url(self: Any, url: str) -> dict[str, Any]:
    """Scrape a single URL (for manual trigger from admin)."""
    import asyncio

    return asyncio.get_event_loop().run_until_complete(_scrape_single_url_async(self, url))


async def _scrape_single_url_async(task: Any, url: str) -> dict[str, Any]:
    """Async implementation of single-URL scraping."""
    from redis.asyncio import Redis

    from src.knowledge.article_processor import process_article
    from src.knowledge.scraper import ProKolesoScraper

    settings = get_settings()
    engine = create_async_engine(settings.database.url)

    redis = Redis.from_url(settings.redis.url, decode_responses=True)
    try:
        config = await _get_scraper_config(redis, settings)
    finally:
        await redis.aclose()

    scraper = ProKolesoScraper(
        base_url=config["base_url"],
        request_delay=config["request_delay"],
    )
    await scraper.open()

    try:
        scraped = await scraper.fetch_article(url)
        if scraped is None:
            return {"status": "error", "reason": "Fetch failed"}

        processed = await process_article(
            title=scraped.title,
            content=scraped.content,
            source_url=url,
            api_key=settings.anthropic.api_key,
            model=config["llm_model"],
        )

        if not processed.is_useful:
            return {"status": "skipped", "reason": processed.skip_reason}

        active = config["auto_approve"]
        embedding_status = "pending" if active else "none"

        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    INSERT INTO knowledge_articles
                        (title, category, content, active, embedding_status)
                    VALUES (:title, :category, :content, :active, :embedding_status)
                    RETURNING id
                """),
                {
                    "title": processed.title,
                    "category": processed.category,
                    "content": processed.content,
                    "active": active,
                    "embedding_status": embedding_status,
                },
            )
            article_row = result.first()
            article_id = str(article_row.id) if article_row else None

        if active and article_id:
            _dispatch_embedding(article_id)

        return {"status": "processed", "article_id": article_id}

    except Exception as exc:
        logger.exception("Single URL scrape failed: %s", url)
        raise task.retry(countdown=60) from exc
    finally:
        await scraper.close()
        await engine.dispose()


async def _get_scraper_config(redis: Any, settings: Any) -> dict[str, Any]:
    """Read scraper config from Redis, with env fallback."""
    import json

    config_json = await redis.get("scraper:config")
    redis_config = json.loads(config_json) if config_json else {}

    return {
        "enabled": redis_config.get("enabled", settings.scraper.enabled),
        "base_url": redis_config.get("base_url", settings.scraper.base_url),
        "info_path": redis_config.get("info_path", settings.scraper.info_path),
        "max_pages": redis_config.get("max_pages", settings.scraper.max_pages),
        "request_delay": redis_config.get("request_delay", settings.scraper.request_delay),
        "auto_approve": redis_config.get("auto_approve", settings.scraper.auto_approve),
        "llm_model": redis_config.get("llm_model", settings.scraper.llm_model),
    }


async def _update_source_status(
    engine: Any, url: str, status: str, *, skip_reason: str | None = None
) -> None:
    """Update knowledge_sources status for a URL."""
    async with engine.begin() as conn:
        await conn.execute(
            text("""
                UPDATE knowledge_sources
                SET status = :status, skip_reason = :skip_reason, processed_at = now()
                WHERE url = :url
            """),
            {"url": url, "status": status, "skip_reason": skip_reason},
        )


def _dispatch_embedding(article_id: str) -> None:
    """Dispatch embedding generation task (best-effort)."""
    try:
        from src.tasks.embedding_tasks import generate_article_embeddings

        generate_article_embeddings.delay(article_id)
    except Exception:
        logger.warning(
            "Could not dispatch embedding task for article %s", article_id, exc_info=True
        )
