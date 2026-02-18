"""Celery tasks for article scraping from prokoleso.ua.

Discovers new articles, processes them via LLM, and adds to knowledge base.
Supports both scheduled (Celery Beat) and manual trigger from admin UI.
"""

from __future__ import annotations

import datetime
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
def run_scraper(self: Any, triggered_by: str = "manual") -> dict[str, Any]:
    """Run the article scraper pipeline.

    Args:
        triggered_by: "beat" for scheduled runs, "manual" for admin-triggered.
            Beat runs check schedule_enabled/hour/day_of_week before proceeding.

    Returns:
        Stats dict with processed, skipped, errors counts.
    """
    import asyncio

    return asyncio.get_event_loop().run_until_complete(
        _run_scraper_async(self, triggered_by=triggered_by)
    )


async def _run_scraper_async(task: Any, *, triggered_by: str = "manual") -> dict[str, Any]:
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

    # For beat-triggered runs, check schedule config
    if triggered_by == "beat":
        if not config.get("schedule_enabled", True):
            return {"status": "schedule_disabled"}
        now = datetime.datetime.now(tz=datetime.UTC)
        # Convert to Kyiv time for schedule check
        try:
            import zoneinfo

            kyiv = zoneinfo.ZoneInfo("Europe/Kyiv")
            now = now.astimezone(kyiv)
        except Exception:
            pass  # fallback to UTC
        current_hour = now.hour
        current_day = now.strftime("%A").lower()
        sched_hour = config.get("schedule_hour", 6)
        sched_day = config.get("schedule_day_of_week", "monday").lower()
        if current_hour != sched_hour or current_day != sched_day:
            return {"status": "not_scheduled_time"}

    stats = {"processed": 0, "skipped": 0, "errors": 0}

    scraper = ProKolesoScraper(
        base_url=config["base_url"],
        request_delay=config["request_delay"],
    )
    await scraper.open()

    try:
        # 1. Compute date range
        min_date_str = config.get("min_date", "")
        min_date = None
        if min_date_str:
            try:
                min_date = datetime.date.fromisoformat(min_date_str)
            except ValueError:
                logger.warning("Invalid min_date %r, ignoring", min_date_str)
        elif not min_date_str:
            # Scheduled mode: empty min_date → last 14 days
            min_date = datetime.date.today() - datetime.timedelta(days=14)

        max_date_str = config.get("max_date", "")
        max_date = None
        if max_date_str:
            try:
                max_date = datetime.date.fromisoformat(max_date_str)
            except ValueError:
                logger.warning("Invalid max_date %r, ignoring", max_date_str)
        # empty max_date → no upper limit (today by default via listing order)

        # 2. Discover article URLs
        discovered = await scraper.discover_article_urls(
            info_path=config["info_path"],
            max_pages=config["max_pages"],
            min_date=min_date,
            max_date=max_date,
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
                scraped = await scraper.fetch_article(url, published=item.get("published"))
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

                # Semantic dedup check
                dedup_result = await _check_duplicate(
                    engine,
                    processed.content,
                    settings,
                    dedup_llm_check=config.get("dedup_llm_check", False),
                )
                if dedup_result["status"] == "duplicate":
                    await _update_source_status(
                        engine,
                        url,
                        "duplicate",
                        skip_reason=f"Duplicate of: {dedup_result.get('similar_title', 'unknown')} "
                        f"(sim={dedup_result.get('similarity', 0):.2f})",
                    )
                    stats["skipped"] += 1
                    logger.info("Duplicate article %s (sim=%.2f)", url, dedup_result.get("similarity", 0))
                    continue
                if dedup_result["status"] == "suspect":
                    await _update_source_status(
                        engine,
                        url,
                        "duplicate_suspect",
                        skip_reason=f"Possible duplicate of: {dedup_result.get('similar_title', 'unknown')} "
                        f"(sim={dedup_result.get('similarity', 0):.2f})",
                    )
                    stats["skipped"] += 1
                    logger.info("Suspect duplicate %s (sim=%.2f)", url, dedup_result.get("similarity", 0))
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
        "schedule_enabled": redis_config.get("schedule_enabled", settings.scraper.schedule_enabled),
        "schedule_hour": redis_config.get("schedule_hour", settings.scraper.schedule_hour),
        "schedule_day_of_week": redis_config.get("schedule_day_of_week", settings.scraper.schedule_day_of_week),
        "min_date": redis_config.get("min_date", settings.scraper.min_date),
        "max_date": redis_config.get("max_date", settings.scraper.max_date),
        "dedup_llm_check": redis_config.get("dedup_llm_check", settings.scraper.dedup_llm_check),
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


async def _check_duplicate(
    engine: Any,
    content: str,
    settings: Any,
    *,
    dedup_llm_check: bool = False,
) -> dict[str, Any]:
    """Check if content is a semantic duplicate of existing articles.

    Uses pgvector cosine similarity on article embeddings.

    Returns:
        {"status": "new"} — not a duplicate
        {"status": "duplicate", "similar_title": ..., "similarity": ...} — auto-skip
        {"status": "suspect", "similar_title": ..., "similarity": ...} — needs review
    """
    try:
        from src.knowledge.embeddings import EmbeddingGenerator

        api_key = settings.openai.api_key
        if not api_key:
            return {"status": "new"}

        model = settings.openai.embedding_model
        dimensions = settings.openai.embedding_dimensions

        generator = EmbeddingGenerator(api_key=api_key, model=model, dimensions=dimensions)
        await generator.open()
        try:
            vectors = await generator.generate([content[:2000]])  # First 2000 chars
            if not vectors or not vectors[0]:
                return {"status": "new"}
            embedding = vectors[0]
        finally:
            await generator.close()

        # Query pgvector for most similar article
        vec_str = "[" + ",".join(str(v) for v in embedding) + "]"

        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    SELECT ka.title, 1 - (ke.embedding <=> :vec::vector) AS similarity
                    FROM knowledge_embeddings ke
                    JOIN knowledge_articles ka ON ka.id = ke.article_id
                    WHERE ka.active = true
                    ORDER BY ke.embedding <=> :vec::vector
                    LIMIT 1
                """),
                {"vec": vec_str},
            )
            row = result.first()

        if not row:
            return {"status": "new"}

        sim = float(row.similarity)
        similar_title = row.title

        if sim > 0.90:
            return {"status": "duplicate", "similar_title": similar_title, "similarity": sim}
        if sim >= 0.80:
            if dedup_llm_check:
                # Could invoke LLM for borderline cases, but for now mark as suspect
                # LLM check can be added later if needed
                logger.info("Borderline sim=%.2f for '%s', marking as suspect", sim, similar_title)
            return {"status": "suspect", "similar_title": similar_title, "similarity": sim}

        return {"status": "new"}

    except Exception:
        logger.warning("Dedup check failed, treating as new article", exc_info=True)
        return {"status": "new"}


def _dispatch_embedding(article_id: str) -> None:
    """Dispatch embedding generation task (best-effort)."""
    try:
        from src.tasks.embedding_tasks import generate_article_embeddings

        generate_article_embeddings.delay(article_id)
    except Exception:
        logger.warning(
            "Could not dispatch embedding task for article %s", article_id, exc_info=True
        )
