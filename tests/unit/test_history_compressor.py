"""Unit tests for conversation history compressor."""

from __future__ import annotations

from src.agent.history_compressor import compress_history, summarize_old_messages


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


class TestSummarizeOldMessages:
    """Tests for summarize_old_messages()."""

    def test_short_history_delegates_to_compress(self) -> None:
        """Below threshold → delegates to compress_history."""
        messages = [
            {"role": "user", "content": "Привіт"},
            {"role": "assistant", "content": [{"type": "text", "text": "Доброго дня!"}]},
        ]
        result = summarize_old_messages(messages, summary_threshold=16, keep_recent=10)
        assert result == messages

    def test_summary_replaces_old_messages(self) -> None:
        """Above threshold → old messages replaced by single summary."""
        # Build 18 messages (above threshold 16)
        messages = []
        for i in range(18):
            if i % 2 == 0:
                messages.append({"role": "user", "content": f"Повідомлення {i}"})
            else:
                messages.append(
                    {"role": "assistant", "content": [{"type": "text", "text": f"Відповідь {i}"}]}
                )
        result = summarize_old_messages(messages, summary_threshold=16, keep_recent=10)
        # First message should be summary
        assert result[0]["role"] == "user"
        assert "Резюме" in result[0]["content"]
        # Recent 10 messages preserved
        assert len(result) == 11  # 1 summary + 10 recent

    def test_summary_includes_tool_names(self) -> None:
        """Summary includes Ukrainian labels for called tools."""
        messages = [
            {"role": "user", "content": "Шукаю шини"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "search_tires", "input": {}},
                ],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
            },
            {"role": "assistant", "content": [{"type": "text", "text": "Знайшла варіанти"}]},
            {"role": "user", "content": "Перевір наявність"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "t2", "name": "check_availability", "input": {}},
                ],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t2", "content": "ok"}],
            },
        ]
        # Add enough recent messages to exceed threshold
        for i in range(12):
            messages.append({"role": "user", "content": f"msg {i}"})
            messages.append(
                {"role": "assistant", "content": [{"type": "text", "text": f"resp {i}"}]}
            )
        result = summarize_old_messages(messages, summary_threshold=16, keep_recent=10)
        summary = result[0]["content"]
        assert "пошук шин" in summary
        assert "перевірка наявності" in summary

    def test_summary_includes_customer_quotes(self) -> None:
        """Summary includes truncated customer quotes."""
        messages = [
            {"role": "user", "content": "Шукаю зимові 205/55 R16"},
            {"role": "assistant", "content": [{"type": "text", "text": "Шукаю..."}]},
            {"role": "user", "content": "Continental або Michelin"},
            {"role": "assistant", "content": [{"type": "text", "text": "Знайшла"}]},
        ]
        # Pad with enough messages
        for i in range(14):
            messages.append({"role": "user", "content": f"padding {i}"})
            messages.append(
                {"role": "assistant", "content": [{"type": "text", "text": f"resp {i}"}]}
            )
        result = summarize_old_messages(messages, summary_threshold=16, keep_recent=10)
        summary = result[0]["content"]
        assert "Клієнт казав" in summary
        assert "Шукаю зимові" in summary

    def test_summary_skips_call_start_marker(self) -> None:
        """(початок дзвінка) is excluded from customer quotes."""
        messages = [
            {"role": "user", "content": "(початок дзвінка)"},
            {"role": "assistant", "content": [{"type": "text", "text": "Привіт"}]},
            {"role": "user", "content": "Хочу шини"},
            {"role": "assistant", "content": [{"type": "text", "text": "Добре"}]},
        ]
        for i in range(14):
            messages.append({"role": "user", "content": f"padding {i}"})
            messages.append(
                {"role": "assistant", "content": [{"type": "text", "text": f"resp {i}"}]}
            )
        result = summarize_old_messages(messages, summary_threshold=16, keep_recent=10)
        summary = result[0]["content"]
        assert "початок дзвінка" not in summary

    def test_does_not_mutate_original(self) -> None:
        """summarize_old_messages should not mutate the input list."""
        messages = [{"role": "user", "content": f"msg {i}"} for i in range(20)]
        original_len = len(messages)
        summarize_old_messages(messages, summary_threshold=16, keep_recent=10)
        assert len(messages) == original_len
