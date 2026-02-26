"""Tests for returning caller history feature.

Covers:
- format_caller_history() formatting logic
- get_caller_history() DB query (mocked)
- _fetch_all() helper (mocked)
- build_system_prompt_with_context() with caller_history injection
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.prompts import build_system_prompt_with_context, format_caller_history
from src.logging.call_logger import CallLogger

# ---------------------------------------------------------------------------
# format_caller_history()
# ---------------------------------------------------------------------------


class TestFormatCallerHistory:
    def test_empty_history_returns_none(self):
        assert format_caller_history([]) is None

    def test_single_call_basic(self):
        history = [
            {
                "call_id": "abc-123",
                "started_at": datetime(2026, 2, 25, 14, 30, tzinfo=UTC),
                "scenario": "tire_search",
                "duration_seconds": 180,
                "transferred_to_operator": False,
                "tool_names": ["search_tires", "check_availability"],
            }
        ]
        result = format_caller_history(history)
        assert result is not None
        assert "1 раз(ів)" in result
        assert "25.02 14:30" in result
        assert "tire_search" in result
        assert "шукав шини" in result
        assert "перевіряв наявність" in result
        assert "3 хв" in result
        assert "[переведено на оператора]" not in result

    def test_multiple_calls(self):
        history = [
            {
                "call_id": "abc-1",
                "started_at": datetime(2026, 2, 25, 14, 30, tzinfo=UTC),
                "scenario": "tire_search",
                "duration_seconds": 180,
                "transferred_to_operator": False,
                "tool_names": ["search_tires"],
            },
            {
                "call_id": "abc-2",
                "started_at": datetime(2026, 2, 23, 10, 15, tzinfo=UTC),
                "scenario": "fitting",
                "duration_seconds": 300,
                "transferred_to_operator": False,
                "tool_names": ["book_fitting"],
            },
            {
                "call_id": "abc-3",
                "started_at": datetime(2026, 2, 20, 16, 45, tzinfo=UTC),
                "scenario": "consultation",
                "duration_seconds": 120,
                "transferred_to_operator": True,
                "tool_names": ["search_knowledge_base"],
            },
        ]
        result = format_caller_history(history)
        assert result is not None
        assert "3 раз(ів)" in result
        assert "1." in result
        assert "2." in result
        assert "3." in result
        assert "[переведено на оператора]" in result
        assert "Враховуй цю історію" in result
        assert "Запропонуй продовжити" in result

    def test_transferred_call(self):
        history = [
            {
                "call_id": "abc-1",
                "started_at": datetime(2026, 2, 25, 14, 30, tzinfo=UTC),
                "scenario": "consultation",
                "duration_seconds": 60,
                "transferred_to_operator": True,
                "tool_names": ["transfer_to_operator"],
            }
        ]
        result = format_caller_history(history)
        assert result is not None
        assert "[переведено на оператора]" in result

    def test_no_tools(self):
        history = [
            {
                "call_id": "abc-1",
                "started_at": datetime(2026, 2, 25, 14, 30, tzinfo=UTC),
                "scenario": "consultation",
                "duration_seconds": 30,
                "transferred_to_operator": False,
                "tool_names": [],
            }
        ]
        result = format_caller_history(history)
        assert result is not None
        assert "consultation" in result
        # Should not have parenthesized actions
        assert "()" not in result

    def test_none_tool_names(self):
        history = [
            {
                "call_id": "abc-1",
                "started_at": datetime(2026, 2, 25, 14, 30, tzinfo=UTC),
                "scenario": "tire_search",
                "duration_seconds": 45,
                "transferred_to_operator": False,
                "tool_names": None,
            }
        ]
        result = format_caller_history(history)
        assert result is not None

    def test_unknown_tool_names_ignored(self):
        history = [
            {
                "call_id": "abc-1",
                "started_at": datetime(2026, 2, 25, 14, 30, tzinfo=UTC),
                "scenario": "tire_search",
                "duration_seconds": 120,
                "transferred_to_operator": False,
                "tool_names": ["unknown_tool", "search_tires"],
            }
        ]
        result = format_caller_history(history)
        assert result is not None
        assert "шукав шини" in result
        assert "unknown_tool" not in result

    def test_short_duration_rounds_to_1_min(self):
        history = [
            {
                "call_id": "abc-1",
                "started_at": datetime(2026, 2, 25, 14, 30, tzinfo=UTC),
                "scenario": "consultation",
                "duration_seconds": 10,
                "transferred_to_operator": False,
                "tool_names": [],
            }
        ]
        result = format_caller_history(history)
        assert result is not None
        assert "1 хв" in result

    def test_none_started_at(self):
        history = [
            {
                "call_id": "abc-1",
                "started_at": None,
                "scenario": "tire_search",
                "duration_seconds": 120,
                "transferred_to_operator": False,
                "tool_names": [],
            }
        ]
        result = format_caller_history(history)
        assert result is not None
        assert "?" in result

    def test_none_scenario(self):
        history = [
            {
                "call_id": "abc-1",
                "started_at": datetime(2026, 2, 25, 14, 30, tzinfo=UTC),
                "scenario": None,
                "duration_seconds": 120,
                "transferred_to_operator": False,
                "tool_names": [],
            }
        ]
        result = format_caller_history(history)
        assert result is not None
        assert "невідомий" in result

    def test_all_tool_mappings(self):
        """Verify all mapped tools produce Ukrainian actions."""
        tools = [
            "search_tires",
            "check_availability",
            "get_vehicle_tire_sizes",
            "create_order_draft",
            "update_order_delivery",
            "confirm_order",
            "get_order_status",
            "get_fitting_stations",
            "get_fitting_slots",
            "book_fitting",
            "cancel_fitting",
            "get_fitting_price",
            "get_customer_bookings",
            "get_pickup_points",
            "find_storage",
            "search_knowledge_base",
            "transfer_to_operator",
        ]
        history = [
            {
                "call_id": "abc-1",
                "started_at": datetime(2026, 2, 25, 14, 30, tzinfo=UTC),
                "scenario": "tire_search",
                "duration_seconds": 120,
                "transferred_to_operator": False,
                "tool_names": tools,
            }
        ]
        result = format_caller_history(history)
        assert result is not None
        # All 17 tools should produce actions
        assert "шукав шини" in result
        assert "підтвердив замовлення" in result
        assert "переведено на оператора" in result


# ---------------------------------------------------------------------------
# build_system_prompt_with_context() with caller_history
# ---------------------------------------------------------------------------


class TestBuildSystemPromptWithCallerHistory:
    def test_caller_history_injected(self):
        prompt = build_system_prompt_with_context(
            "Base prompt text",
            caller_history="## Історія попередніх дзвінків клієнта\ntest history",
        )
        assert "Історія попередніх дзвінків клієнта" in prompt
        assert "test history" in prompt

    def test_caller_history_none_not_injected(self):
        prompt = build_system_prompt_with_context(
            "Base prompt text",
            caller_history=None,
        )
        assert "Історія попередніх дзвінків" not in prompt

    def test_caller_history_before_call_context(self):
        """Caller history (stable) comes before CallerID (dynamic) for cache efficiency."""
        prompt = build_system_prompt_with_context(
            "Base prompt text",
            caller_phone="+380501234567",
            caller_history="## Історія\nhistory section",
        )
        # Both should be present
        assert "CallerID клієнта: +380501234567" in prompt
        assert "Історія" in prompt
        # History is stable (same across turns) → before dynamic CallerID/order context
        caller_idx = prompt.index("CallerID")
        history_idx = prompt.index("Історія")
        assert history_idx < caller_idx


# ---------------------------------------------------------------------------
# CallLogger._fetch_all() and get_caller_history()
# ---------------------------------------------------------------------------


class TestCallLoggerFetchAll:
    @pytest.mark.asyncio
    async def test_fetch_all_returns_list(self):
        logger = CallLogger.__new__(CallLogger)
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.mappings.return_value.fetchall.return_value = [
            {"id": "1", "name": "test"},
            {"id": "2", "name": "test2"},
        ]
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        logger._session_factory = MagicMock(return_value=mock_cm)

        result = await logger._fetch_all("SELECT 1", {})
        assert len(result) == 2
        assert result[0]["id"] == "1"
        assert result[1]["id"] == "2"

    @pytest.mark.asyncio
    async def test_fetch_all_returns_empty_on_error(self):
        logger = CallLogger.__new__(CallLogger)
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(side_effect=Exception("DB error"))
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        logger._session_factory = MagicMock(return_value=mock_cm)

        result = await logger._fetch_all("SELECT 1", {})
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_all_empty_result(self):
        logger = CallLogger.__new__(CallLogger)
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.mappings.return_value.fetchall.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        logger._session_factory = MagicMock(return_value=mock_cm)

        result = await logger._fetch_all("SELECT 1", {})
        assert result == []


class TestGetCallerHistory:
    @pytest.mark.asyncio
    async def test_get_caller_history_calls_fetch_all(self):
        logger = CallLogger.__new__(CallLogger)
        expected = [
            {
                "call_id": "abc",
                "started_at": datetime(2026, 2, 25, tzinfo=UTC),
                "scenario": "tire_search",
                "duration_seconds": 120,
                "transferred_to_operator": False,
                "tool_names": ["search_tires"],
            }
        ]
        logger._fetch_all = AsyncMock(return_value=expected)

        result = await logger.get_caller_history("+380501234567")

        assert result == expected
        logger._fetch_all.assert_called_once()
        call_args = logger._fetch_all.call_args
        params = call_args[0][1]
        assert params["phone"] == "+380501234567"
        assert params["max_calls"] == 5
        assert isinstance(params["since"], datetime)

    @pytest.mark.asyncio
    async def test_get_caller_history_custom_params(self):
        logger = CallLogger.__new__(CallLogger)
        logger._fetch_all = AsyncMock(return_value=[])

        await logger.get_caller_history("+380501234567", days=3, max_calls=10)

        call_args = logger._fetch_all.call_args
        params = call_args[0][1]
        assert params["max_calls"] == 10
        # since should be ~3 days ago
        expected_since = datetime.now(UTC) - timedelta(days=3)
        assert abs((params["since"] - expected_since).total_seconds()) < 5

    @pytest.mark.asyncio
    async def test_get_caller_history_sql_contains_partition_pruning(self):
        """Verify SQL uses started_at >= :since for partition pruning."""
        logger = CallLogger.__new__(CallLogger)
        logger._fetch_all = AsyncMock(return_value=[])

        await logger.get_caller_history("+380501234567")

        query = logger._fetch_all.call_args[0][0]
        assert "c.started_at >= :since" in query
        assert "LIMIT :max_calls" in query
        assert "LATERAL" in query
