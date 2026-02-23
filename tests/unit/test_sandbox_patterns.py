"""Unit tests for sandbox pattern search and export (Phase 4b/4c)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.sandbox.patterns import PatternSearch


class TestPatternSearch:
    """Test PatternSearch vector search and formatting."""

    @pytest.fixture
    def mock_generator(self) -> AsyncMock:
        gen = AsyncMock()
        gen.generate_single = AsyncMock(return_value=[0.1] * 1536)
        return gen

    @pytest.fixture
    def mock_pool(self) -> MagicMock:
        pool = MagicMock()
        conn = AsyncMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        return pool

    @pytest.mark.asyncio
    async def test_search_calls_generator(self, mock_pool, mock_generator) -> None:
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)

        ps = PatternSearch(mock_pool, mock_generator)
        result = await ps.search("test query")

        mock_generator.generate_single.assert_called_once_with("test query")
        assert result == []

    @pytest.mark.asyncio
    async def test_search_returns_dicts(self, mock_pool, mock_generator) -> None:
        pid = uuid4()
        mock_row = {
            "id": pid,
            "intent_label": "tire_search",
            "pattern_type": "positive",
            "customer_messages": "I need winter tires",
            "agent_messages": "What size?",
            "guidance_note": "Always ask about size",
            "rating": 5,
            "tags": ["tires"],
            "similarity": 0.92,
        }

        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[mock_row])
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)

        ps = PatternSearch(mock_pool, mock_generator)
        result = await ps.search("winter tires")

        assert len(result) == 1
        assert result[0]["intent_label"] == "tire_search"
        assert result[0]["similarity"] == 0.92

    @pytest.mark.asyncio
    async def test_format_for_prompt_empty(self, mock_pool, mock_generator) -> None:
        ps = PatternSearch(mock_pool, mock_generator)
        result = await ps.format_for_prompt([])
        assert result == ""

    @pytest.mark.asyncio
    async def test_format_for_prompt_positive(self, mock_pool, mock_generator) -> None:
        patterns = [
            {
                "pattern_type": "positive",
                "intent_label": "size_clarification",
                "guidance_note": "Always confirm tire size",
            },
        ]
        ps = PatternSearch(mock_pool, mock_generator)
        result = await ps.format_for_prompt(patterns)

        assert "size_clarification" in result
        assert "Always confirm tire size" in result
        assert "\u2705" in result  # checkmark emoji

    @pytest.mark.asyncio
    async def test_format_for_prompt_negative(self, mock_pool, mock_generator) -> None:
        patterns = [
            {
                "pattern_type": "negative",
                "intent_label": "verbose_response",
                "guidance_note": "Do not give long monologues about tire brands",
            },
        ]
        ps = PatternSearch(mock_pool, mock_generator)
        result = await ps.format_for_prompt(patterns)

        assert "verbose_response" in result
        assert "\u274c" in result  # cross emoji
        assert "\u041d\u0415 \u0420\u041e\u0411\u0418\u0422\u0418" in result  # "НЕ РОБИТИ"

    @pytest.mark.asyncio
    async def test_format_for_prompt_mixed(self, mock_pool, mock_generator) -> None:
        patterns = [
            {
                "pattern_type": "positive",
                "intent_label": "greeting",
                "guidance_note": "Always greet warmly",
            },
            {
                "pattern_type": "negative",
                "intent_label": "jargon",
                "guidance_note": "Do not use technical jargon",
            },
        ]
        ps = PatternSearch(mock_pool, mock_generator)
        result = await ps.format_for_prompt(patterns)

        assert "\u2705" in result
        assert "\u274c" in result
        # Contains Ukrainian header
        assert "\u0406\u043d\u0441\u0442\u0440\u0443\u043a\u0446\u0456\u0457" in result

    @pytest.mark.asyncio
    async def test_increment_usage_empty(self, mock_pool, mock_generator) -> None:
        ps = PatternSearch(mock_pool, mock_generator)
        await ps.increment_usage([])
        # Should not call pool at all
        mock_pool.acquire.assert_not_called()

    @pytest.mark.asyncio
    async def test_increment_usage(self, mock_pool, mock_generator) -> None:
        conn = AsyncMock()
        conn.execute = AsyncMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)

        ps = PatternSearch(mock_pool, mock_generator)
        ids = [uuid4(), uuid4()]
        await ps.increment_usage(ids)

        conn.execute.assert_called_once()
        call_args = conn.execute.call_args
        assert "times_used = times_used + 1" in call_args[0][0]


class TestAgentPatternContext:
    """Test that build_system_prompt_with_context correctly handles pattern_context."""

    @pytest.mark.asyncio
    async def test_build_system_prompt_with_pattern_context(self) -> None:
        from src.agent.prompts import SYSTEM_PROMPT, build_system_prompt_with_context

        prompt = build_system_prompt_with_context(
            SYSTEM_PROMPT, pattern_context="## Test patterns\n- Test pattern"
        )

        assert "## Test patterns" in prompt
        assert "Test pattern" in prompt

    @pytest.mark.asyncio
    async def test_build_system_prompt_without_pattern_context(self) -> None:
        from src.agent.prompts import SYSTEM_PROMPT, build_system_prompt_with_context

        prompt = build_system_prompt_with_context(SYSTEM_PROMPT)

        assert "Test patterns" not in prompt
        # Season hint is always present
        assert "\u0441\u0435\u0437\u043e\u043d" in prompt  # "сезон"
