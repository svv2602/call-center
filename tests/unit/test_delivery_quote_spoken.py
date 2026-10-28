"""The delivery quote is said by the code, word for word (R08, goldset 2026-10-28).

The model retold the owner's phrase — dropped the cash-on-delivery fee, turned
«ще 60 грн» into a total. Both loops now speak the ``get_delivery_cost`` phrase
themselves, as they speak a tyre caveat, and the tool result tells the model
the caller has heard it.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, create_autospec

import pytest

from src.agent import delivery_quote as dq
from src.agent.agent import LLMAgent, ToolRouter
from src.agent.tool_result_compressor import compress_tool_result
from src.integrations.novaposhta import Quote
from src.llm.models import (
    LLMResponse,
    StreamDone,
    TextDelta,
    ToolCall,
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
    Usage,
)
from src.llm.router import LLMRouter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

PHRASE = dq.quote_phrase(Quote(cost=810.0, door_cost=None, cod_fee=0.0), courier=False)
_RESULT = {"status": dq.STATUS_QUOTED, "phrase": PHRASE, "cost": 810}
_ARGS = {"city": "Одеса", "items": [{"product_id": "t3", "quantity": 4}]}
_TOOLS = [{"name": dq.DELIVERY_COST_TOOL, "description": "", "input_schema": {"type": "object"}}]


def _router() -> ToolRouter:
    router = ToolRouter()

    async def _quote(**_: Any) -> dict[str, Any]:
        return dict(_RESULT)

    router.register(dq.DELIVERY_COST_TOOL, _quote)
    return router


class TestCompressor:
    def test_the_model_is_told_the_phrase_was_said(self) -> None:
        content = json.loads(
            compress_tool_result(dq.DELIVERY_COST_TOOL, dict(_RESULT), sales_enabled=True)
        )
        assert content["already_said_to_caller"] == PHRASE
        assert "phrase" not in content
        assert content["status"] == dq.STATUS_QUOTED
        assert "Не повторюй" in content["note"]

    def test_code_spoken_phrase_is_only_the_quote_tool(self) -> None:
        assert dq.code_spoken_phrase(dq.DELIVERY_COST_TOOL, dict(_RESULT)) == PHRASE
        assert dq.code_spoken_phrase("search_tires", dict(_RESULT)) is None
        assert dq.code_spoken_phrase(dq.DELIVERY_COST_TOOL, {"error": "x"}) is None


class TestTextPath:
    """LLMAgent (sandbox, goldset): the phrase opens the reply."""

    def test_reply_starts_with_the_phrase_the_model_did_not_retell(self) -> None:
        llm_router = create_autospec(LLMRouter, instance=True)
        llm_router.complete = AsyncMock(
            side_effect=[
                LLMResponse(
                    text="",
                    tool_calls=[ToolCall(id="q1", name=dq.DELIVERY_COST_TOOL, arguments=_ARGS)],
                    stop_reason="tool_use",
                    usage=Usage(10, 5),
                    provider="test",
                ),
                LLMResponse(text="Оформлюємо заявку?", usage=Usage(10, 5), provider="test"),
            ]
        )
        agent = LLMAgent(
            api_key="test-key",
            system_prompt="base",
            llm_router=llm_router,
            tool_router=_router(),
            tools=_TOOLS,
        )
        text, _ = asyncio.run(agent.process_message("скільки доставка до Одеси?", []))
        assert text.startswith(PHRASE)
        assert text.endswith("Оформлюємо заявку?")


class _RecordingTTS:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def initialize(self) -> None:
        return None

    async def synthesize(self, text: str) -> bytes:
        self.texts.append(text)
        return b"\x00" * 640

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        self.texts.append(text)
        yield b"\x00" * 640


class TestStreamingPath:
    """StreamingAgentLoop (the call): the code says the phrase after the tool round."""

    @pytest.mark.asyncio
    async def test_the_code_speaks_the_phrase(self) -> None:
        from src.agent.streaming_loop import StreamingAgentLoop
        from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection
        from tests.unit.mocks.mock_llm_router import MockLLMRouter

        rounds = [
            [
                ToolCallStart(id="q1", name=dq.DELIVERY_COST_TOOL),
                ToolCallDelta(id="q1", arguments_chunk=json.dumps(_ARGS, ensure_ascii=False)),
                ToolCallEnd(id="q1"),
                StreamDone(stop_reason="tool_use", usage=Usage(1, 1)),
            ],
            [
                TextDelta(text="Оформлюємо заявку?"),
                StreamDone(stop_reason="end_turn", usage=Usage(1, 1)),
            ],
        ]
        tts = _RecordingTTS()
        loop = StreamingAgentLoop(
            llm_router=MockLLMRouter(rounds),
            tool_router=_router(),
            tts=tts,
            conn=MockAudioSocketConnection(),
            barge_in_event=asyncio.Event(),
            tools=_TOOLS,
            system_prompt="Test system prompt",
        )
        await loop.run_turn("скільки доставка до Одеси?", [])
        assert PHRASE in tts.texts
        assert tts.texts.index(PHRASE) < tts.texts.index("Оформлюємо заявку?")
