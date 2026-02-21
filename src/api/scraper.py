"""Admin API for article scraper management.

Manage scraper config, view/approve/reject scraped sources,
trigger manual scraper runs.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal
from uuid import UUID  # noqa: TC003 - FastAPI needs UUID at runtime for path params

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
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
        _engine = create_async_engine(settings.database.url, pool_pre_ping=True)
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
    max_pages: int | None = Field(default=None, ge=1, le=500)
    request_delay: float | None = Field(default=None, gt=0.0)
    min_date: str | None = None
    max_date: str | None = None
    dedup_llm_check: bool | None = None
    schedule_enabled: bool | None = None
    schedule_hour: int | None = Field(default=None, ge=0, le=23)
    schedule_day_of_week: str | None = None


# ─── Source Config models ──────────────────────────────────
_VALID_SOURCE_TYPES = {"prokoleso", "rss", "generic_html"}
_VALID_LANGUAGES = {"uk", "de", "en", "fr", "pl"}
_URL_PATTERN = r"^https?://.+"


class SourceConfigCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    source_type: str = Field(min_length=1, max_length=30)
    source_url: str = Field(min_length=1, max_length=2000, pattern=_URL_PATTERN)
    language: str = Field(default="uk", max_length=5)
    enabled: bool = False
    auto_approve: bool = False
    request_delay: float = Field(default=2.0, gt=0.0)
    max_articles_per_run: int = Field(default=20, ge=1, le=200)
    schedule_enabled: bool = True
    schedule_hour: int = Field(default=6, ge=0, le=23)
    schedule_day_of_week: str = Field(default="monday", max_length=10)
    settings: dict[str, Any] = {}


class SourceConfigUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    source_type: str | None = Field(default=None, min_length=1, max_length=30)
    source_url: str | None = Field(
        default=None, min_length=1, max_length=2000, pattern=_URL_PATTERN
    )
    language: str | None = Field(default=None, max_length=5)
    enabled: bool | None = None
    auto_approve: bool | None = None
    request_delay: float | None = Field(default=None, gt=0.0)
    max_articles_per_run: int | None = Field(default=None, ge=1, le=200)
    schedule_enabled: bool | None = None
    schedule_hour: int | None = Field(default=None, ge=0, le=23)
    schedule_day_of_week: str | None = Field(default=None, max_length=10)
    settings: dict[str, Any] | None = None


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
            "schedule_enabled": redis_config.get(
                "schedule_enabled", settings.scraper.schedule_enabled
            ),
            "schedule_hour": redis_config.get("schedule_hour", settings.scraper.schedule_hour),
            "schedule_day_of_week": redis_config.get(
                "schedule_day_of_week", settings.scraper.schedule_day_of_week
            ),
            "min_date": redis_config.get("min_date", settings.scraper.min_date),
            "max_date": redis_config.get("max_date", settings.scraper.max_date),
            "dedup_llm_check": redis_config.get(
                "dedup_llm_check", settings.scraper.dedup_llm_check
            ),
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
    source_config_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: dict[str, Any] = _admin_dep,
) -> dict[str, Any]:
    """List scraped sources with optional status/source_config_id filter."""
    engine = await _get_engine()

    conditions = ["1=1"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    if status:
        conditions.append("ks.status = :status")
        params["status"] = status
    if source_config_id:
        conditions.append("ks.source_config_id = CAST(:source_config_id AS uuid)")
        params["source_config_id"] = source_config_id

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
#  Content Source Configs
# ═══════════════════════════════════════════════════════════


@router.get("/source-configs")
async def list_source_configs(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: dict[str, Any] = _admin_dep,
) -> dict[str, Any]:
    """List all content source configurations."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        count_result = await conn.execute(text("SELECT COUNT(*) FROM content_source_configs"))
        total = count_result.scalar() or 0

        result = await conn.execute(
            text("""
                SELECT id, name, source_type, source_url, language,
                       enabled, auto_approve, request_delay,
                       max_articles_per_run, schedule_enabled,
                       schedule_hour, schedule_day_of_week, settings,
                       last_run_at, last_run_status, last_run_stats,
                       created_at, updated_at
                FROM content_source_configs
                ORDER BY created_at
                LIMIT :limit OFFSET :offset
            """),
            {"limit": limit, "offset": offset},
        )
        configs = [dict(row._mapping) for row in result]

    return {"configs": configs, "total": total}


