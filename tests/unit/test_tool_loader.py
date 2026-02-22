"""Unit tests for tool_loader — DB overrides merge logic."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.agent.tool_loader as _loader_mod
from src.agent.tool_loader import get_tools_with_overrides
from src.agent.tools import ALL_TOOLS


@pytest.fixture(autouse=True)
def _reset_tools_cache():
    """Clear module-level tools cache between tests."""
    _loader_mod._tools_cache = []
    _loader_mod._tools_cache_ts = 0.0
    yield
    _loader_mod._tools_cache = []
    _loader_mod._tools_cache_ts = 0.0


def _make_engine(rows: list[MagicMock] | None = None):
    """Create mock engine that returns given rows from SELECT."""
    mock_conn = AsyncMock()
    mock_engine = AsyncMock()

    @asynccontextmanager
    async def fake_begin():
        yield mock_conn

    mock_engine.begin = fake_begin

    mock_result = MagicMock()
    mock_result.__iter__ = lambda self: iter(rows or [])
    mock_conn.execute = AsyncMock(return_value=mock_result)

    return mock_engine, mock_conn


def _make_override_row(
    tool_name: str,
    description: str | None = None,
    input_schema_override: dict | None = None,
) -> MagicMock:
    row = MagicMock()
    row.tool_name = tool_name
    row.description = description
    row.input_schema_override = input_schema_override
    return row


class TestGetToolsWithOverrides:
    """Test get_tools_with_overrides merge logic."""

    @pytest.mark.asyncio
    async def test_returns_all_tools_when_no_overrides(self) -> None:
        engine, _ = _make_engine(rows=[])
        tools = await get_tools_with_overrides(engine)
        assert len(tools) == len(ALL_TOOLS)
        assert [t["name"] for t in tools] == [t["name"] for t in ALL_TOOLS]

    @pytest.mark.asyncio
    async def test_merges_description_override(self) -> None:
        row = _make_override_row("search_tires", description="Пошук шин (кастомний)")
        engine, _ = _make_engine(rows=[row])

        tools = await get_tools_with_overrides(engine)

        search = next(t for t in tools if t["name"] == "search_tires")
        assert search["description"] == "Пошук шин (кастомний)"

    @pytest.mark.asyncio
    async def test_merges_input_schema_override(self) -> None:
        custom_schema = {"type": "object", "properties": {"q": {"type": "string"}}}
        row = _make_override_row(
            "check_availability", input_schema_override=custom_schema
        )
        engine, _ = _make_engine(rows=[row])

        tools = await get_tools_with_overrides(engine)

        check = next(t for t in tools if t["name"] == "check_availability")
        assert check["input_schema"] == custom_schema

    @pytest.mark.asyncio
    async def test_non_overridden_tools_unchanged(self) -> None:
        row = _make_override_row("search_tires", description="Custom")
        engine, _ = _make_engine(rows=[row])

        tools = await get_tools_with_overrides(engine)

        # check_availability should be unchanged
        original = next(t for t in ALL_TOOLS if t["name"] == "check_availability")
        merged = next(t for t in tools if t["name"] == "check_availability")
        assert merged["description"] == original["description"]

    @pytest.mark.asyncio
    async def test_does_not_mutate_all_tools(self) -> None:
        original_desc = ALL_TOOLS[0]["description"]
        row = _make_override_row(ALL_TOOLS[0]["name"], description="MUTATED")
        engine, _ = _make_engine(rows=[row])

        await get_tools_with_overrides(engine)

        assert ALL_TOOLS[0]["description"] == original_desc

    @pytest.mark.asyncio
    async def test_deep_copy_on_override(self) -> None:
        """Overridden tools should be deep copies, not references."""
        row = _make_override_row("search_tires", description="Deep copy test")
        engine, _ = _make_engine(rows=[row])

        tools = await get_tools_with_overrides(engine)

        search = next(t for t in tools if t["name"] == "search_tires")
        original = next(t for t in ALL_TOOLS if t["name"] == "search_tires")
        assert search is not original
        assert search["input_schema"] is not original["input_schema"]

    @pytest.mark.asyncio
    async def test_fallback_on_db_error(self) -> None:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=RuntimeError("DB connection failed"))
        engine = AsyncMock()

        @asynccontextmanager
        async def failing_begin():
            yield mock_conn

        engine.begin = failing_begin

        tools = await get_tools_with_overrides(engine)

        assert len(tools) == len(ALL_TOOLS)
        # Should be a list copy, not the original tuple/list
        assert tools is not ALL_TOOLS

    @pytest.mark.asyncio
    async def test_returns_correct_count_with_overrides(self) -> None:
        """Override should not add or remove tools, only modify existing."""
        rows = [
            _make_override_row("search_tires", description="A"),
            _make_override_row("book_fitting", description="B"),
        ]
        engine, _ = _make_engine(rows=rows)

        tools = await get_tools_with_overrides(engine)

        assert len(tools) == len(ALL_TOOLS)

    @pytest.mark.asyncio
    async def test_ignores_empty_description_override(self) -> None:
        """Override with empty/None description should not clear the original."""
        original = next(t for t in ALL_TOOLS if t["name"] == "search_tires")
        row = _make_override_row(
            "search_tires", description=None, input_schema_override=None
        )
        engine, _ = _make_engine(rows=[row])

        tools = await get_tools_with_overrides(engine)

        search = next(t for t in tools if t["name"] == "search_tires")
        # With both overrides empty/None, deep copy still happens but desc unchanged
        assert search["description"] == original["description"]
