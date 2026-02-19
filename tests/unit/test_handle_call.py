"""Unit tests for handle_call() orchestration in src/main.py.

Tests that handle_call properly loads DB templates and tool overrides,
passes them to CallPipeline and LLMAgent, and falls back gracefully.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


def _make_mock_conn() -> MagicMock:
    """Create a mock AudioSocketConnection."""
    conn = MagicMock()
    conn.channel_uuid = uuid4()
    conn.is_closed = False
    return conn


def _make_patches(
    db_engine: Any = None,
    templates: dict[str, str] | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a dict of patches for handle_call dependencies."""
    return {
        "db_engine": db_engine,
        "templates": templates or {
            "greeting": "Тестове привітання",
            "farewell": "Тестове прощання",
            "silence_prompt": "Тест тиша",
            "transfer": "Тест переведення",
            "error": "Тест помилка",
            "wait": "Тест зачекайте",
            "order_cancelled": "Тест скасовано",
        },
        "tools": tools or [{"name": "search_tires", "description": "test"}],
    }


class TestHandleCallDBIntegration:
    """Test that handle_call loads templates and tools from DB."""

    @pytest.mark.asyncio
    async def test_loads_templates_when_db_available(self) -> None:
        """handle_call should call PromptManager.get_active_templates() when _db_engine is set."""
        p = _make_patches(db_engine=MagicMock())
        mock_pm = AsyncMock()
        mock_pm.get_active_templates = AsyncMock(return_value=p["templates"])
        mock_pm.get_active_prompt = AsyncMock(return_value={"id": None, "name": "test", "system_prompt": "test"})

        captured_pipeline_args: dict[str, Any] = {}

        class FakePipeline:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                # Positional: conn, stt, tts, agent, session, stt_config, templates
                captured_pipeline_args["templates"] = args[6] if len(args) > 6 else kwargs.get("templates")

            async def run(self) -> None:
                pass

        with (
            patch("src.main._db_engine", p["db_engine"]),
            patch("src.main._redis", None),
            patch("src.main._tts_engine", MagicMock()),
            patch("src.main._store_client", MagicMock()),
            patch("src.main.PromptManager", return_value=mock_pm),
            patch("src.main.get_tools_with_overrides", new_callable=AsyncMock, return_value=p["tools"]),
            patch("src.main.GoogleSTTEngine", return_value=MagicMock()),
            patch("src.main.LLMAgent", return_value=MagicMock()),
            patch("src.main.CallPipeline", FakePipeline),
            patch("src.main._build_tool_router", return_value=MagicMock()),
            patch("src.main.PIIVault", return_value=MagicMock()),
            patch("src.main.publish_event", new_callable=AsyncMock),
            patch("src.main.active_calls"),
            patch("src.main.calls_total"),
        ):
            from src.main import handle_call

            await handle_call(_make_mock_conn())

        mock_pm.get_active_templates.assert_called_once()
        assert captured_pipeline_args["templates"] == p["templates"]

    @pytest.mark.asyncio
    async def test_loads_tool_overrides_when_db_available(self) -> None:
        """handle_call should call get_tools_with_overrides() when _db_engine is set."""
        p = _make_patches(db_engine=MagicMock())
        mock_pm = AsyncMock()
        mock_pm.get_active_templates = AsyncMock(return_value=p["templates"])
        mock_pm.get_active_prompt = AsyncMock(return_value={"id": None, "name": "test", "system_prompt": "test"})

        captured_agent_kwargs: dict[str, Any] = {}

        class FakeAgent:
            def __init__(self, **kwargs: Any) -> None:
                captured_agent_kwargs.update(kwargs)

        mock_get_tools = AsyncMock(return_value=p["tools"])

        with (
            patch("src.main._db_engine", p["db_engine"]),
            patch("src.main._redis", None),
            patch("src.main._tts_engine", MagicMock()),
            patch("src.main._store_client", MagicMock()),
            patch("src.main.PromptManager", return_value=mock_pm),
            patch("src.main.get_tools_with_overrides", mock_get_tools),
            patch("src.main.GoogleSTTEngine", return_value=MagicMock()),
            patch("src.main.LLMAgent", FakeAgent),
            patch("src.main.CallPipeline") as mock_pipeline_cls,
            patch("src.main._build_tool_router", return_value=MagicMock()),
            patch("src.main.PIIVault", return_value=MagicMock()),
            patch("src.main.publish_event", new_callable=AsyncMock),
            patch("src.main.active_calls"),
            patch("src.main.calls_total"),
        ):
            mock_pipeline_cls.return_value.run = AsyncMock()
            from src.main import handle_call

            await handle_call(_make_mock_conn())

        mock_get_tools.assert_called_once_with(p["db_engine"])
        assert captured_agent_kwargs["tools"] == p["tools"]

    @pytest.mark.asyncio
    async def test_no_db_passes_none_templates_and_tools(self) -> None:
        """When _db_engine is None, templates and tools should be None (use defaults)."""
        captured_pipeline_args: list[Any] = []
        captured_agent_kwargs: dict[str, Any] = {}

        class FakePipeline:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                captured_pipeline_args.extend(args)

            async def run(self) -> None:
                pass

        class FakeAgent:
            def __init__(self, **kwargs: Any) -> None:
                captured_agent_kwargs.update(kwargs)

        with (
            patch("src.main._db_engine", None),
            patch("src.main._redis", None),
            patch("src.main._tts_engine", MagicMock()),
            patch("src.main._store_client", MagicMock()),
            patch("src.main.GoogleSTTEngine", return_value=MagicMock()),
            patch("src.main.LLMAgent", FakeAgent),
            patch("src.main.CallPipeline", FakePipeline),
            patch("src.main._build_tool_router", return_value=MagicMock()),
            patch("src.main.PIIVault", return_value=MagicMock()),
            patch("src.main.publish_event", new_callable=AsyncMock),
            patch("src.main.active_calls"),
            patch("src.main.calls_total"),
        ):
            from src.main import handle_call

            await handle_call(_make_mock_conn())

        # Templates is 7th positional arg (index 6), should be None
        assert captured_pipeline_args[6] is None
        # Tools should be None
        assert captured_agent_kwargs["tools"] is None

    @pytest.mark.asyncio
    async def test_db_error_does_not_crash_call(self) -> None:
        """If PromptManager raises, handle_call should still complete (graceful fallback)."""
        mock_pm = AsyncMock()
        mock_pm.get_active_templates = AsyncMock(side_effect=RuntimeError("DB down"))

        with (
            patch("src.main._db_engine", MagicMock()),
            patch("src.main._redis", None),
            patch("src.main._tts_engine", MagicMock()),
            patch("src.main._store_client", MagicMock()),
            patch("src.main.PromptManager", return_value=mock_pm),
            patch("src.main.get_tools_with_overrides", new_callable=AsyncMock, side_effect=RuntimeError("DB down")),
            patch("src.main.GoogleSTTEngine", return_value=MagicMock()),
            patch("src.main.LLMAgent", return_value=MagicMock()),
            patch("src.main.CallPipeline") as mock_pipeline_cls,
            patch("src.main._build_tool_router", return_value=MagicMock()),
            patch("src.main.PIIVault", return_value=MagicMock()),
            patch("src.main.publish_event", new_callable=AsyncMock),
            patch("src.main.active_calls"),
            patch("src.main.calls_total"),
        ):
            mock_pipeline_cls.return_value.run = AsyncMock()
            from src.main import handle_call

            # Should not raise — error is caught by the outer try/except
            await handle_call(_make_mock_conn())
