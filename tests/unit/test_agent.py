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

    def test_all_mvp_tools_defined(self) -> None:
        tool_names = {t["name"] for t in MVP_TOOLS}
        assert tool_names == {
            "get_vehicle_tire_sizes",
            "search_tires",
            "check_availability",
            "transfer_to_operator",
        }

    def test_search_tires_schema(self) -> None:
        tool = next(t for t in MVP_TOOLS if t["name"] == "search_tires")
        schema = tool["input_schema"]
        props = schema["properties"]
        assert "width" in props
        assert "profile" in props
        assert "diameter" in props
        assert "season" in props
        assert props["season"]["enum"] == ["summer", "winter", "all_season"]
        # Tire dimensions and season are required to prevent premature search
        assert set(schema["required"]) == {"width", "profile", "diameter", "season"}

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


class TestLLMAgentInit:
    """Test LLMAgent initialization with custom tools."""

    def test_defaults_to_all_tools(self) -> None:
        from src.agent.agent import LLMAgent

        agent = LLMAgent(api_key="test-key")
        assert agent._tools == list(ALL_TOOLS)
        assert len(agent._tools) == len(ALL_TOOLS)

    def test_accepts_custom_tools(self) -> None:
        from src.agent.agent import LLMAgent

        custom = [{"name": "my_tool", "description": "desc", "input_schema": {"type": "object"}}]
        agent = LLMAgent(api_key="test-key", tools=custom)
        assert agent._tools == custom
        assert agent._tools is custom

    def test_custom_tools_not_all_tools(self) -> None:
        from src.agent.agent import LLMAgent

        custom = [ALL_TOOLS[0]]
        agent = LLMAgent(api_key="test-key", tools=custom)
        assert len(agent._tools) == 1
        assert len(agent._tools) != len(ALL_TOOLS)

    @pytest.mark.asyncio
    async def test_custom_tools_passed_to_api(self) -> None:
        """process_message should pass self._tools (not ALL_TOOLS) to Claude API."""
        from unittest.mock import AsyncMock, MagicMock

        from src.agent.agent import LLMAgent

        custom = [{"name": "test_tool", "description": "t", "input_schema": {"type": "object"}}]
        agent = LLMAgent(api_key="test-key", tools=custom)

        # Mock API response — end_turn with text
        mock_response = MagicMock()
        mock_response.stop_reason = "end_turn"
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Відповідь"
        mock_response.content = [text_block]

        agent._client = MagicMock()
        agent._client.messages = MagicMock()
        agent._client.messages.create = AsyncMock(return_value=mock_response)

        await agent.process_message("Тест", [])

        call_kwargs = agent._client.messages.create.call_args
        assert call_kwargs.kwargs["tools"] is custom


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
        assert PROMPT_VERSION == "v3.3-pronunciation"

    def test_prompt_has_order_capabilities(self) -> None:
        assert "замовлення" in SYSTEM_PROMPT.lower()

    def test_all_tools_count(self) -> None:
        assert len(ALL_TOOLS) == 14

    def test_season_hint_winter(self) -> None:
        import datetime
        from unittest.mock import patch

        from src.agent.agent import LLMAgent

        agent = LLMAgent(api_key="test-key")
        with patch("src.agent.agent.datetime") as mock_dt:
            mock_dt.date.today.return_value = datetime.date(2025, 1, 15)
            prompt = agent._build_system_prompt()
        assert "зимовий сезон" in prompt
        assert "Підказка по сезону" in prompt

    def test_season_hint_summer(self) -> None:
        import datetime
        from unittest.mock import patch

        from src.agent.agent import LLMAgent

        agent = LLMAgent(api_key="test-key")
        with patch("src.agent.agent.datetime") as mock_dt:
            mock_dt.date.today.return_value = datetime.date(2025, 7, 15)
            prompt = agent._build_system_prompt()
        assert "літній сезон" in prompt

    def test_season_hint_transition(self) -> None:
        import datetime
        from unittest.mock import patch

        from src.agent.agent import LLMAgent

        agent = LLMAgent(api_key="test-key")
        with patch("src.agent.agent.datetime") as mock_dt:
            mock_dt.date.today.return_value = datetime.date(2025, 4, 15)
            prompt = agent._build_system_prompt()
        assert "міжсезоння" in prompt

    def test_custom_system_prompt(self) -> None:
        from src.agent.agent import LLMAgent

        custom = "Custom prompt for testing"
        agent = LLMAgent(api_key="test-key", system_prompt=custom)
        prompt = agent._build_system_prompt()
        assert prompt.startswith(custom)
        assert "Підказка по сезону" in prompt

    def test_fallback_to_hardcoded_prompt(self) -> None:
        from src.agent.agent import LLMAgent

        agent = LLMAgent(api_key="test-key")
        prompt = agent._build_system_prompt()
        assert prompt.startswith(SYSTEM_PROMPT)

    def test_prompt_version_name_default(self) -> None:
        from src.agent.agent import LLMAgent

        agent = LLMAgent(api_key="test-key")
        assert agent.prompt_version_name == PROMPT_VERSION

    def test_prompt_version_name_custom(self) -> None:
        from src.agent.agent import LLMAgent

        agent = LLMAgent(api_key="test-key", prompt_version_name="v4.0-test")
        assert agent.prompt_version_name == "v4.0-test"


