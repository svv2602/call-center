"""Sync LLM pricing catalog from LiteLLM community JSON.

Daily task fetches model_prices_and_context_window.json from GitHub,
filters relevant providers, and upserts into llm_pricing_catalog.
Also auto-updates prices in llm_model_pricing for linked models.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from celery import shared_task  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

LITELLM_PRICING_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)

SUPPORTED_PROVIDERS = {"openai", "anthropic", "deepseek", "vertex_ai-language-models"}

# Map litellm_provider → our provider_type
PROVIDER_MAP = {
    "openai": "openai",
    "anthropic": "anthropic",
    "deepseek": "deepseek",
    "vertex_ai-language-models": "gemini",
}


def _generate_display_name(model_key: str) -> str:
    """Generate human-readable display name from model key.

    Examples:
        gpt-5-mini → GPT 5 Mini
        claude-sonnet-4-5 → Claude Sonnet 4 5
        gemini-2.5-flash → Gemini 2.5 Flash
    """
    # Strip common provider prefixes
    name = model_key
    for prefix in ("openai/", "anthropic/", "deepseek/", "vertex_ai/"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break

    # Replace separators with spaces
    name = name.replace("-", " ").replace("_", " ")
    # Capitalize words, but keep version numbers as-is
    parts = []
    for word in name.split():
        if re.match(r"^\d", word):
            parts.append(word)
        else:
            parts.append(word.capitalize())
    return " ".join(parts)


def _parse_litellm_json(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse LiteLLM JSON into catalog rows.

    Filters: mode=chat, supported providers, skip fine-tuned models.
    """
    rows = []
    for key, info in data.items():
        if not isinstance(info, dict):
            continue

        mode = info.get("mode")
        if mode != "chat":
            continue

        provider = info.get("litellm_provider", "")
        if provider not in SUPPORTED_PROVIDERS:
            continue

        # Skip fine-tuned models
        if key.startswith("ft:"):
            continue

        input_cost = info.get("input_cost_per_token")
        output_cost = info.get("output_cost_per_token")
        if input_cost is None or output_cost is None:
            continue

        provider_type = PROVIDER_MAP.get(provider)
        if not provider_type:
            continue

        rows.append({
            "model_key": key,
            "provider_type": provider_type,
            "display_name": _generate_display_name(key),
            "input_price_per_1m": round(float(input_cost) * 1_000_000, 4),
            "output_price_per_1m": round(float(output_cost) * 1_000_000, 4),
            "max_input_tokens": info.get("max_input_tokens"),
            "max_output_tokens": info.get("max_output_tokens"),
        })

    return rows


async def _do_sync() -> dict[str, int]:
    """Core sync logic — fetch, parse, upsert."""
    import aiohttp
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from src.config import get_settings

    settings = get_settings()

    # Fetch LiteLLM JSON
    timeout = aiohttp.ClientTimeout(total=30)
    async with (
        aiohttp.ClientSession(timeout=timeout) as session,
        session.get(LITELLM_PRICING_URL) as resp,
    ):
        resp.raise_for_status()
        raw_data = await resp.json(content_type=None)

    rows = _parse_litellm_json(raw_data)
    if not rows:
        logger.warning("pricing_sync: no models parsed from LiteLLM JSON")
        return {"inserted": 0, "updated": 0, "auto_updated": 0}

    engine = create_async_engine(settings.database.url, pool_pre_ping=True)
    sync_time = datetime.now(UTC)

    try:
        async with engine.begin() as conn:
            # Detect first sync: if catalog is empty, mark all as is_new=false
            count_result = await conn.execute(
                text("SELECT COUNT(*) AS cnt FROM llm_pricing_catalog")
            )
            is_first_sync = count_result.scalar() == 0

            inserted = 0
            updated = 0

            for row in rows:
                result = await conn.execute(
                    text("""
                        INSERT INTO llm_pricing_catalog
                            (model_key, provider_type, display_name,
                             input_price_per_1m, output_price_per_1m,
                             max_input_tokens, max_output_tokens,
                             is_new, synced_at)
                        VALUES
                            (:model_key, :provider_type, :display_name,
                             :input_price, :output_price,
                             :max_input, :max_output,
                             :is_new, :synced_at)
                        ON CONFLICT (model_key) DO UPDATE SET
                            input_price_per_1m  = EXCLUDED.input_price_per_1m,
                            output_price_per_1m = EXCLUDED.output_price_per_1m,
                            max_input_tokens    = EXCLUDED.max_input_tokens,
                            max_output_tokens   = EXCLUDED.max_output_tokens,
                            display_name        = EXCLUDED.display_name,
                            synced_at           = EXCLUDED.synced_at
                        RETURNING (xmax = 0) AS is_insert
                    """),
                    {
                        "model_key": row["model_key"],
                        "provider_type": row["provider_type"],
                        "display_name": row["display_name"],
                        "input_price": row["input_price_per_1m"],
                        "output_price": row["output_price_per_1m"],
                        "max_input": row["max_input_tokens"],
                        "max_output": row["max_output_tokens"],
                        "is_new": not is_first_sync,
                        "synced_at": sync_time,
                    },
                )
                r = result.first()
                if r and r.is_insert:
                    inserted += 1
                else:
                    updated += 1

            # Auto-update prices in llm_model_pricing for linked models
            auto_result = await conn.execute(
                text("""
                    UPDATE llm_model_pricing p
                    SET input_price_per_1m  = c.input_price_per_1m,
                        output_price_per_1m = c.output_price_per_1m,
                        updated_at          = now()
                    FROM llm_pricing_catalog c
                    WHERE p.catalog_model_key = c.model_key
                      AND (p.input_price_per_1m  != c.input_price_per_1m
                        OR p.output_price_per_1m != c.output_price_per_1m)
                """)
            )
            auto_updated = auto_result.rowcount

        # Save last sync timestamp to Redis
        try:
            import redis.asyncio as aioredis

            redis_client = aioredis.from_url(settings.redis.url)
            await redis_client.set(
                "llm_pricing:last_sync_at", sync_time.isoformat()
            )
            await redis_client.close()
        except Exception:
            logger.warning("pricing_sync: failed to update Redis timestamp", exc_info=True)

    finally:
        await engine.dispose()

    stats = {"inserted": inserted, "updated": updated, "auto_updated": auto_updated}
    logger.info("pricing_sync completed: %s", stats)
    return stats


@shared_task(
    name="src.tasks.pricing_sync.sync_llm_pricing_catalog",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    soft_time_limit=120,
    time_limit=180,
)
def sync_llm_pricing_catalog(self: Any, **kwargs: Any) -> dict[str, int]:
    """Celery task: sync LLM pricing catalog from LiteLLM."""
    import asyncio

    try:
        return asyncio.get_event_loop().run_until_complete(_do_sync())
    except Exception as exc:
        logger.error("pricing_sync failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc) from exc
