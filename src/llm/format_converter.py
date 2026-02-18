"""Bidirectional format conversion between Anthropic and OpenAI APIs.

Tools and conversation history are stored in Anthropic format (canonical).
This module converts to/from OpenAI format when using OpenAI-compatible providers.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from src.llm.models import LLMResponse, ToolCall, Usage


def anthropic_tools_to_openai(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Anthropic tool definitions to OpenAI function-calling format.

    Anthropic: {"name": ..., "description": ..., "input_schema": {...}}
    OpenAI:    {"type": "function", "function": {"name": ..., "description": ..., "parameters": {...}}}
    """
    result = []
    for tool in tools:
        result.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return result


def anthropic_messages_to_openai(
    messages: list[dict[str, Any]],
    system: str | None = None,
) -> list[dict[str, Any]]:
    """Convert Anthropic message format to OpenAI chat messages format.

    Handles:
    - system prompt → {"role": "system", "content": ...}
    - user text → {"role": "user", "content": ...}
    - assistant text + tool_use blocks → assistant with tool_calls
    - user tool_result blocks → {"role": "tool", ...} messages
    """
    result: list[dict[str, Any]] = []

    if system:
        result.append({"role": "system", "content": system})

    for msg in messages:
        role = msg["role"]
        content = msg["content"]

        if isinstance(content, str):
            result.append({"role": role, "content": content})
            continue

        # Content is a list of blocks (Anthropic format)
        if role == "assistant":
            result.extend(_convert_assistant_blocks(content))
        elif role == "user":
            result.extend(_convert_user_blocks(content))

    return result


def _convert_assistant_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert assistant content blocks to OpenAI format."""
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []

    for block in blocks:
        if block.get("type") == "text":
            text_parts.append(block["text"])
        elif block.get("type") == "tool_use":
            tool_calls.append({
                "id": block["id"],
                "type": "function",
                "function": {
                    "name": block["name"],
                    "arguments": json.dumps(block["input"]),
                },
            })

    msg: dict[str, Any] = {"role": "assistant"}
    msg["content"] = "\n".join(text_parts) if text_parts else None
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return [msg]


def _convert_user_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert user content blocks to OpenAI format.

    tool_result blocks become separate {"role": "tool"} messages.
    Regular text blocks become a single user message.
    """
    tool_messages: list[dict[str, Any]] = []
    text_parts: list[str] = []

    for block in blocks:
        if block.get("type") == "tool_result":
            tool_messages.append({
                "role": "tool",
                "tool_call_id": block["tool_use_id"],
                "content": block.get("content", ""),
            })
        elif isinstance(block, str):
            text_parts.append(block)
        elif block.get("type") == "text":
            text_parts.append(block.get("text", ""))

    result: list[dict[str, Any]] = []
    if text_parts:
        result.append({"role": "user", "content": "\n".join(text_parts)})
    result.extend(tool_messages)
    return result


def openai_response_to_llm_response(
    data: dict[str, Any],
    provider: str,
    model: str,
) -> LLMResponse:
    """Parse OpenAI chat completion JSON response into LLMResponse."""
    choice = data["choices"][0]
    message = choice["message"]

    text = message.get("content") or ""

    tool_calls: list[ToolCall] = []
    if message.get("tool_calls"):
        for tc in message["tool_calls"]:
            func = tc["function"]
            try:
                arguments = json.loads(func["arguments"])
            except (json.JSONDecodeError, TypeError):
                arguments = {}
            tool_calls.append(ToolCall(
                id=tc.get("id", str(uuid.uuid4())),
                name=func["name"],
                arguments=arguments,
            ))

    # Map finish_reason to Anthropic-style stop_reason
    finish_reason = choice.get("finish_reason", "stop")
    if finish_reason == "tool_calls":
        stop_reason = "tool_use"
    elif finish_reason == "length":
        stop_reason = "max_tokens"
    else:
        stop_reason = "end_turn"

    usage_data = data.get("usage", {})
    usage = Usage(
        input_tokens=usage_data.get("prompt_tokens", 0),
        output_tokens=usage_data.get("completion_tokens", 0),
    )

    return LLMResponse(
        text=text,
        tool_calls=tool_calls,
        stop_reason=stop_reason,
        usage=usage,
        provider=provider,
        model=model,
    )


def llm_response_to_anthropic_blocks(response: LLMResponse) -> list[dict[str, Any]]:
    """Convert LLMResponse back to Anthropic content blocks for conversation history."""
    blocks: list[dict[str, Any]] = []

    if response.text:
        blocks.append({"type": "text", "text": response.text})

    for tc in response.tool_calls:
        blocks.append({
            "type": "tool_use",
            "id": tc.id,
            "name": tc.name,
            "input": tc.arguments,
        })

    return blocks