class TestProcessMessageHistory:
    """Test process_message conversation history handling."""

    @pytest.mark.asyncio
    async def test_no_duplicate_user_message(self) -> None:
        """process_message should not duplicate user_text if already last in history."""
        from unittest.mock import AsyncMock, MagicMock

        from src.agent.agent import LLMAgent

        agent = LLMAgent(api_key="test-key")

        mock_response = MagicMock()
        mock_response.stop_reason = "end_turn"
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Відповідь"
        mock_response.content = [text_block]

        agent._client = MagicMock()
        agent._client.messages = MagicMock()
        agent._client.messages.create = AsyncMock(return_value=mock_response)

        # History already contains the user message (as pipeline would add)
        history: list[dict[str, Any]] = [
            {"role": "assistant", "content": "Привіт!"},
            {"role": "user", "content": "шини"},
        ]
        await agent.process_message("шини", history)

        # The messages sent to API should have user-first + greeting + one user msg
        call_kwargs = agent._client.messages.create.call_args
        messages = call_kwargs.kwargs["messages"]
        user_msgs = [m for m in messages if m["role"] == "user" and m.get("content") == "шини"]
        assert len(user_msgs) == 1, f"Expected 1 'шини' message, got {len(user_msgs)}"

    @pytest.mark.asyncio
    async def test_user_first_message_ensured(self) -> None:
        """process_message should prepend synthetic user message when history starts with assistant."""
        from unittest.mock import AsyncMock, MagicMock

        from src.agent.agent import LLMAgent

        agent = LLMAgent(api_key="test-key")

        mock_response = MagicMock()
        mock_response.stop_reason = "end_turn"
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Відповідь"
        mock_response.content = [text_block]

        agent._client = MagicMock()
        agent._client.messages = MagicMock()
        agent._client.messages.create = AsyncMock(return_value=mock_response)

        # History starts with assistant (greeting)
        history: list[dict[str, Any]] = [
            {"role": "assistant", "content": "Добрий день!"},
        ]
        await agent.process_message("шини", history)

        call_kwargs = agent._client.messages.create.call_args
        messages = call_kwargs.kwargs["messages"]
        assert messages[0]["role"] == "user", f"First message should be user, got {messages[0]}"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "Добрий день!"

    @pytest.mark.asyncio
    async def test_user_message_added_when_not_present(self) -> None:
        """process_message should add user_text when not already in history."""
        from unittest.mock import AsyncMock, MagicMock

        from src.agent.agent import LLMAgent

        agent = LLMAgent(api_key="test-key")

        mock_response = MagicMock()
        mock_response.stop_reason = "end_turn"
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Відповідь"
        mock_response.content = [text_block]

        # Capture messages at call time (before they're mutated by assistant append)
        captured_messages: list[dict[str, Any]] = []

        async def capture_create(**kwargs: Any) -> MagicMock:
            captured_messages.extend(list(kwargs["messages"]))
            return mock_response

        agent._client = MagicMock()
        agent._client.messages = MagicMock()
        agent._client.messages.create = AsyncMock(side_effect=capture_create)

        # Empty history — process_message should add the user text
        history: list[dict[str, Any]] = []
        await agent.process_message("шини", history)

        assert len(captured_messages) == 1
        assert captured_messages[0] == {"role": "user", "content": "шини"}
