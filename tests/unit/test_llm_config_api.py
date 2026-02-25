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
        assert data["config"]["providers"]["openai-gpt41-mini"]["api_key_set"] is False

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
                    json={"providers": {"openai-gpt41-mini": {"enabled": True}}},
                )

        assert response.status_code == 200
        mock_redis.set.assert_called_once()
        stored = json.loads(mock_redis.set.call_args[0][1])
        assert stored["providers"]["openai-gpt41-mini"]["enabled"] is True

    @pytest.mark.asyncio()
    async def test_update_task_route(self, app: Any, mock_redis: AsyncMock) -> None:
        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_config._get_redis", AsyncMock(return_value=mock_redis)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.patch(
                    "/admin/llm/config",
                    json={"tasks": {"agent": {"primary": "openai-gpt41-mini"}}},
                )

        assert response.status_code == 200
        stored = json.loads(mock_redis.set.call_args[0][1])
        assert stored["tasks"]["agent"]["primary"] == "openai-gpt41-mini"

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
                                "fallbacks": ["openai-gpt41-mini", "deepseek-chat"],
                            }
                        }
                    },
                )

        assert response.status_code == 200
        stored = json.loads(mock_redis.set.call_args[0][1])
        assert stored["tasks"]["agent"]["fallbacks"] == ["openai-gpt41-mini", "deepseek-chat"]


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


class TestSandboxConfig:
    """Test sandbox section in LLM config."""

    @pytest.mark.asyncio()
    async def test_get_returns_sandbox_section(self, app: Any, mock_redis: AsyncMock) -> None:
        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch.dict("os.environ", {}, clear=True),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.get("/admin/llm/config")

        assert response.status_code == 200
        config = response.json()["config"]
        assert "sandbox" in config
        assert config["sandbox"]["default_model"] == ""
        assert config["sandbox"]["auto_customer_model"] == ""

    @pytest.mark.asyncio()
    async def test_get_merges_sandbox_from_redis(self, app: Any, mock_redis: AsyncMock) -> None:
        redis_config = {
            "sandbox": {"default_model": "gemini-flash", "auto_customer_model": "openai-gpt41-mini"}
        }
        mock_redis.get = AsyncMock(return_value=json.dumps(redis_config))

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch.dict("os.environ", {}, clear=True),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.get("/admin/llm/config")

        config = response.json()["config"]
        assert config["sandbox"]["default_model"] == "gemini-flash"
        assert config["sandbox"]["auto_customer_model"] == "openai-gpt41-mini"

    @pytest.mark.asyncio()
    async def test_patch_sandbox_section(self, app: Any, mock_redis: AsyncMock) -> None:
        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_config._get_redis", AsyncMock(return_value=mock_redis)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.patch(
                    "/admin/llm/config",
                    json={
                        "sandbox": {
                            "default_model": "gemini-flash",
                            "auto_customer_model": "deepseek-chat",
                        }
                    },
                )

        assert response.status_code == 200
        stored = json.loads(mock_redis.set.call_args[0][1])
        assert stored["sandbox"]["default_model"] == "gemini-flash"
        assert stored["sandbox"]["auto_customer_model"] == "deepseek-chat"

        # Response also includes merged config with sandbox
        resp_config = response.json()["config"]
        assert resp_config["sandbox"]["default_model"] == "gemini-flash"

    @pytest.mark.asyncio()
    async def test_patch_sandbox_partial_update(self, app: Any, mock_redis: AsyncMock) -> None:
        """Patching only default_model should not reset auto_customer_model."""
        existing = {"sandbox": {"default_model": "old", "auto_customer_model": "existing"}}
        mock_redis.get = AsyncMock(return_value=json.dumps(existing))

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_config._get_redis", AsyncMock(return_value=mock_redis)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.patch(
                    "/admin/llm/config",
                    json={"sandbox": {"default_model": "new-model"}},
                )

        assert response.status_code == 200
        stored = json.loads(mock_redis.set.call_args[0][1])
        assert stored["sandbox"]["default_model"] == "new-model"
        assert stored["sandbox"]["auto_customer_model"] == "existing"


