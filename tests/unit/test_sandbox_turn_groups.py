"""Unit tests for sandbox turn groups (Phase 4a)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.api.sandbox import (
    ExportPatternRequest,
    PatternUpdate,
    TurnGroupCreate,
    TurnGroupUpdate,
)


class TestTurnGroupModels:
    """Test turn group Pydantic model validation."""

    def test_turn_group_create_valid(self) -> None:
        req = TurnGroupCreate(
            turn_ids=["12345678-1234-1234-1234-123456789012"],
            intent_label="tire_search_clarification",
        )
        assert req.intent_label == "tire_search_clarification"
        assert req.pattern_type == "positive"
        assert req.tags == []

    def test_turn_group_create_full(self) -> None:
        req = TurnGroupCreate(
            turn_ids=[
                "12345678-1234-1234-1234-123456789012",
                "12345678-1234-1234-1234-123456789013",
            ],
            intent_label="order_confirmation",
            pattern_type="negative",
            rating=3,
            rating_comment="Agent was too verbose",
            correction="Should confirm order concisely",
            tags=["order", "verbose"],
        )
        assert req.pattern_type == "negative"
        assert req.rating == 3
        assert len(req.turn_ids) == 2
        assert len(req.tags) == 2

    def test_turn_group_create_empty_turn_ids_fails(self) -> None:
        with pytest.raises(ValidationError):
            TurnGroupCreate(turn_ids=[], intent_label="test")

    def test_turn_group_create_empty_intent_fails(self) -> None:
        with pytest.raises(ValidationError):
            TurnGroupCreate(
                turn_ids=["12345678-1234-1234-1234-123456789012"],
                intent_label="",
            )

    def test_turn_group_create_rating_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            TurnGroupCreate(
                turn_ids=["12345678-1234-1234-1234-123456789012"],
                intent_label="test",
                rating=6,
            )

    def test_turn_group_update_all_optional(self) -> None:
        req = TurnGroupUpdate()
        assert req.intent_label is None
        assert req.pattern_type is None
        assert req.tags is None

    def test_turn_group_update_with_fields(self) -> None:
        req = TurnGroupUpdate(
            intent_label="updated_intent",
            pattern_type="negative",
            rating=4,
            correction="Better approach",
        )
        assert req.intent_label == "updated_intent"
        assert req.pattern_type == "negative"
        assert req.rating == 4


class TestExportPatternModel:
    """Test export pattern request validation."""

    def test_export_valid(self) -> None:
        req = ExportPatternRequest(guidance_note="Always confirm tire size before searching")
        assert req.guidance_note == "Always confirm tire size before searching"

    def test_export_empty_guidance_fails(self) -> None:
        with pytest.raises(ValidationError):
            ExportPatternRequest(guidance_note="")


class TestPatternUpdateModel:
    """Test pattern update model validation."""

    def test_pattern_update_all_optional(self) -> None:
        req = PatternUpdate()
        assert req.guidance_note is None
        assert req.is_active is None

    def test_pattern_update_with_fields(self) -> None:
        req = PatternUpdate(
            guidance_note="Updated guidance",
            is_active=False,
            tags=["updated"],
        )
        assert req.guidance_note == "Updated guidance"
        assert req.is_active is False
        assert req.tags == ["updated"]
