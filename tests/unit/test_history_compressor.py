"""Unit tests for conversation history compressor."""

from __future__ import annotations

from src.agent.history_compressor import compress_history


class TestCompressHistory:
    """Tests for compress_history()."""

    def test_short_history_unchanged(self) -> None:
        """History shorter than keep_recent is returned as-is."""
        messages = [
            {"role": "user", "content": "Привіт"},
            {"role": "assistant", "content": [{"type": "text", "text": "Доброго дня!"}]},
        ]
        result = compress_history(messages, keep_recent=10)
        assert result == messages

    def test_old_tool_results_compressed(self) -> None:
        """Tool results in old messages get replaced with [ок]."""
        messages = [
            {"role": "user", "content": "Привіт"},
            {"role": "assistant", "content": [{"type": "text", "text": "Шукаю..."}]},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": "{'items': [{'brand': 'Michelin', 'model': 'Primacy 4', 'price': 3200}]}",
                    }
                ],
            },
            {"role": "assistant", "content": [{"type": "text", "text": "Знайшов шини"}]},
            {"role": "user", "content": "Дякую"},
        ]
        result = compress_history(messages, keep_recent=2)
        # Last 2 messages are kept verbatim
        assert result[-1]["content"] == "Дякую"
        assert result[-2]["content"][0]["text"] == "Знайшов шини"
        # Old tool_result is compressed
        old_tool_msg = result[2]
        assert old_tool_msg["content"][0]["content"] == "[ок]"
        assert old_tool_msg["content"][0]["tool_use_id"] == "t1"

    def test_recent_messages_preserved_verbatim(self) -> None:
        """Messages within keep_recent window are not compressed."""
        tool_content = "{'available': True, 'price': 3200, 'warehouses': [...]}"
        messages = [
            {"role": "user", "content": "Перевір наявність"},
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t2", "content": tool_content}],
            },
        ]
        result = compress_history(messages, keep_recent=5)
        assert result[1]["content"][0]["content"] == tool_content

    def test_non_tool_messages_preserved(self) -> None:
        """User text messages and assistant messages are never compressed."""
        messages = [
            {"role": "user", "content": "Шукаю шини 205/55 R16"},
            {"role": "assistant", "content": [{"type": "text", "text": "Зараз пошукаю"}]},
            {"role": "user", "content": "Скільки коштує?"},
            {"role": "assistant", "content": [{"type": "text", "text": "3200 грн"}]},
            {"role": "user", "content": "Дякую"},
        ]
        result = compress_history(messages, keep_recent=2)
        # Old text messages preserved
        assert result[0]["content"] == "Шукаю шини 205/55 R16"
        assert result[1]["content"][0]["text"] == "Зараз пошукаю"

    def test_empty_history(self) -> None:
        result = compress_history([], keep_recent=10)
        assert result == []

    def test_multiple_tool_results_in_one_message(self) -> None:
        """When a message has multiple tool_result blocks, all get compressed."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "big result A"},
                    {"type": "tool_result", "tool_use_id": "t2", "content": "big result B"},
                ],
            },
            {"role": "user", "content": "Що маєте?"},
        ]
        result = compress_history(messages, keep_recent=1)
        assert result[0]["content"][0]["content"] == "[ок]"
        assert result[0]["content"][1]["content"] == "[ок]"

    def test_does_not_mutate_original(self) -> None:
        """compress_history should not mutate the original list."""
        original_content = "{'items': [{'brand': 'Continental'}]}"
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": original_content}
                ],
            },
            {"role": "user", "content": "ok"},
            {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
        ]
        compress_history(messages, keep_recent=1)
        # Original message not mutated
        assert messages[0]["content"][0]["content"] == original_content
