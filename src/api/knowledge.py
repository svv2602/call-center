"""Knowledge base CRUD API endpoints.

Manage knowledge articles: create, read, update, delete.
Triggers embedding regeneration on content changes.
Supports bulk document import (MD, PDF, DOCX).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.api.auth import require_role
from src.config import get_settings
from src.knowledge.categories import CATEGORIES, is_valid_category

if TYPE_CHECKING:
    from uuid import UUID

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/knowledge", tags=["knowledge"])

_engine: AsyncEngine | None = None

# Module-level dependencies to satisfy B008 lint rule
_admin_dep = Depends(require_role("admin"))
_analyst_dep = Depends(require_role("admin", "analyst"))


async def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database.url)
    return _engine


def _dispatch_embedding(article_id: str) -> None:
    """Dispatch embedding generation task (best-effort, no failure on import error)."""
    try:
        from src.tasks.embedding_tasks import generate_article_embeddings

        generate_article_embeddings.delay(article_id)
    except Exception:
        logger.warning(
            "Could not dispatch embedding task for article %s", article_id, exc_info=True
        )


class ArticleCreateRequest(BaseModel):
    title: str
    category: str
    content: str


class ArticleUpdateRequest(BaseModel):
    title: str | None = None
    category: str | None = None
    content: str | None = None
    active: bool | None = None


@router.get("/article-categories")
async def get_article_categories(_: dict[str, Any] = _analyst_dep) -> dict[str, Any]:
    """Return the list of available article categories."""
    return {"categories": CATEGORIES}


@router.get("/articles")
async def list_articles(
    category: str | None = Query(None),
    active: bool | None = Query(None),
    search: str | None = Query(None, description="Full-text search in title and content"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: dict[str, Any] = _analyst_dep,
) -> dict[str, Any]:
    """List knowledge articles with filters."""
    engine = await _get_engine()

    conditions = ["1=1"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    if category:
        conditions.append("category = :category")
        params["category"] = category
    if active is not None:
        conditions.append("active = :active")
        params["active"] = active
    if search:
        conditions.append("(title ILIKE :search OR content ILIKE :search)")
        params["search"] = f"%{search}%"

    where_clause = " AND ".join(conditions)

    async with engine.begin() as conn:
        count_result = await conn.execute(
            text(f"SELECT COUNT(*) FROM knowledge_articles WHERE {where_clause}"),
            params,
        )
        total = count_result.scalar()

        result = await conn.execute(
            text(f"""
                SELECT id, title, category, active, embedding_status, created_at, updated_at
                FROM knowledge_articles
                WHERE {where_clause}
                ORDER BY category, title
                LIMIT :limit OFFSET :offset
            """),
            params,
        )
        articles = [dict(row._mapping) for row in result]

    return {"total": total, "articles": articles}


@router.post("/articles/import")
async def import_articles(
    files: list[UploadFile],
    category: str | None = Query(None, description="Override category for all imported files"),
    _: dict[str, Any] = _admin_dep,
) -> dict[str, Any]:
    """Bulk import documents (MD, PDF, DOCX) as knowledge articles."""
    from src.knowledge.parsers import PARSERS, SUPPORTED_EXTENSIONS, detect_category_from_filename

    if category and not is_valid_category(category):
        raise HTTPException(status_code=400, detail=f"Invalid category: {category}")

    engine = await _get_engine()
    imported: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for file in files:
        filename = file.filename or "unknown"
        ext = Path(filename).suffix.lower()

        if ext not in SUPPORTED_EXTENSIONS:
            errors.append(
                {
                    "filename": filename,
                    "error": f"Unsupported file type: {ext}. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
                }
            )
            continue

        try:
            content_bytes = await file.read()
            parser = PARSERS[ext]
            title, body = parser(content_bytes, filename)

            if not body.strip():
                errors.append({"filename": filename, "error": "Empty content after parsing"})
                continue

            file_category = category or detect_category_from_filename(filename)

            async with engine.begin() as conn:
                result = await conn.execute(
                    text("""
                        INSERT INTO knowledge_articles (title, category, content, embedding_status)
                        VALUES (:title, :category, :content, 'pending')
                        RETURNING id, title, category, active, embedding_status, created_at
                    """),
                    {"title": title, "category": file_category, "content": body},
                )
                row = result.first()
                if row is None:
                    msg = "Expected row from INSERT RETURNING"
                    raise RuntimeError(msg)
                article = dict(row._mapping)

            _dispatch_embedding(str(article["id"]))
            imported.append(article)
            logger.info("Imported article from %s: %s (%s)", filename, title, article["id"])

        except Exception as exc:
            logger.exception("Failed to import %s", filename)
            errors.append({"filename": filename, "error": str(exc)})

    return {
        "imported": len(imported),
        "errors": len(errors),
        "articles": imported,
        "error_details": errors,
    }


@router.post("/articles/{article_id}/reindex")
async def reindex_article(article_id: UUID, _: dict[str, Any] = _admin_dep) -> dict[str, Any]:
    """Trigger embedding regeneration for a specific article."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                UPDATE knowledge_articles
                SET embedding_status = 'pending'
                WHERE id = :id
                RETURNING id, title
            """),
            {"id": str(article_id)},
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Article not found")

    _dispatch_embedding(str(article_id))
    return {"message": f"Reindex queued for '{row.title}'", "article_id": str(article_id)}


@router.post("/reindex-all")
async def reindex_all(_: dict[str, Any] = _admin_dep) -> dict[str, Any]:
    """Trigger embedding regeneration for all active articles."""
    try:
        from src.tasks.embedding_tasks import reindex_all_articles

        reindex_all_articles.delay()
    except Exception as exc:
        logger.exception("Could not dispatch reindex-all task")
        raise HTTPException(status_code=500, detail="Failed to dispatch reindex task") from exc

    return {"message": "Reindex-all task dispatched"}


@router.get("/articles/{article_id}")
async def get_article(article_id: UUID, _: dict[str, Any] = _analyst_dep) -> dict[str, Any]:
    """Get a specific article with full content."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT id, title, category, content, active, embedding_status, created_at, updated_at
                FROM knowledge_articles
                WHERE id = :id
            """),
            {"id": str(article_id)},
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Article not found")

    return {"article": dict(row._mapping)}


@router.post("/articles")
async def create_article(
    request: ArticleCreateRequest, _: dict[str, Any] = _admin_dep
) -> dict[str, Any]:
    """Create a new knowledge article."""
    if not is_valid_category(request.category):
        raise HTTPException(status_code=400, detail=f"Invalid category: {request.category}")

    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                INSERT INTO knowledge_articles (title, category, content, embedding_status)
                VALUES (:title, :category, :content, 'pending')
                RETURNING id, title, category, active, embedding_status, created_at
            """),
            {
                "title": request.title,
                "category": request.category,
                "content": request.content,
            },
        )
        row = result.first()
        if row is None:
            msg = "Expected row from INSERT RETURNING"
            raise RuntimeError(msg)
        article = dict(row._mapping)

    _dispatch_embedding(str(article["id"]))
    logger.info("Knowledge article created: %s (%s)", article["title"], article["id"])
    return {"article": article, "message": "Article created. Embedding generation queued."}


