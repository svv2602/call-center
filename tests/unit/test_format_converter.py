"""Tests for src/llm/format_converter — Anthropic ↔ OpenAI format conversion."""

from __future__ import annotations

import json

import pytest

from src.llm.format_converter import (
    anthropic_messages_to_openai,
    anthropic_tools_to_openai,
    llm_response_to_anthropic_blocks,
    openai_response_to_llm_response,
)
from src.llm.models import LLMResponse, ToolCall, Usage


class TestAnthropicToolsToOpenAI:
    """Test Anthropic → OpenAI tool conversion."""

    def test_basic_tool(self) -> None:
        tools = [
            {
                "name": "search_tires",
                "description": "Search tires",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "brand": {"type": "string"},
                    },
                    "required": ["brand"],
                },
            }
        ]
        result = anthropic_tools_to_openai(tools)
        assert len(result) == 1
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "search_tires"
        assert result[0]["function"]["description"] == "Search tires"
        assert result[0]["function"]["parameters"]["required"] == ["brand"]

    def test_empty_tools(self) -> None:
        assert anthropic_tools_to_openai([]) == []

    def test_multiple_tools(self) -> None:
        tools = [
            {
                "name": "tool_a",
                "description": "A",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "tool_b",
                "description": "B",
                "input_schema": {"type": "object", "properties": {}},
            },
        ]
        result = anthropic_tools_to_openai(tools)
        assert len(result) == 2
        assert result[0]["function"]["name"] == "tool_a"
        assert result[1]["function"]["name"] == "tool_b"

    def test_tool_without_description(self) -> None:
        tools = [{"name": "no_desc", "input_schema": {"type": "object", "properties": {}}}]
        result = anthropic_tools_to_openai(tools)
        assert result[0]["function"]["description"] == ""

    def test_tool_without_input_schema(self) -> None:
        tools = [{"name": "bare", "description": "Bare tool"}]
        result = anthropic_tools_to_openai(tools)
        assert result[0]["function"]["parameters"] == {"type": "object", "properties": {}}


class TestAnthropicMessagesToOpenAI:
    """Test Anthropic → OpenAI message conversion."""

    def test_simple_text_messages(self) -> None:
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        result = anthropic_messages_to_openai(messages)
        assert len(result) == 2
        assert result[0] == {"role": "user", "content": "Hello"}
        assert result[1] == {"role": "assistant", "content": "Hi there"}

    def test_system_prompt(self) -> None:
        messages = [{"role": "user", "content": "Hello"}]
        result = anthropic_messages_to_openai(messages, system="You are helpful")
        assert result[0] == {"role": "system", "content": "You are helpful"}
        assert result[1] == {"role": "user", "content": "Hello"}

    def test_assistant_with_tool_use(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Searching..."},
                    {
                        "type": "tool_use",
                        "id": "tool_1",
                        "name": "search_tires",
                        "input": {"brand": "Michelin"},
                    },
                ],
            }
        ]
        result = anthropic_messages_to_openai(messages)
        assert len(result) == 1
        msg = result[0]
        assert msg["role"] == "assistant"
        assert msg["content"] == "Searching..."
        assert len(msg["tool_calls"]) == 1
        tc = msg["tool_calls"][0]
        assert tc["id"] == "tool_1"
        assert tc["function"]["name"] == "search_tires"
        assert json.loads(tc["function"]["arguments"]) == {"brand": "Michelin"}

    def test_user_with_tool_results(self) -> None:
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool_1",
                        "content": '{"results": []}',
                    }
                ],
            }
        ]
        result = anthropic_messages_to_openai(messages)
        assert len(result) == 1
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "tool_1"
        assert result[0]["content"] == '{"results": []}'

    def test_assistant_tool_use_no_text(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "check_availability",
                        "input": {"product_id": "abc"},
                    },
                ],
            }
        ]
        result = anthropic_messages_to_openai(messages)
        assert result[0]["content"] is None
        assert len(result[0]["tool_calls"]) == 1

    def test_full_conversation(self) -> None:
        """Test a realistic multi-turn conversation with tool use."""
        messages = [
            {"role": "user", "content": "Find Michelin tires"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me search..."},
                    {"type": "tool_use", "id": "t1", "name": "search", "input": {"q": "Michelin"}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "found 3 items"},
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I found 3 Michelin tires."},
                ],
            },
        ]
        result = anthropic_messages_to_openai(messages, system="You are a tire assistant")
        assert len(result) == 5  # system + user + assistant(tool) + tool + assistant
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"
        assert result[2]["role"] == "assistant"
        assert result[3]["role"] == "tool"
        assert result[4]["role"] == "assistant"


