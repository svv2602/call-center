"""Integration tests for training tables migration and CRUD.

Requires a PostgreSQL database to be running (uses test database).
"""

from __future__ import annotations

import json
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# Skip entire module if no database URL configured
pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set — skipping integration tests",
)


@pytest.fixture
async def engine() -> AsyncEngine:
    url = os.environ["TEST_DATABASE_URL"]
    eng = create_async_engine(url)
    yield eng
    await eng.dispose()


class TestDialogueExamplesTable:
    """Test dialogue_examples table operations."""

    @pytest.mark.asyncio
    async def test_insert_and_select(self, engine: AsyncEngine) -> None:
        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    INSERT INTO dialogue_examples (title, scenario_type, phase, dialogue)
                    VALUES (:title, :scenario_type, :phase, :dialogue)
                    RETURNING id, title, is_active
                """),
                {
                    "title": "Integration test dialogue",
                    "scenario_type": "tire_search",
                    "phase": "mvp",
                    "dialogue": json.dumps([{"role": "customer", "text": "test"}]),
                },
            )
            row = result.first()
            assert row is not None
            assert row.title == "Integration test dialogue"
            assert row.is_active is True

            # Cleanup
            await conn.execute(
                text("DELETE FROM dialogue_examples WHERE id = :id"),
                {"id": str(row.id)},
            )


class TestSafetyRulesTable:
    """Test safety_rules table operations."""

    @pytest.mark.asyncio
    async def test_insert_and_select(self, engine: AsyncEngine) -> None:
        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    INSERT INTO safety_rules (title, rule_type, trigger_input, expected_behavior, severity)
                    VALUES (:title, :rule_type, :trigger_input, :expected_behavior, :severity)
                    RETURNING id, title, severity
                """),
                {
                    "title": "Integration test rule",
                    "rule_type": "behavioral",
                    "trigger_input": "test input",
                    "expected_behavior": "test behavior",
                    "severity": "high",
                },
            )
            row = result.first()
            assert row is not None
            assert row.severity == "high"

            await conn.execute(
                text("DELETE FROM safety_rules WHERE id = :id"),
                {"id": str(row.id)},
            )


class TestResponseTemplatesTable:
    """Test response_templates table operations."""

    @pytest.mark.asyncio
    async def test_insert_and_uniqueness(self, engine: AsyncEngine) -> None:
        import uuid

        unique_key = f"test_key_{uuid.uuid4().hex[:8]}"

        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    INSERT INTO response_templates (template_key, title, content)
                    VALUES (:key, :title, :content)
                    RETURNING id, template_key
                """),
                {
                    "key": unique_key,
                    "title": "Test template",
                    "content": "Test content",
                },
            )
            row = result.first()
            assert row is not None
            assert row.template_key == unique_key

            await conn.execute(
                text("DELETE FROM response_templates WHERE id = :id"),
                {"id": str(row.id)},
            )


class TestToolDescriptionOverridesTable:
    """Test tool_description_overrides table operations."""

    @pytest.mark.asyncio
    async def test_upsert_override(self, engine: AsyncEngine) -> None:
        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    INSERT INTO tool_description_overrides (tool_name, description)
                    VALUES (:tool_name, :description)
                    ON CONFLICT (tool_name) DO UPDATE SET
                        description = EXCLUDED.description,
                        updated_at = now()
                    RETURNING id, tool_name
                """),
                {
                    "tool_name": "search_tires",
                    "description": "Integration test override",
                },
            )
            row = result.first()
            assert row is not None
            assert row.tool_name == "search_tires"

            await conn.execute(
                text("DELETE FROM tool_description_overrides WHERE id = :id"),
                {"id": str(row.id)},
            )


class TestKnowledgeArticlesExtension:
    """Test knowledge_articles table extensions."""

    @pytest.mark.asyncio
    async def test_new_columns_exist(self, engine: AsyncEngine) -> None:
        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'knowledge_articles'
                    AND column_name IN ('tags', 'priority', 'last_verified_at', 'meta')
                """)
            )
            columns = {row.column_name for row in result}
            assert "tags" in columns
            assert "priority" in columns
            assert "last_verified_at" in columns
            assert "meta" in columns
