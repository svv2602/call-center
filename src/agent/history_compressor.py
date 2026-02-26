"""Compress old messages in conversation history to save tokens.

Keeps the last ``keep_recent`` messages verbatim.  For older messages,
replaces verbose tool_result content with a short ``[ок]`` stub — this
removes 50-200 tokens per stale tool result while preserving the
conversational structure the LLM expects.

When history exceeds ``summary_threshold`` messages, the oldest messages
are replaced with a deterministic template summary (no LLM call).
"""

from __future__ import annotations

from typing import Any

# Maximum length for a single customer quote in the summary
_MAX_QUOTE_LEN = 80

# Ukrainian labels for tool actions (used in summary generation)
_TOOL_LABELS: dict[str, str] = {
    "search_tires": "пошук шин",
    "check_availability": "перевірка наявності",
    "get_vehicle_tire_sizes": "підбір розміру шин",
    "create_order_draft": "створення замовлення",
    "update_order_delivery": "вибір доставки",
    "confirm_order": "підтвердження замовлення",
    "get_order_status": "перевірка статусу замовлення",
    "get_fitting_stations": "пошук шиномонтажу",
    "get_fitting_slots": "перевірка вільних слотів",
    "book_fitting": "запис на шиномонтаж",
    "cancel_fitting": "скасування запису",
    "get_fitting_price": "вартість монтажу",
    "get_customer_bookings": "перевірка записів",
    "get_pickup_points": "пошук пунктів видачі",
    "find_storage": "перевірка зберігання",
    "search_knowledge_base": "пошук інформації",
    "transfer_to_operator": "переведення на оператора",
}


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


def summarize_old_messages(
    messages: list[dict[str, Any]],
    *,
    summary_threshold: int = 16,
    keep_recent: int = 10,
) -> list[dict[str, Any]]:
    """Summarize old messages when history is long, compress otherwise.

    When ``len(messages) > summary_threshold``, the first (N - keep_recent)
    messages are replaced with a single deterministic summary message.
    The summary is generated from a template (no LLM call) and includes:
    - Tool actions performed (extracted from assistant tool_use blocks)
    - First 3 + last customer utterances (truncated)

    When ``len(messages) <= summary_threshold``, delegates to
    ``compress_history()`` for lightweight tool_result compression.

    Args:
        messages: Full conversation history (not mutated).
        summary_threshold: Message count above which summarization kicks in.
        keep_recent: Number of trailing messages to keep verbatim.

    Returns:
        New message list with old messages summarized or compressed.
    """
    if len(messages) <= summary_threshold:
        return compress_history(messages, keep_recent=keep_recent)

    cutoff = len(messages) - keep_recent
    old_messages = messages[:cutoff]
    recent_messages = messages[cutoff:]

    # Extract tool names from assistant messages in old portion
    tool_names: list[str] = []
    seen_tools: set[str] = set()
    for msg in old_messages:
        if msg.get("role") == "assistant" and isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if block.get("type") == "tool_use":
                    name = block.get("name", "")
                    if name and name not in seen_tools:
                        tool_names.append(name)
                        seen_tools.add(name)

    # Extract customer utterances (plain text user messages, not tool results)
    customer_texts: list[str] = []
    for msg in old_messages:
        if msg.get("role") == "user" and isinstance(msg.get("content"), str):
            text = msg["content"].strip()
            if text and text != "(початок дзвінка)":
                customer_texts.append(text)

    # Build summary text
    parts: list[str] = []
    parts.append("(Резюме попередніх ходів розмови:")

    # Tool actions
    if tool_names:
        labels = [_TOOL_LABELS.get(n, n) for n in tool_names]
        parts.append(f" Виконані дії: {', '.join(labels)}.")

    # Customer quotes: first 3 + last (if different)
    if customer_texts:
        quotes: list[str] = []
        for t in customer_texts[:3]:
            quotes.append(_truncate(t))
        if len(customer_texts) > 3:
            last = _truncate(customer_texts[-1])
            if last not in quotes:
                quotes.append(last)
        parts.append(' Клієнт казав: ' + ' | '.join(f'"{q}"' for q in quotes) + ".")

    parts.append(")")
    summary_text = "".join(parts)

    # Build result: summary as first user message + recent messages verbatim
    summary_msg: dict[str, Any] = {"role": "user", "content": summary_text}
    return [summary_msg] + list(recent_messages)


def _truncate(text: str) -> str:
    """Truncate text to _MAX_QUOTE_LEN characters."""
    if len(text) <= _MAX_QUOTE_LEN:
        return text
    return text[:_MAX_QUOTE_LEN - 3] + "..."


def _compress_tool_results(content_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace tool_result content strings with short stubs."""
    out: list[dict[str, Any]] = []
    for block in content_blocks:
        if block.get("type") == "tool_result":
            out.append({**block, "content": "[ок]"})
        else:
            out.append(block)
    return out
