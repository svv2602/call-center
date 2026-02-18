"""Admin API for article scraper management.

Manage scraper config, view/approve/reject scraped sources,
trigger manual scraper runs.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID  # noqa: TC003 - FastAPI needs UUID at runtime for path params

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.api.auth import require_role
from src.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/scraper", tags=["scraper"])

_engine: AsyncEngine | None = None
_redis: Redis | None = None

# Module-level dependency to satisfy B008 lint rule
_admin_dep = Depends(require_role("admin"))


async def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database.url)
    return _engine


async def _get_redis() -> Redis:
    global _redis
    if _redis is None:
        settings = get_settings()
        _redis = Redis.from_url(settings.redis.url, decode_responses=True)
    return _redis


class ScraperConfigUpdate(BaseModel):
    enabled: bool | None = None
    auto_approve: bool | None = None
    max_pages: int | None = None
    request_delay: float | None = None
    min_date: str | None = None
    max_date: str | None = None
    dedup_llm_check: bool | None = None
    schedule_enabled: bool | None = None
    schedule_hour: int | None = None
    schedule_day_of_week: str | None = None


@router.get("/config")
async def get_scraper_config(_: dict[str, Any] = _admin_dep) -> dict[str, Any]:
    """Get current scraper configuration (Redis + env fallback)."""
    settings = get_settings()
    redis = await _get_redis()

    config_json = await redis.get("scraper:config")
    redis_config = json.loads(config_json) if config_json else {}

    return {
        "config": {
            "enabled": redis_config.get("enabled", settings.scraper.enabled),
            "auto_approve": redis_config.get("auto_approve", settings.scraper.auto_approve),
            "base_url": redis_config.get("base_url", settings.scraper.base_url),
            "info_path": redis_config.get("info_path", settings.scraper.info_path),
            "max_pages": redis_config.get("max_pages", settings.scraper.max_pages),
            "request_delay": redis_config.get("request_delay", settings.scraper.request_delay),
            "llm_model": redis_config.get("llm_model", settings.scraper.llm_model),
            "schedule_enabled": redis_config.get("schedule_enabled", settings.scraper.schedule_enabled),
            "schedule_hour": redis_config.get("schedule_hour", settings.scraper.schedule_hour),
            "schedule_day_of_week": redis_config.get("schedule_day_of_week", settings.scraper.schedule_day_of_week),
            "min_date": redis_config.get("min_date", settings.scraper.min_date),
            "max_date": redis_config.get("max_date", settings.scraper.max_date),
            "dedup_llm_check": redis_config.get("dedup_llm_check", settings.scraper.dedup_llm_check),
        }
    }


@router.patch("/config")
async def update_scraper_config(
    request: ScraperConfigUpdate, _: dict[str, Any] = _admin_dep
) -> dict[str, Any]:
    """Update scraper configuration in Redis."""
    redis = await _get_redis()

    config_json = await redis.get("scraper:config")
    config = json.loads(config_json) if config_json else {}

    if request.enabled is not None:
        config["enabled"] = request.enabled
    if request.auto_approve is not None:
        config["auto_approve"] = request.auto_approve
    if request.max_pages is not None:
        config["max_pages"] = request.max_pages
    if request.request_delay is not None:
        config["request_delay"] = request.request_delay
    if request.min_date is not None:
        config["min_date"] = request.min_date
    if request.max_date is not None:
        config["max_date"] = request.max_date
    if request.dedup_llm_check is not None:
        config["dedup_llm_check"] = request.dedup_llm_check
    if request.schedule_enabled is not None:
        config["schedule_enabled"] = request.schedule_enabled
    if request.schedule_hour is not None:
        config["schedule_hour"] = request.schedule_hour
    if request.schedule_day_of_week is not None:
        config["schedule_day_of_week"] = request.schedule_day_of_week

    await redis.set("scraper:config", json.dumps(config))
    logger.info("Scraper config updated: %s", config)

    return {"message": "Config updated", "config": config}


@router.post("/run")
async def trigger_scraper_run(_: dict[str, Any] = _admin_dep) -> dict[str, Any]:
    """Trigger a manual scraper run via Celery."""
    try:
        from src.tasks.scraper_tasks import run_scraper

        result = run_scraper.delay()
        return {"message": "Scraper run dispatched", "task_id": str(result.id)}
    except Exception as exc:
        logger.exception("Failed to dispatch scraper task")
        raise HTTPException(status_code=500, detail="Failed to dispatch scraper task") from exc


@router.get("/sources")
async def list_sources(
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: dict[str, Any] = _admin_dep,
) -> dict[str, Any]:
    """List scraped sources with optional status filter."""
    engine = await _get_engine()

    conditions = ["1=1"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    if status:
        conditions.append("ks.status = :status")
        params["status"] = status

    where_clause = " AND ".join(conditions)

    async with engine.begin() as conn:
        count_result = await conn.execute(
            text(f"SELECT COUNT(*) FROM knowledge_sources ks WHERE {where_clause}"),
            params,
        )
        total = count_result.scalar()

        result = await conn.execute(
            text(f"""
                SELECT ks.id, ks.url, ks.source_site, ks.article_id,
                       ks.status, ks.original_title, ks.skip_reason,
                       ks.fetched_at, ks.processed_at, ks.created_at,
                       ka.title AS article_title, ka.active AS article_active
                FROM knowledge_sources ks
                LEFT JOIN knowledge_articles ka ON ka.id = ks.article_id
                WHERE {where_clause}
                ORDER BY ks.created_at DESC
                LIMIT :limit OFFSET :offset
            """),
            params,
        )
        sources = [dict(row._mapping) for row in result]

    return {"total": total, "sources": sources}


@router.get("/sources/{source_id}")
async def get_source(source_id: UUID, _: dict[str, Any] = _admin_dep) -> dict[str, Any]:
    """Get a single source detail."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT ks.*, ka.title AS article_title, ka.active AS article_active
                FROM knowledge_sources ks
                LEFT JOIN knowledge_articles ka ON ka.id = ks.article_id
                WHERE ks.id = :id
            """),
            {"id": str(source_id)},
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Source not found")

    return {"source": dict(row._mapping)}


@router.post("/sources/{source_id}/approve")
async def approve_source(source_id: UUID, _: dict[str, Any] = _admin_dep) -> dict[str, Any]:
    """Approve a scraped source: activate its article and trigger embeddings."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        # Get source with article
        result = await conn.execute(
            text("SELECT article_id FROM knowledge_sources WHERE id = :id"),
            {"id": str(source_id)},
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Source not found")
        if not row.article_id:
            raise HTTPException(status_code=400, detail="Source has no linked article")

        # Activate article
        await conn.execute(
            text("""
                UPDATE knowledge_articles
                SET active = true, embedding_status = 'pending', updated_at = now()
                WHERE id = :id
            """),
            {"id": str(row.article_id)},
        )

    # Dispatch embeddings
    try:
        from src.tasks.embedding_tasks import generate_article_embeddings

        generate_article_embeddings.delay(str(row.article_id))
    except Exception:
        logger.warning("Could not dispatch embedding task for %s", row.article_id, exc_info=True)

    return {"message": "Source approved, article activated", "article_id": str(row.article_id)}


@router.post("/sources/{source_id}/reject")
async def reject_source(source_id: UUID, _: dict[str, Any] = _admin_dep) -> dict[str, Any]:
    """Reject a scraped source: deactivate article, mark source as skipped."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("SELECT article_id FROM knowledge_sources WHERE id = :id"),
            {"id": str(source_id)},
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Source not found")

        # Mark source as skipped
        await conn.execute(
            text("""
                UPDATE knowledge_sources
                SET status = 'skipped', skip_reason = 'Rejected by admin', processed_at = now()
                WHERE id = :id
            """),
            {"id": str(source_id)},
        )

        # Deactivate article if linked
        if row.article_id:
            await conn.execute(
                text("""
                    UPDATE knowledge_articles
                    SET active = false, updated_at = now()
                    WHERE id = :id
                """),
                {"id": str(row.article_id)},
            )

    return {"message": "Source rejected"}


# ═══════════════════════════════════════════════════════════
#  Watched pages
# ═══════════════════════════════════════════════════════════

_VALID_CATEGORIES = {
    "brands", "guides", "faq", "comparisons", "general",
    "policies", "procedures", "returns", "warranty", "delivery",
}


class WatchedPageCreate(BaseModel):
    url: str
    category: str = "general"
    rescrape_interval_hours: int = 168  # default: weekly


class WatchedPageUpdate(BaseModel):
    category: str | None = None
    rescrape_interval_hours: int | None = None


@router.get("/watched-pages")
async def list_watched_pages(_: dict[str, Any] = _admin_dep) -> dict[str, Any]:
    """List all watched pages with their status."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT ks.id, ks.url, ks.article_id, ks.status,
                       ks.content_hash, ks.rescrape_interval_hours,
                       ks.fetched_at, ks.next_scrape_at, ks.created_at,
                       ka.title AS article_title, ka.category AS article_category,
                       ka.active AS article_active
                FROM knowledge_sources ks
                LEFT JOIN knowledge_articles ka ON ka.id = ks.article_id
                WHERE ks.source_type = 'watched_page'
                ORDER BY ks.created_at
            """)
        )
        pages = [dict(row._mapping) for row in result]

    return {"pages": pages}


@router.post("/watched-pages")
async def add_watched_page(
    request: WatchedPageCreate, _: dict[str, Any] = _admin_dep
) -> dict[str, Any]:
    """Add a new watched page for periodic rescraping."""
    url = request.url.strip()
    if not url.startswith("http"):
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")

    category = request.category.strip().lower()
    if category not in _VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Invalid category: {category}")

    interval = request.rescrape_interval_hours
    if interval < 1 or interval > 8760:
        raise HTTPException(status_code=400, detail="Interval must be between 1 and 8760 hours")

    engine = await _get_engine()

    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    INSERT INTO knowledge_sources
                        (url, source_site, source_type, status, rescrape_interval_hours,
                         original_title, next_scrape_at)
                    VALUES (:url, 'prokoleso.ua', 'watched_page', 'new',
                            :interval, :url, now())
                    RETURNING id
                """),
                {"url": url, "interval": interval},
            )
            row = result.first()
            page_id = str(row.id) if row else None
    except Exception as exc:
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise HTTPException(status_code=409, detail="URL already exists") from exc
        raise

    return {"message": "Watched page added", "id": page_id}


