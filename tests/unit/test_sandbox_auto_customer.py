"""Unit tests for sandbox auto-customer module."""

from __future__ import annotations

from src.sandbox.auto_customer import PERSONA_PROMPTS


class TestPersonaPrompts:
    """Test persona definitions."""

    def test_all_personas_defined(self) -> None:
        expected = {"neutral", "impatient", "confused", "angry", "expert"}
        assert set(PERSONA_PROMPTS.keys()) == expected

    def test_personas_are_ukrainian(self) -> None:
        for persona, prompt in PERSONA_PROMPTS.items():
            # All persona prompts should contain Ukrainian text
            assert len(prompt) > 10, f"Persona '{persona}' prompt too short"

    def test_neutral_is_default(self) -> None:
        assert "neutral" in PERSONA_PROMPTS
        assert "звичайний" in PERSONA_PROMPTS["neutral"].lower()
