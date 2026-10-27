"""Empty LLM reply in the voice loop: retry, then the router's fallback.

gemini-2.5-flash — the agent primary for networks without a provider override
(prokoleso) — now and then answers with no text and no tool call
(`tokens_out=0`, `stop=end_turn`): 2 of 145 calls in the 2026-10-27 goldset.
`StreamingAgentLoop.run_turn` asks again, and the last retry goes to the first
fallback of the agent chain; a network pinned to one provider retries that
provider. A turn still empty after the retries reaches `pipeline.py`, which
answers it with `EMPTY_RESPONSE_SOFT_TEXT`.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from src.agent.agent import ToolRouter
from src.agent.streaming_loop import _MAX_EMPTY_RETRIES, StreamingAgentLoop
from src.llm.models import LLMTask, StreamDone, StreamEvent, TextDelta, Usage
from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection
from tests.unit.mocks.mock_llm_router import MockLLMRouter
from tests.unit.mocks.mock_tts import MockTTSEngine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

PRIMARY = "gemini-2.5-flash"
FALLBACK = "openai-gpt41-mini"


class ChainRouter(MockLLMRouter):
    """MockLLMRouter that records each call's provider and knows its chain."""

    def __init__(self, responses: list[list[StreamEvent]]) -> None:
        super().__init__(responses)
        self.overrides: list[str | None] = []

    def _resolve_chain(self, task: LLMTask, provider_override: str | None) -> list[str]:
        assert task is LLMTask.AGENT
        return [provider_override] if provider_override else [PRIMARY, FALLBACK]

    async def complete_stream(
        self, task: Any, messages: Any, **kwargs: Any
    ) -> AsyncIterator[StreamEvent]:
        self.overrides.append(kwargs.get("provider_override"))
        async for e in super().complete_stream(task, messages, **kwargs):
            yield e


def _empty() -> list[StreamEvent]:
    return [StreamDone(stop_reason="end_turn", usage=Usage(13628, 0))]


def _text(text: str) -> list[StreamEvent]:
    return [TextDelta(text=text), StreamDone(stop_reason="end_turn", usage=Usage(10, 20))]


def _loop(router: ChainRouter, provider_override: str | None = None) -> StreamingAgentLoop:
    return StreamingAgentLoop(
        llm_router=router,
        tool_router=ToolRouter(),
        tts=MockTTSEngine(),
        conn=MockAudioSocketConnection(),
        barge_in_event=asyncio.Event(),
        system_prompt="Test system prompt",
        provider_override=provider_override,
    )


@pytest.mark.asyncio
async def test_one_empty_reply_is_asked_again_on_the_same_provider() -> None:
    router = ChainRouter([_empty(), _text("Шиповані — Nokian Nordman 8.")])
    history: list[dict[str, Any]] = []

    result = await _loop(router).run_turn("а які з них шиповані?", history)

    assert "Nokian Nordman 8" in result.spoken_text
    assert router.overrides == [None, None]
    # The empty reply leaves no blank assistant message behind.
    assert [m["role"] for m in history] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_last_retry_goes_to_the_chain_fallback() -> None:
    router = ChainRouter([_empty(), _empty(), _text("Є Nokian Nordman 8.")])

    result = await _loop(router).run_turn("а які з них шиповані?", [])

    assert "Nokian Nordman 8" in result.spoken_text
    assert router.overrides == [None, None, FALLBACK]


@pytest.mark.asyncio
async def test_a_pinned_network_retries_its_own_provider() -> None:
    # Not the chain's fallback, or leaving the pin would look the same.
    pinned = "anthropic-sonnet"
    router = ChainRouter([_empty(), _empty(), _text("Є Nokian Nordman 8.")])

    result = await _loop(router, provider_override=pinned).run_turn("шиповані?", [])

    assert "Nokian Nordman 8" in result.spoken_text
    assert router.overrides == [pinned, pinned, pinned]


@pytest.mark.asyncio
async def test_retries_are_capped_and_the_turn_comes_back_empty() -> None:
    replies = [_empty() for _ in range(_MAX_EMPTY_RETRIES + 1)]
    router = ChainRouter([*replies, _text("never asked")])

    result = await _loop(router).run_turn("шиповані?", [])

    # Empty turn → pipeline.py speaks EMPTY_RESPONSE_SOFT_TEXT.
    assert result.spoken_text == ""
    assert router.call_count == _MAX_EMPTY_RETRIES + 1