@router.patch("/watched-pages/{page_id}")
async def update_watched_page(
    page_id: UUID, request: WatchedPageUpdate, _: dict[str, Any] = _admin_dep
) -> dict[str, Any]:
    """Update a watched page's category or rescrape interval."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT id, article_id FROM knowledge_sources
                WHERE id = :id AND source_type = 'watched_page'
            """),
            {"id": str(page_id)},
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Watched page not found")

        if request.rescrape_interval_hours is not None:
            if request.rescrape_interval_hours < 1 or request.rescrape_interval_hours > 8760:
                raise HTTPException(status_code=400, detail="Interval must be between 1 and 8760 hours")
            await conn.execute(
                text("""
                    UPDATE knowledge_sources
                    SET rescrape_interval_hours = :interval
                    WHERE id = :id
                """),
                {"id": str(page_id), "interval": request.rescrape_interval_hours},
            )

        if request.category is not None:
            category = request.category.strip().lower()
            if category not in _VALID_CATEGORIES:
                raise HTTPException(status_code=400, detail=f"Invalid category: {category}")
            # Update article category if linked
            if row.article_id:
                await conn.execute(
                    text("""
                        UPDATE knowledge_articles SET category = :category, updated_at = now()
                        WHERE id = :article_id
                    """),
                    {"article_id": str(row.article_id), "category": category},
                )

    return {"message": "Watched page updated"}


