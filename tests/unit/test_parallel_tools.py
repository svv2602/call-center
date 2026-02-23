"""Unit tests for parallel tool execution in agent and streaming loop."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.agent import LLMAgent, ToolRouter


class TestParallelToolExecution:
    """Tests for parallel tool call execution via asyncio.gather."""

    @pytest.fixture()
    def tool_router(self) -> ToolRouter:
        router = ToolRouter()

        async def _search_tires(**kwargs: Any) -> dict[str, Any]:
            await asyncio.sleep(0.05)  # simulate I/O
            return {"items": [{"brand": "Michelin"}]}

        async def _check_avail(**kwargs: Any) -> dict[str, Any]:
            await asyncio.sleep(0.05)
            return {"available": True, "price": 3200}

        router.register("search_tires", _search_tires)
        router.register("check_availability", _check_avail)
        return router

    @pytest.fixture()
    def mock_llm_router(self) -> MagicMock:
        router = MagicMock()

        # First call returns tool_uses, second returns text only
        call_count = 0

        async def _complete(task: Any, messages: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1

            resp = MagicMock()
            resp.usage = MagicMock(input_tokens=100, output_tokens=50)
            resp.provider = "test"

            if call_count == 1:
                resp.text = ""
                resp.stop_reason = "tool_use"
                tc1 = MagicMock()
                tc1.id = "tc_1"
                tc1.name = "search_tires"
                tc1.arguments = {"size": "205/55 R16"}
                tc2 = MagicMock()
                tc2.id = "tc_2"
                tc2.name = "check_availability"
                tc2.arguments = {"product_id": "p1"}
                resp.tool_calls = [tc1, tc2]
            else:
                resp.text = "Знайшов шини!"
                resp.stop_reason = "end_turn"
                resp.tool_calls = []
            return resp

        router.complete = AsyncMock(side_effect=_complete)
        return router

    @pytest.mark.asyncio()
    async def test_two_tools_execute_concurrently(
        self, tool_router: ToolRouter, mock_llm_router: MagicMock
    ) -> None:
        """Two independent tools should run in parallel (total < 2x single)."""
        agent = LLMAgent(
            api_key="test-key",
            tool_router=tool_router,
            llm_router=mock_llm_router,
            tools=[],
        )
        history: list[dict[str, Any]] = []
        start = asyncio.get_event_loop().time()
        text, _ = await agent.process_message("Шукаю шини", history)
        elapsed = asyncio.get_event_loop().time() - start

        assert "Знайшов" in text
        # Both tools sleep 50ms each; parallel should be ~50ms, sequential ~100ms
        # Allow generous margin for CI but assert it's less than sequential
        assert elapsed < 0.15, f"Took {elapsed:.3f}s — tools may not be parallel"

    @pytest.mark.asyncio()
    async def test_single_tool_still_works(self, tool_router: ToolRouter) -> None:
        """Single tool call should work normally."""
        router = MagicMock()
        call_count = 0

        async def _complete(task: Any, messages: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.usage = MagicMock(input_tokens=50, output_tokens=30)
            resp.provider = "test"
            if call_count == 1:
                resp.text = ""
                resp.stop_reason = "tool_use"
                tc = MagicMock()
                tc.id = "tc_1"
                tc.name = "search_tires"
                tc.arguments = {"size": "205/55 R16"}
                resp.tool_calls = [tc]
            else:
                resp.text = "Ось результат"
                resp.stop_reason = "end_turn"
                resp.tool_calls = []
            return resp

        router.complete = AsyncMock(side_effect=_complete)
        agent = LLMAgent(
            api_key="test-key",
            tool_router=tool_router,
            llm_router=router,
            tools=[],
        )
        text, _ = await agent.process_message("test", [])
        assert "Ось результат" in text

    @pytest.mark.asyncio()
    async def test_tool_error_doesnt_cancel_others(self) -> None:
        """If one tool errors, other results still come through."""
        router = ToolRouter()

        async def _good_tool(**kwargs: Any) -> dict[str, Any]:
            await asyncio.sleep(0.02)
            return {"result": "ok"}

        async def _bad_tool(**kwargs: Any) -> dict[str, Any]:
            await asyncio.sleep(0.02)
            raise RuntimeError("Tool failed")

        router.register("good_tool", _good_tool)
        router.register("bad_tool", _bad_tool)

        mock_router = MagicMock()
        call_count = 0

        async def _complete(task: Any, messages: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.usage = MagicMock(input_tokens=50, output_tokens=30)
            resp.provider = "test"
            if call_count == 1:
                resp.text = ""
                resp.stop_reason = "tool_use"
                tc1 = MagicMock()
                tc1.id = "tc_1"
                tc1.name = "good_tool"
                tc1.arguments = {}
                tc2 = MagicMock()
                tc2.id = "tc_2"
                tc2.name = "bad_tool"
                tc2.arguments = {}
                resp.tool_calls = [tc1, tc2]
            else:
                resp.text = "Done"
                resp.stop_reason = "end_turn"
                resp.tool_calls = []
            return resp

        mock_router.complete = AsyncMock(side_effect=_complete)

        agent = LLMAgent(
            api_key="test-key",
            tool_router=router,
            llm_router=mock_router,
            tools=[],
        )
        text, history = await agent.process_message("test", [])
        assert text == "Done"
        # Both tool results should be in history (one with error)
        tool_msg = [m for m in history if m.get("role") == "user" and isinstance(m.get("content"), list)]
        assert len(tool_msg) == 1
        results = tool_msg[0]["content"]
        assert len(results) == 2

    @pytest.mark.asyncio()
    async def test_results_match_tool_use_ids(self, tool_router: ToolRouter) -> None:
        """Tool results must have correct tool_use_id matching."""
        mock_router = MagicMock()
        call_count = 0

        async def _complete(task: Any, messages: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.usage = MagicMock(input_tokens=50, output_tokens=30)
            resp.provider = "test"
            if call_count == 1:
                resp.text = ""
                resp.stop_reason = "tool_use"
                tc1 = MagicMock()
                tc1.id = "id_search"
                tc1.name = "search_tires"
                tc1.arguments = {"size": "205/55 R16"}
                tc2 = MagicMock()
                tc2.id = "id_avail"
                tc2.name = "check_availability"
                tc2.arguments = {"product_id": "p1"}
                resp.tool_calls = [tc1, tc2]
            else:
                resp.text = "OK"
                resp.stop_reason = "end_turn"
                resp.tool_calls = []
            return resp

        mock_router.complete = AsyncMock(side_effect=_complete)
        agent = LLMAgent(
            api_key="test-key",
            tool_router=tool_router,
            llm_router=mock_router,
            tools=[],
        )
        _, history = await agent.process_message("test", [])

        tool_msg = [m for m in history if m.get("role") == "user" and isinstance(m.get("content"), list)]
        results = tool_msg[0]["content"]
        ids = {r["tool_use_id"] for r in results}
        assert ids == {"id_search", "id_avail"}
