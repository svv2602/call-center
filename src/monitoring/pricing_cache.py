"""In-memory cache for LLM pricing from the llm_model_pricing table.

Pattern: synchronous reads (hot path in CostBreakdown) + async refresh from DB.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

# provider_key -> (input_price_per_1m, output_price_per_1m)
_cache: dict[str, tuple[float, float]] = {}

# Fallback when cache is empty or provider unknown
_FALLBACK: tuple[float, float] = (0.30, 2.50)


def get_pricing(provider_key: str) -> tuple[float, float]:
    """Return (input_price_per_1m, output_price_per_1m) — sync, safe for hot path."""
    return _cache.get(provider_key, _FALLBACK)


async def refresh_from_db(engine: Any) -> None:
    """Reload all pricing from llm_model_pricing into memory."""
    try:
        async with engine.begin() as conn:
            rows = await conn.execute(
                text(
                    "SELECT provider_key, input_price_per_1m, output_price_per_1m "
                    "FROM llm_model_pricing"
                )
            )
            new: dict[str, tuple[float, float]] = {}
            for r in rows:
                new[r.provider_key] = (float(r.input_price_per_1m), float(r.output_price_per_1m))

        _cache.clear()
        _cache.update(new)
        logger.info("Pricing cache refreshed: %d entries", len(_cache))
    except Exception:
        logger.warning("Failed to refresh pricing cache from DB", exc_info=True)


def invalidate() -> None:
    """Clear the in-memory cache (call refresh_from_db afterwards)."""
    _cache.clear()
