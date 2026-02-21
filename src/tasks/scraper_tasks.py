"""Celery tasks for article scraping from multiple content sources.

Discovers new articles, processes them via LLM, and adds to knowledge base.
Supports both scheduled (Celery Beat) and manual trigger from admin UI.
Includes multi-source support via content_source_configs table.
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
    soft_time_limit=900,
    time_limit=960,
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

    return asyncio.run(
        _run_scraper_async(self, triggered_by=triggered_by)
    )


async def _run_scraper_async(task: Any, *, triggered_by: str = "manual") -> dict[str, Any]:
    """Async implementation of the scraper pipeline."""
    import json

    from redis.asyncio import Redis

    from src.knowledge.article_processor import process_article
    from src.knowledge.scraper import ProKolesoScraper

    settings = get_settings()
    engine = create_async_engine(settings.database.url, pool_pre_ping=True)

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

                # LLM processing (auto-detect promotion URLs)
                _is_promo = "/promotions/" in url or "/promo/" in url
                processed = await process_article(
                    title=scraped.title,
                    content=scraped.content,
                    source_url=url,
                    api_key=settings.anthropic.api_key,
                    model=config["llm_model"],
                    is_promotion=_is_promo,
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
    soft_time_limit=600,
    time_limit=660,
)  # type: ignore[untyped-decorator]
def scrape_single_url(
    self: Any, url: str, *, is_promotion: bool = False, is_shop_info: bool = False
) -> dict[str, Any]:
    """Scrape a single URL (for manual trigger from admin)."""
    import asyncio

    return asyncio.run(
        _scrape_single_url_async(self, url, is_promotion=is_promotion, is_shop_info=is_shop_info)
    )


async def _scrape_single_url_async(
    task: Any, url: str, *, is_promotion: bool = False, is_shop_info: bool = False
) -> dict[str, Any]:
    """Async implementation of single-URL scraping."""
    from redis.asyncio import Redis

    from src.knowledge.article_processor import process_article
    from src.knowledge.scraper import ProKolesoScraper

    settings = get_settings()
    engine = create_async_engine(settings.database.url, pool_pre_ping=True)

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

        # Auto-detect promotion URLs if not explicitly set
        _is_promotion = is_promotion or "/promotions/" in url or "/promo/" in url
        _is_shop_info = is_shop_info

        processed = await process_article(
            title=scraped.title,
            content=scraped.content,
            source_url=url,
            api_key=settings.anthropic.api_key,
            model=config["llm_model"],
            is_promotion=_is_promotion,
            is_shop_info=_is_shop_info,
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
                    SELECT ka.title, 1 - (ke.embedding <=> CAST(:vec AS vector)) AS similarity
                    FROM knowledge_embeddings ke
                    JOIN knowledge_articles ka ON ka.id = ke.article_id
                    WHERE ka.active = true
                    ORDER BY ke.embedding <=> CAST(:vec AS vector)
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


# ─── Watched pages rescraping ──────────────────────────────────


@app.task(
    name="src.tasks.scraper_tasks.rescrape_watched_pages",
    bind=True,
    max_retries=2,
    soft_time_limit=900,
    time_limit=960,
)  # type: ignore[untyped-decorator]
def rescrape_watched_pages(self: Any) -> dict[str, Any]:
    """Check and rescrape watched pages that are due for update."""
    import asyncio

    return asyncio.run(
        _rescrape_watched_pages_async(self)
    )


async def _rescrape_watched_pages_async(task: Any) -> dict[str, Any]:
    """Async implementation of watched pages rescraping."""
    from redis.asyncio import Redis

    from src.knowledge.article_processor import process_article
    from src.knowledge.scraper import ProKolesoScraper, content_hash

    settings = get_settings()
    engine = create_async_engine(settings.database.url, pool_pre_ping=True)

    redis = Redis.from_url(settings.redis.url, decode_responses=True)
    try:
        config = await _get_scraper_config(redis, settings)
    finally:
        await redis.aclose()

    stats: dict[str, int] = {"checked": 0, "updated": 0, "unchanged": 0, "errors": 0}

    # Find watched pages due for rescraping (exclude children of discovery pages — they are handled by parent)
    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT id, url, article_id, content_hash, rescrape_interval_hours,
                       COALESCE(is_discovery, false) AS is_discovery
                FROM knowledge_sources
                WHERE source_type = 'watched_page'
                  AND parent_id IS NULL
                  AND (next_scrape_at IS NULL OR next_scrape_at <= now())
                ORDER BY next_scrape_at NULLS FIRST
            """)
        )
        pages = [dict(row._mapping) for row in result]

    if not pages:
        logger.info("No watched pages due for rescraping")
        await engine.dispose()
        return {"status": "ok", **stats}

    logger.info("Found %d watched pages due for rescraping", len(pages))

    scraper = ProKolesoScraper(
        base_url=config["base_url"],
        request_delay=config["request_delay"],
    )
    await scraper.open()

    try:
        for page in pages:
            stats["checked"] += 1
            page_url = page["url"]
            source_id = str(page["id"])
            interval = page["rescrape_interval_hours"] or 168

            # Discovery pages: discover child links and process each as a separate watched page
            if page.get("is_discovery"):
                try:
                    await _handle_discovery_page(
                        scraper, engine, settings, config, page, stats
                    )
                except Exception:
                    logger.exception("Error processing discovery page %s", page_url)
                    stats["errors"] += 1
                continue

            try:
                scraped = await scraper.fetch_article(page_url)
                if scraped is None:
                    logger.warning("Failed to fetch watched page %s", page_url)
                    # Update next_scrape_at so we retry later
                    async with engine.begin() as conn:
                        await conn.execute(
                            text("""
                                UPDATE knowledge_sources
                                SET next_scrape_at = now() + make_interval(hours => :interval),
                                    fetched_at = now()
                                WHERE id = CAST(:id AS uuid)
                            """),
                            {"id": source_id, "interval": interval},
                        )
                    stats["errors"] += 1
                    continue

                new_hash = content_hash(scraped.content)
                old_hash = page["content_hash"]

                # Update fetch timestamp and schedule next scrape
                async with engine.begin() as conn:
                    await conn.execute(
                        text("""
                            UPDATE knowledge_sources
                            SET fetched_at = now(),
                                next_scrape_at = now() + make_interval(hours => :interval)
                            WHERE id = CAST(:id AS uuid)
                        """),
                        {"id": source_id, "interval": interval},
                    )

                if old_hash and new_hash == old_hash:
                    logger.info("Watched page %s unchanged (hash match)", page_url)
                    stats["unchanged"] += 1
                    continue

                # Content changed — process through LLM (watched pages are admin-added, always include)
                logger.info("Watched page %s content changed, processing", page_url)
                processed = await process_article(
                    title=scraped.title,
                    content=scraped.content,
                    source_url=page_url,
                    api_key=settings.anthropic.api_key,
                    model=config["llm_model"],
                    is_shop_info=True,
                )

                if not processed.is_useful:
                    logger.info("Watched page %s deemed not useful: %s", page_url, processed.skip_reason)
                    async with engine.begin() as conn:
                        await conn.execute(
                            text("""
                                UPDATE knowledge_sources
                                SET content_hash = :hash, processed_at = now()
                                WHERE id = CAST(:id AS uuid)
                            """),
                            {"id": source_id, "hash": new_hash},
                        )
                    stats["unchanged"] += 1
                    continue

                article_id = page["article_id"]

                if article_id:
                    # Update existing article
                    async with engine.begin() as conn:
                        await conn.execute(
                            text("""
                                UPDATE knowledge_articles
                                SET title = :title, category = :category, content = :content,
                                    embedding_status = 'pending', updated_at = now()
                                WHERE id = CAST(:article_id AS uuid)
                            """),
                            {
                                "article_id": str(article_id),
                                "title": processed.title,
                                "category": processed.category,
                                "content": processed.content,
                            },
                        )
                    # Update source hash
                    async with engine.begin() as conn:
                        await conn.execute(
                            text("""
                                UPDATE knowledge_sources
                                SET content_hash = :hash, status = 'processed', processed_at = now()
                                WHERE id = CAST(:id AS uuid)
                            """),
                            {"id": source_id, "hash": new_hash},
                        )
                    # Re-generate embeddings
                    _dispatch_embedding(str(article_id))
                else:
                    # Create new article (first scrape of this watched page)
                    async with engine.begin() as conn:
                        result = await conn.execute(
                            text("""
                                INSERT INTO knowledge_articles
                                    (title, category, content, active, embedding_status)
                                VALUES (:title, :category, :content, true, 'pending')
                                RETURNING id
                            """),
                            {
                                "title": processed.title,
                                "category": processed.category,
                                "content": processed.content,
                            },
                        )
                        article_row = result.first()
                        new_article_id = str(article_row.id) if article_row else None

                    # Link source → article
                    async with engine.begin() as conn:
                        await conn.execute(
                            text("""
                                UPDATE knowledge_sources
                                SET article_id = CAST(:article_id AS uuid), content_hash = :hash,
                                    status = 'processed', processed_at = now()
                                WHERE id = CAST(:id AS uuid)
                            """),
                            {"id": source_id, "article_id": new_article_id, "hash": new_hash},
                        )

                    if new_article_id:
                        _dispatch_embedding(new_article_id)

                stats["updated"] += 1
                logger.info("Updated watched page: %s", page_url)

            except Exception:
                logger.exception("Error rescraping watched page %s", page_url)
                stats["errors"] += 1

    except Exception as exc:
        logger.exception("Watched pages rescrape pipeline failed")
        raise task.retry(countdown=300) from exc
    finally:
        await scraper.close()
        await engine.dispose()

    logger.info("Watched pages rescrape finished: %s", stats)
    return {"status": "ok", **stats}