@router.delete("/watched-pages/{page_id}")
async def delete_watched_page(page_id: UUID, _: dict[str, Any] = _admin_dep) -> dict[str, Any]:
    """Remove a watched page. Linked article is kept but no longer auto-updated."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                DELETE FROM knowledge_sources
                WHERE id = :id AND source_type = 'watched_page'
                RETURNING id
            """),
            {"id": str(page_id)},
        )
        if not result.first():
            raise HTTPException(status_code=404, detail="Watched page not found")

    return {"message": "Watched page removed"}


@router.post("/watched-pages/{page_id}/scrape-now")
async def scrape_watched_page_now(
    page_id: UUID, _: dict[str, Any] = _admin_dep
) -> dict[str, Any]:
    """Trigger immediate rescraping of a single watched page."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                UPDATE knowledge_sources
                SET next_scrape_at = now()
                WHERE id = :id AND source_type = 'watched_page'
                RETURNING id
            """),
            {"id": str(page_id)},
        )
        if not result.first():
            raise HTTPException(status_code=404, detail="Watched page not found")

    # Trigger rescrape task
    try:
        from src.tasks.scraper_tasks import rescrape_watched_pages

        result = rescrape_watched_pages.delay()
        return {"message": "Rescrape triggered", "task_id": str(result.id)}
    except Exception as exc:
        logger.exception("Failed to dispatch rescrape task")
        raise HTTPException(status_code=500, detail="Failed to dispatch rescrape task") from exc
