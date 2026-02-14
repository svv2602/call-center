"""Knowledge base CRUD API endpoints.

Manage knowledge articles: create, read, update, delete.
Triggers embedding regeneration on content changes.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.config import get_settings

if TYPE_CHECKING:
    from uuid import UUID

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/knowledge", tags=["knowledge"])

_engine: AsyncEngine | None = None


async def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database.url)
    return _engine


class ArticleCreateRequest(BaseModel):
    title: str
    category: str
    content: str


class ArticleUpdateRequest(BaseModel):
    title: str | None = None
    category: str | None = None
    content: str | None = None
    active: bool | None = None


@router.get("/articles")
async def list_articles(
    category: str | None = Query(None),
    active: bool | None = Query(None),
    search: str | None = Query(None, description="Full-text search in title and content"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
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
                SELECT id, title, category, active, created_at, updated_at
                FROM knowledge_articles
                WHERE {where_clause}
                ORDER BY category, title
                LIMIT :limit OFFSET :offset
            """),
            params,
        )
        articles = [dict(row._mapping) for row in result]

    return {"total": total, "articles": articles}


@router.get("/articles/{article_id}")
async def get_article(article_id: UUID) -> dict[str, Any]:
    """Get a specific article with full content."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT id, title, category, content, active, created_at, updated_at
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
async def create_article(request: ArticleCreateRequest) -> dict[str, Any]:
    """Create a new knowledge article."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                INSERT INTO knowledge_articles (title, category, content)
                VALUES (:title, :category, :content)
                RETURNING id, title, category, active, created_at
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

    logger.info("Knowledge article created: %s (%s)", article["title"], article["id"])
    return {"article": article, "message": "Article created. Run embedding generation to index."}


@router.patch("/articles/{article_id}")
async def update_article(article_id: UUID, request: ArticleUpdateRequest) -> dict[str, Any]:
    """Update a knowledge article."""
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

    updates.append("updated_at = now()")
    set_clause = ", ".join(updates)

    async with engine.begin() as conn:
        result = await conn.execute(
            text(f"""
                UPDATE knowledge_articles
                SET {set_clause}
                WHERE id = :id
                RETURNING id, title, category, active, updated_at
            """),
            params,
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Article not found")

    needs_reindex = request.content is not None or request.title is not None
    msg = "Article updated. Embeddings need regeneration." if needs_reindex else "Article updated."

    return {"article": dict(row._mapping), "message": msg}


@router.delete("/articles/{article_id}")
async def delete_article(article_id: UUID) -> dict[str, Any]:
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
async def list_categories() -> dict[str, Any]:
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
