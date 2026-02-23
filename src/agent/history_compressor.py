"""Compress old messages in conversation history to save tokens.

Keeps the last ``keep_recent`` messages verbatim.  For older messages,
replaces verbose tool_result content with a short ``[ок]`` stub — this
removes 50-200 tokens per stale tool result while preserving the
conversational structure the LLM expects.
"""

from __future__ import annotations

from typing import Any


def compress_history(
    messages: list[dict[str, Any]],
    keep_recent: int = 10,
) -> list[dict[str, Any]]:
    """Compress old tool_result messages in conversation history.

    Args:
        messages: Full conversation history (not mutated).
        keep_recent: Number of trailing messages to keep verbatim.

    Returns:
        New list with old tool_result contents replaced by ``[ок]``.
    """
    if len(messages) <= keep_recent:
        return messages

    cutoff = len(messages) - keep_recent
    compressed: list[dict[str, Any]] = []

    for idx, msg in enumerate(messages):
        if idx >= cutoff:
            # Recent — keep as-is
            compressed.append(msg)
            continue

        # Only compress user messages that carry tool_result blocks
        if msg.get("role") == "user" and isinstance(msg.get("content"), list):
            new_content = _compress_tool_results(msg["content"])
            compressed.append({**msg, "content": new_content})
        else:
            compressed.append(msg)

    return compressed


def _compress_tool_results(content_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace tool_result content strings with short stubs."""
    out: list[dict[str, Any]] = []
    for block in content_blocks:
        if block.get("type") == "tool_result":
            out.append({**block, "content": "[ок]"})
        else:
            out.append(block)
    return out
