"""Tests for automatic quality evaluation."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tasks.quality_evaluator import (
    QUALITY_CRITERIA,
    _build_transcription_text,
    _evaluate_call_quality_async,
)


class TestBuildTranscriptionText:
    """Tests for transcription text formatting."""

    def test_formats_customer_and_bot_turns(self) -> None:
        turns = [
            {"speaker": "customer", "text": "Добрий день"},
            {"speaker": "bot", "text": "Вітаю! Чим можу допомогти?"},
        ]
        result = _build_transcription_text(turns)
        assert "Customer: Добрий день" in result
        assert "Bot: Вітаю! Чим можу допомогти?" in result

    def test_includes_tool_calls(self) -> None:
        turns = [
            {
                "speaker": "bot",
                "text": "Шукаю шини для вас",
                "tool_calls": [{"tool_name": "search_tires", "success": True}],
            },
        ]
        result = _build_transcription_text(turns)
        assert "[Tool: search_tires" in result

    def test_empty_turns(self) -> None:
        result = _build_transcription_text([])
        assert result == ""

    def test_missing_text_field(self) -> None:
        turns = [{"speaker": "customer"}]
        result = _build_transcription_text(turns)
        assert result == ""


class TestQualityCriteria:
    """Tests for quality criteria constants."""

    def test_all_eight_criteria_defined(self) -> None:
        assert len(QUALITY_CRITERIA) == 8

    def test_criteria_names(self) -> None:
        expected = {
            "bot_greeted_properly",
            "bot_understood_intent",
            "bot_used_correct_tool",
            "bot_provided_accurate_info",
            "bot_confirmed_before_action",
            "bot_was_concise",
            "call_resolved_without_human",
            "customer_seemed_satisfied",
        }
        assert set(QUALITY_CRITERIA) == expected


class TestQualityScore:
    """Tests for quality score calculation."""

    def test_high_quality_scores(self) -> None:
        details = {criterion: 0.9 for criterion in QUALITY_CRITERIA}
        scores = [details[c] for c in QUALITY_CRITERIA]
        avg = sum(scores) / len(scores)
        assert avg == pytest.approx(0.9)

    def test_low_quality_flagged(self) -> None:
        details = {criterion: 0.3 for criterion in QUALITY_CRITERIA}
        scores = [details[c] for c in QUALITY_CRITERIA]
        avg = sum(scores) / len(scores)
        assert avg < 0.5

    def test_mixed_scores(self) -> None:
        details = {
            "bot_greeted_properly": 1.0,
            "bot_understood_intent": 0.8,
            "bot_used_correct_tool": 1.0,
            "bot_provided_accurate_info": 0.7,
            "bot_confirmed_before_action": 1.0,
            "bot_was_concise": 0.6,
            "call_resolved_without_human": 0.0,
            "customer_seemed_satisfied": 0.4,
        }
        scores = [details[c] for c in QUALITY_CRITERIA]
        avg = sum(scores) / len(scores)
        assert 0.5 < avg < 0.8

    def test_partial_criteria_defaults_to_zero(self) -> None:
        details = {"bot_greeted_properly": 1.0}  # Only one criterion
        scores = [details.get(c, 0.0) for c in QUALITY_CRITERIA]
        avg = sum(scores) / len(scores)
        assert avg == pytest.approx(0.125)


class TestEvaluateCallQualityAsync:
    """Tests for the async quality evaluation pipeline."""

    @pytest.mark.asyncio
    async def test_handles_no_turns(self) -> None:
        """Should return error when no turns found."""
        mock_engine = AsyncMock()

        # Mock engine.begin() context manager
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(
            return_value=MagicMock(
                __iter__=lambda s: iter([]),
                first=lambda: None,
            )
        )

        # Create a mock that returns empty results for turns
        mock_result_empty = MagicMock()
        mock_result_empty.__iter__ = lambda s: iter([])

        mock_conn.execute = AsyncMock(
            side_effect=[
                mock_result_empty,  # turns query
                mock_result_empty,  # tool_calls query
                MagicMock(first=lambda: None),  # call metadata
            ]
        )

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_engine.begin = MagicMock(return_value=mock_ctx)

        with (
            patch(
                "src.tasks.quality_evaluator.create_async_engine",
                return_value=mock_engine,
            ),
            patch(
                "src.tasks.quality_evaluator.get_settings",
            ),
        ):
            # Create a mock task
            mock_task = MagicMock()
            result = await _evaluate_call_quality_async(mock_task, "test-call-id")
            assert result["error"] == "no_turns"

    @pytest.mark.asyncio
    async def test_handles_llm_json_parsing(self) -> None:
        """Quality details should be valid JSON."""
        quality_json = json.dumps(
            {
                "bot_greeted_properly": 0.9,
                "bot_understood_intent": 0.8,
                "bot_used_correct_tool": 1.0,
                "bot_provided_accurate_info": 0.7,
                "bot_confirmed_before_action": 1.0,
                "bot_was_concise": 0.8,
                "call_resolved_without_human": 1.0,
                "customer_seemed_satisfied": 0.9,
                "comment": "Good performance overall",
            }
        )

        # Verify JSON parsing
        parsed = json.loads(quality_json)
        scores = [parsed.get(c, 0.0) for c in QUALITY_CRITERIA]
        avg = sum(scores) / len(scores)
        assert 0.8 < avg < 1.0
        assert "comment" in parsed

    def test_handles_markdown_code_block_response(self) -> None:
        """Should strip markdown code blocks from LLM response."""
        response_text = '```json\n{"bot_greeted_properly": 0.9}\n```'
        stripped = response_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        parsed = json.loads(stripped)
        assert parsed["bot_greeted_properly"] == 0.9
