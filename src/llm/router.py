"""Multi-provider LLM router with fallback and circuit breakers.

Routes LLM tasks to configured providers, with per-provider circuit breakers
and automatic fallback to alternative providers on failure.
"""

from __future__ import annotations

import asyncio
import contextvars
import copy
import json
import logging
import os
import socket
import ssl
import time
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import aiohttp
import anthropic
import httpx  # the Anthropic SDK's transport; its errors surface mid-stream unwrapped
from aiobreaker import CircuitBreaker, CircuitBreakerError

from src.config import FeatureFlagSettings
from src.llm.models import (
    DEFAULT_ROUTING_CONFIG,
    LLMResponse,
    LLMTask,
    ProviderType,
    StreamDone,
)
from src.llm.providers.anthropic_provider import AnthropicProvider
from src.llm.providers.openai_compat import (
    FAILURE_REASONS,
    OpenAICompatProvider,
    ProviderHTTPError,
    classify_http_failure,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from src.llm.models import StreamEvent
    from src.llm.providers.base import AbstractProvider

logger = logging.getLogger(__name__)

REDIS_CONFIG_KEY = "llm:routing_config"

# Context vars for associating LLM calls with call/tenant
llm_call_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "llm_call_id", default=None
)
llm_tenant_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "llm_tenant_id", default=None
)

# Checked before the network errors: ``TimeoutError`` is an ``OSError``, and
# the timeouts of aiohttp, httpx and the Anthropic SDK inherit from their
# connection errors.
_TIMEOUT_ERRORS: tuple[type[BaseException], ...] = (
    TimeoutError,
    anthropic.APITimeoutError,
    httpx.TimeoutException,
)
_NETWORK_ERRORS: tuple[type[BaseException], ...] = (
    aiohttp.ClientConnectionError,
    aiohttp.ClientPayloadError,
    anthropic.APIConnectionError,
    httpx.TransportError,
    ConnectionError,
    socket.gaierror,
    ssl.SSLError,
)

# The exception text in the failure log: an HTTP error message already carries
# no more than 300 characters of the reply, the rest is cut to the same order.
_LOG_ERROR_CHARS = 400


def _provider_error(exc: BaseException) -> BaseException:
    """The provider's own exception behind the one the circuit breaker raised.

    The failure that opens a breaker arrives as ``CircuitBreakerError`` with
    the real one in ``__cause__``. A breaker that was already open has no
    cause — the provider was not called — and stays what it is.
    """
    if isinstance(exc, CircuitBreakerError) and exc.__cause__ is not None:
        return exc.__cause__
    return exc


def failure_reason(exc: BaseException) -> str:
    """Why a provider attempt failed — one of ``FAILURE_REASONS``.

    Default-deny: whatever matches nothing below is ``other``, including a
    call an open circuit breaker turned away. A missed first-event deadline
    and a provider's own timeout are both ``timeout``; what tells them apart
    is ``callcenter_llm_first_event_timeouts_total``.
    """
    exc = _provider_error(exc)
    if isinstance(exc, ProviderHTTPError):
        return exc.reason if exc.reason in FAILURE_REASONS else "other"
    if isinstance(exc, anthropic.APIStatusError):
        return classify_http_failure(exc.status_code, exc.body)
    if isinstance(exc, _TIMEOUT_ERRORS):
        return "timeout"
    if isinstance(exc, _NETWORK_ERRORS):
        return "network"
    return "other"


def _failure_status(exc: BaseException) -> int | None:
    """HTTP status of a failed attempt, when the failure was an HTTP reply."""
    exc = _provider_error(exc)
    for name in ("status", "status_code"):
        status = getattr(exc, name, None)
        if isinstance(status, int):
            return status
    return None


