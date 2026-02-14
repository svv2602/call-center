"""Tests for A/B testing and prompt management."""

from __future__ import annotations

import pytest

from src.agent.ab_testing import calculate_significance
from src.agent.prompts import PROMPT_VERSION, SYSTEM_PROMPT


class TestCalculateSignificance:
    """Tests for statistical significance calculation."""

    def test_insufficient_samples(self) -> None:
        result = calculate_significance(n_a=5, n_b=5, mean_a=0.8, mean_b=0.6)
        assert not result["is_significant"]
        assert result["recommended_variant"] is None
        assert result["min_samples_needed"] == 30

    def test_no_significant_difference(self) -> None:
        result = calculate_significance(n_a=100, n_b=100, mean_a=0.75, mean_b=0.74)
        assert not result["is_significant"]
        assert result["recommended_variant"] is None

    def test_significant_difference_a_wins(self) -> None:
        result = calculate_significance(n_a=200, n_b=200, mean_a=0.85, mean_b=0.65)
        assert result["is_significant"]
        assert result["recommended_variant"] == "A"
        assert result["z_score"] > 0

    def test_significant_difference_b_wins(self) -> None:
        result = calculate_significance(n_a=200, n_b=200, mean_a=0.60, mean_b=0.85)
        assert result["is_significant"]
        assert result["recommended_variant"] == "B"
        assert result["z_score"] < 0

    def test_equal_means(self) -> None:
        result = calculate_significance(n_a=100, n_b=100, mean_a=0.75, mean_b=0.75)
        assert result["z_score"] == 0.0
        assert not result["is_significant"]

    def test_zero_standard_error(self) -> None:
        """Edge case: should handle zero SE gracefully."""
        result = calculate_significance(
            n_a=100, n_b=100, mean_a=0.5, mean_b=0.5, std_dev=0.0
        )
        assert result["z_score"] == 0.0
        assert not result["is_significant"]

    def test_p_value_range(self) -> None:
        result = calculate_significance(n_a=50, n_b=50, mean_a=0.8, mean_b=0.7)
        assert 0.0 <= result["p_value_approx"] <= 1.0


class TestPromptVersionFallback:
    """Tests for prompt version fallback behavior."""

    def test_hardcoded_prompt_version_exists(self) -> None:
        assert PROMPT_VERSION is not None
        assert isinstance(PROMPT_VERSION, str)
        assert len(PROMPT_VERSION) > 0

    def test_hardcoded_system_prompt_exists(self) -> None:
        assert SYSTEM_PROMPT is not None
        assert isinstance(SYSTEM_PROMPT, str)
        assert len(SYSTEM_PROMPT) > 100  # Should be substantial

    def test_system_prompt_contains_key_instructions(self) -> None:
        """System prompt should contain essential instructions."""
        assert "Олена" in SYSTEM_PROMPT or "олена" in SYSTEM_PROMPT.lower()
        assert "українськ" in SYSTEM_PROMPT.lower() or "uk" in SYSTEM_PROMPT.lower()