# ─── Discovery page helper ──────────────────────────────────


async def _handle_discovery_page(
    scraper: Any,
    engine: Any,
    settings: Any,
    config: dict[str, Any],
    page: dict[str, Any],
    stats: dict[str, int],
) -> None:
    """Handle a discovery-mode watched page.

    Discovers child page links, creates watched page entries for new ones,
    removes stale children (links no longer on the parent), then scrapes each child.
    """
    import asyncio

    from src.knowledge.article_processor import process_article
    from src.knowledge.scraper import content_hash

    page_url = page["url"]
    source_id = str(page["id"])
    interval = page["rescrape_interval_hours"] or 168

    logger.info("Discovery page %s: discovering child links", page_url)
    discovered_links = await scraper.discover_page_links(page_url)
    logger.info("Discovery page %s: found %d child links", page_url, len(discovered_links))

    # Update parent's next_scrape_at and fetched_at
    async with engine.begin() as conn:
        await conn.execute(
            text("""
                UPDATE knowledge_sources
                SET fetched_at = now(),
                    next_scrape_at = now() + make_interval(hours => :interval)
                WHERE id = CAST(:id AS uuid)
            """),
            {"id": source_id, "interval": interval},
        )

    # Get existing children
    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT id, url, article_id, content_hash, rescrape_interval_hours
                FROM knowledge_sources
                WHERE parent_id = CAST(:parent_id AS uuid)
            """),
            {"parent_id": source_id},
        )
        existing_children = {row.url: dict(row._mapping) for row in result}

    # Normalize discovered URLs for comparison
    discovered_set = set(discovered_links)

    # Remove stale children (no longer on parent page)
    stale_urls = set(existing_children.keys()) - discovered_set
    for stale_url in stale_urls:
        child = existing_children[stale_url]
        logger.info("Discovery page %s: removing stale child %s", page_url, stale_url)
        async with engine.begin() as conn:
            # Deactivate the linked article if any
            if child["article_id"]:
                await conn.execute(
                    text("""
                        UPDATE knowledge_articles
                        SET active = false, updated_at = now()
                        WHERE id = CAST(:article_id AS uuid)
                    """),
                    {"article_id": str(child["article_id"])},
                )
            await conn.execute(
                text("DELETE FROM knowledge_sources WHERE id = CAST(:id AS uuid)"),
                {"id": str(child["id"])},
            )

    # Create new children
    new_urls = discovered_set - set(existing_children.keys())
    for child_url in new_urls:
        logger.info("Discovery page %s: adding new child %s", page_url, child_url)
        async with engine.begin() as conn:
            await conn.execute(
                text("""
                    INSERT INTO knowledge_sources
                        (url, source_site, source_type, status, rescrape_interval_hours,
                         original_title, parent_id, next_scrape_at)
                    VALUES (:url, 'prokoleso.ua', 'watched_page', 'new',
                            :interval, :url, CAST(:parent_id AS uuid), now())
                    ON CONFLICT (url) DO NOTHING
                """),
                {"url": child_url, "interval": interval, "parent_id": source_id},
            )

    # Now scrape all current children (new + existing that are due)
    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT id, url, article_id, content_hash, rescrape_interval_hours
                FROM knowledge_sources
                WHERE parent_id = CAST(:parent_id AS uuid)
                  AND (next_scrape_at IS NULL OR next_scrape_at <= now())
            """),
            {"parent_id": source_id},
        )
        children_to_scrape = [dict(row._mapping) for row in result]

    logger.info(
        "Discovery page %s: %d children to scrape", page_url, len(children_to_scrape)
    )

    for child in children_to_scrape:
        child_url = child["url"]
        child_id = str(child["id"])
        child_interval = child["rescrape_interval_hours"] or interval

        try:
            scraped = await scraper.fetch_article(child_url)
            if scraped is None:
                logger.warning("Failed to fetch child page %s", child_url)
                async with engine.begin() as conn:
                    await conn.execute(
                        text("""
                            UPDATE knowledge_sources
                            SET next_scrape_at = now() + make_interval(hours => :interval),
                                fetched_at = now()
                            WHERE id = CAST(:id AS uuid)
                        """),
                        {"id": child_id, "interval": child_interval},
                    )
                stats["errors"] += 1
                continue

            new_hash = content_hash(scraped.content)
            old_hash = child["content_hash"]

            # Update fetch timestamp and schedule
            async with engine.begin() as conn:
                await conn.execute(
                    text("""
                        UPDATE knowledge_sources
                        SET fetched_at = now(),
                            next_scrape_at = now() + make_interval(hours => :interval)
                        WHERE id = CAST(:id AS uuid)
                    """),
                    {"id": child_id, "interval": child_interval},
                )

            if old_hash and new_hash == old_hash:
                logger.info("Child page %s unchanged (hash match)", child_url)
                stats["unchanged"] += 1
                continue

            # Content changed — process through LLM (children of discovery pages are promotions)
            logger.info("Child page %s content changed, processing", child_url)
            processed = await process_article(
                title=scraped.title,
                content=scraped.content,
                source_url=child_url,
                api_key=settings.anthropic.api_key,
                model=config["llm_model"],
                is_promotion=True,
            )

            if not processed.is_useful:
                logger.info("Child page %s not useful: %s", child_url, processed.skip_reason)
                async with engine.begin() as conn:
                    await conn.execute(
                        text("""
                            UPDATE knowledge_sources
                            SET content_hash = :hash, processed_at = now()
                            WHERE id = CAST(:id AS uuid)
                        """),
                        {"id": child_id, "hash": new_hash},
                    )
                stats["unchanged"] += 1
                continue

            article_id = child["article_id"]

            if article_id:
                # Update existing article
                async with engine.begin() as conn:
                    await conn.execute(
                        text("""
                            UPDATE knowledge_articles
                            SET title = :title, category = :category, content = :content,
                                embedding_status = 'pending', updated_at = now()
                            WHERE id = CAST(:article_id AS uuid)
                        """),
                        {
                            "article_id": str(article_id),
                            "title": processed.title,
                            "category": processed.category,
                            "content": processed.content,
                        },
                    )
                async with engine.begin() as conn:
                    await conn.execute(
                        text("""
                            UPDATE knowledge_sources
                            SET content_hash = :hash, status = 'processed', processed_at = now()
                            WHERE id = CAST(:id AS uuid)
                        """),
                        {"id": child_id, "hash": new_hash},
                    )
                _dispatch_embedding(str(article_id))
            else:
                # Create new article
                async with engine.begin() as conn:
                    result = await conn.execute(
                        text("""
                            INSERT INTO knowledge_articles
                                (title, category, content, active, embedding_status)
                            VALUES (:title, :category, :content, true, 'pending')
                            RETURNING id
                        """),
                        {
                            "title": processed.title,
                            "category": processed.category,
                            "content": processed.content,
                        },
                    )
                    article_row = result.first()
                    new_article_id = str(article_row.id) if article_row else None

                async with engine.begin() as conn:
                    await conn.execute(
                        text("""
                            UPDATE knowledge_sources
                            SET article_id = CAST(:article_id AS uuid), content_hash = :hash,
                                status = 'processed', processed_at = now()
                            WHERE id = CAST(:id AS uuid)
                        """),
                        {"id": child_id, "article_id": new_article_id, "hash": new_hash},
                    )

                if new_article_id:
                    _dispatch_embedding(new_article_id)

            stats["updated"] += 1
            logger.info("Processed child page: %s", child_url)

        except Exception:
            logger.exception("Error scraping child page %s", child_url)
            stats["errors"] += 1

        # Be polite between child page fetches
        await asyncio.sleep(config.get("request_delay", 2.0))


