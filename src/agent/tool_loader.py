"""Tool loader with DB overrides.

Merges tool definitions from code (tools.py) with DB overrides
from tool_description_overrides table. Falls back to code defaults
if DB is unavailable.
"""

from __future__ import annotations

import copy
import logging
import time
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from src.agent.tools import ALL_TOOLS

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# ── Cache ──────────────────────────────────────────────────
_tools_cache: list[dict[str, Any]] = []
_tools_cache_ts: float = 0.0

TOOLS_CACHE_REDIS_KEY = "tools:overrides_cache_ts"


async def get_tools_with_overrides(
    engine: AsyncEngine, redis: Any = None
) -> list[dict[str, Any]]:
    """Load ALL_TOOLS and merge active DB overrides.

    Cached in-process; invalidated when Redis key changes.
    Returns a new list (does not mutate ALL_TOOLS).
    Falls back to unmodified ALL_TOOLS if DB is unavailable.
    """
    global _tools_cache, _tools_cache_ts

    # Check Redis invalidation signal
    if redis is not None:
        try:
            raw = await redis.get(TOOLS_CACHE_REDIS_KEY)
            remote_ts = float(raw) if raw else 0.0
        except Exception:
            remote_ts = 0.0
        if remote_ts > _tools_cache_ts and _tools_cache:
            _tools_cache = []

    if _tools_cache:
        return _tools_cache

    overrides: dict[str, dict[str, Any]] = {}

    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    SELECT tool_name, description, input_schema_override
                    FROM tool_description_overrides
                    WHERE is_active = true
                """)
            )
            for row in result:
                overrides[row.tool_name] = {
                    "description": row.description,
                    "input_schema_override": row.input_schema_override,
                }
    except Exception:
        logger.warning("Failed to load tool overrides from DB, using code defaults")
        return list(ALL_TOOLS)

    if not overrides:
        result_tools = list(ALL_TOOLS)
    else:
        merged: list[dict[str, Any]] = []
        for tool in ALL_TOOLS:
            if tool["name"] in overrides:
                tool_copy = copy.deepcopy(tool)
                override = overrides[tool["name"]]
                if override.get("description"):
                    tool_copy["description"] = override["description"]
                if override.get("input_schema_override"):
                    tool_copy["input_schema"] = override["input_schema_override"]
                merged.append(tool_copy)
            else:
                merged.append(tool)
        result_tools = merged

    _tools_cache = result_tools
    _tools_cache_ts = time.time()
    return result_tools
