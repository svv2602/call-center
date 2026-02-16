"""Adversarial tests for order safety and security."""

from __future__ import annotations

import pytest

from src.agent.agent import LLMAgent
from src.agent.prompts import PROMPT_VERSION, SYSTEM_PROMPT
from src.agent.tools import ORDER_TOOLS


class TestOrderSafetyPrompt:
    """Test that safety rules are enforced in the prompt."""

    def test_prompt_requires_confirmation_before_confirm(self) -> None:
        """Prompt must require customer confirmation before confirm_order."""
        assert "підтверджуєте" in SYSTEM_PROMPT.lower() or "підтвердження" in SYSTEM_PROMPT.lower()

    def test_prompt_forbids_confirm_without_consent(self) -> None:
        """Prompt must explicitly forbid calling confirm_order without consent."""
        assert "НІКОЛИ не викликай confirm_order" in SYSTEM_PROMPT

    def test_prompt_handles_cancellation(self) -> None:
        """Prompt must handle order cancellation."""
        assert "скасуй" in SYSTEM_PROMPT.lower() or "відміни" in SYSTEM_PROMPT.lower()

    def test_prompt_enforces_caller_id_check(self) -> None:
        """Prompt must prevent leaking other customers' orders."""
        assert "CallerID" in SYSTEM_PROMPT

    def test_prompt_has_quantity_limit(self) -> None:
        """Prompt must limit large orders (redirect to operator)."""
        assert "20" in SYSTEM_PROMPT and "оператор" in SYSTEM_PROMPT.lower()


class TestConfirmOrderToolSafety:
    """Test confirm_order tool description enforces safety."""

    @pytest.fixture
    def confirm_tool(self) -> dict:
        return next(t for t in ORDER_TOOLS if t["name"] == "confirm_order")

    def test_description_mentions_mandatory_confirmation(self, confirm_tool: dict) -> None:
        desc = confirm_tool["description"]
        assert "ОБОВ'ЯЗКОВО" in desc

    def test_description_mentions_sum_announcement(self, confirm_tool: dict) -> None:
        desc = confirm_tool["description"]
        assert "суму" in desc.lower() or "сум" in desc


class TestBuildSystemPromptSecurity:
    """Test _build_system_prompt with caller context."""

    def test_caller_phone_injected(self) -> None:
        prompt = LLMAgent._build_system_prompt(caller_phone="+380501234567")
        assert "+380501234567" in prompt
        assert "CallerID" in prompt

    def test_order_id_injected(self) -> None:
        prompt = LLMAgent._build_system_prompt(order_id="order-abc")
        assert "order-abc" in prompt

    def test_no_context_returns_base_prompt(self) -> None:
        prompt = LLMAgent._build_system_prompt()
        assert prompt == SYSTEM_PROMPT

    def test_prompt_version_updated(self) -> None:
        assert PROMPT_VERSION == "v3.0-services"


class TestToolSchemaValidation:
    """Test that tool schemas enforce constraints."""

    def test_create_order_draft_requires_items(self) -> None:
        tool = next(t for t in ORDER_TOOLS if t["name"] == "create_order_draft")
        assert "items" in tool["input_schema"]["required"]

    def test_create_order_draft_requires_phone(self) -> None:
        tool = next(t for t in ORDER_TOOLS if t["name"] == "create_order_draft")
        assert "customer_phone" in tool["input_schema"]["required"]

    def test_item_quantity_is_integer(self) -> None:
        """quantity must be integer to prevent injection of strings."""
        tool = next(t for t in ORDER_TOOLS if t["name"] == "create_order_draft")
        item_schema = tool["input_schema"]["properties"]["items"]["items"]
        assert item_schema["properties"]["quantity"]["type"] == "integer"

    def test_payment_method_is_enum(self) -> None:
        """payment_method must be enum to prevent arbitrary values."""
        tool = next(t for t in ORDER_TOOLS if t["name"] == "confirm_order")
        pm = tool["input_schema"]["properties"]["payment_method"]
        assert "enum" in pm
        assert len(pm["enum"]) == 3

    def test_delivery_type_is_enum(self) -> None:
        """delivery_type must be enum to prevent arbitrary values."""
        tool = next(t for t in ORDER_TOOLS if t["name"] == "update_order_delivery")
        dt = tool["input_schema"]["properties"]["delivery_type"]
        assert "enum" in dt
        assert set(dt["enum"]) == {"delivery", "pickup"}