# ─── Multi-source scraping ───────────────────────────────────


@app.task(
    name="src.tasks.scraper_tasks.run_all_sources",
    bind=True,
    max_retries=2,
    soft_time_limit=900,
    time_limit=960,
)  # type: ignore[untyped-decorator]
def run_all_sources(self: Any, triggered_by: str = "manual") -> dict[str, Any]:
    """Dispatch per-source scraping tasks for all enabled content sources.

    Args:
        triggered_by: "beat" for scheduled runs (checks per-source schedule),
                      "manual" for admin-triggered (runs all enabled sources).

    Returns:
        Dict with dispatched source count.
    """
    import asyncio

    return asyncio.run(
        _run_all_sources_async(self, triggered_by=triggered_by)
    )


async def _run_all_sources_async(
    task: Any, *, triggered_by: str = "manual"
) -> dict[str, Any]:
    """Async implementation of run_all_sources."""
    settings = get_settings()
    engine = create_async_engine(settings.database.url, pool_pre_ping=True)

    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    SELECT id, name, schedule_enabled, schedule_hour, schedule_day_of_week
                    FROM content_source_configs
                    WHERE enabled = true
                """)
            )
            configs = [dict(row._mapping) for row in result]
    finally:
        await engine.dispose()

    if not configs:
        logger.info("No enabled content sources found")
        return {"status": "ok", "dispatched": 0}

    dispatched = 0
    for cfg in configs:
        # For beat-triggered runs, check per-source schedule
        if triggered_by == "beat":
            if not cfg.get("schedule_enabled", True):
                continue
            now = datetime.datetime.now(tz=datetime.UTC)
            try:
                import zoneinfo

                kyiv = zoneinfo.ZoneInfo("Europe/Kyiv")
                now = now.astimezone(kyiv)
            except Exception:
                pass
            current_hour = now.hour
            current_day = now.strftime("%A").lower()
            sched_hour = cfg.get("schedule_hour", 6)
            sched_day = (cfg.get("schedule_day_of_week") or "monday").lower()
            if current_hour != sched_hour or current_day != sched_day:
                continue

        run_source.delay(str(cfg["id"]), triggered_by=triggered_by)
        dispatched += 1
        logger.info("Dispatched run_source for %s (%s)", cfg["name"], cfg["id"])

    logger.info("run_all_sources dispatched %d sources", dispatched)
    return {"status": "ok", "dispatched": dispatched}


@app.task(
    name="src.tasks.scraper_tasks.run_source",
    bind=True,
    max_retries=3,
    soft_time_limit=600,
    time_limit=660,
)  # type: ignore[untyped-decorator]
def run_source(
    self: Any, source_config_id: str, triggered_by: str = "manual"
) -> dict[str, Any]:
    """Run the scraper pipeline for a single content source.

    Args:
        source_config_id: UUID of the content_source_configs row.
        triggered_by: "beat" or "manual".

    Returns:
        Stats dict with processed, skipped, errors counts.
    """
    import asyncio

    return asyncio.run(
        _run_source_async(self, source_config_id, triggered_by=triggered_by)
    )


async def _run_source_async(
    task: Any,
    source_config_id: str,
    *,
    triggered_by: str = "manual",
) -> dict[str, Any]:
    """Async implementation of run_source."""
    import json

    from src.knowledge.article_processor import process_article
    from src.knowledge.fetchers import create_fetcher

    settings = get_settings()
    engine = create_async_engine(settings.database.url, pool_pre_ping=True)

    # Load source config from DB
    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT id, name, source_type, source_url, language,
                       enabled, auto_approve, request_delay,
                       max_articles_per_run, settings
                FROM content_source_configs
                WHERE id = :id
            """),
            {"id": source_config_id},
        )
        row = result.first()

    if not row:
        logger.error("Source config %s not found", source_config_id)
        await engine.dispose()
        return {"status": "error", "reason": "config_not_found"}

    config = dict(row._mapping)
    # Parse settings JSONB
    if isinstance(config["settings"], str):
        config["settings"] = json.loads(config["settings"])

    if not config["enabled"]:
        logger.info("Source %s is disabled, skipping", config["name"])
        await engine.dispose()
        return {"status": "disabled"}

    stats: dict[str, int] = {"processed": 0, "skipped": 0, "errors": 0, "discovered": 0}

    fetcher = create_fetcher(config)
    await fetcher.open()

    try:
        # Default min_date: last 14 days
        min_date = datetime.date.today() - datetime.timedelta(days=14)

        discovered = await fetcher.discover_articles(
            max_articles=config["max_articles_per_run"],
            min_date=min_date,
        )
        stats["discovered"] = len(discovered)
        logger.info(
            "Source %s: discovered %d articles", config["name"], len(discovered)
        )

        if not discovered:
            await _update_source_config_run(
                engine, source_config_id, "ok", stats
            )
            await engine.dispose()
            return {"status": "ok", **stats}

        # Filter against known URLs
        urls = [item["url"] for item in discovered]
        async with engine.begin() as conn:
            result = await conn.execute(
                text("SELECT url FROM knowledge_sources WHERE url = ANY(:urls)"),
                {"urls": urls},
            )
            known_urls = {r.url for r in result}

        new_articles = [item for item in discovered if item["url"] not in known_urls]
        logger.info(
            "Source %s: %d new articles (filtered %d known)",
            config["name"],
            len(new_articles),
            len(known_urls),
        )

        # Process each new article
        source_site = config["name"]
        source_language = config.get("language", "uk")

        for item in new_articles:
            url = item["url"]
            try:
                # Insert source record
                async with engine.begin() as conn:
                    await conn.execute(
                        text("""
                            INSERT INTO knowledge_sources
                                (url, source_site, original_title, status, source_config_id)
                            VALUES (:url, :site, :title, 'processing', CAST(:config_id AS uuid))
                            ON CONFLICT (url) DO NOTHING
                        """),
                        {
                            "url": url,
                            "site": source_site,
                            "title": item.get("title", ""),
                            "config_id": source_config_id,
                        },
                    )

                # Fetch article
                scraped = await fetcher.fetch_article(
                    url, published=item.get("published")
                )
                if scraped is None:
                    await _update_source_status(
                        engine, url, "error", skip_reason="Fetch failed"
                    )
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

                # LLM processing (with translation for non-Ukrainian sources)
                processed = await process_article(
                    title=scraped.title,
                    content=scraped.content,
                    source_url=url,
                    api_key=settings.anthropic.api_key,
                    source_language=source_language,
                )

                if not processed.is_useful:
                    await _update_source_status(
                        engine,
                        url,
                        "skipped",
                        skip_reason=processed.skip_reason or "Not useful",
                    )
                    stats["skipped"] += 1
                    continue

                # Semantic dedup check
                dedup_result = await _check_duplicate(engine, processed.content, settings)
                if dedup_result["status"] == "duplicate":
                    await _update_source_status(
                        engine,
                        url,
                        "duplicate",
                        skip_reason=f"Duplicate of: {dedup_result.get('similar_title', 'unknown')} "
                        f"(sim={dedup_result.get('similarity', 0):.2f})",
                    )
                    stats["skipped"] += 1
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

                if active and article_id:
                    _dispatch_embedding(article_id)

                stats["processed"] += 1
                logger.info(
                    "Source %s: processed %s → %s", config["name"], url, article_id
                )

            except Exception:
                logger.exception(
                    "Source %s: error processing %s", config["name"], url
                )
                await _update_source_status(
                    engine, url, "error", skip_reason="Processing error"
                )
                stats["errors"] += 1

        await _update_source_config_run(engine, source_config_id, "ok", stats)

    except Exception as exc:
        logger.exception("Source %s pipeline failed", config["name"])
        await _update_source_config_run(
            engine, source_config_id, "error", stats
        )
        raise task.retry(countdown=300) from exc
    finally:
        await fetcher.close()
        await engine.dispose()

    logger.info("Source %s finished: %s", config["name"], json.dumps(stats))
    return {"status": "ok", **stats}


async def _update_source_config_run(
    engine: Any,
    config_id: str,
    status: str,
    stats: dict[str, int],
) -> None:
    """Update last_run_* fields on a content_source_configs row."""
    import json

    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("""
                    UPDATE content_source_configs
                    SET last_run_at = now(),
                        last_run_status = :status,
                        last_run_stats = CAST(:stats AS jsonb),
                        updated_at = now()
                    WHERE id = :id
                """),
                {"id": config_id, "status": status, "stats": json.dumps(stats)},
            )
    except Exception:
        logger.warning(
            "Failed to update run status for config %s", config_id, exc_info=True
        )
