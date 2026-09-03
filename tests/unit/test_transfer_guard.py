"""Tests for the false-transfer backend guard.

Guards `transfer_to_operator(reason=customer_request|cannot_help)` against
LLM hallucinations where the customer said only their name (or nothing
substantial) but the model invented a "customer wants operator" claim.
See prompts.py:108-109 (calls 21f61d17, Wave 4 #7) — prompt-level rules
keep regressing under attention dilution.
"""

from __future__ import annotations

import pytest

from src.agent.streaming_loop import _should_block_false_transfer


def _hist(*user_turns: str) -> list[dict]:
    """Build a synthetic conversation history alternating bot/user."""
    msgs: list[dict] = []
    for i, text in enumerate(user_turns):
        msgs.append({"role": "assistant", "content": f"bot msg {i}"})
        msgs.append({"role": "user", "content": text})
    return msgs


class TestBlocksHallucinatedCustomerRequest:
    """reason=customer_request without operator keyword → blocked."""

    def test_single_name_blocked(self) -> None:
        history = _hist("Олексій")
        result = _should_block_false_transfer(
            {"reason": "customer_request", "summary": "wants operator"}, history
        )
        assert result is not None
        assert "HALLUCINATION_GUARD" in result
        assert "customer_request" in result

    def test_name_then_city_still_blocked(self) -> None:
        history = _hist("Василь", "Дніпро")
        result = _should_block_false_transfer(
            {"reason": "customer_request", "summary": "..."}, history
        )
        assert result is not None

    def test_stt_garbage_blocked(self) -> None:
        history = _hist("каша")
        result = _should_block_false_transfer(
            {"reason": "customer_request", "summary": "..."}, history
        )
        assert result is not None

    def test_empty_history_blocked(self) -> None:
        result = _should_block_false_transfer(
            {"reason": "customer_request", "summary": "..."}, []
        )
        assert result is not None


class TestAllowsGenuineCustomerRequest:
    """reason=customer_request WITH operator keyword → allowed."""

    def test_operator_keyword_allows(self) -> None:
        history = _hist("Олексій", "Дніпро", "з'єднайте з оператором")
        result = _should_block_false_transfer(
            {"reason": "customer_request", "summary": "..."}, history
        )
        assert result is None

    def test_manager_keyword_allows(self) -> None:
        history = _hist("хочу говорити з менеджером")
        result = _should_block_false_transfer(
            {"reason": "customer_request", "summary": "..."}, history
        )
        assert result is None

    def test_live_person_ru_allows(self) -> None:
        history = _hist("дайте живого человека")
        result = _should_block_false_transfer(
            {"reason": "customer_request", "summary": "..."}, history
        )
        assert result is None


class TestBlocksHallucinatedCannotHelp:
    """reason=cannot_help too early → blocked."""

    def test_first_turn_cannot_help_blocked(self) -> None:
        history = _hist("Олексій")
        result = _should_block_false_transfer(
            {"reason": "cannot_help", "summary": "..."}, history
        )
        assert result is not None
        assert "cannot_help" in result

    def test_two_turns_cannot_help_blocked(self) -> None:
        history = _hist("Олексій", "Дніпро")
        result = _should_block_false_transfer(
            {"reason": "cannot_help", "summary": "..."}, history
        )
        assert result is not None


class TestAllowsCannotHelpWithContext:
    def test_five_turns_allows(self) -> None:
        history = _hist("Олексій", "Дніпро", "AA1234BB", "літня", "215 55 R17")
        result = _should_block_false_transfer(
            {"reason": "cannot_help", "summary": "..."}, history
        )
        assert result is None

    def test_three_turns_with_escalation_keyword_allows(self) -> None:
        history = _hist("Дніпро", "не працює нічого", "погано")
        result = _should_block_false_transfer(
            {"reason": "cannot_help", "summary": "..."}, history
        )
        assert result is None


class TestPassthroughForOtherReasons:
    """Reasons other than customer_request/cannot_help are never blocked here."""

    @pytest.mark.parametrize(
        "reason",
        [
            "complex_question",
            "negative_emotion",
            "non_fitting_scope",
            "fitting_service_unavailable",
            "no_storage_info",
        ],
    )
    def test_other_reasons_pass(self, reason: str) -> None:
        history = _hist("Олексій")
        result = _should_block_false_transfer(
            {"reason": reason, "summary": "..."}, history
        )
        assert result is None


class TestHistoryFiltering:
    """Guard must ignore tool_result content (list) and count only free-text user turns."""

    def test_tool_results_do_not_count_as_user_turns(self) -> None:
        # A user message can be a list (tool_result blocks) — must NOT be counted.
        history = [
            {"role": "assistant", "content": "bot 0"},
            {"role": "user", "content": "Олексій"},
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "t1", "name": "x", "input": {}}],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}
                ],
            },
        ]
        # Only 1 real user text turn → cannot_help must still be blocked.
        result = _should_block_false_transfer(
            {"reason": "cannot_help", "summary": "..."}, history
        )
        assert result is not None
