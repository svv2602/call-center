"""Tests for LLM streaming infrastructure — event types, provider streaming, router."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiobreaker import CircuitBreaker

from src.llm.format_converter import openai_stream_chunk_to_events
from src.llm.models import (
    LLMResponse,
    LLMTask,
    StreamDone,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
    Usage,
)
from src.llm.providers.base import AbstractProvider
from src.llm.router import LLMRouter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(text: str = "ok", provider: str = "test") -> LLMResponse:
    return LLMResponse(
        text=text,
        tool_calls=[],
        stop_reason="end_turn",
        usage=Usage(input_tokens=10, output_tokens=5),
        provider=provider,
        model="test-model",
    )


def _make_response_with_tools() -> LLMResponse:
    return LLMResponse(
        text="I'll search",
        tool_calls=[
            ToolCall(id="tc_1", name="search_tires", arguments={"size": "205/55R16"}),
        ],
        stop_reason="tool_use",
        usage=Usage(input_tokens=20, output_tokens=15),
        provider="test",
        model="test-model",
    )


async def _collect_events(gen) -> list[StreamEvent]:
    """Collect all events from an async generator."""
    events = []
    async for event in gen:
        events.append(event)
    return events


def _setup_router_with_mock(provider_mock: AsyncMock) -> LLMRouter:
    """Create a router with a single mock provider."""
    router = LLMRouter()
    router._providers = {"test-provider": provider_mock}
    router._breakers = {"test-provider": CircuitBreaker(fail_max=5, timeout_duration=30)}
    router._config = {
        "providers": {"test-provider": {"enabled": True}},
        "tasks": {"agent": {"primary": "test-provider", "fallbacks": []}},
    }
    router._initialized = True
    return router


# ---------------------------------------------------------------------------
# 1. Event types
# ---------------------------------------------------------------------------


class TestStreamEventTypes:
    """Test creation of all streaming event types."""

    def test_text_delta(self) -> None:
        ev = TextDelta(text="Hello")
        assert ev.text == "Hello"

    def test_tool_call_start(self) -> None:
        ev = ToolCallStart(id="tc_1", name="search_tires")
        assert ev.id == "tc_1"
        assert ev.name == "search_tires"

    def test_tool_call_delta(self) -> None:
        ev = ToolCallDelta(id="tc_1", arguments_chunk='{"size":')
        assert ev.id == "tc_1"
        assert ev.arguments_chunk == '{"size":'

    def test_tool_call_end(self) -> None:
        ev = ToolCallEnd(id="tc_1")
        assert ev.id == "tc_1"

    def test_stream_done(self) -> None:
        ev = StreamDone(stop_reason="end_turn", usage=Usage(10, 5))
        assert ev.stop_reason == "end_turn"
        assert ev.usage.input_tokens == 10
        assert ev.usage.output_tokens == 5

    def test_frozen_dataclasses(self) -> None:
        ev = TextDelta(text="hello")
        with pytest.raises(AttributeError):
            ev.text = "world"  # type: ignore[misc]

    def test_stream_event_union_type(self) -> None:
        """StreamEvent union covers all event types."""
        events: list[StreamEvent] = [
            TextDelta(text="a"),
            ToolCallStart(id="t", name="n"),
            ToolCallDelta(id="t", arguments_chunk="{}"),
            ToolCallEnd(id="t"),
            StreamDone(stop_reason="end_turn", usage=Usage(0, 0)),
        ]
        assert len(events) == 5
        assert isinstance(events[0], TextDelta)
        assert isinstance(events[-1], StreamDone)


# ---------------------------------------------------------------------------
# 2. Base provider fallback streaming
# ---------------------------------------------------------------------------


class TestBaseProviderStreamFallback:
    """Default stream_with_tools falls back to complete_with_tools."""

    @pytest.mark.asyncio()
    async def test_fallback_text_only(self) -> None:
        """Text-only response yields TextDelta + StreamDone."""

        class FakeProvider(AbstractProvider):
            async def complete(self, messages, system=None, max_tokens=1024):
                return _make_response()

            async def complete_with_tools(self, messages, tools, system=None, max_tokens=300):
                return _make_response("text response")

            async def health_check(self):
                return True

        provider = FakeProvider()
        events = await _collect_events(
            provider.stream_with_tools([{"role": "user", "content": "hi"}], [])
        )

        assert len(events) == 2
        assert isinstance(events[0], TextDelta)
        assert events[0].text == "text response"
        assert isinstance(events[1], StreamDone)
        assert events[1].stop_reason == "end_turn"

    @pytest.mark.asyncio()
    async def test_fallback_with_tool_calls(self) -> None:
        """Response with tool calls yields ToolCallStart/Delta/End + StreamDone."""

        class FakeProvider(AbstractProvider):
            async def complete(self, messages, system=None, max_tokens=1024):
                return _make_response()

            async def complete_with_tools(self, messages, tools, system=None, max_tokens=300):
                return _make_response_with_tools()

            async def health_check(self):
                return True

        provider = FakeProvider()
        events = await _collect_events(
            provider.stream_with_tools([{"role": "user", "content": "hi"}], [])
        )

        # TextDelta, ToolCallStart, ToolCallDelta, ToolCallEnd, StreamDone
        assert len(events) == 5
        assert isinstance(events[0], TextDelta)
        assert events[0].text == "I'll search"
        assert isinstance(events[1], ToolCallStart)
        assert events[1].name == "search_tires"
        assert isinstance(events[2], ToolCallDelta)
        assert json.loads(events[2].arguments_chunk) == {"size": "205/55R16"}
        assert isinstance(events[3], ToolCallEnd)
        assert events[3].id == "tc_1"
        assert isinstance(events[4], StreamDone)
        assert events[4].stop_reason == "tool_use"

    @pytest.mark.asyncio()
    async def test_fallback_empty_text(self) -> None:
        """No TextDelta if response.text is empty."""

        class FakeProvider(AbstractProvider):
            async def complete(self, messages, system=None, max_tokens=1024):
                return _make_response()

            async def complete_with_tools(self, messages, tools, system=None, max_tokens=300):
                return LLMResponse(
                    text="",
                    tool_calls=[ToolCall(id="tc_1", name="check", arguments={})],
                    stop_reason="tool_use",
                    usage=Usage(5, 3),
                )

            async def health_check(self):
                return True

        provider = FakeProvider()
        events = await _collect_events(
            provider.stream_with_tools([{"role": "user", "content": "hi"}], [])
        )

        # ToolCallStart, ToolCallDelta, ToolCallEnd, StreamDone (no TextDelta)
        assert len(events) == 4
        assert isinstance(events[0], ToolCallStart)


# ---------------------------------------------------------------------------
# 3. OpenAI SSE chunk parser
# ---------------------------------------------------------------------------


class TestOpenAIStreamChunkParser:
    """Test openai_stream_chunk_to_events for various chunk types."""

    def test_text_content_chunk(self) -> None:
        chunk = {
            "choices": [{"delta": {"content": "Hello"}, "finish_reason": None}],
        }
        events = openai_stream_chunk_to_events(chunk, "openai", "gpt-4o")
        assert len(events) == 1
        assert isinstance(events[0], TextDelta)
        assert events[0].text == "Hello"

    def test_empty_content_skipped(self) -> None:
        chunk = {
            "choices": [{"delta": {"content": ""}, "finish_reason": None}],
        }
        events = openai_stream_chunk_to_events(chunk, "openai", "gpt-4o")
        assert len(events) == 0

    def test_tool_call_start_chunk(self) -> None:
        chunk = {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "id": "call_123",
                                "function": {"name": "search_tires", "arguments": ""},
                            }
                        ],
                    },
                    "finish_reason": None,
                }
            ],
        }
        events = openai_stream_chunk_to_events(chunk, "openai", "gpt-4o")
        assert len(events) == 1
        assert isinstance(events[0], ToolCallStart)
        assert events[0].id == "call_123"
        assert events[0].name == "search_tires"

    def test_tool_call_delta_chunk(self) -> None:
        chunk = {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "id": "",
                                "function": {"arguments": '{"size":'},
                            }
                        ],
                    },
                    "finish_reason": None,
                }
            ],
        }
        events = openai_stream_chunk_to_events(chunk, "openai", "gpt-4o")
        assert len(events) == 1
        assert isinstance(events[0], ToolCallDelta)
        assert events[0].arguments_chunk == '{"size":'

    def test_tool_call_start_and_delta_in_one(self) -> None:
        """First chunk with id + name + arguments fragment."""
        chunk = {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "id": "call_456",
                                "function": {"name": "check_availability", "arguments": '{"sku"'},
                            }
                        ],
                    },
                    "finish_reason": None,
                }
            ],
        }
        events = openai_stream_chunk_to_events(chunk, "openai", "gpt-4o")
        assert len(events) == 2
        assert isinstance(events[0], ToolCallStart)
        assert isinstance(events[1], ToolCallDelta)

    def test_finish_reason_stop(self) -> None:
        chunk = {
            "choices": [{"delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }
        events = openai_stream_chunk_to_events(chunk, "openai", "gpt-4o")
        assert len(events) == 1
        assert isinstance(events[0], StreamDone)
        assert events[0].stop_reason == "end_turn"
        assert events[0].usage.input_tokens == 100

    def test_finish_reason_tool_calls(self) -> None:
        chunk = {
            "choices": [{"delta": {}, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 20},
        }
        events = openai_stream_chunk_to_events(chunk, "openai", "gpt-4o")
        assert len(events) == 1
        assert isinstance(events[0], StreamDone)
        assert events[0].stop_reason == "tool_use"

    def test_finish_reason_length(self) -> None:
        chunk = {
            "choices": [{"delta": {}, "finish_reason": "length"}],
        }
        events = openai_stream_chunk_to_events(chunk, "openai", "gpt-4o")
        assert len(events) == 1
        assert isinstance(events[0], StreamDone)
        assert events[0].stop_reason == "max_tokens"

    def test_empty_choices(self) -> None:
        chunk: dict[str, Any] = {"choices": []}
        events = openai_stream_chunk_to_events(chunk, "openai", "gpt-4o")
        assert len(events) == 0

    def test_no_choices_key(self) -> None:
        chunk: dict[str, Any] = {"id": "chatcmpl-xxx"}
        events = openai_stream_chunk_to_events(chunk, "openai", "gpt-4o")
        assert len(events) == 0


# ---------------------------------------------------------------------------
# 4. Router _resolve_chain
# ---------------------------------------------------------------------------


class TestResolveChain:
    """Test _resolve_chain helper."""

    def test_override_returns_single(self) -> None:
        router = LLMRouter()
        router._providers = {"a": MagicMock(), "b": MagicMock()}
        router._config = {"tasks": {"agent": {"primary": "a", "fallbacks": ["b"]}}}

        chain = router._resolve_chain(LLMTask.AGENT, provider_override="b")
        assert chain == ["b"]

    def test_override_not_found_raises(self) -> None:
        router = LLMRouter()
        router._providers = {"a": MagicMock()}
        router._config = {"tasks": {"agent": {"primary": "a", "fallbacks": []}}}

        with pytest.raises(RuntimeError, match="Provider override 'missing' not found"):
            router._resolve_chain(LLMTask.AGENT, provider_override="missing")

    def test_no_override_returns_chain(self) -> None:
        router = LLMRouter()
        router._providers = {"a": MagicMock(), "b": MagicMock()}
        router._config = {"tasks": {"agent": {"primary": "a", "fallbacks": ["b"]}}}

        chain = router._resolve_chain(LLMTask.AGENT, provider_override=None)
        assert chain == ["a", "b"]

    def test_filters_unavailable_providers(self) -> None:
        router = LLMRouter()
        router._providers = {"b": MagicMock()}  # "a" not in providers
        router._config = {"tasks": {"agent": {"primary": "a", "fallbacks": ["b"]}}}

        chain = router._resolve_chain(LLMTask.AGENT, provider_override=None)
        assert chain == ["b"]

    def test_empty_chain_raises(self) -> None:
        router = LLMRouter()
        router._providers = {}
        router._config = {"tasks": {"agent": {"primary": "gone", "fallbacks": []}}}

        with pytest.raises(RuntimeError, match="No available providers"):
            router._resolve_chain(LLMTask.AGENT, provider_override=None)


# ---------------------------------------------------------------------------
# 5. Router complete_stream
# ---------------------------------------------------------------------------


class TestRouterCompleteStream:
    """Test complete_stream with mock providers."""

    @pytest.mark.asyncio()
    async def test_stream_success(self) -> None:
        """Streaming from a working provider yields all events."""

        async def fake_stream(messages, tools, system=None, max_tokens=300):
            yield TextDelta(text="Hello")
            yield TextDelta(text=" world")
            yield StreamDone(stop_reason="end_turn", usage=Usage(10, 5))

        provider_mock = AsyncMock()
        provider_mock.stream_with_tools = fake_stream
        provider_mock.close = AsyncMock()

        router = _setup_router_with_mock(provider_mock)

        events = await _collect_events(
            router.complete_stream(
                LLMTask.AGENT,
                messages=[{"role": "user", "content": "hi"}],
            )
        )
        assert len(events) == 3
        assert isinstance(events[0], TextDelta)
        assert isinstance(events[1], TextDelta)
        assert isinstance(events[2], StreamDone)
        await router.close()

    @pytest.mark.asyncio()
    async def test_stream_fallback(self) -> None:
        """If primary fails, falls back to next provider."""

        async def fail_stream(messages, tools, system=None, max_tokens=300):
            raise RuntimeError("Primary down")
            yield  # make it a generator  # noqa: RUF027

        async def ok_stream(messages, tools, system=None, max_tokens=300):
            yield TextDelta(text="fallback")
            yield StreamDone(stop_reason="end_turn", usage=Usage(5, 3))

        primary = AsyncMock()
        primary.stream_with_tools = fail_stream
        primary.close = AsyncMock()

        fallback = AsyncMock()
        fallback.stream_with_tools = ok_stream
        fallback.close = AsyncMock()

        router = LLMRouter()
        router._providers = {"a": primary, "b": fallback}
        router._breakers = {
            "a": CircuitBreaker(fail_max=5, timeout_duration=30),
            "b": CircuitBreaker(fail_max=5, timeout_duration=30),
        }
        router._config = {
            "providers": {"a": {"enabled": True}, "b": {"enabled": True}},
            "tasks": {"agent": {"primary": "a", "fallbacks": ["b"]}},
        }
        router._initialized = True

        events = await _collect_events(
            router.complete_stream(
                LLMTask.AGENT,
                messages=[{"role": "user", "content": "hi"}],
            )
        )
        assert len(events) == 2
        assert isinstance(events[0], TextDelta)
        assert events[0].text == "fallback"
        await router.close()

    @pytest.mark.asyncio()
    async def test_stream_all_fail(self) -> None:
        """RuntimeError when all providers fail streaming."""

        async def fail_stream(messages, tools, system=None, max_tokens=300):
            raise RuntimeError("down")
            yield  # noqa: RUF027

        provider_a = AsyncMock()
        provider_a.stream_with_tools = fail_stream
        provider_a.close = AsyncMock()

        provider_b = AsyncMock()
        provider_b.stream_with_tools = fail_stream
        provider_b.close = AsyncMock()

        router = LLMRouter()
        router._providers = {"a": provider_a, "b": provider_b}
        router._breakers = {
            "a": CircuitBreaker(fail_max=5, timeout_duration=30),
            "b": CircuitBreaker(fail_max=5, timeout_duration=30),
        }
        router._config = {
            "providers": {"a": {"enabled": True}, "b": {"enabled": True}},
            "tasks": {"agent": {"primary": "a", "fallbacks": ["b"]}},
        }
        router._initialized = True

        with pytest.raises(RuntimeError, match="All providers failed streaming"):
            await _collect_events(
                router.complete_stream(
                    LLMTask.AGENT,
                    messages=[{"role": "user", "content": "hi"}],
                )
            )
        await router.close()

    @pytest.mark.asyncio()
    async def test_stream_with_provider_override(self) -> None:
        """Provider override routes to specific provider."""

        async def override_stream(messages, tools, system=None, max_tokens=300):
            yield TextDelta(text="override")
            yield StreamDone(stop_reason="end_turn", usage=Usage(5, 3))

        primary = AsyncMock()
        primary.stream_with_tools = AsyncMock(side_effect=RuntimeError("should not be called"))
        primary.close = AsyncMock()

        override = AsyncMock()
        override.stream_with_tools = override_stream
        override.close = AsyncMock()

        router = LLMRouter()
        router._providers = {"a": primary, "b": override}
        router._breakers = {
            "a": CircuitBreaker(fail_max=5, timeout_duration=30),
            "b": CircuitBreaker(fail_max=5, timeout_duration=30),
        }
        router._config = {
            "providers": {"a": {"enabled": True}, "b": {"enabled": True}},
            "tasks": {"agent": {"primary": "a", "fallbacks": []}},
        }
        router._initialized = True

        events = await _collect_events(
            router.complete_stream(
                LLMTask.AGENT,
                messages=[{"role": "user", "content": "hi"}],
                provider_override="b",
            )
        )
        assert len(events) == 2
        assert events[0].text == "override"
        await router.close()

    @pytest.mark.asyncio()
    async def test_stream_with_tool_events(self) -> None:
        """Stream includes tool call events."""

        async def tool_stream(messages, tools, system=None, max_tokens=300):
            yield TextDelta(text="Let me search")
            yield ToolCallStart(id="tc_1", name="search_tires")
            yield ToolCallDelta(id="tc_1", arguments_chunk='{"size":"205/55R16"}')
            yield ToolCallEnd(id="tc_1")
            yield StreamDone(stop_reason="tool_use", usage=Usage(20, 15))

        provider_mock = AsyncMock()
        provider_mock.stream_with_tools = tool_stream
        provider_mock.close = AsyncMock()

        router = _setup_router_with_mock(provider_mock)

        events = await _collect_events(
            router.complete_stream(
                LLMTask.AGENT,
                messages=[{"role": "user", "content": "hi"}],
                tools=[{"name": "search_tires", "description": "...", "input_schema": {}}],
            )
        )
        assert len(events) == 5
        assert isinstance(events[1], ToolCallStart)
        assert isinstance(events[2], ToolCallDelta)
        assert isinstance(events[3], ToolCallEnd)
        assert isinstance(events[4], StreamDone)
        assert events[4].stop_reason == "tool_use"
        await router.close()


# ---------------------------------------------------------------------------
# 6. Feature flag
# ---------------------------------------------------------------------------


class TestStreamingFeatureFlag:
    """Test FF_STREAMING_LLM feature flag."""

    def test_default_false(self) -> None:
        from src.config import FeatureFlagSettings

        settings = FeatureFlagSettings()
        assert settings.streaming_llm is False

    def test_env_override(self) -> None:
        from src.config import FeatureFlagSettings

        with patch.dict("os.environ", {"FF_STREAMING_LLM": "true"}):
            settings = FeatureFlagSettings()
            assert settings.streaming_llm is True
