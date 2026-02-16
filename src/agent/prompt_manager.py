"""Prompt version management.

Stores and retrieves prompt versions from PostgreSQL.
Falls back to hardcoded prompt if database is unavailable.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from src.agent.prompts import (
    ERROR_TEXT,
    FAREWELL_TEXT,
    GREETING_TEXT,
    ORDER_CANCELLED_TEXT,
    PROMPT_VERSION,
    SILENCE_PROMPT_TEXT,
    SYSTEM_PROMPT,
    TRANSFER_TEXT,
    WAIT_TEXT,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


class PromptManager:
    """Manages prompt versions stored in PostgreSQL."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def get_active_prompt(self) -> dict[str, Any]:
        """Get the currently active prompt version.

        Returns:
            Dict with id, name, system_prompt, tools_config, metadata.
            Falls back to hardcoded prompt if DB unavailable.
        """
        try:
            async with self._engine.begin() as conn:
                result = await conn.execute(
                    text("""
                        SELECT id, name, system_prompt, tools_config, metadata
                        FROM prompt_versions
                        WHERE is_active = true
                        ORDER BY created_at DESC
                        LIMIT 1
                    """)
                )
                row = result.first()

            if row:
                return dict(row._mapping)

        except Exception:
            logger.warning("Failed to load prompt from DB, using hardcoded fallback")

        return {
            "id": None,
            "name": PROMPT_VERSION,
            "system_prompt": SYSTEM_PROMPT,
            "tools_config": None,
            "metadata": {},
        }

    async def create_version(
        self,
        name: str,
        system_prompt: str,
        tools_config: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new prompt version."""
        import json

        async with self._engine.begin() as conn:
            result = await conn.execute(
                text("""
                    INSERT INTO prompt_versions (name, system_prompt, tools_config, metadata)
                    VALUES (:name, :system_prompt, :tools_config, :metadata)
                    RETURNING id, name, system_prompt, is_active, created_at
                """),
                {
                    "name": name,
                    "system_prompt": system_prompt,
                    "tools_config": json.dumps(tools_config) if tools_config else None,
                    "metadata": json.dumps(metadata or {}),
                },
            )
            row = result.first()
            if row is None:
                msg = "Expected row from INSERT RETURNING"
                raise RuntimeError(msg)
            return dict(row._mapping)

    async def activate_version(self, version_id: UUID) -> dict[str, Any]:
        """Activate a prompt version (deactivates all others)."""
        async with self._engine.begin() as conn:
            # Deactivate all
            await conn.execute(
                text("UPDATE prompt_versions SET is_active = false WHERE is_active = true")
            )
            # Activate target
            result = await conn.execute(
                text("""
                    UPDATE prompt_versions
                    SET is_active = true
                    WHERE id = :version_id
                    RETURNING id, name, is_active, created_at
                """),
                {"version_id": str(version_id)},
            )
            row = result.first()
            if not row:
                msg = f"Prompt version {version_id} not found"
                raise ValueError(msg)
            return dict(row._mapping)

    async def list_versions(self) -> list[dict[str, Any]]:
        """List all prompt versions."""
        async with self._engine.begin() as conn:
            result = await conn.execute(
                text("""
                    SELECT id, name, is_active, metadata, created_at
                    FROM prompt_versions
                    ORDER BY created_at DESC
                """)
            )
            return [dict(row._mapping) for row in result]

    async def get_active_templates(self) -> dict[str, str]:
        """Get active response templates from DB.

        Returns:
            Dict mapping template_key → content.
            Falls back to hardcoded constants from prompts.py if DB unavailable.
        """
        fallback = {
            "greeting": GREETING_TEXT,
            "farewell": FAREWELL_TEXT,
            "silence_prompt": SILENCE_PROMPT_TEXT,
            "transfer": TRANSFER_TEXT,
            "error": ERROR_TEXT,
            "wait": WAIT_TEXT,
            "order_cancelled": ORDER_CANCELLED_TEXT,
        }

        try:
            async with self._engine.begin() as conn:
                result = await conn.execute(
                    text("""
                        SELECT template_key, content
                        FROM response_templates
                        WHERE is_active = true
                    """)
                )
                rows = result.fetchall()

            if rows:
                templates = {row.template_key: row.content for row in rows}
                # Fill in any missing keys from fallback
                for key, value in fallback.items():
                    if key not in templates:
                        templates[key] = value
                return templates

        except Exception:
            logger.warning("Failed to load templates from DB, using hardcoded fallback")

        return fallback

    async def get_version(self, version_id: UUID) -> dict[str, Any] | None:
        """Get a specific prompt version by ID."""
        async with self._engine.begin() as conn:
            result = await conn.execute(
                text("""
                    SELECT id, name, system_prompt, tools_config, is_active, metadata, created_at
                    FROM prompt_versions
                    WHERE id = :version_id
                """),
                {"version_id": str(version_id)},
            )
            row = result.first()
            return dict(row._mapping) if row else None
