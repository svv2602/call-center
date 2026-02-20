"""Unit tests for training safety rules API."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.api.training_safety import (
    RULE_TYPES,
    SEVERITIES,
    SafetyRuleCreateRequest,
    SafetyRuleUpdateRequest,
)


class TestSafetyEnums:
    """Test enum definitions."""

    def test_rule_types_complete(self) -> None:
        assert "prompt_injection" in RULE_TYPES
        assert "data_validation" in RULE_TYPES
        assert "off_topic" in RULE_TYPES
        assert "language" in RULE_TYPES
        assert "behavioral" in RULE_TYPES
        assert "escalation" in RULE_TYPES
        assert len(RULE_TYPES) == 6

    def test_severities_complete(self) -> None:
        assert SEVERITIES == ["low", "medium", "high", "critical"]


class TestSafetyRuleCreateRequest:
    """Test Pydantic request model."""

    def test_valid_request(self) -> None:
        req = SafetyRuleCreateRequest(
            title="Test rule",
            rule_type="prompt_injection",
            trigger_input="test input",
            expected_behavior="test behavior",
        )
        assert req.severity == "medium"
        assert req.sort_order == 0

    def test_custom_severity(self) -> None:
        req = SafetyRuleCreateRequest(
            title="Test",
            rule_type="behavioral",
            trigger_input="x",
            expected_behavior="y",
            severity="critical",
        )
        assert req.severity == "critical"


class TestSafetyRuleUpdateRequest:
    """Test Pydantic update model."""

    def test_all_optional(self) -> None:
        req = SafetyRuleUpdateRequest()
        assert req.title is None
        assert req.severity is None

    def test_partial_update(self) -> None:
        req = SafetyRuleUpdateRequest(severity="high", is_active=False)
        assert req.severity == "high"
        assert req.is_active is False


class TestSafetyEndpoints:
    """Test API endpoint logic with mocked DB."""

    @pytest.mark.asyncio
    async def test_create_validates_rule_type(self) -> None:
        """Invalid rule_type should raise 400."""
        from fastapi import HTTPException

        from src.api.training_safety import create_safety_rule

        req = SafetyRuleCreateRequest(
            title="Test",
            rule_type="invalid_type",
            trigger_input="x",
            expected_behavior="y",
        )
        with pytest.raises(HTTPException) as exc_info:
            await create_safety_rule(req, {})
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_create_validates_severity(self) -> None:
        """Invalid severity should raise 400."""
        from fastapi import HTTPException

        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="severity"):
            SafetyRuleCreateRequest(
                title="Test",
                rule_type="behavioral",
                trigger_input="x",
                expected_behavior="y",
                severity="invalid",
            )

    @pytest.mark.asyncio
    async def test_update_no_fields_raises_400(self) -> None:
        """Empty update request should raise 400."""
        from uuid import uuid4

        from fastapi import HTTPException

        from src.api.training_safety import update_safety_rule

        with patch("src.api.training_safety._get_engine", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await update_safety_rule(uuid4(), SafetyRuleUpdateRequest(), {})
            assert exc_info.value.status_code == 400