def live_call_stream_options() -> dict[str, Any]:
    """``complete_stream`` keywords of a live call, read from the env right now.

    Empty unless FF_LLM_OVERRIDE_FALLBACK is on — the call then goes out
    exactly as before. Only the voice loop of a real call asks for these:
    sandbox, goldset and replays must get the one model they named, and a
    replay measures time to first event, which a deadline would cut short.
    Read here, not from ``get_settings()``: that snapshot is built at import.
    """
    flags = FeatureFlagSettings()
    if not flags.llm_override_fallback:
        return {}
    options: dict[str, Any] = {"override_fallback": True}
    if flags.llm_first_event_timeout_sec > 0:
        options["first_event_timeout"] = flags.llm_first_event_timeout_sec
    return options


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
                self._breakers[key] = CircuitBreaker(
                    fail_max=5, timeout_duration=timedelta(seconds=30)
                )
                logger.info("LLM provider initialized: %s (%s)", key, provider_cfg.get("model"))

        self._export_failure_series()
        self._initialized = True
        logger.info("LLM router initialized: %d providers active", len(self._providers))

    async def reload_config(self, redis: Any = None) -> None:
        """Reload config from Redis and reinitialize providers."""
        await self.close()
        self._providers.clear()
        self._breakers.clear()
        await self.initialize(redis)

    def _task_chain(self, task: LLMTask) -> list[str]:
        """The task's configured providers (primary, then fallbacks) that are up."""
        task_config = self._config.get("tasks", {}).get(task.value, {})
        primary = task_config.get("primary", "")
        fallbacks = task_config.get("fallbacks", [])
        return [key for key in [primary, *fallbacks] if key in self._providers]

    def _resolve_chain(
        self,
        task: LLMTask,
        provider_override: str | None,
        *,
        override_fallback: bool = False,
    ) -> list[str]:
        """Build ordered provider chain for a task.

        An override is the whole chain: whoever names a model (sandbox,
        goldset, replay) gets that model or an error. ``override_fallback``
        — a live call — makes the override the first choice instead, followed
        by the task's own chain without repeats.
        """
        if provider_override is not None:
            if provider_override not in self._providers:
                raise RuntimeError(
                    f"Provider override '{provider_override}' not found. "
                    f"Available: {list(self._providers.keys())}"
                )
            chain = [provider_override]
            if override_fallback:
                for key in self._task_chain(task):
                    if key not in chain:
                        chain.append(key)
            return chain

        chain = self._task_chain(task)

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
        tool_choice: str | None = None,
        *,
        override_fallback: bool = False,
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
            tool_choice: Name of a tool the model must call in this response
                (every provider of the chain); ``None`` — the model decides.
            override_fallback: The override is the first choice, not the only
                one — the task's chain follows it (see ``_resolve_chain``).

        Tries primary provider first, then fallbacks in order.
        Raises RuntimeError if all providers fail.
        """
        chain = self._resolve_chain(task, provider_override, override_fallback=override_fallback)

        last_error: Exception | None = None
        for idx, provider_key in enumerate(chain):
            try:
                response = await self._call_provider(
                    provider_key,
                    messages,
                    system,
                    tools,
                    max_tokens,
                    task=task,
                    tool_choice=tool_choice,
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
                self._note_failure("Provider", provider_key, task, exc)

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
        tool_choice: str | None = None,
        *,
        override_fallback: bool = False,
        first_event_timeout: float | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Streaming version of complete() with circuit breaker + fallback.

        ``override_fallback`` and ``first_event_timeout`` belong to live calls
        (``live_call_stream_options``); without them an override is one
        provider, waited for as long as it takes.

        ``first_event_timeout`` — seconds each provider of the chain has to
        produce its first event; a provider that misses it is dropped like a
        failed one.
        """
        chain = self._resolve_chain(task, provider_override, override_fallback=override_fallback)
        # A chain grown from an override switches only while the caller has
        # heard nothing: events already yielded are being spoken, and a second
        # provider would start the reply over. (A strict override is a chain
        # of one — nothing to switch to either way.) The task's own chain, no
        # override, keeps its old behaviour.
        switch_only_before_first_event = provider_override is not None

        last_error: Exception | None = None
        for idx, provider_key in enumerate(chain):
            yielded = False
            try:
                async for event in self._stream_provider(
                    provider_key,
                    messages,
                    system,
                    tools or [],
                    max_tokens,
                    task=task,
                    tool_choice=tool_choice,
                    first_event_timeout=first_event_timeout,
                ):
                    yielded = True
                    yield event
                # Stream completed successfully
                if idx > 0:
                    self._record_fallback(chain[0], provider_key, task)
                self._record_success(provider_key, task)
                return
            except Exception as exc:
                last_error = exc
                self._note_failure("Stream provider", provider_key, task, exc)
                if yielded and switch_only_before_first_event:
                    break

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
        task: LLMTask | None = None,
        tool_choice: str | None = None,
        first_event_timeout: float | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream from a single provider with circuit breaker."""
        provider = self._providers[provider_key]
        breaker = self._breakers[provider_key]
        start = time.monotonic()

        # Passed only when set: a provider without the keyword keeps working.
        extra = {"tool_choice": tool_choice} if tool_choice else {}
        gen = provider.stream_with_tools(messages, tools, system, max_tokens, **extra)

        # Sentinel-first-event: await first event through circuit breaker.
        # The deadline lives inside the guarded call: the breaker counts an
        # `Exception` raised in here, and would not count a cancellation
        # (`BaseException`) coming from a timeout wrapped around it.
        async def _get_first() -> StreamEvent:
            if first_event_timeout is None:
                return await gen.__anext__()
            try:
                async with asyncio.timeout(first_event_timeout) as deadline:
                    return await gen.__anext__()
            except TimeoutError:
                if not deadline.expired():
                    raise  # the provider's own timeout, not this deadline
                # Nobody will read this stream, and nothing is left to close:
                # the cancellation has already unwound the provider's
                # generator and, with it, its HTTP response.
                logger.warning(
                    "LLM first event deadline missed: provider=%s, task=%s, deadline=%.1fs",
                    provider_key,
                    task.value if task is not None else "",
                    first_event_timeout,
                )
                self._record_first_event_timeout(provider_key, task)
                raise

        first_event = await breaker.call_async(_get_first)
        yield first_event

        # Remaining events — provider is alive, stream directly
        async for event in gen:
            if isinstance(event, StreamDone):
                # Enrich with provider_key so callers know which provider served this
                event = StreamDone(
                    stop_reason=event.stop_reason,
                    usage=event.usage,
                    provider_key=provider_key,
                )
                latency_ms = int((time.monotonic() - start) * 1000)
                self._record_latency(provider_key, latency_ms)
                if task is not None:
                    self._log_usage(
                        task, provider_key, "", latency_ms,
                        event.usage.input_tokens, event.usage.output_tokens,
                        cached_input_tokens=event.usage.cached_input_tokens,
                    )
            yield event

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

    async def warmup(self) -> None:
        """Pre-establish HTTP connections to providers.

        Makes a cheap API call (e.g. count_tokens or minimal completion)
        to force TCP+TLS handshake. Non-critical — failures are logged
        and silently ignored.
        """
        for key, provider in self._providers.items():
            try:
                await provider.health_check()
                logger.info("LLM warmup OK: %s", key)
            except Exception:
                logger.debug("LLM warmup failed for %s (non-critical)", key, exc_info=True)

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
        task: LLMTask | None = None,
        tool_choice: str | None = None,
    ) -> LLMResponse:
        """Call a specific provider through its circuit breaker."""
        provider = self._providers[provider_key]
        breaker = self._breakers[provider_key]

        start = time.monotonic()

        if tools:
            extra = {"tool_choice": tool_choice} if tool_choice else {}
            response: LLMResponse = await breaker.call_async(
                provider.complete_with_tools,
                messages,
                tools,
                system,
                max_tokens,
                **extra,
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

        if task is not None:
            self._log_usage(
                task, provider_key, response.model, latency_ms,
                response.usage.input_tokens, response.usage.output_tokens,
                cached_input_tokens=response.usage.cached_input_tokens,
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
                reasoning_effort=cfg.get("reasoning_effort"),
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

    def _note_failure(self, what: str, provider_key: str, task: LLMTask, exc: Exception) -> None:
        """Log one failed provider attempt and count it, once, with its reason."""
        reason = failure_reason(exc)
        logger.warning(
            "%s %s failed for task %s: reason=%s, status=%s: %s",
            what,
            provider_key,
            task.value,
            reason,
            _failure_status(exc),
            str(exc)[:_LOG_ERROR_CHARS],
        )
        self._record_error(provider_key, task)
        self._record_failure(provider_key, task, reason)

    @staticmethod
    def _record_failure(provider_key: str, task: LLMTask, reason: str) -> None:
        """Count a failed provider attempt under its reason."""
        try:
            from src.monitoring.metrics import llm_provider_failures_total

            llm_provider_failures_total.labels(
                provider=provider_key, task=task.value, reason=reason
            ).inc()
        except Exception:
            # Never fatal to the call, never silent either: the outage alerts
            # stand on this counter.
            logger.exception("LLM provider failure was not counted: provider=%s", provider_key)

    def _export_failure_series(self) -> None:
        """Export the failure counter of every active provider at zero.

        A series that first appears with the value 1 gives ``increase()``
        nothing to compare it with, so the alert on a single failure would
        miss the first one after each restart — with a handful of calls a day
        that is most of them.
        """
        try:
            from src.monitoring.metrics import llm_provider_failures_total

            for provider_key in self._providers:
                for task in LLMTask:
                    for reason in FAILURE_REASONS:
                        llm_provider_failures_total.labels(
                            provider=provider_key, task=task.value, reason=reason
                        )
        except Exception:
            logger.exception("LLM provider failure series were not exported")

    @staticmethod
    def _record_latency(provider_key: str, latency_ms: int) -> None:
        """Record LLM latency metric."""
        try:
            from src.monitoring.metrics import llm_provider_latency_ms

            llm_provider_latency_ms.labels(provider=provider_key).observe(latency_ms)
        except Exception:
            pass

    @staticmethod
    def _record_first_event_timeout(provider_key: str, task: LLMTask | None) -> None:
        """Record a stream dropped for missing the first-event deadline."""
        try:
            from src.monitoring.metrics import llm_first_event_timeouts_total

            llm_first_event_timeouts_total.labels(
                provider=provider_key, task=task.value if task is not None else ""
            ).inc()
        except Exception:
            pass

    @staticmethod
    def _log_usage(
        task: LLMTask,
        provider_key: str,
        model_name: str,
        latency_ms: int,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
    ) -> None:
        """Fire-and-forget: persist LLM usage to llm_usage_log."""
        try:
            from src.monitoring.llm_usage_logger import log_llm_usage

            log_llm_usage(
                task_type=task.value,
                provider_key=provider_key,
                model_name=model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
                latency_ms=latency_ms,
                call_id=llm_call_id_var.get(None),
                tenant_id=llm_tenant_id_var.get(None),
            )
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
