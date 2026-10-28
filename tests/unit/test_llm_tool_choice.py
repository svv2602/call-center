"""``tool_choice``: the code can make the model call one named tool.

The delivery quote needs it: told to call ``get_delivery_cost`` «ЗАВЖДИ», the
model still answered a delivery-cost question with «Записала: чотири шини.»
(goldset 2026-10-28). The first LLM round of such a turn now forces the tool;
every other call is unchanged (no ``tool_choice`` sent at all).
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, create_autospec

import pytest
from aiobreaker import CircuitBreaker

from src.agent import delivery_quote as dq
from src.agent.agent import LLMAgent, ToolRouter
from src.llm.models import LLMResponse, LLMTask, StreamDone, TextDelta, ToolCall, Usage
from src.llm.providers.anthropic_provider import AnthropicProvider
from src.llm.providers.base import AbstractProvider
from src.llm.providers.openai_compat import OpenAICompatProvider
from src.llm.router import LLMRouter

_TOOLS = [{"name": dq.DELIVERY_COST_TOOL, "description": "", "input_schema": {"type": "object"}}]
_ASK = "беру Bridgestone, чотири штуки. А доставка скільки коштує?"


class _FakeResponse:
    def __init__(self, data: dict[str, Any]) -> None:
        self.status = 200
        self._data = data

    async def json(self) -> dict[str, Any]:
        return self._data

    async def text(self) -> str:
        return json.dumps(self._data)

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


def _openai() -> tuple[OpenAICompatProvider, MagicMock]:
    provider = OpenAICompatProvider(
        api_key="k", model="gpt-4.1-mini", base_url="https://api.invalid/v1", provider_key="oa"
    )
    session = MagicMock()
    session.closed = False
    session.post.return_value = _FakeResponse(
        {
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
    )
    provider._session = session
    return provider, session


class TestProviders:
    def test_openai_forces_the_named_function(self) -> None:
        provider, session = _openai()
        asyncio.run(
            provider.complete_with_tools(
                [{"role": "user", "content": "x"}], _TOOLS, tool_choice=dq.DELIVERY_COST_TOOL
            )
        )
        body = session.post.call_args.kwargs["json"]
        assert body["tool_choice"] == {
            "type": "function",
            "function": {"name": dq.DELIVERY_COST_TOOL},
        }

    def test_openai_without_choice_sends_none(self) -> None:
        provider, session = _openai()
        asyncio.run(provider.complete_with_tools([{"role": "user", "content": "x"}], _TOOLS))
        assert "tool_choice" not in session.post.call_args.kwargs["json"]

    def test_openai_stream_forces_the_named_function(self) -> None:
        provider, session = _openai()
        session.post.side_effect = RuntimeError("stop after the body is built")

        async def run() -> None:
            async for _ in provider.stream_with_tools(
                [{"role": "user", "content": "x"}], _TOOLS, tool_choice=dq.DELIVERY_COST_TOOL
            ):
                pass

        with pytest.raises(RuntimeError):
            asyncio.run(run())
        body = session.post.call_args.kwargs["json"]
        assert body["tool_choice"]["function"]["name"] == dq.DELIVERY_COST_TOOL

    def test_anthropic_forces_the_named_tool(self) -> None:
        provider = AnthropicProvider(api_key="k", model="claude", provider_key="an")
        response = MagicMock()
        response.content = []
        response.stop_reason = "tool_use"
        response.usage.input_tokens = 1
        response.usage.output_tokens = 1
        provider._client.messages.create = AsyncMock(return_value=response)
        asyncio.run(
            provider.complete_with_tools(
                [{"role": "user", "content": "x"}], _TOOLS, tool_choice=dq.DELIVERY_COST_TOOL
            )
        )
        kwargs = provider._client.messages.create.call_args.kwargs
        assert kwargs["tool_choice"] == {"type": "tool", "name": dq.DELIVERY_COST_TOOL}


def _router(provider: Any) -> LLMRouter:
    router = LLMRouter()
    router._providers = {"p": provider}
    router._breakers = {"p": CircuitBreaker(fail_max=5, timeout_duration=timedelta(seconds=30))}
    router._config = {
        "providers": {"p": {"enabled": True}},
        "tasks": {"agent": {"primary": "p", "fallbacks": []}},
    }
    router._initialized = True
    return router


class TestRouter:
    def test_complete_hands_the_choice_to_the_provider(self) -> None:
        provider = create_autospec(AbstractProvider, instance=True)
        provider.complete_with_tools.return_value = LLMResponse(text="", provider="p")
        router = _router(provider)
        asyncio.run(
            router.complete(LLMTask.AGENT, [], tools=_TOOLS, tool_choice=dq.DELIVERY_COST_TOOL)
        )
        assert provider.complete_with_tools.call_args.kwargs["tool_choice"] == (
            dq.DELIVERY_COST_TOOL
        )

    def test_complete_without_choice_passes_no_keyword(self) -> None:
        provider = create_autospec(AbstractProvider, instance=True)
        provider.complete_with_tools.return_value = LLMResponse(text="", provider="p")
        asyncio.run(_router(provider).complete(LLMTask.AGENT, [], tools=_TOOLS))
        assert "tool_choice" not in provider.complete_with_tools.call_args.kwargs

    def test_stream_hands_the_choice_to_the_provider(self) -> None:
        seen: dict[str, Any] = {}

        class _Provider(AbstractProvider):
            async def complete(self, *a: Any, **k: Any) -> LLMResponse:
                raise AssertionError

            async def complete_with_tools(self, *a: Any, **k: Any) -> LLMResponse:
                seen.update(k)
                return LLMResponse(text="ok", provider="p")

            async def health_check(self) -> bool:
                return True

        router = _router(_Provider())

        async def run() -> None:
            async for _ in router.complete_stream(
                LLMTask.AGENT, [], tools=_TOOLS, tool_choice=dq.DELIVERY_COST_TOOL
            ):
                pass

        asyncio.run(run())
        assert seen["tool_choice"] == dq.DELIVERY_COST_TOOL


class TestTrigger:
    def test_a_cost_question_with_the_tool_forces_it(self) -> None:
        assert dq.forced_quote_tool(_ASK, _TOOLS) == dq.DELIVERY_COST_TOOL
        assert dq.forced_quote_tool("сколько стоит доставка во Львов?", _TOOLS) == (
            dq.DELIVERY_COST_TOOL
        )

    def test_no_tool_no_force(self) -> None:
        assert dq.forced_quote_tool(_ASK, [{"name": "search_tires"}]) is None

    @pytest.mark.parametrize(
        "line",
        ["як швидко доставите шини у Київ?", "потрібні чотири літні шини 205/55 R16", ""],
    )
    def test_not_a_cost_question(self, line: str) -> None:
        assert dq.forced_quote_tool(line, _TOOLS) is None


def _quote_router() -> ToolRouter:
    router = ToolRouter()

    async def _quote(**_: Any) -> dict[str, Any]:
        return {"status": dq.STATUS_NEED_MORE, "phrase": "Доставка — за тарифами перевізника."}

    router.register(dq.DELIVERY_COST_TOOL, _quote)
    return router


class TestWiring:
    def test_text_path_forces_only_the_first_call(self) -> None:
        llm_router = create_autospec(LLMRouter, instance=True)
        llm_router.complete = AsyncMock(
            side_effect=[
                LLMResponse(
                    text="",
                    tool_calls=[ToolCall(id="q1", name=dq.DELIVERY_COST_TOOL, arguments={})],
                    stop_reason="tool_use",
                    usage=Usage(1, 1),
                    provider="t",
                ),
                LLMResponse(text="У яке місто?", usage=Usage(1, 1), provider="t"),
            ]
        )
        agent = LLMAgent(
            api_key="k",
            system_prompt="base",
            llm_router=llm_router,
            tool_router=_quote_router(),
            tools=_TOOLS,
        )
        asyncio.run(agent.process_message(_ASK, []))
        first, second = llm_router.complete.call_args_list
        assert first.kwargs["tool_choice"] == dq.DELIVERY_COST_TOOL
        assert "tool_choice" not in second.kwargs

    @pytest.mark.asyncio
    async def test_streaming_path_forces_only_the_first_round(self) -> None:
        from src.agent.streaming_loop import StreamingAgentLoop
        from src.llm.models import ToolCallDelta, ToolCallEnd, ToolCallStart
        from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection
        from tests.unit.mocks.mock_llm_router import MockLLMRouter
        from tests.unit.test_delivery_quote_spoken import _RecordingTTS

        llm = MockLLMRouter(
            [
                [
                    ToolCallStart(id="q1", name=dq.DELIVERY_COST_TOOL),
                    ToolCallDelta(id="q1", arguments_chunk="{}"),
                    ToolCallEnd(id="q1"),
                    StreamDone(stop_reason="tool_use", usage=Usage(1, 1)),
                ],
                [TextDelta(text="У яке місто?"), StreamDone(stop_reason="end_turn", usage=Usage(1, 1))],
            ]
        )
        loop = StreamingAgentLoop(
            llm_router=llm,
            tool_router=_quote_router(),
            tts=_RecordingTTS(),
            conn=MockAudioSocketConnection(),
            barge_in_event=asyncio.Event(),
            tools=_TOOLS,
            system_prompt="Test system prompt",
        )
        await loop.run_turn(_ASK, [])
        assert llm.tool_choices == [dq.DELIVERY_COST_TOOL, None]

    @pytest.mark.asyncio
    async def test_streaming_path_other_questions_are_not_forced(self) -> None:
        from src.agent.streaming_loop import StreamingAgentLoop
        from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection
        from tests.unit.mocks.mock_llm_router import MockLLMRouter
        from tests.unit.test_delivery_quote_spoken import _RecordingTTS

        llm = MockLLMRouter(
            [[TextDelta(text="Добре."), StreamDone(stop_reason="end_turn", usage=Usage(1, 1))]]
        )
        loop = StreamingAgentLoop(
            llm_router=llm,
            tool_router=_quote_router(),
            tts=_RecordingTTS(),
            conn=MockAudioSocketConnection(),
            barge_in_event=asyncio.Event(),
            tools=_TOOLS,
            system_prompt="Test system prompt",
        )
        await loop.run_turn("потрібні чотири літні шини 205/55 R16", [])
        assert llm.tool_choices == [None]
