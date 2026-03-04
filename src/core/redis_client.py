"""Shared Redis client with connection pooling.

Provides a single Redis connection pool shared across all components
instead of 20+ independent Redis.from_url() calls scattered across
the codebase. This reduces connection count and simplifies lifecycle
management (single close on shutdown).

Two clients are maintained:
  - ``get_redis()`` — decode_responses=True (strings, for most API endpoints)
  - ``get_redis_binary()`` — decode_responses=False (bytes, for session/stock data)

Usage in API routers and other components::

    from src.core.redis_client import get_redis

    async def my_endpoint():
        redis = await get_redis()
        await redis.set("key", "value")
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Shared Redis instances (initialized lazily or explicitly via init_redis)
_redis_str: Any = None  # decode_responses=True
_redis_bin: Any = None  # decode_responses=False


async def init_redis(redis_url: str) -> None:
    """Initialize shared Redis clients from a URL.

    Called once during application startup (e.g. in src/main.py lifespan).
    """
    from redis.asyncio import Redis

    global _redis_str, _redis_bin
    _redis_str = Redis.from_url(redis_url, decode_responses=True)
    _redis_bin = Redis.from_url(redis_url, decode_responses=False)
    logger.info("Shared Redis clients initialized: %s", redis_url)


async def get_redis() -> Any:
    """Get the shared Redis client (decode_responses=True).

    Lazily initializes from settings if init_redis() was not called.
    """
    global _redis_str
    if _redis_str is None:
        from redis.asyncio import Redis

        from src.config import get_settings

        settings = get_settings()
        _redis_str = Redis.from_url(settings.redis.url, decode_responses=True)
    return _redis_str


async def get_redis_binary() -> Any:
    """Get the shared Redis client (decode_responses=False).

    Used for binary data like session serialization and stock cache.
    Lazily initializes from settings if init_redis() was not called.
    """
    global _redis_bin
    if _redis_bin is None:
        from redis.asyncio import Redis

        from src.config import get_settings

        settings = get_settings()
        _redis_bin = Redis.from_url(settings.redis.url, decode_responses=False)
    return _redis_bin


async def close_redis() -> None:
    """Close all shared Redis connections.

    Called during application shutdown.
    """
    global _redis_str, _redis_bin
    for client, label in [(_redis_str, "str"), (_redis_bin, "bin")]:
        if client is not None:
            try:
                await client.close()
            except Exception:
                logger.warning("Failed to close Redis client (%s)", label)
    _redis_str = None
    _redis_bin = None
    logger.info("Shared Redis clients closed")
