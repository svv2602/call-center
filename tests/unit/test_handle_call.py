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
        "templates": templates
        or {
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
        mock_pm.get_active_prompt = AsyncMock(
            return_value={"id": None, "name": "test", "system_prompt": "test"}
        )

        captured_pipeline_args: dict[str, Any] = {}

        class FakePipeline:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                # Positional: conn, stt, tts, agent, session, stt_config, templates
                captured_pipeline_args["templates"] = (
                    args[6] if len(args) > 6 else kwargs.get("templates")
                )

            async def run(self) -> None:
                pass

        with (
            patch("src.main._db_engine", p["db_engine"]),
            patch("src.main._redis", None),
            patch("src.main._tts_engine", MagicMock()),
            patch("src.main._store_client", MagicMock()),
            patch("src.main.PromptManager", return_value=mock_pm),
            patch(
                "src.main.get_tools_with_overrides", new_callable=AsyncMock, return_value=p["tools"]
            ),
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
        mock_pm.get_active_prompt = AsyncMock(
            return_value={"id": None, "name": "test", "system_prompt": "test"}
        )

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

        mock_get_tools.assert_called_once_with(p["db_engine"], redis=None)
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
            patch(
                "src.main.get_tools_with_overrides",
                new_callable=AsyncMock,
                side_effect=RuntimeError("DB down"),
            ),
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


class TestHandleCallTenantIntegration:
    """Test tenant-related behavior in handle_call."""

    def _base_patches(
        self, db_engine: Any = None, tenant: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Return common patch set for tenant tests."""
        return {
            "db_engine": db_engine or MagicMock(),
            "tenant": tenant,
        }

    @pytest.mark.asyncio
    async def test_tenant_resolved_sets_session_fields(self) -> None:
        """When _resolve_tenant returns a tenant, session gets tenant_id/slug/network_id."""
        import uuid

        tid = uuid.uuid4()
        tenant = {
            "id": tid,
            "slug": "prokoleso",
            "name": "ProKoleso",
            "network_id": "prokoleso-net",
            "agent_name": "Олена",
            "greeting": None,
            "enabled_tools": [],
            "prompt_suffix": None,
            "config": {},
            "is_active": True,
        }

        captured_sessions: list[Any] = []

        class FakePipeline:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                captured_sessions.append(args[4])  # session is 5th positional arg

            async def run(self) -> None:
                pass

        p = _make_patches(db_engine=MagicMock())
        mock_pm = AsyncMock()
        mock_pm.get_active_templates = AsyncMock(return_value=p["templates"])
        mock_pm.get_active_prompt = AsyncMock(
            return_value={"id": None, "name": "test", "system_prompt": "test"}
        )

        with (
            patch("src.main._db_engine", p["db_engine"]),
            patch("src.main._redis", None),
            patch("src.main._tts_engine", MagicMock()),
            patch("src.main._store_client", MagicMock()),
            patch("src.main._resolve_tenant", new_callable=AsyncMock, return_value=tenant),
            patch("src.main.PromptManager", return_value=mock_pm),
            patch(
                "src.main.get_tools_with_overrides", new_callable=AsyncMock, return_value=p["tools"]
            ),
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

        assert len(captured_sessions) == 1
        session = captured_sessions[0]
        assert session.tenant_id == str(tid)
        assert session.tenant_slug == "prokoleso"
        assert session.network_id == "prokoleso-net"

    @pytest.mark.asyncio
    async def test_tenant_tools_filter(self) -> None:
        """Tenant with enabled_tools filters the tools list passed to LLMAgent."""
        import uuid

        tenant = {
            "id": uuid.uuid4(),
            "slug": "limited",
            "name": "Limited",
            "network_id": "limited-net",
            "agent_name": "Олена",
            "greeting": None,
            "enabled_tools": ["search_tires"],
            "prompt_suffix": None,
            "config": {},
            "is_active": True,
        }

        captured_agent_kwargs: dict[str, Any] = {}

        class FakeAgent:
            def __init__(self, **kwargs: Any) -> None:
                captured_agent_kwargs.update(kwargs)

        all_tools = [
            {"name": "search_tires", "description": "search"},
            {"name": "check_availability", "description": "check"},
            {"name": "transfer_to_operator", "description": "transfer"},
        ]

        p = _make_patches(db_engine=MagicMock(), tools=all_tools)
        mock_pm = AsyncMock()
        mock_pm.get_active_templates = AsyncMock(return_value=p["templates"])
        mock_pm.get_active_prompt = AsyncMock(
            return_value={"id": None, "name": "test", "system_prompt": "test"}
        )

        with (
            patch("src.main._db_engine", p["db_engine"]),
            patch("src.main._redis", None),
            patch("src.main._tts_engine", MagicMock()),
            patch("src.main._store_client", MagicMock()),
            patch("src.main._resolve_tenant", new_callable=AsyncMock, return_value=tenant),
            patch("src.main.PromptManager", return_value=mock_pm),
            patch(
                "src.main.get_tools_with_overrides", new_callable=AsyncMock, return_value=all_tools
            ),
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

        assert len(captured_agent_kwargs["tools"]) == 1
        assert captured_agent_kwargs["tools"][0]["name"] == "search_tires"

    @pytest.mark.asyncio
    async def test_tenant_greeting_override(self) -> None:
        """Tenant with greeting overrides templates dict."""
        import uuid

        custom_greeting = "Вітаю! Твоя Шина."
        tenant = {
            "id": uuid.uuid4(),
            "slug": "tvoya-shina",
            "name": "Твоя Шина",
            "network_id": "Tshina",
            "agent_name": "Марія",
            "greeting": custom_greeting,
            "enabled_tools": [],
            "prompt_suffix": None,
            "config": {},
            "is_active": True,
        }

        captured_pipeline_args: dict[str, Any] = {}

        class FakePipeline:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                captured_pipeline_args["templates"] = (
                    args[6] if len(args) > 6 else kwargs.get("templates")
                )

            async def run(self) -> None:
                pass

        p = _make_patches(db_engine=MagicMock())
        mock_pm = AsyncMock()
        mock_pm.get_active_templates = AsyncMock(return_value=p["templates"])
        mock_pm.get_active_prompt = AsyncMock(
            return_value={"id": None, "name": "test", "system_prompt": "test"}
        )

        with (
            patch("src.main._db_engine", p["db_engine"]),
            patch("src.main._redis", None),
            patch("src.main._tts_engine", MagicMock()),
            patch("src.main._store_client", MagicMock()),
            patch("src.main._resolve_tenant", new_callable=AsyncMock, return_value=tenant),
            patch("src.main.PromptManager", return_value=mock_pm),
            patch(
                "src.main.get_tools_with_overrides", new_callable=AsyncMock, return_value=p["tools"]
            ),
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

        assert captured_pipeline_args["templates"]["greeting"] == custom_greeting

    @pytest.mark.asyncio
    async def test_tenant_prompt_suffix(self) -> None:
        """Tenant prompt_suffix is appended to system_prompt."""
        import uuid

        suffix = "Ти також можеш рекомендувати акційні товари."
        tenant = {
            "id": uuid.uuid4(),
            "slug": "promo",
            "name": "PromoNet",
            "network_id": "promo-net",
            "agent_name": "Олена",
            "greeting": None,
            "enabled_tools": [],
            "prompt_suffix": suffix,
            "config": {},
            "is_active": True,
        }

        captured_agent_kwargs: dict[str, Any] = {}

        class FakeAgent:
            def __init__(self, **kwargs: Any) -> None:
                captured_agent_kwargs.update(kwargs)

        p = _make_patches(db_engine=MagicMock())
        mock_pm = AsyncMock()
        mock_pm.get_active_templates = AsyncMock(return_value=p["templates"])
        mock_pm.get_active_prompt = AsyncMock(
            return_value={
                "id": "test-id",
                "name": "v2.1",
                "system_prompt": "Ти — помічник магазину шин.",
            }
        )

        with (
            patch("src.main._db_engine", p["db_engine"]),
            patch("src.main._redis", None),
            patch("src.main._tts_engine", MagicMock()),
            patch("src.main._store_client", MagicMock()),
            patch("src.main._resolve_tenant", new_callable=AsyncMock, return_value=tenant),
            patch("src.main.PromptManager", return_value=mock_pm),
            patch(
                "src.main.get_tools_with_overrides", new_callable=AsyncMock, return_value=p["tools"]
            ),
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

        assert captured_agent_kwargs["system_prompt"].endswith(suffix)

    @pytest.mark.asyncio
    async def test_per_tenant_store_client_created_and_closed(self) -> None:
        """Tenant with store_api_url in config gets a per-tenant StoreClient."""
        import uuid

        tenant = {
            "id": uuid.uuid4(),
            "slug": "custom-store",
            "name": "Custom Store",
            "network_id": "custom-net",
            "agent_name": "Олена",
            "greeting": None,
            "enabled_tools": [],
            "prompt_suffix": None,
            "config": {"store_api_url": "http://custom-store:3000/api/v1"},
            "is_active": True,
        }

        mock_store = MagicMock()
        mock_store.open = AsyncMock()
        mock_store.close = AsyncMock()

        p = _make_patches(db_engine=MagicMock())
        mock_pm = AsyncMock()
        mock_pm.get_active_templates = AsyncMock(return_value=p["templates"])
        mock_pm.get_active_prompt = AsyncMock(
            return_value={"id": None, "name": "test", "system_prompt": "test"}
        )

        with (
            patch("src.main._db_engine", p["db_engine"]),
            patch("src.main._redis", None),
            patch("src.main._tts_engine", MagicMock()),
            patch("src.main._store_client", MagicMock()),
            patch("src.main._resolve_tenant", new_callable=AsyncMock, return_value=tenant),
            patch("src.main.PromptManager", return_value=mock_pm),
            patch(
                "src.main.get_tools_with_overrides", new_callable=AsyncMock, return_value=p["tools"]
            ),
            patch("src.main.GoogleSTTEngine", return_value=MagicMock()),
            patch("src.main.LLMAgent", return_value=MagicMock()),
            patch("src.main.CallPipeline") as mock_pipeline_cls,
            patch("src.main._build_tool_router", return_value=MagicMock()),
            patch("src.main.PIIVault", return_value=MagicMock()),
            patch("src.main.StoreClient", return_value=mock_store),
            patch("src.main.publish_event", new_callable=AsyncMock),
            patch("src.main.active_calls"),
            patch("src.main.calls_total"),
        ):
            mock_pipeline_cls.return_value.run = AsyncMock()
            from src.main import handle_call

            await handle_call(_make_mock_conn())

        mock_store.open.assert_called_once()
        mock_store.close.assert_called_once()