@router.post("/source-configs")
async def create_source_config(
    request: SourceConfigCreate, _: dict[str, Any] = _admin_dep
) -> dict[str, Any]:
    """Create a new content source configuration."""
    if request.source_type not in _VALID_SOURCE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid source_type: {request.source_type}. "
            f"Must be one of: {', '.join(sorted(_VALID_SOURCE_TYPES))}",
        )
    if request.language not in _VALID_LANGUAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid language: {request.language}. "
            f"Must be one of: {', '.join(sorted(_VALID_LANGUAGES))}",
        )
    engine = await _get_engine()

    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    INSERT INTO content_source_configs
                        (name, source_type, source_url, language, enabled, auto_approve,
                         request_delay, max_articles_per_run, schedule_enabled,
                         schedule_hour, schedule_day_of_week, settings)
                    VALUES (:name, :source_type, :source_url, :language, :enabled,
                            :auto_approve, :request_delay, :max_articles_per_run,
                            :schedule_enabled, :schedule_hour, :schedule_day_of_week,
                            CAST(:settings AS jsonb))
                    RETURNING id
                """),
                {
                    "name": request.name,
                    "source_type": request.source_type,
                    "source_url": request.source_url,
                    "language": request.language,
                    "enabled": request.enabled,
                    "auto_approve": request.auto_approve,
                    "request_delay": request.request_delay,
                    "max_articles_per_run": request.max_articles_per_run,
                    "schedule_enabled": request.schedule_enabled,
                    "schedule_hour": request.schedule_hour,
                    "schedule_day_of_week": request.schedule_day_of_week,
                    "settings": json.dumps(request.settings),
                },
            )
            row = result.first()
            config_id = str(row.id) if row else None
    except Exception as exc:
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise HTTPException(
                status_code=409, detail="Source with this URL already exists"
            ) from exc
        raise

    logger.info("Created source config: %s (%s)", request.name, config_id)
    return {"message": "Source config created", "id": config_id}


@router.patch("/source-configs/{config_id}")
async def update_source_config(
    config_id: UUID, request: SourceConfigUpdate, _: dict[str, Any] = _admin_dep
) -> dict[str, Any]:
    """Update a content source configuration."""
    if request.source_type is not None and request.source_type not in _VALID_SOURCE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid source_type: {request.source_type}",
        )
    if request.language is not None and request.language not in _VALID_LANGUAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid language: {request.language}",
        )

    engine = await _get_engine()

    # Build dynamic SET clause
    updates: list[str] = []
    params: dict[str, Any] = {"id": str(config_id)}

    for field_name in (
        "name",
        "source_type",
        "source_url",
        "language",
        "enabled",
        "auto_approve",
        "request_delay",
        "max_articles_per_run",
        "schedule_enabled",
        "schedule_hour",
        "schedule_day_of_week",
    ):
        value = getattr(request, field_name, None)
        if value is not None:
            updates.append(f"{field_name} = :{field_name}")
            params[field_name] = value

    if request.settings is not None:
        updates.append("settings = CAST(:settings AS jsonb)")
        params["settings"] = json.dumps(request.settings)

    if not updates:
        return {"message": "No changes"}

    updates.append("updated_at = now()")
    set_clause = ", ".join(updates)

    async with engine.begin() as conn:
        result = await conn.execute(
            text(f"""
                UPDATE content_source_configs
                SET {set_clause}
                WHERE id = :id
                RETURNING id
            """),
            params,
        )
        if not result.first():
            raise HTTPException(status_code=404, detail="Source config not found")

    logger.info("Updated source config %s", config_id)
    return {"message": "Source config updated"}


@router.delete("/source-configs/{config_id}")
async def delete_source_config(config_id: UUID, _: dict[str, Any] = _admin_dep) -> dict[str, Any]:
    """Delete a content source configuration."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                DELETE FROM content_source_configs
                WHERE id = :id
                RETURNING id
            """),
            {"id": str(config_id)},
        )
        if not result.first():
            raise HTTPException(status_code=404, detail="Source config not found")

    logger.info("Deleted source config %s", config_id)
    return {"message": "Source config deleted"}


@router.post("/source-configs/{config_id}/run")
async def trigger_source_run(config_id: UUID, _: dict[str, Any] = _admin_dep) -> dict[str, Any]:
    """Trigger a manual scraper run for a single source."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("SELECT id, name FROM content_source_configs WHERE id = :id"),
            {"id": str(config_id)},
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Source config not found")

    try:
        from src.tasks.scraper_tasks import run_source

        result = run_source.delay(str(config_id))
        return {
            "message": f"Source run dispatched for {row.name}",
            "task_id": str(result.id),
        }
    except Exception as exc:
        logger.exception("Failed to dispatch source task for %s", config_id)
        raise HTTPException(status_code=500, detail="Failed to dispatch source task") from exc


