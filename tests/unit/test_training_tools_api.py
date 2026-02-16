"""Unit tests for training tools API."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.agent.tools import ALL_TOOLS


class TestToolNames:
    """Test tool name validation."""

    def test_all_tools_have_names(self) -> None:
        for tool in ALL_TOOLS:
            assert "name" in tool
            assert isinstance(tool["name"], str)
            assert len(tool["name"]) > 0

    def test_all_tools_have_descriptions(self) -> None:
        for tool in ALL_TOOLS:
            assert "description" in tool
            assert len(tool["description"]) > 0

    def test_all_tools_have_input_schema(self) -> None:
        for tool in ALL_TOOLS:
            assert "input_schema" in tool
            assert tool["input_schema"]["type"] == "object"

    def test_canonical_tools_present(self) -> None:
        names = [t["name"] for t in ALL_TOOLS]
        assert "search_tires" in names
        assert "check_availability" in names
        assert "transfer_to_operator" in names
        assert "create_order_draft" in names
        assert "confirm_order" in names
        assert "book_fitting" in names
        assert "search_knowledge_base" in names


class TestToolEndpoints:
    """Test tool API endpoint logic."""

    @pytest.mark.asyncio
    async def test_update_validates_tool_name(self) -> None:
        """Invalid tool name should raise 404."""
        from fastapi import HTTPException

        from src.api.training_tools import ToolOverrideRequest, update_tool_override

        req = ToolOverrideRequest(description="test")
        with pytest.raises(HTTPException) as exc_info:
            await update_tool_override("nonexistent_tool", req, {})
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_validates_tool_name(self) -> None:
        """Invalid tool name should raise 404."""
        from fastapi import HTTPException

        from src.api.training_tools import delete_tool_override

        with pytest.raises(HTTPException) as exc_info:
            await delete_tool_override("nonexistent_tool", {})
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_list_tools_returns_all(self) -> None:
        """List should return all tools from ALL_TOOLS."""
        from src.api.training_tools import list_tools

        mock_engine = AsyncMock()
        mock_conn = AsyncMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_result = AsyncMock()
        mock_result.__iter__ = lambda self: iter([])
        mock_conn.execute = AsyncMock(return_value=mock_result)

        with patch("src.api.training_tools._get_engine", new_callable=AsyncMock, return_value=mock_engine):
            result = await list_tools({})
            assert len(result["items"]) == len(ALL_TOOLS)
            assert all(item["has_override"] is False for item in result["items"])
