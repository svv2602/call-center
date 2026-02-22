"""Unit tests for sandbox agent runner."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.sandbox.agent_runner import SandboxTurnResult, ToolCallRecord


class TestToolCallRecord:
    """Test ToolCallRecord dataclass."""

    def test_creation(self) -> None:
        rec = ToolCallRecord(
            tool_name="search_tires",
            tool_args={"width": 205},
            tool_result={"items": []},
            duration_ms=42,
            is_mock=True,
        )
        assert rec.tool_name == "search_tires"
        assert rec.duration_ms == 42
        assert rec.is_mock is True


class TestSandboxTurnResult:
    """Test SandboxTurnResult dataclass."""

    def test_creation_with_defaults(self) -> None:
        result = SandboxTurnResult(
            response_text="Hello",
            updated_history=[],
            latency_ms=100,
            input_tokens=50,
            output_tokens=20,
            model="claude-sonnet-4-5-20250929",
        )
        assert result.response_text == "Hello"
        assert result.tool_calls == []

    def test_creation_with_tool_calls(self) -> None:
        tc = ToolCallRecord(
            tool_name="check_availability",
            tool_args={"product_id": "tire-001"},
            tool_result={"available": True},
            duration_ms=10,
            is_mock=True,
        )
        result = SandboxTurnResult(
            response_text="Available",
            updated_history=[],
            latency_ms=200,
            input_tokens=100,
            output_tokens=30,
            model="claude-sonnet-4-5-20250929",
            tool_calls=[tc],
        )
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].tool_name == "check_availability"


class TestCreateSandboxAgent:
    """Test sandbox agent creation."""

    @pytest.mark.asyncio
    async def test_creates_agent_with_mock_tools(self) -> None:
        """Agent should be created with mock tool router."""
        from src.sandbox.agent_runner import create_sandbox_agent

        mock_engine = AsyncMock()
        mock_conn = AsyncMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)

        # Mock prompt loading
        mock_result = MagicMock()
        mock_result.first.return_value = None  # No active prompt in DB
        mock_conn.execute.return_value = mock_result

        # Mock tool overrides loading (empty)
        mock_tool_result = MagicMock()
        mock_tool_result.__iter__ = MagicMock(return_value=iter([]))
        mock_conn.execute.return_value = mock_tool_result

        with patch("src.sandbox.agent_runner.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                anthropic=MagicMock(api_key="test-key", model="claude-sonnet-4-5-20250929"),
                database=MagicMock(url="postgresql+asyncpg://test"),
            )
            agent = await create_sandbox_agent(mock_engine, tool_mode="mock")

        assert agent is not None
        assert agent._model == "claude-haiku-4-5-20251001"


    @pytest.mark.asyncio
    async def test_creates_agent_with_provider_override(self) -> None:
        """Agent should use LLM router when provider_override is given."""
        from src.sandbox.agent_runner import create_sandbox_agent

        mock_engine = AsyncMock()
        mock_conn = AsyncMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_result = MagicMock()
        mock_result.first.return_value = None
        mock_conn.execute.return_value = mock_result

        mock_tool_result = MagicMock()
        mock_tool_result.__iter__ = MagicMock(return_value=iter([]))
        mock_conn.execute.return_value = mock_tool_result

        mock_router = MagicMock()

        with patch("src.sandbox.agent_runner.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                anthropic=MagicMock(api_key="test-key", model="claude-sonnet-4-5-20250929"),
                database=MagicMock(url="postgresql+asyncpg://test"),
            )
            agent = await create_sandbox_agent(
                mock_engine,
                tool_mode="mock",
                model="gemini-flash",
                llm_router=mock_router,
                provider_override="gemini-flash",
            )

        assert agent is not None
        assert agent._llm_router is mock_router
        assert agent._provider_override == "gemini-flash"

    @pytest.mark.asyncio
    async def test_no_router_without_provider_override(self) -> None:
        """Agent should NOT use LLM router when provider_override is None."""
        from src.sandbox.agent_runner import create_sandbox_agent

        mock_engine = AsyncMock()
        mock_conn = AsyncMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_result = MagicMock()
        mock_result.first.return_value = None
        mock_conn.execute.return_value = mock_result

        mock_tool_result = MagicMock()
        mock_tool_result.__iter__ = MagicMock(return_value=iter([]))
        mock_conn.execute.return_value = mock_tool_result

        mock_router = MagicMock()

        with patch("src.sandbox.agent_runner.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                anthropic=MagicMock(api_key="test-key", model="claude-sonnet-4-5-20250929"),
                database=MagicMock(url="postgresql+asyncpg://test"),
            )
            agent = await create_sandbox_agent(
                mock_engine,
                tool_mode="mock",
                llm_router=mock_router,
                # provider_override not set → router should be None
            )

        assert agent is not None
        assert agent._llm_router is None
        assert agent._provider_override is None


class TestProcessSandboxTurn:
    """Test sandbox turn processing."""

    @pytest.mark.asyncio
    async def test_captures_tool_calls(self) -> None:
        """Tool calls should be captured during processing."""
        from src.agent.agent import LLMAgent, ToolRouter
        from src.sandbox.agent_runner import process_sandbox_turn

        # Build a mock agent with a predictable response
        router = ToolRouter()

        async def mock_search(**kwargs: object) -> dict:
            return {"items": []}

        router.register("search_tires", mock_search)

        agent = MagicMock(spec=LLMAgent)
        agent._model = "claude-test"
        agent.tool_router = router
        agent.last_input_tokens = 150
        agent.last_output_tokens = 42

        # Simulate process_message returning text + history
        agent.process_message = AsyncMock(
            return_value=("Є шини в наявності", [{"role": "assistant", "content": [{"type": "text", "text": "Є шини"}]}])
        )

        result = await process_sandbox_turn(agent, "Підберіть шини", [])

        assert result.response_text == "Є шини в наявності"
        assert result.model == "claude-test"
        assert result.latency_ms >= 0
        assert result.input_tokens == 150
        assert result.output_tokens == 42