# ═══════════════════════════════════════════════════════════
#  Watched pages
# ═══════════════════════════════════════════════════════════

_VALID_CATEGORIES = {
    "brands",
    "guides",
    "faq",
    "comparisons",
    "general",
    "policies",
    "procedures",
    "returns",
    "warranty",
    "delivery",
}

KnowledgeCategory = Literal[
    "brands",
    "guides",
    "faq",
    "comparisons",
    "general",
    "policies",
    "procedures",
    "returns",
    "warranty",
    "delivery",
]


class WatchedPageCreate(BaseModel):
    url: str = Field(min_length=1, max_length=2000, pattern=_URL_PATTERN)
    category: KnowledgeCategory = "general"
    rescrape_interval_hours: int = Field(default=168, ge=1, le=8760)
    is_discovery: bool = False
    tenant_id: str | None = None


class WatchedPageUpdate(BaseModel):
    category: KnowledgeCategory | None = None
    rescrape_interval_hours: int | None = Field(default=None, ge=1, le=8760)
    is_discovery: bool | None = None


@router.get("/watched-pages")
async def list_watched_pages(
    tenant_id: str | None = Query(None),
    _: dict[str, Any] = _admin_dep,
) -> dict[str, Any]:
    """List all watched pages with their status."""
    engine = await _get_engine()

    tenant_filter = ""
    params: dict[str, Any] = {}
    if tenant_id:
        tenant_filter = "AND ks.tenant_id = CAST(:tenant_id AS uuid)"
        params["tenant_id"] = tenant_id

    async with engine.begin() as conn:
        result = await conn.execute(
            text(f"""
                SELECT ks.id, ks.url, ks.article_id, ks.status,
                       ks.content_hash, ks.rescrape_interval_hours,
                       COALESCE(ks.is_discovery, false) AS is_discovery,
                       ks.parent_id, ks.tenant_id,
                       ks.fetched_at, ks.next_scrape_at, ks.created_at,
                       ka.title AS article_title, ka.category AS article_category,
                       ka.active AS article_active,
                       (SELECT COUNT(*) FROM knowledge_sources ch
                        WHERE ch.parent_id = ks.id) AS child_count
                FROM knowledge_sources ks
                LEFT JOIN knowledge_articles ka ON ka.id = ks.article_id
                WHERE ks.source_type = 'watched_page' {tenant_filter}
                ORDER BY ks.parent_id NULLS FIRST, ks.created_at
            """),
            params,
        )
        pages = [dict(row._mapping) for row in result]

    return {"pages": pages}


