"""Unit tests for LLM Agent and ToolRouter."""

from __future__ import annotations

from typing import Any

import pytest

from src.agent.agent import ToolRouter
from src.agent.prompts import PROMPT_VERSION, SYSTEM_PROMPT
from src.agent.tools import ALL_TOOLS, MVP_TOOLS


class TestToolRouter:
    """Test ToolRouter dispatch."""

    @pytest.mark.asyncio
    async def test_execute_registered_handler(self) -> None:
        router = ToolRouter()

        async def mock_search(**kwargs: Any) -> dict[str, Any]:
            return {"items": [{"name": "Michelin"}], "total": 1}

        router.register("search_tires", mock_search)
        result = await router.execute("search_tires", {"brand": "Michelin"})
        assert result["total"] == 1

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self) -> None:
        router = ToolRouter()
        result = await router.execute("nonexistent_tool", {})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_execute_handler_error(self) -> None:
        router = ToolRouter()

        async def failing_handler(**kwargs: Any) -> dict[str, Any]:
            raise ValueError("API error")

        router.register("failing_tool", failing_handler)
        result = await router.execute("failing_tool", {})
        assert "error" in result
        assert "API error" in result["error"]


class TestMVPTools:
    """Test MVP tool definitions."""

    def test_all_three_tools_defined(self) -> None:
        tool_names = {t["name"] for t in MVP_TOOLS}
        assert tool_names == {"search_tires", "check_availability", "transfer_to_operator"}

    def test_search_tires_schema(self) -> None:
        tool = next(t for t in MVP_TOOLS if t["name"] == "search_tires")
        props = tool["input_schema"]["properties"]
        assert "vehicle_make" in props
        assert "width" in props
        assert "season" in props
        assert props["season"]["enum"] == ["summer", "winter", "all_season"]

    def test_transfer_to_operator_required_fields(self) -> None:
        tool = next(t for t in MVP_TOOLS if t["name"] == "transfer_to_operator")
        assert "required" in tool["input_schema"]
        assert set(tool["input_schema"]["required"]) == {"reason", "summary"}

    def test_transfer_reasons(self) -> None:
        tool = next(t for t in MVP_TOOLS if t["name"] == "transfer_to_operator")
        reasons = tool["input_schema"]["properties"]["reason"]["enum"]
        assert "customer_request" in reasons
        assert "cannot_help" in reasons
        assert "negative_emotion" in reasons


class TestSystemPrompt:
    """Test system prompt content."""

    def test_prompt_is_in_ukrainian(self) -> None:
        assert "українською" in SYSTEM_PROMPT

    def test_prompt_has_role(self) -> None:
        assert "голосовий асистент" in SYSTEM_PROMPT

    def test_prompt_has_response_rules(self) -> None:
        assert "2-3 речення" in SYSTEM_PROMPT

    def test_prompt_has_price_rule(self) -> None:
        assert "гривнях" in SYSTEM_PROMPT or "грн" in SYSTEM_PROMPT

    def test_prompt_version(self) -> None:
        assert PROMPT_VERSION == "v2.0-orders"

    def test_prompt_has_order_capabilities(self) -> None:
        assert "замовлення" in SYSTEM_PROMPT.lower()

    def test_all_tools_count(self) -> None:
        assert len(ALL_TOOLS) == 7
