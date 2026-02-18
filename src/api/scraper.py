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
    dedup_llm_check: bool | None = None


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
            "schedule_hour": settings.scraper.schedule_hour,
            "schedule_day_of_week": settings.scraper.schedule_day_of_week,
            "min_date": redis_config.get("min_date", settings.scraper.min_date),
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
    if request.dedup_llm_check is not None:
        config["dedup_llm_check"] = request.dedup_llm_check

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
