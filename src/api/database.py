"""Shared async database engine for API routers.

Consolidates all per-router engine instances into a single connection pool.
All API routers should import `get_engine` from this module instead of
creating their own `create_async_engine` instances.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.config import get_settings

_engine: AsyncEngine | None = None


async def get_engine() -> AsyncEngine:
    """Return the shared async database engine, creating it on first call.

    Pool configuration:
      - pool_size=5: base number of persistent connections
      - max_overflow=10: extra connections under load (up to 15 total)
      - pool_pre_ping=True: verify connections before use
      - pool_recycle=1800: recycle connections after 30 minutes
    """
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database.url,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=1800,
        )
    return _engine


async def dispose_engine() -> None:
    """Dispose the shared engine (call on app shutdown)."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