@router.patch("/articles/{article_id}")
async def update_article(
    article_id: UUID, request: ArticleUpdateRequest, _: dict[str, Any] = _admin_dep
) -> dict[str, Any]:
    """Update a knowledge article."""
    if request.category is not None and not is_valid_category(request.category):
        raise HTTPException(status_code=400, detail=f"Invalid category: {request.category}")

    engine = await _get_engine()

    updates: list[str] = []
    params: dict[str, Any] = {"id": str(article_id)}

    if request.title is not None:
        updates.append("title = :title")
        params["title"] = request.title
    if request.category is not None:
        updates.append("category = :category")
        params["category"] = request.category
    if request.content is not None:
        updates.append("content = :content")
        params["content"] = request.content
    if request.active is not None:
        updates.append("active = :active")
        params["active"] = request.active

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    needs_reindex = request.content is not None or request.title is not None
    if needs_reindex:
        updates.append("embedding_status = 'pending'")

    updates.append("updated_at = now()")
    set_clause = ", ".join(updates)

    async with engine.begin() as conn:
        result = await conn.execute(
            text(f"""
                UPDATE knowledge_articles
                SET {set_clause}
                WHERE id = :id
                RETURNING id, title, category, active, embedding_status, updated_at
            """),
            params,
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Article not found")

    article = dict(row._mapping)

    if needs_reindex:
        _dispatch_embedding(str(article_id))

    msg = "Article updated. Embedding regeneration queued." if needs_reindex else "Article updated."
    return {"article": article, "message": msg}


@router.delete("/articles/{article_id}")
async def delete_article(article_id: UUID, _: dict[str, Any] = _admin_dep) -> dict[str, Any]:
    """Delete (deactivate) a knowledge article."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                UPDATE knowledge_articles
                SET active = false, updated_at = now()
                WHERE id = :id
                RETURNING id, title
            """),
            {"id": str(article_id)},
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Article not found")

    return {"message": f"Article '{row.title}' deactivated"}


@router.get("/categories")
async def list_categories(_: dict[str, Any] = _analyst_dep) -> dict[str, Any]:
    """List all knowledge base categories with article counts."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT category, COUNT(*) AS count,
                       COUNT(*) FILTER (WHERE active) AS active_count
                FROM knowledge_articles
                GROUP BY category
                ORDER BY category
            """)
        )
        categories = [dict(row._mapping) for row in result]

    return {"categories": categories}