class TestOpenAIResponseToLLMResponse:
    """Test OpenAI response JSON → LLMResponse parsing."""

    def test_simple_text_response(self) -> None:
        data = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "Hello!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        result = openai_response_to_llm_response(data, "openai-gpt4o", "gpt-4o")
        assert result.text == "Hello!"
        assert result.tool_calls == []
        assert result.stop_reason == "end_turn"
        assert result.usage.input_tokens == 10
        assert result.usage.output_tokens == 5
        assert result.provider == "openai-gpt4o"
        assert result.model == "gpt-4o"

    def test_tool_calls_response(self) -> None:
        data = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_123",
                                "type": "function",
                                "function": {
                                    "name": "search_tires",
                                    "arguments": '{"brand": "Michelin"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 50, "completion_tokens": 20},
        }
        result = openai_response_to_llm_response(data, "openai-gpt4o", "gpt-4o")
        assert result.text == ""
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "search_tires"
        assert result.tool_calls[0].arguments == {"brand": "Michelin"}
        assert result.stop_reason == "tool_use"

    def test_max_tokens_stop(self) -> None:
        data = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "Truncated..."},
                    "finish_reason": "length",
                }
            ],
            "usage": {"prompt_tokens": 50, "completion_tokens": 300},
        }
        result = openai_response_to_llm_response(data, "deepseek-chat", "deepseek-chat")
        assert result.stop_reason == "max_tokens"

    def test_malformed_tool_arguments(self) -> None:
        data = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {
                                    "name": "test",
                                    "arguments": "not-json",
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {},
        }
        result = openai_response_to_llm_response(data, "test", "test")
        assert result.tool_calls[0].arguments == {}

    def test_missing_usage(self) -> None:
        data = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
        }
        result = openai_response_to_llm_response(data, "test", "test")
        assert result.usage.input_tokens == 0
        assert result.usage.output_tokens == 0


class TestLLMResponseToAnthropicBlocks:
    """Test LLMResponse → Anthropic content blocks conversion."""

    def test_text_only(self) -> None:
        response = LLMResponse(text="Hello!", provider="test", model="test")
        blocks = llm_response_to_anthropic_blocks(response)
        assert blocks == [{"type": "text", "text": "Hello!"}]

    def test_tool_calls(self) -> None:
        response = LLMResponse(
            text="Searching...",
            tool_calls=[
                ToolCall(id="t1", name="search", arguments={"q": "test"}),
            ],
            provider="test",
            model="test",
        )
        blocks = llm_response_to_anthropic_blocks(response)
        assert len(blocks) == 2
        assert blocks[0] == {"type": "text", "text": "Searching..."}
        assert blocks[1]["type"] == "tool_use"
        assert blocks[1]["id"] == "t1"
        assert blocks[1]["name"] == "search"
        assert blocks[1]["input"] == {"q": "test"}

    def test_empty_response(self) -> None:
        response = LLMResponse(text="", provider="test", model="test")
        blocks = llm_response_to_anthropic_blocks(response)
        assert blocks == []

    def test_multiple_tool_calls(self) -> None:
        response = LLMResponse(
            text="",
            tool_calls=[
                ToolCall(id="t1", name="a", arguments={}),
                ToolCall(id="t2", name="b", arguments={"x": 1}),
            ],
            provider="test",
            model="test",
        )
        blocks = llm_response_to_anthropic_blocks(response)
        assert len(blocks) == 2
        assert blocks[0]["name"] == "a"
        assert blocks[1]["name"] == "b"
