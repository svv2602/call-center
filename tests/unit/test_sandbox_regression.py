"""Unit tests for sandbox regression module."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.api.sandbox import ReplayRequest
from src.sandbox.regression import RegressionResult, TurnDiff


class TestTurnDiff:
    """Test TurnDiff dataclass."""

    def test_creation(self) -> None:
        td = TurnDiff(
            turn_number=1,
            customer_message="Привіт",
            source_response="Доброго дня!",
            new_response="Вітаю!",
            source_rating=4,
            diff_lines=["- Доброго дня!", "+ Вітаю!"],
        )
        assert td.turn_number == 1
        assert td.source_rating == 4
        assert len(td.diff_lines) == 2


class TestRegressionResult:
    """Test RegressionResult dataclass."""

    def test_creation_with_defaults(self) -> None:
        result = RegressionResult(
            turns_compared=5,
            avg_source_rating=4.2,
            avg_new_rating=None,
            score_diff=None,
        )
        assert result.turns_compared == 5
        assert result.turn_diffs == []
        assert result.error is None

    def test_creation_with_error(self) -> None:
        result = RegressionResult(
            turns_compared=0,
            avg_source_rating=None,
            avg_new_rating=None,
            score_diff=None,
            error="Source conversation not found",
        )
        assert result.error is not None

    def test_creation_with_diffs(self) -> None:
        td = TurnDiff(
            turn_number=1,
            customer_message="Hello",
            source_response="Hi",
            new_response="Hey",
            source_rating=None,
            diff_lines=[],
        )
        result = RegressionResult(
            turns_compared=1,
            avg_source_rating=None,
            avg_new_rating=None,
            score_diff=None,
            turn_diffs=[td],
            new_conversation_id="abc-123",
        )
        assert len(result.turn_diffs) == 1
        assert result.new_conversation_id == "abc-123"


class TestReplayRequest:
    """Test ReplayRequest Pydantic model."""

    def test_valid_request(self) -> None:
        req = ReplayRequest(
            new_prompt_version_id="12345678-1234-1234-1234-123456789012"
        )
        assert req.branch_turn_ids is None

    def test_with_branch_ids(self) -> None:
        req = ReplayRequest(
            new_prompt_version_id="12345678-1234-1234-1234-123456789012",
            branch_turn_ids=[
                "11111111-1111-1111-1111-111111111111",
                "22222222-2222-2222-2222-222222222222",
            ],
        )
        assert len(req.branch_turn_ids) == 2

    def test_missing_prompt_version_fails(self) -> None:
        with pytest.raises(ValidationError):
            ReplayRequest()