@router.post("/watched-pages")
async def add_watched_page(
    request: WatchedPageCreate, _: dict[str, Any] = _admin_dep
) -> dict[str, Any]:
    """Add a new watched page for periodic rescraping."""
    url = request.url.strip()
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
                         original_title, next_scrape_at, is_discovery, tenant_id)
                    VALUES (:url, 'prokoleso.ua', 'watched_page', 'new',
                            :interval, :url, now(), :is_discovery,
                            CAST(:tenant_id AS uuid))
                    RETURNING id
                """),
                {
                    "url": url,
                    "interval": interval,
                    "is_discovery": request.is_discovery,
                    "tenant_id": request.tenant_id,
                },
            )
            row = result.first()
            page_id = str(row.id) if row else None
    except Exception as exc:
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise HTTPException(status_code=409, detail="URL already exists") from exc
        raise

    return {"message": "Watched page added", "id": page_id}


class BulkWatchedPageCreate(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=500)
    category: KnowledgeCategory = "general"
    rescrape_interval_hours: int = Field(default=168, ge=1, le=8760)
    tenant_id: str | None = None


@router.post("/watched-pages/bulk")
async def bulk_add_watched_pages(
    request: BulkWatchedPageCreate, _: dict[str, Any] = _admin_dep
) -> dict[str, Any]:
    """Bulk-add watched pages. Skips duplicates."""
    engine = await _get_engine()
    added = 0
    skipped = 0
    errors: list[str] = []

    for raw_url in request.urls:
        url = raw_url.strip()
        if not url:
            continue

        try:
            async with engine.begin() as conn:
                result = await conn.execute(
                    text("""
                        INSERT INTO knowledge_sources
                            (url, source_site, source_type, status,
                             rescrape_interval_hours, original_title,
                             next_scrape_at, tenant_id)
                        VALUES (:url, 'prokoleso.ua', 'watched_page', 'new',
                                :interval, :url, now(),
                                CAST(:tenant_id AS uuid))
                        ON CONFLICT (url) DO NOTHING
                        RETURNING id
                    """),
                    {
                        "url": url,
                        "interval": request.rescrape_interval_hours,
                        "tenant_id": request.tenant_id,
                    },
                )
                row = result.first()
                if row:
                    added += 1
                else:
                    skipped += 1
        except Exception as exc:
            skipped += 1
            errors.append(f"{url}: {exc!s}"[:200])

    logger.info("Bulk watched pages: added=%d, skipped=%d", added, skipped)
    return {"added": added, "skipped": skipped, "errors": errors}


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
                raise HTTPException(
                    status_code=400, detail="Interval must be between 1 and 8760 hours"
                )
            await conn.execute(
                text("""
                    UPDATE knowledge_sources
                    SET rescrape_interval_hours = :interval
                    WHERE id = :id
                """),
                {"id": str(page_id), "interval": request.rescrape_interval_hours},
            )

        if request.is_discovery is not None:
            await conn.execute(
                text("""
                    UPDATE knowledge_sources
                    SET is_discovery = :is_discovery
                    WHERE id = :id
                """),
                {"id": str(page_id), "is_discovery": request.is_discovery},
            )

        if request.category is not None:
            category = request.category
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
    """Remove a watched page. Children are cascade-deleted (FK). Linked articles are kept."""
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
async def scrape_watched_page_now(page_id: UUID, _: dict[str, Any] = _admin_dep) -> dict[str, Any]:
    """Scrape a single watched page immediately (inline, no Celery needed)."""
    from src.knowledge.article_processor import process_article
    from src.knowledge.scraper import ProKolesoScraper, content_hash

    engine = await _get_engine()
    settings = get_settings()

    # Load page info
    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT id, url, article_id, content_hash, rescrape_interval_hours,
                       COALESCE(is_discovery, false) AS is_discovery, tenant_id
                FROM knowledge_sources
                WHERE id = :id AND source_type = 'watched_page'
            """),
            {"id": str(page_id)},
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Watched page not found")

    page = dict(row._mapping)
    page_url = page["url"]
    source_id = str(page["id"])
    interval = page["rescrape_interval_hours"] or 168

    # Read scraper config
    redis = await _get_redis()
    config_json = await redis.get("scraper:config")
    redis_config = json.loads(config_json) if config_json else {}
    llm_model = redis_config.get("llm_model", settings.scraper.llm_model)
    request_delay = redis_config.get("request_delay", settings.scraper.request_delay)

    scraper = ProKolesoScraper(
        base_url=redis_config.get("base_url", settings.scraper.base_url),
        request_delay=request_delay,
    )
    await scraper.open()

    try:
        # Discovery mode: discover children and scrape each
        if page.get("is_discovery"):
            return await _scrape_discovery_page_inline(
                scraper, engine, settings, llm_model, request_delay, page
            )

        # Regular page: fetch, compare hash, process
        scraped = await scraper.fetch_article(page_url)
        if scraped is None:
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
            raise HTTPException(status_code=422, detail="Failed to fetch page content")

        new_hash = content_hash(scraped.content)
        old_hash = page["content_hash"]

        # Update fetch time
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
            return {"status": "unchanged", "message": "Content has not changed"}

        # LLM processing (watched pages are admin-added, always include)
        processed = await process_article(
            title=scraped.title,
            content=scraped.content,
            source_url=page_url,
            api_key=settings.anthropic.api_key,
            model=llm_model,
            is_shop_info=True,
        )

        if not processed.is_useful:
            async with engine.begin() as conn:
                await conn.execute(
                    text("""
                        UPDATE knowledge_sources
                        SET content_hash = :hash, processed_at = now()
                        WHERE id = CAST(:id AS uuid)
                    """),
                    {"id": source_id, "hash": new_hash},
                )
            return {"status": "skipped", "message": processed.skip_reason or "Not useful"}

        article_id = page["article_id"]

        page_tenant_id = str(page["tenant_id"]) if page.get("tenant_id") else None

        if article_id:
            async with engine.begin() as conn:
                await conn.execute(
                    text("""
                        UPDATE knowledge_articles
                        SET title = :title, category = :category, content = :content,
                            tenant_id = CAST(:tenant_id AS uuid),
                            embedding_status = 'pending', updated_at = now()
                        WHERE id = CAST(:article_id AS uuid)
                    """),
                    {
                        "article_id": str(article_id),
                        "title": processed.title,
                        "category": processed.category,
                        "content": processed.content,
                        "tenant_id": page_tenant_id,
                    },
                )
            async with engine.begin() as conn:
                await conn.execute(
                    text("""
                        UPDATE knowledge_sources
                        SET content_hash = :hash, status = 'processed', processed_at = now()
                        WHERE id = CAST(:id AS uuid)
                    """),
                    {"id": source_id, "hash": new_hash},
                )
            await _generate_embedding_inline(str(article_id))
            return {"status": "updated", "article_id": str(article_id)}
        else:
            async with engine.begin() as conn:
                result = await conn.execute(
                    text("""
                        INSERT INTO knowledge_articles
                            (title, category, content, active, embedding_status, tenant_id)
                        VALUES (:title, :category, :content, true, 'pending',
                                CAST(:tenant_id AS uuid))
                        RETURNING id
                    """),
                    {
                        "title": processed.title,
                        "category": processed.category,
                        "content": processed.content,
                        "tenant_id": page_tenant_id,
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
                    {"id": source_id, "article_id": new_article_id, "hash": new_hash},
                )

            if new_article_id:
                await _generate_embedding_inline(new_article_id)
            return {"status": "created", "article_id": new_article_id}

    finally:
        await scraper.close()


async def _scrape_discovery_page_inline(
    scraper: Any,
    engine: Any,
    settings: Any,
    llm_model: str,
    request_delay: float,
    page: dict[str, Any],
) -> dict[str, Any]:
    """Inline scrape of a discovery page: discover children, scrape each."""
    import asyncio

    from src.knowledge.article_processor import process_article
    from src.knowledge.scraper import content_hash

    page_url = page["url"]
    source_id = str(page["id"])
    interval = page["rescrape_interval_hours"] or 168

    discovered_links = await scraper.discover_page_links(page_url)

    # Update parent timestamps
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
            text("SELECT id, url FROM knowledge_sources WHERE parent_id = CAST(:pid AS uuid)"),
            {"pid": source_id},
        )
        existing_urls = {row.url for row in result}

    # Create new children (inherit parent's tenant_id)
    parent_tenant_id = str(page["tenant_id"]) if page.get("tenant_id") else None
    new_urls = set(discovered_links) - existing_urls
    for child_url in new_urls:
        async with engine.begin() as conn:
            await conn.execute(
                text("""
                    INSERT INTO knowledge_sources
                        (url, source_site, source_type, status, rescrape_interval_hours,
                         original_title, parent_id, next_scrape_at, tenant_id)
                    VALUES (:url, 'prokoleso.ua', 'watched_page', 'new',
                            :interval, :url, CAST(:parent_id AS uuid), now(),
                            CAST(:tenant_id AS uuid))
                    ON CONFLICT (url) DO NOTHING
                """),
                {
                    "url": child_url,
                    "interval": interval,
                    "parent_id": source_id,
                    "tenant_id": parent_tenant_id,
                },
            )

    # Remove stale
    stale_urls = existing_urls - set(discovered_links)
    if stale_urls:
        async with engine.begin() as conn:
            await conn.execute(
                text("""
                    DELETE FROM knowledge_sources
                    WHERE parent_id = CAST(:pid AS uuid) AND url = ANY(:urls)
                """),
                {"pid": source_id, "urls": list(stale_urls)},
            )

    # Scrape all children
    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT id, url, article_id, content_hash, rescrape_interval_hours, tenant_id
                FROM knowledge_sources WHERE parent_id = CAST(:pid AS uuid)
            """),
            {"pid": source_id},
        )
        children = [dict(row._mapping) for row in result]

    stats: dict[str, int] = {"created": 0, "updated": 0, "unchanged": 0, "errors": 0}

    for child in children:
        child_url = child["url"]
        child_id = str(child["id"])
        child_interval = child["rescrape_interval_hours"] or interval

        try:
            scraped = await scraper.fetch_article(child_url)
            if scraped is None:
                stats["errors"] += 1
                continue

            new_hash = content_hash(scraped.content)
            old_hash = child["content_hash"]

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
                stats["unchanged"] += 1
                continue

            processed = await process_article(
                title=scraped.title,
                content=scraped.content,
                source_url=child_url,
                api_key=settings.anthropic.api_key,
                model=llm_model,
                is_promotion=True,
            )

            if not processed.is_useful:
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
            child_tenant_id = str(child["tenant_id"]) if child.get("tenant_id") else None
            if article_id:
                async with engine.begin() as conn:
                    await conn.execute(
                        text("""
                            UPDATE knowledge_articles
                            SET title = :title, category = :category, content = :content,
                                tenant_id = CAST(:tenant_id AS uuid),
                                embedding_status = 'pending', updated_at = now()
                            WHERE id = CAST(:article_id AS uuid)
                        """),
                        {
                            "article_id": str(article_id),
                            "title": processed.title,
                            "category": processed.category,
                            "content": processed.content,
                            "tenant_id": child_tenant_id,
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
                await _generate_embedding_inline(str(article_id))
                stats["updated"] += 1
            else:
                async with engine.begin() as conn:
                    result = await conn.execute(
                        text("""
                            INSERT INTO knowledge_articles
                                (title, category, content, active, embedding_status, tenant_id)
                            VALUES (:title, :category, :content, true, 'pending',
                                    CAST(:tenant_id AS uuid))
                            RETURNING id
                        """),
                        {
                            "title": processed.title,
                            "category": processed.category,
                            "content": processed.content,
                            "tenant_id": child_tenant_id,
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
                    await _generate_embedding_inline(new_article_id)
                stats["created"] += 1

        except Exception:
            logger.exception("Error scraping child %s", child_url)
            stats["errors"] += 1

        await asyncio.sleep(request_delay)

    return {
        "status": "ok",
        "discovered": len(discovered_links),
        "new_children": len(new_urls),
        "removed_stale": len(stale_urls),
        **stats,
    }


async def _generate_embedding_inline(article_id: str) -> dict[str, Any]:
    """Generate embeddings inline (no Celery needed)."""
    try:
        from src.knowledge.embeddings import generate_embeddings_inline

        return await generate_embeddings_inline(article_id)
    except Exception:
        logger.warning("Inline embedding generation failed for %s", article_id, exc_info=True)
        return {"article_id": article_id, "status": "error"}
