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

# provider_key -> cached_input_price_per_1m. Separate from `_cache` because the
# rate is optional: a provider without a prompt cache, or a model LiteLLM has
# no cache_read cost for, is simply absent here.
_cached_input: dict[str, float] = {}

# Fallback when cache is empty or provider unknown
_FALLBACK: tuple[float, float] = (0.30, 2.50)


def get_pricing(provider_key: str) -> tuple[float, float]:
    """Return (input_price_per_1m, output_price_per_1m) — sync, safe for hot path."""
    return _cache.get(provider_key, _FALLBACK)


def get_cached_input_price(provider_key: str) -> float | None:
    """Return the per-1M rate for cache-read input tokens, or None if unknown.

    None means «bill these at the standard input rate». There is deliberately no
    fallback multiplier: the one this replaced assumed every provider halved the
    price, and not one of the ten models in production does — the real discounts
    run from 0.25x down to 0.10x.
    """
    return _cached_input.get(provider_key)


async def refresh_from_db(engine: Any) -> None:
    """Reload all pricing from llm_model_pricing into memory."""
    try:
        async with engine.begin() as conn:
            rows = await conn.execute(
                text(
                    "SELECT provider_key, input_price_per_1m, output_price_per_1m, "
                    "cached_input_price_per_1m FROM llm_model_pricing"
                )
            )
            new: dict[str, tuple[float, float]] = {}
            new_cached: dict[str, float] = {}
            for r in rows:
                new[r.provider_key] = (float(r.input_price_per_1m), float(r.output_price_per_1m))
                if r.cached_input_price_per_1m is not None:
                    new_cached[r.provider_key] = float(r.cached_input_price_per_1m)

        _cache.clear()
        _cache.update(new)
        _cached_input.clear()
        _cached_input.update(new_cached)
        logger.info("Pricing cache refreshed: %d entries", len(_cache))
    except Exception:
        logger.warning("Failed to refresh pricing cache from DB", exc_info=True)


def invalidate() -> None:
    """Clear the in-memory cache (call refresh_from_db afterwards)."""
    _cache.clear()
    _cached_input.clear()
