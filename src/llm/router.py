"""Multi-provider LLM router with fallback and circuit breakers.

Routes LLM tasks to configured providers, with per-provider circuit breakers
and automatic fallback to alternative providers on failure.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import time
from typing import TYPE_CHECKING, Any

from aiobreaker import CircuitBreaker

from src.llm.models import (
    DEFAULT_ROUTING_CONFIG,
    LLMResponse,
    LLMTask,
    ProviderType,
    StreamDone,
)
from src.llm.providers.anthropic_provider import AnthropicProvider
from src.llm.providers.openai_compat import OpenAICompatProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from src.llm.models import StreamEvent
    from src.llm.providers.base import AbstractProvider

logger = logging.getLogger(__name__)

REDIS_CONFIG_KEY = "llm:routing_config"


class LLMRouter:
    """Routes LLM calls to configured providers with fallback.

    Config is loaded from Redis (key: llm:routing_config) with fallback
    to DEFAULT_ROUTING_CONFIG. API keys are read from environment variables
    (never stored in Redis).
    """

    def __init__(self) -> None:
        self._providers: dict[str, AbstractProvider] = {}
        self._breakers: dict[str, CircuitBreaker] = {}
        self._config: dict[str, Any] = {}
        self._initialized = False

    @property
    def config(self) -> dict[str, Any]:
        return self._config

    @property
    def providers(self) -> dict[str, AbstractProvider]:
        return self._providers

    async def initialize(self, redis: Any = None) -> None:
        """Load config and create provider instances.

        Args:
            redis: Optional Redis connection for loading saved config.
        """
        # Load config: Redis → env defaults
        self._config = await self._load_config(redis)

        # Create provider instances
        for key, provider_cfg in self._config.get("providers", {}).items():
            if not provider_cfg.get("enabled", False):
                continue

            api_key_env = provider_cfg.get("api_key_env", "")
            api_key = os.environ.get(api_key_env, "")
            if not api_key:
                logger.warning("Provider %s: env var %s not set, skipping", key, api_key_env)
                continue

            provider = self._create_provider(key, provider_cfg, api_key)
            if provider is not None:
                self._providers[key] = provider
                self._breakers[key] = CircuitBreaker(fail_max=5, timeout_duration=30)
                logger.info("LLM provider initialized: %s (%s)", key, provider_cfg.get("model"))

        self._initialized = True
        logger.info("LLM router initialized: %d providers active", len(self._providers))

    async def reload_config(self, redis: Any = None) -> None:
        """Reload config from Redis and reinitialize providers."""
        await self.close()
        self._providers.clear()
        self._breakers.clear()
        await self.initialize(redis)

    def _resolve_chain(self, task: LLMTask, provider_override: str | None) -> list[str]:
        """Build ordered provider chain for a task."""
        if provider_override is not None:
            if provider_override not in self._providers:
                raise RuntimeError(
                    f"Provider override '{provider_override}' not found. "
                    f"Available: {list(self._providers.keys())}"
                )
            return [provider_override]

        task_config = self._config.get("tasks", {}).get(task.value, {})
        primary = task_config.get("primary", "")
        fallbacks = task_config.get("fallbacks", [])
        chain = [key for key in [primary, *fallbacks] if key in self._providers]

        if not chain:
            raise RuntimeError(
                f"No available providers for task {task.value}. "
                f"Configured providers: {list(self._providers.keys())}"
            )
        return chain

    async def complete(
        self,
        task: LLMTask,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 1024,
        provider_override: str | None = None,
    ) -> LLMResponse:
        """Route an LLM call through the provider chain for the given task.

        Args:
            task: The LLM task type (used for routing when no override).
            messages: Conversation messages.
            system: Optional system prompt.
            tools: Optional tool definitions.
            max_tokens: Max tokens for the response.
            provider_override: If set, bypass task routing and use this
                provider key directly (e.g. "gemini-flash").

        Tries primary provider first, then fallbacks in order.
        Raises RuntimeError if all providers fail.
        """
        chain = self._resolve_chain(task, provider_override)

        last_error: Exception | None = None
        for idx, provider_key in enumerate(chain):
            try:
                response = await self._call_provider(
                    provider_key, messages, system, tools, max_tokens
                )
                if idx > 0:
                    logger.warning(
                        "LLM fallback activated: task=%s, from=%s to=%s",
                        task.value,
                        chain[0],
                        provider_key,
                    )
                    self._record_fallback(chain[0], provider_key, task)
                self._record_success(provider_key, task)
                return response
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Provider %s failed for task %s: %s",
                    provider_key,
                    task.value,
                    exc,
                )
                self._record_error(provider_key, task)

        raise RuntimeError(
            f"All providers failed for task {task.value}: {last_error}"
        ) from last_error

    async def complete_stream(
        self,
        task: LLMTask,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 1024,
        provider_override: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Streaming version of complete() with circuit breaker + fallback."""
        chain = self._resolve_chain(task, provider_override)

        last_error: Exception | None = None
        for idx, provider_key in enumerate(chain):
            try:
                async for event in self._stream_provider(
                    provider_key, messages, system, tools or [], max_tokens
                ):
                    yield event
                # Stream completed successfully
                if idx > 0:
                    self._record_fallback(chain[0], provider_key, task)
                self._record_success(provider_key, task)
                return
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Stream provider %s failed for task %s: %s",
                    provider_key,
                    task.value,
                    exc,
                )
                self._record_error(provider_key, task)

        raise RuntimeError(
            f"All providers failed streaming for task {task.value}: {last_error}"
        ) from last_error

    async def _stream_provider(
        self,
        provider_key: str,
        messages: list[dict[str, Any]],
        system: str | None,
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> AsyncIterator[StreamEvent]:
        """Stream from a single provider with circuit breaker."""
        provider = self._providers[provider_key]
        breaker = self._breakers[provider_key]
        start = time.monotonic()

        gen = provider.stream_with_tools(messages, tools, system, max_tokens)

        # Sentinel-first-event: await first event through circuit breaker
        async def _get_first() -> StreamEvent:
            return await gen.__anext__()

        first_event = await breaker.call_async(_get_first)
        yield first_event

        # Remaining events — provider is alive, stream directly
        async for event in gen:
            yield event
            if isinstance(event, StreamDone):
                latency_ms = int((time.monotonic() - start) * 1000)
                self._record_latency(provider_key, latency_ms)

    def get_available_models(self) -> list[dict[str, str]]:
        """Return list of available provider models for UI dropdowns.

        Returns a list of dicts with keys: id (provider key), label, model, type.
        Only includes providers that are enabled and have valid API keys.
        """
        models: list[dict[str, str]] = []
        for key, _provider in self._providers.items():
            cfg = self._config.get("providers", {}).get(key, {})
            models.append(
                {
                    "id": key,
                    "label": key,
                    "model": cfg.get("model", ""),
                    "type": cfg.get("type", ""),
                }
            )
        return models

    async def health_check_all(self) -> dict[str, bool]:
        """Run health checks on all initialized providers."""
        results: dict[str, bool] = {}
        for key, provider in self._providers.items():
            try:
                results[key] = await provider.health_check()
            except Exception:
                results[key] = False
        return results

    async def close(self) -> None:
        """Close all provider sessions."""
        for key, provider in self._providers.items():
            try:
                await provider.close()
            except Exception:
                logger.warning("Error closing provider %s", key, exc_info=True)

    async def _call_provider(
        self,
        provider_key: str,
        messages: list[dict[str, Any]],
        system: str | None,
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
    ) -> LLMResponse:
        """Call a specific provider through its circuit breaker."""
        provider = self._providers[provider_key]
        breaker = self._breakers[provider_key]

        start = time.monotonic()

        if tools:
            response: LLMResponse = await breaker.call_async(
                provider.complete_with_tools,
                messages,
                tools,
                system,
                max_tokens,
            )
        else:
            response = await breaker.call_async(
                provider.complete,
                messages,
                system,
                max_tokens,
            )

        latency_ms = int((time.monotonic() - start) * 1000)
        self._record_latency(provider_key, latency_ms)

        logger.info(
            "LLM call: provider=%s, model=%s, latency=%dms, tokens_in=%d, tokens_out=%d",
            provider_key,
            response.model,
            latency_ms,
            response.usage.input_tokens,
            response.usage.output_tokens,
        )

        return response

    @staticmethod
    def _create_provider(key: str, cfg: dict[str, Any], api_key: str) -> AbstractProvider | None:
        """Create a provider instance based on config type."""
        provider_type = cfg.get("type", "")
        model = cfg.get("model", "")

        if provider_type == ProviderType.ANTHROPIC:
            return AnthropicProvider(api_key=api_key, model=model, provider_key=key)

        if provider_type in (ProviderType.OPENAI, ProviderType.DEEPSEEK, ProviderType.GEMINI):
            base_url = cfg.get("base_url", "")
            if not base_url:
                logger.warning("Provider %s: no base_url configured", key)
                return None
            return OpenAICompatProvider(
                api_key=api_key,
                model=model,
                base_url=base_url,
                provider_key=key,
            )

        logger.warning("Unknown provider type: %s for key %s", provider_type, key)
        return None

    @staticmethod
    async def _load_config(redis: Any = None) -> dict[str, Any]:
        """Load routing config from Redis, falling back to defaults."""
        config = copy.deepcopy(DEFAULT_ROUTING_CONFIG)

        if redis is not None:
            try:
                raw = await redis.get(REDIS_CONFIG_KEY)
                if raw:
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    redis_config = json.loads(raw)
                    # Merge Redis config over defaults
                    if "providers" in redis_config:
                        for key, val in redis_config["providers"].items():
                            if key in config["providers"]:
                                config["providers"][key].update(val)
                            else:
                                config["providers"][key] = val
                    if "tasks" in redis_config:
                        config["tasks"].update(redis_config["tasks"])
                    logger.info("LLM routing config loaded from Redis")
            except Exception:
                logger.warning(
                    "Failed to load LLM config from Redis, using defaults", exc_info=True
                )

        return config

    @staticmethod
    def _record_success(provider_key: str, task: LLMTask) -> None:
        """Record successful LLM request metric."""
        try:
            from src.monitoring.metrics import llm_requests_total

            llm_requests_total.labels(provider=provider_key, task=task.value).inc()
        except Exception:
            pass

    @staticmethod
    def _record_error(provider_key: str, task: LLMTask) -> None:
        """Record failed LLM request metric."""
        try:
            from src.monitoring.metrics import llm_errors_total

            llm_errors_total.labels(provider=provider_key, task=task.value).inc()
        except Exception:
            pass

    @staticmethod
    def _record_latency(provider_key: str, latency_ms: int) -> None:
        """Record LLM latency metric."""
        try:
            from src.monitoring.metrics import llm_provider_latency_ms

            llm_provider_latency_ms.labels(provider=provider_key).observe(latency_ms)
        except Exception:
            pass

    @staticmethod
    def _record_fallback(from_key: str, to_key: str, task: LLMTask) -> None:
        """Record fallback activation metric."""
        try:
            from src.monitoring.metrics import llm_fallbacks_total

            llm_fallbacks_total.labels(
                from_provider=from_key, to_provider=to_key, task=task.value
            ).inc()
        except Exception:
            pass
