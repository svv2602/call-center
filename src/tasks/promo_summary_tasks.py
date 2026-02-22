"""Celery tasks for generating concise promo summaries via LLM.

When a promotions article is created or updated, this task generates
a short (~150-200 char) summary that is injected into the system prompt
instead of the full article content — keeping prompt compact while
preserving key selling points.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import anthropic
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.config import get_settings
from src.tasks.celery_app import app

logger = logging.getLogger(__name__)

_SUMMARY_SYSTEM_PROMPT = """Ти — помічник для створення коротких підсумків акцій інтернет-магазину шин.

Твоє завдання: прочитати повний текст акції та створити СТИСЛИЙ підсумок українською мовою.

Вимоги до підсумку:
- Максимум 200 символів
- Тільки ключові факти: що пропонується, які бренди/товари, розмір знижки, термін дії
- Без маркетингу та закликів до дії
- Без markdown-розмітки
- Одним абзацом

Відповідай ТІЛЬКИ текстом підсумку, без пояснень."""


@app.task(
    name="src.tasks.promo_summary_tasks.generate_promo_summary",
    bind=True,
    max_retries=2,
    soft_time_limit=30,
    time_limit=45,
    queue="embeddings",
)  # type: ignore[untyped-decorator]
def generate_promo_summary(self: Any, article_id: str) -> dict[str, Any]:
    """Generate a concise promo summary for a knowledge article."""
    return asyncio.run(_generate_promo_summary_async(self, article_id))


async def _generate_promo_summary_async(task: Any, article_id: str) -> dict[str, Any]:
    """Async implementation of promo summary generation."""
    settings = get_settings()
    engine = create_async_engine(settings.database.url, pool_pre_ping=True)

    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    SELECT id, title, content, category
                    FROM knowledge_articles
                    WHERE id = :id AND active = true
                """),
                {"id": article_id},
            )
            row = result.first()

        if not row:
            return {"article_id": article_id, "error": "not_found"}

        if row.category != "promotions":
            return {"article_id": article_id, "status": "skipped", "reason": "not_promotions"}

        # Generate summary via Haiku
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic.api_key)
        content_truncated = row.content[:3000] if len(row.content) > 3000 else row.content

        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=_SUMMARY_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": f"Назва акції: {row.title}\n\nПовний текст:\n{content_truncated}"}
            ],
        )

        summary = response.content[0].text.strip() if response.content else ""

        if not summary:
            return {"article_id": article_id, "error": "empty_summary"}

        # Save summary
        async with engine.begin() as conn:
            await conn.execute(
                text("""
                    UPDATE knowledge_articles
                    SET promo_summary = :summary, updated_at = now()
                    WHERE id = :id
                """),
                {"id": article_id, "summary": summary},
            )

        # Invalidate promotions cache
        try:
            from redis import Redis as SyncRedis

            redis_url = settings.redis.url
            r = SyncRedis.from_url(redis_url)
            r.set("promotions:cache_ts", str(asyncio.get_event_loop().time()))
            r.close()
        except Exception:
            logger.debug("Could not invalidate promotions cache", exc_info=True)

        logger.info("Promo summary generated for %s: %s", article_id, summary[:80])
        return {"article_id": article_id, "status": "ok", "summary_length": len(summary)}

    except Exception as exc:
        logger.exception("Promo summary failed for %s", article_id)
        raise task.retry(countdown=30) from exc
    finally:
        await engine.dispose()


@app.task(
    name="src.tasks.promo_summary_tasks.generate_all_promo_summaries",
    bind=True,
    soft_time_limit=300,
    time_limit=360,
)  # type: ignore[untyped-decorator]
def generate_all_promo_summaries(self: Any) -> dict[str, Any]:
    """Generate summaries for all active promotions missing a summary."""
    return asyncio.run(_generate_all_promo_summaries_async())


async def _generate_all_promo_summaries_async() -> dict[str, Any]:
    settings = get_settings()
    engine = create_async_engine(settings.database.url, pool_pre_ping=True)

    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    SELECT id FROM knowledge_articles
                    WHERE active = true
                      AND category = 'promotions'
                      AND (promo_summary IS NULL OR promo_summary = '')
                """)
            )
            ids = [str(row.id) for row in result]

        for aid in ids:
            generate_promo_summary.delay(aid)

        logger.info("Dispatched promo summary generation for %d articles", len(ids))
        return {"dispatched": len(ids)}
    finally:
        await engine.dispose()
