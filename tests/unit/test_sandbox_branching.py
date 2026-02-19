"""Unit tests for sandbox branching and Phase 2 API models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.api.sandbox import (
    AutoCustomerRequest,
    StarterCreate,
    StarterUpdate,
)


class TestAutoCustomerRequest:
    """Test auto-customer request model."""

    def test_defaults(self) -> None:
        req = AutoCustomerRequest()
        assert req.persona == "neutral"
        assert req.context_hint is None

    def test_with_persona(self) -> None:
        req = AutoCustomerRequest(persona="angry", context_hint="wants refund")
        assert req.persona == "angry"
        assert req.context_hint == "wants refund"


class TestStarterCreate:
    """Test scenario starter creation model."""

    def test_valid_starter(self) -> None:
        req = StarterCreate(
            title="Tire search scenario",
            first_message="Привіт, шукаю шини на Kia Sportage",
        )
        assert req.title == "Tire search scenario"
        assert req.customer_persona == "neutral"
        assert req.sort_order == 0
        assert req.tags == []

    def test_full_starter(self) -> None:
        req = StarterCreate(
            title="Expert consultation",
            first_message="Порівняйте Michelin і Continental",
            scenario_type="expert_consultation",
            tags=["expert", "comparison"],
            customer_persona="expert",
            description="Expert customer comparing brands",
            sort_order=5,
        )
        assert req.scenario_type == "expert_consultation"
        assert len(req.tags) == 2

    def test_empty_title_fails(self) -> None:
        with pytest.raises(ValidationError):
            StarterCreate(title="", first_message="Hello")

    def test_empty_message_fails(self) -> None:
        with pytest.raises(ValidationError):
            StarterCreate(title="Test", first_message="")


class TestStarterUpdate:
    """Test scenario starter update model."""

    def test_all_optional(self) -> None:
        req = StarterUpdate()
        assert req.title is None
        assert req.is_active is None

    def test_partial_update(self) -> None:
        req = StarterUpdate(title="Updated", is_active=False)
        assert req.title == "Updated"
        assert req.is_active is False
