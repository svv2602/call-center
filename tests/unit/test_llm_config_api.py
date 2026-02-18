"""Tests for LLM config admin API endpoints."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.llm_config import router


async def _fake_require_admin(*_args: object, **_kwargs: object) -> dict[str, Any]:
    return {"sub": "test-user", "role": "admin"}


@pytest.fixture()
def mock_redis():
    """Create mock Redis."""
    mock = AsyncMock()
    mock.get = AsyncMock(return_value=None)
    mock.set = AsyncMock()
    return mock


@pytest.fixture()
def app():
    """Create FastAPI app with mocked auth."""
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)
    return test_app


class TestGetConfig:
    """Test GET /admin/llm/config."""

    @pytest.mark.asyncio()
    async def test_returns_default_config(self, app: Any, mock_redis: AsyncMock) -> None:
        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.get("/admin/llm/config")

        assert response.status_code == 200
        data = response.json()
        assert "config" in data
        config = data["config"]
        assert "providers" in config
        assert "tasks" in config
        assert "anthropic-sonnet" in config["providers"]
        assert config["providers"]["anthropic-sonnet"]["api_key_set"] is True

    @pytest.mark.asyncio()
    async def test_masks_missing_api_key(self, app: Any, mock_redis: AsyncMock) -> None:
        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch.dict("os.environ", {}, clear=True),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.get("/admin/llm/config")

        data = response.json()
        assert data["config"]["providers"]["openai-gpt4o"]["api_key_set"] is False

    @pytest.mark.asyncio()
    async def test_merges_redis_config(self, app: Any, mock_redis: AsyncMock) -> None:
        redis_config = {"providers": {"anthropic-sonnet": {"model": "custom-model"}}}
        mock_redis.get = AsyncMock(return_value=json.dumps(redis_config))

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.get("/admin/llm/config")

        data = response.json()
        assert data["config"]["providers"]["anthropic-sonnet"]["model"] == "custom-model"


class TestPatchConfig:
    """Test PATCH /admin/llm/config."""

    @pytest.mark.asyncio()
    async def test_update_provider_enabled(self, app: Any, mock_redis: AsyncMock) -> None:
        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_config._get_redis", AsyncMock(return_value=mock_redis)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.patch(
                    "/admin/llm/config",
                    json={"providers": {"openai-gpt4o": {"enabled": True}}},
                )

        assert response.status_code == 200
        mock_redis.set.assert_called_once()
        stored = json.loads(mock_redis.set.call_args[0][1])
        assert stored["providers"]["openai-gpt4o"]["enabled"] is True

    @pytest.mark.asyncio()
    async def test_update_task_route(self, app: Any, mock_redis: AsyncMock) -> None:
        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_config._get_redis", AsyncMock(return_value=mock_redis)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.patch(
                    "/admin/llm/config",
                    json={"tasks": {"agent": {"primary": "openai-gpt4o"}}},
                )

        assert response.status_code == 200
        stored = json.loads(mock_redis.set.call_args[0][1])
        assert stored["tasks"]["agent"]["primary"] == "openai-gpt4o"

    @pytest.mark.asyncio()
    async def test_update_fallbacks(self, app: Any, mock_redis: AsyncMock) -> None:
        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_config._get_redis", AsyncMock(return_value=mock_redis)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.patch(
                    "/admin/llm/config",
                    json={
                        "tasks": {
                            "agent": {
                                "primary": "anthropic-sonnet",
                                "fallbacks": ["openai-gpt4o", "deepseek-chat"],
                            }
                        }
                    },
                )

        assert response.status_code == 200
        stored = json.loads(mock_redis.set.call_args[0][1])
        assert stored["tasks"]["agent"]["fallbacks"] == ["openai-gpt4o", "deepseek-chat"]


class TestGetProvidersHealth:
    """Test GET /admin/llm/providers."""

    @pytest.mark.asyncio()
    async def test_returns_providers(self, app: Any) -> None:
        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.llm._router_instance", None),
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.get("/admin/llm/providers")

        assert response.status_code == 200
        data = response.json()
        assert "providers" in data
        providers = {p["key"]: p for p in data["providers"]}
        assert "anthropic-sonnet" in providers
        assert providers["anthropic-sonnet"]["type"] == "anthropic"
        assert providers["anthropic-sonnet"]["healthy"] is None


class TestTestProvider:
    """Test POST /admin/llm/providers/{key}/test."""

    @pytest.mark.asyncio()
    async def test_router_not_enabled(self, app: Any) -> None:
        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.llm._router_instance", None),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post("/admin/llm/providers/anthropic-sonnet/test")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "disabled" in data["error"]

    @pytest.mark.asyncio()
    async def test_provider_not_found(self, app: Any) -> None:
        mock_router = MagicMock()
        mock_router.providers = {}

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.llm._router_instance", mock_router),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post("/admin/llm/providers/nonexistent/test")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "not active" in data["error"]
