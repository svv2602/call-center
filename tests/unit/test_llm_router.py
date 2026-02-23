"""Tests for LLM router — config loading, fallback chain, circuit breaker."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.llm.models import DEFAULT_ROUTING_CONFIG, LLMResponse, LLMTask, Usage
from src.llm.router import LLMRouter, REDIS_CONFIG_KEY


def _make_response(text: str = "ok", provider: str = "test") -> LLMResponse:
    return LLMResponse(
        text=text,
        tool_calls=[],
        stop_reason="end_turn",
        usage=Usage(input_tokens=10, output_tokens=5),
        provider=provider,
        model="test-model",
    )


class TestLLMRouterConfig:
    """Test config loading from Redis and defaults."""

    @pytest.mark.asyncio()
    async def test_default_config_when_redis_empty(self) -> None:
        router = LLMRouter()
        # Mock Redis returns None
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            await router.initialize(redis=mock_redis)

        assert router.config is not None
        assert "providers" in router.config
        assert "tasks" in router.config
        assert router.config["tasks"]["agent"]["primary"] == "anthropic-haiku"
        # Anthropic providers should be initialized (key is set)
        assert "anthropic-haiku" in router.providers
        assert "anthropic-haiku" in router.providers
        await router.close()

    @pytest.mark.asyncio()
    async def test_redis_config_override(self) -> None:
        router = LLMRouter()
        redis_config = {
            "providers": {
                "anthropic-sonnet": {"model": "custom-model-v2"},
            },
            "tasks": {
                "agent": {"primary": "anthropic-haiku", "fallbacks": ["anthropic-sonnet"]},
            },
        }
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=json.dumps(redis_config))

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            await router.initialize(redis=mock_redis)

        assert router.config["providers"]["anthropic-sonnet"]["model"] == "custom-model-v2"
        assert router.config["tasks"]["agent"]["primary"] == "anthropic-haiku"
        assert router.config["tasks"]["agent"]["fallbacks"] == ["anthropic-sonnet"]
        await router.close()

    @pytest.mark.asyncio()
    async def test_redis_config_as_bytes(self) -> None:
        """Redis may return bytes when decode_responses=False."""
        router = LLMRouter()
        redis_config = {"providers": {"anthropic-sonnet": {"enabled": True}}}
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=json.dumps(redis_config).encode("utf-8"))

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            await router.initialize(redis=mock_redis)

        assert "anthropic-sonnet" in router.providers
        await router.close()

    @pytest.mark.asyncio()
    async def test_missing_api_key_skips_provider(self) -> None:
        router = LLMRouter()
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)

        with patch.dict("os.environ", {}, clear=True):
            await router.initialize(redis=mock_redis)

        # No providers because ANTHROPIC_API_KEY is not set
        assert len(router.providers) == 0
        await router.close()

    @pytest.mark.asyncio()
    async def test_no_redis_uses_defaults(self) -> None:
        router = LLMRouter()
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            await router.initialize(redis=None)

        assert router.config["tasks"]["agent"]["primary"] == "anthropic-haiku"
        await router.close()


class TestLLMRouterRouting:
    """Test request routing and fallback chain."""

    @pytest.mark.asyncio()
    async def test_route_to_primary(self) -> None:
        router = LLMRouter()
        mock_provider = AsyncMock()
        mock_provider.complete = AsyncMock(return_value=_make_response("primary"))
        mock_provider.close = AsyncMock()

        router._providers = {"anthropic-sonnet": mock_provider}
        from aiobreaker import CircuitBreaker

        router._breakers = {"anthropic-sonnet": CircuitBreaker(fail_max=5, timeout_duration=30)}
        router._config = {
            "providers": {"anthropic-sonnet": {"enabled": True}},
            "tasks": {"agent": {"primary": "anthropic-sonnet", "fallbacks": []}},
        }
        router._initialized = True

        result = await router.complete(
            LLMTask.AGENT,
            messages=[{"role": "user", "content": "hi"}],
        )
        assert result.text == "primary"
        await router.close()

    @pytest.mark.asyncio()
    async def test_fallback_on_primary_failure(self) -> None:
        router = LLMRouter()

        primary = AsyncMock()
        primary.complete = AsyncMock(side_effect=RuntimeError("Primary down"))
        primary.close = AsyncMock()

        fallback = AsyncMock()
        fallback.complete = AsyncMock(return_value=_make_response("fallback"))
        fallback.close = AsyncMock()

        router._providers = {"provider-a": primary, "provider-b": fallback}
        from aiobreaker import CircuitBreaker

        router._breakers = {
            "provider-a": CircuitBreaker(fail_max=5, timeout_duration=30),
            "provider-b": CircuitBreaker(fail_max=5, timeout_duration=30),
        }
        router._config = {
            "providers": {"provider-a": {"enabled": True}, "provider-b": {"enabled": True}},
            "tasks": {"agent": {"primary": "provider-a", "fallbacks": ["provider-b"]}},
        }
        router._initialized = True

        result = await router.complete(
            LLMTask.AGENT,
            messages=[{"role": "user", "content": "hi"}],
        )
        assert result.text == "fallback"
        await router.close()

    @pytest.mark.asyncio()
    async def test_all_providers_fail(self) -> None:
        router = LLMRouter()

        provider_a = AsyncMock()
        provider_a.complete = AsyncMock(side_effect=RuntimeError("Down"))
        provider_a.close = AsyncMock()

        provider_b = AsyncMock()
        provider_b.complete = AsyncMock(side_effect=RuntimeError("Also down"))
        provider_b.close = AsyncMock()

        router._providers = {"a": provider_a, "b": provider_b}
        from aiobreaker import CircuitBreaker

        router._breakers = {
            "a": CircuitBreaker(fail_max=5, timeout_duration=30),
            "b": CircuitBreaker(fail_max=5, timeout_duration=30),
        }
        router._config = {
            "providers": {"a": {"enabled": True}, "b": {"enabled": True}},
            "tasks": {"agent": {"primary": "a", "fallbacks": ["b"]}},
        }
        router._initialized = True

        with pytest.raises(RuntimeError, match="All providers failed"):
            await router.complete(
                LLMTask.AGENT,
                messages=[{"role": "user", "content": "hi"}],
            )
        await router.close()

    @pytest.mark.asyncio()
    async def test_no_available_providers(self) -> None:
        router = LLMRouter()
        router._providers = {}
        router._config = {
            "providers": {},
            "tasks": {"agent": {"primary": "nonexistent", "fallbacks": []}},
        }
        router._initialized = True

        with pytest.raises(RuntimeError, match="No available providers"):
            await router.complete(
                LLMTask.AGENT,
                messages=[{"role": "user", "content": "hi"}],
            )

    @pytest.mark.asyncio()
    async def test_route_with_tools(self) -> None:
        router = LLMRouter()
        mock_provider = AsyncMock()
        mock_provider.complete_with_tools = AsyncMock(return_value=_make_response("tools"))
        mock_provider.close = AsyncMock()

        router._providers = {"anthropic-sonnet": mock_provider}
        from aiobreaker import CircuitBreaker

        router._breakers = {"anthropic-sonnet": CircuitBreaker(fail_max=5, timeout_duration=30)}
        router._config = {
            "providers": {"anthropic-sonnet": {"enabled": True}},
            "tasks": {"agent": {"primary": "anthropic-sonnet", "fallbacks": []}},
        }
        router._initialized = True

        tools = [
            {
                "name": "search",
                "description": "Search",
                "input_schema": {"type": "object", "properties": {}},
            }
        ]
        result = await router.complete(
            LLMTask.AGENT,
            messages=[{"role": "user", "content": "hi"}],
            tools=tools,
        )
        assert result.text == "tools"
        mock_provider.complete_with_tools.assert_called_once()
        await router.close()


class TestLLMRouterProviderOverride:
    """Test provider_override bypasses task routing."""

    @pytest.mark.asyncio()
    async def test_provider_override_routes_directly(self) -> None:
        router = LLMRouter()
        primary = AsyncMock()
        primary.complete = AsyncMock(return_value=_make_response("primary"))
        primary.close = AsyncMock()

        override = AsyncMock()
        override.complete = AsyncMock(return_value=_make_response("override"))
        override.close = AsyncMock()

        router._providers = {"anthropic-sonnet": primary, "gemini-flash": override}
        from aiobreaker import CircuitBreaker

        router._breakers = {
            "anthropic-sonnet": CircuitBreaker(fail_max=5, timeout_duration=30),
            "gemini-flash": CircuitBreaker(fail_max=5, timeout_duration=30),
        }
        router._config = {
            "providers": {
                "anthropic-sonnet": {"enabled": True},
                "gemini-flash": {"enabled": True},
            },
            "tasks": {"agent": {"primary": "anthropic-sonnet", "fallbacks": []}},
        }
        router._initialized = True

        result = await router.complete(
            LLMTask.AGENT,
            messages=[{"role": "user", "content": "hi"}],
            provider_override="gemini-flash",
        )
        assert result.text == "override"
        override.complete.assert_called_once()
        primary.complete.assert_not_called()
        await router.close()

    @pytest.mark.asyncio()
    async def test_provider_override_not_found(self) -> None:
        router = LLMRouter()
        router._providers = {"anthropic-sonnet": AsyncMock()}
        router._config = {
            "providers": {"anthropic-sonnet": {"enabled": True}},
            "tasks": {"agent": {"primary": "anthropic-sonnet", "fallbacks": []}},
        }
        router._initialized = True

        with pytest.raises(RuntimeError, match="Provider override 'nonexistent' not found"):
            await router.complete(
                LLMTask.AGENT,
                messages=[{"role": "user", "content": "hi"}],
                provider_override="nonexistent",
            )

    @pytest.mark.asyncio()
    async def test_provider_override_with_tools(self) -> None:
        router = LLMRouter()
        mock_provider = AsyncMock()
        mock_provider.complete_with_tools = AsyncMock(return_value=_make_response("tools-override"))
        mock_provider.close = AsyncMock()

        router._providers = {"gemini-flash": mock_provider}
        from aiobreaker import CircuitBreaker

        router._breakers = {"gemini-flash": CircuitBreaker(fail_max=5, timeout_duration=30)}
        router._config = {
            "providers": {"gemini-flash": {"enabled": True}},
            "tasks": {"agent": {"primary": "anthropic-sonnet", "fallbacks": []}},
        }
        router._initialized = True

        tools = [
            {
                "name": "search",
                "description": "Search",
                "input_schema": {"type": "object", "properties": {}},
            }
        ]
        result = await router.complete(
            LLMTask.AGENT,
            messages=[{"role": "user", "content": "hi"}],
            tools=tools,
            provider_override="gemini-flash",
        )
        assert result.text == "tools-override"
        mock_provider.complete_with_tools.assert_called_once()
        await router.close()


class TestLLMRouterGetAvailableModels:
    """Test get_available_models method."""

    def test_returns_active_providers(self) -> None:
        router = LLMRouter()
        router._providers = {"anthropic-sonnet": MagicMock(), "gemini-flash": MagicMock()}
        router._config = {
            "providers": {
                "anthropic-sonnet": {"model": "claude-sonnet-4-5-20250929", "type": "anthropic"},
                "gemini-flash": {"model": "gemini-2.0-flash", "type": "gemini"},
            },
        }

        models = router.get_available_models()
        assert len(models) == 2
        ids = {m["id"] for m in models}
        assert ids == {"anthropic-sonnet", "gemini-flash"}

    def test_empty_when_no_providers(self) -> None:
        router = LLMRouter()
        router._providers = {}
        router._config = {"providers": {}}

        models = router.get_available_models()
        assert models == []


class TestLLMRouterHealthCheck:
    """Test health check aggregation."""

    @pytest.mark.asyncio()
    async def test_health_check_all(self) -> None:
        router = LLMRouter()

        healthy_provider = AsyncMock()
        healthy_provider.health_check = AsyncMock(return_value=True)
        healthy_provider.close = AsyncMock()

        unhealthy_provider = AsyncMock()
        unhealthy_provider.health_check = AsyncMock(return_value=False)
        unhealthy_provider.close = AsyncMock()

        router._providers = {"good": healthy_provider, "bad": unhealthy_provider}

        results = await router.health_check_all()
        assert results == {"good": True, "bad": False}
        await router.close()

    @pytest.mark.asyncio()
    async def test_health_check_exception(self) -> None:
        router = LLMRouter()

        error_provider = AsyncMock()
        error_provider.health_check = AsyncMock(side_effect=Exception("boom"))
        error_provider.close = AsyncMock()

        router._providers = {"error": error_provider}

        results = await router.health_check_all()
        assert results == {"error": False}
        await router.close()