class TestListModels:
    """Test GET /admin/sandbox/models returns only enabled providers."""

    @pytest.fixture()
    def sandbox_app(self):
        from fastapi import FastAPI

        from src.api.sandbox import router as sandbox_router

        test_app = FastAPI()
        test_app.include_router(sandbox_router)
        return test_app

    @pytest.mark.asyncio()
    async def test_returns_only_enabled_providers(
        self, sandbox_app: Any, mock_redis: AsyncMock
    ) -> None:
        # Only gemini-flash is enabled in this config
        redis_config = {
            "providers": {
                "gemini-flash": {"enabled": True},
                "anthropic-sonnet": {"enabled": False},
            },
            "sandbox": {"default_model": "gemini-flash"},
        }
        mock_redis.get = AsyncMock(return_value=json.dumps(redis_config))

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_config._get_redis", AsyncMock(return_value=mock_redis)),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=sandbox_app), base_url="http://test"
            ) as ac:
                response = await ac.get("/admin/sandbox/models")

        assert response.status_code == 200
        data = response.json()
        model_ids = [m["id"] for m in data["models"]]
        assert "gemini-flash" in model_ids
        assert "anthropic-sonnet" not in model_ids
        assert data["default_model"] == "gemini-flash"

    @pytest.mark.asyncio()
    async def test_returns_empty_when_all_disabled(
        self, sandbox_app: Any, mock_redis: AsyncMock
    ) -> None:
        # All providers disabled
        redis_config = {
            "providers": {
                "anthropic-sonnet": {"enabled": False},
                "anthropic-haiku": {"enabled": False},
            },
        }
        mock_redis.get = AsyncMock(return_value=json.dumps(redis_config))

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_config._get_redis", AsyncMock(return_value=mock_redis)),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=sandbox_app), base_url="http://test"
            ) as ac:
                response = await ac.get("/admin/sandbox/models")

        assert response.status_code == 200
        data = response.json()
        assert data["models"] == []
        assert data["default_model"] == ""


class TestDeleteProvider:
    """Test DELETE /admin/llm/config/providers/{key}."""

    @pytest.mark.asyncio()
    async def test_delete_provider_success(self, app: Any, mock_redis: AsyncMock) -> None:
        """Delete a provider not used in any task."""
        # Redis has a custom provider that is not referenced in tasks
        redis_config = {
            "providers": {
                "custom-test": {
                    "type": "openai",
                    "model": "test-model",
                    "api_key_env": "TEST_KEY",
                    "enabled": False,
                }
            }
        }
        mock_redis.get = AsyncMock(return_value=json.dumps(redis_config))

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.llm._router_instance", None),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.delete("/admin/llm/config/providers/custom-test")

        assert response.status_code == 200
        data = response.json()
        assert "deleted" in data["message"]
        # Provider should be gone from returned config
        assert "custom-test" not in data["config"]["providers"]
        # Redis should have been updated
        mock_redis.set.assert_called_once()

    @pytest.mark.asyncio()
    async def test_delete_provider_in_use_as_primary(self, app: Any, mock_redis: AsyncMock) -> None:
        """Cannot delete provider used as primary in a task."""
        redis_config = {
            "tasks": {"agent": {"primary": "anthropic-sonnet"}}
        }
        mock_redis.get = AsyncMock(return_value=json.dumps(redis_config))

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_config._get_redis", AsyncMock(return_value=mock_redis)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.delete("/admin/llm/config/providers/anthropic-sonnet")

        assert response.status_code == 409
        data = response.json()
        assert "agent (primary)" in data["detail"]

    @pytest.mark.asyncio()
    async def test_delete_provider_in_use_as_fallback(self, app: Any, mock_redis: AsyncMock) -> None:
        """Cannot delete provider used as fallback."""
        redis_config = {
            "tasks": {"agent": {"primary": "gemini-flash", "fallbacks": ["anthropic-sonnet"]}}
        }
        mock_redis.get = AsyncMock(return_value=json.dumps(redis_config))

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_config._get_redis", AsyncMock(return_value=mock_redis)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.delete("/admin/llm/config/providers/anthropic-sonnet")

        assert response.status_code == 409
        data = response.json()
        assert "agent (fallback)" in data["detail"]

    @pytest.mark.asyncio()
    async def test_delete_default_provider(self, app: Any, mock_redis: AsyncMock) -> None:
        """Delete a provider that exists only in defaults — marks as __deleted__."""
        # No Redis override, so openai-gpt41-mini comes from defaults
        # First ensure it's not used in tasks by overriding tasks
        redis_config = {
            "tasks": {
                "agent": {"primary": "anthropic-sonnet", "fallbacks": []},
                "article_processor": {"primary": "anthropic-sonnet", "fallbacks": []},
                "quality_scoring": {"primary": "anthropic-sonnet", "fallbacks": []},
                "prompt_optimizer": {"primary": "anthropic-sonnet", "fallbacks": []},
            }
        }
        mock_redis.get = AsyncMock(return_value=json.dumps(redis_config))

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.llm._router_instance", None),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.delete("/admin/llm/config/providers/openai-gpt41-mini")

        assert response.status_code == 200
        # Check that __deleted__ marker was saved in Redis
        stored = json.loads(mock_redis.set.call_args[0][1])
        assert stored["providers"]["openai-gpt41-mini"]["__deleted__"] is True
        # Provider should not appear in merged config
        assert "openai-gpt41-mini" not in response.json()["config"]["providers"]

    @pytest.mark.asyncio()
    async def test_delete_nonexistent(self, app: Any, mock_redis: AsyncMock) -> None:
        """404 for provider that doesn't exist."""
        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_config._get_redis", AsyncMock(return_value=mock_redis)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.delete("/admin/llm/config/providers/nonexistent-xyz")

        assert response.status_code == 404
