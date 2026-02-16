"""Tool loader with DB overrides.

Merges tool definitions from code (tools.py) with DB overrides
from tool_description_overrides table. Falls back to code defaults
if DB is unavailable.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from src.agent.tools import ALL_TOOLS

logger = logging.getLogger(__name__)


async def get_tools_with_overrides(engine: AsyncEngine) -> list[dict[str, Any]]:
    """Load ALL_TOOLS and merge active DB overrides.

    Returns a new list (does not mutate ALL_TOOLS).
    Falls back to unmodified ALL_TOOLS if DB is unavailable.
    """
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
        return list(ALL_TOOLS)

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
    return merged
