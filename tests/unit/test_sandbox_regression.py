"""Unit tests for sandbox regression module."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.api.sandbox import (
    BatchReplayRequest,
    RateRegressionTurn,
    RegressionVerdict,
    ReplayRequest,
)
from src.sandbox.regression import RegressionResult, TurnDiff, _cosine_similarity


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

    def test_similarity_score_default_none(self) -> None:
        td = TurnDiff(
            turn_number=1,
            customer_message="msg",
            source_response="a",
            new_response="b",
            source_rating=None,
            diff_lines=[],
        )
        assert td.similarity_score is None

    def test_similarity_score_set(self) -> None:
        td = TurnDiff(
            turn_number=1,
            customer_message="msg",
            source_response="a",
            new_response="b",
            source_rating=None,
            diff_lines=[],
            similarity_score=0.95,
        )
        assert td.similarity_score == 0.95


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
        assert result.avg_similarity is None

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

    def test_avg_similarity_set(self) -> None:
        result = RegressionResult(
            turns_compared=2,
            avg_source_rating=None,
            avg_new_rating=None,
            score_diff=None,
            avg_similarity=0.87,
        )
        assert result.avg_similarity == 0.87


class TestCosineSimilarity:
    """Test cosine similarity helper."""

    def test_identical_vectors(self) -> None:
        v = [1.0, 2.0, 3.0]
        assert abs(_cosine_similarity(v, v) - 1.0) < 1e-6

    def test_orthogonal_vectors(self) -> None:
        assert abs(_cosine_similarity([1.0, 0.0], [0.0, 1.0])) < 1e-6

    def test_zero_vector(self) -> None:
        assert _cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0


class TestReplayRequest:
    """Test ReplayRequest Pydantic model."""

    def test_valid_request(self) -> None:
        req = ReplayRequest(new_prompt_version_id="12345678-1234-1234-1234-123456789012")
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


class TestRateRegressionTurn:
    """Test RateRegressionTurn Pydantic model."""

    def test_valid(self) -> None:
        m = RateRegressionTurn(turn_number=1, rating=5)
        assert m.turn_number == 1
        assert m.rating == 5

    def test_rating_below_1_fails(self) -> None:
        with pytest.raises(ValidationError):
            RateRegressionTurn(turn_number=1, rating=0)

    def test_rating_above_5_fails(self) -> None:
        with pytest.raises(ValidationError):
            RateRegressionTurn(turn_number=1, rating=6)

    def test_turn_number_below_1_fails(self) -> None:
        with pytest.raises(ValidationError):
            RateRegressionTurn(turn_number=0, rating=3)


class TestRegressionVerdict:
    """Test RegressionVerdict Pydantic model."""

    def test_approved(self) -> None:
        m = RegressionVerdict(verdict="approved")
        assert m.verdict == "approved"

    def test_rejected(self) -> None:
        m = RegressionVerdict(verdict="rejected")
        assert m.verdict == "rejected"

    def test_invalid_verdict_fails(self) -> None:
        with pytest.raises(ValidationError):
            RegressionVerdict(verdict="maybe")

    def test_empty_verdict_fails(self) -> None:
        with pytest.raises(ValidationError):
            RegressionVerdict(verdict="")


class TestBatchReplayRequest:
    """Test BatchReplayRequest Pydantic model."""

    def test_valid(self) -> None:
        m = BatchReplayRequest(
            conversation_ids=["12345678-1234-1234-1234-123456789012"],
            new_prompt_version_id="12345678-1234-1234-1234-123456789012",
        )
        assert len(m.conversation_ids) == 1

    def test_empty_conversations_fails(self) -> None:
        with pytest.raises(ValidationError):
            BatchReplayRequest(
                conversation_ids=[],
                new_prompt_version_id="12345678-1234-1234-1234-123456789012",
            )

    def test_max_20_conversations(self) -> None:
        ids = [f"{i:08d}-1234-1234-1234-123456789012" for i in range(21)]
        with pytest.raises(ValidationError):
            BatchReplayRequest(
                conversation_ids=ids,
                new_prompt_version_id="12345678-1234-1234-1234-123456789012",
            )
