"""Tests for the false-transfer backend guard.

Guards `transfer_to_operator` against LLM hallucinations where the customer
said only their name (or nothing substantial) but the model invented a
"customer wants operator" claim. See prompts.py:108-109 (calls 21f61d17,
Wave 4 #7) — prompt-level rules keep regressing under attention dilution.

Wave 16 made the guard default-deny across all reasons; the loop-breaker
below is what keeps that from trapping a caller who genuinely needs a human.
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
    def test_recent_escalation_allows(self) -> None:
        history = _hist(
            "Олексій", "Дніпро", "AA1234BB", "літня", "215 55 R17",
            "не працює нічого", "погано",
        )
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

    def test_stale_escalation_blocks(self) -> None:
        # Wave 5 regression test: escalation keyword in turn 1-2 but the
        # last 3 turns are normal fitting data → still block. Call
        # dd3dd368 turn 62: 16 turns in, cannot_help after «не feat Fiat».
        history = _hist(
            "не працює",       # escalation in turn 1
            "Дніпро",           # then normal fitting flow
            "AA1234BB",
            "літня",
            "215 55 R17",
            "Fiat",
            "не feat Fiat",     # last 3: no escalation
        )
        result = _should_block_false_transfer(
            {"reason": "cannot_help", "summary": "..."}, history
        )
        assert result is not None
        assert "cannot_help" in result


class TestOtherReasonsNeedEvidenceToo:
    """Wave 16: every reason is default-deny, not just the two named ones.

    Call 347317e4 walked customer_request → cannot_help → complex_question
    in 10 seconds and escaped through the third, abandoning a booking where
    date, time, colour, make and name had all been collected.
    """

    @pytest.mark.parametrize(
        "reason",
        [
            "complex_question",
            "negative_emotion",
            # Not in the tool enum — the LLM invents these.
            "non_fitting_scope",
            "fitting_service_unavailable",
            "no_storage_info",
            "",
        ],
    )
    def test_other_reasons_blocked_without_evidence(self, reason: str) -> None:
        history = _hist("Олексій")
        result = _should_block_false_transfer(
            {"reason": reason, "summary": "..."}, history
        )
        assert result is not None
        assert "HALLUCINATION_GUARD" in result

    def test_the_347317e4_escape_is_closed(self) -> None:
        history = _hist("Дніпро", "на п'ятницю", "14:20", "Вікторія")
        blocked = _should_block_false_transfer(
            {"reason": "complex_question", "summary": "виникла плутанина"}, history
        )
        assert blocked is not None

    @pytest.mark.parametrize(
        "last_turn",
        [
            "а чи можна шини в кредит",
            "хочу купити гуму",
            "де мій заказ",
            "коли буде доставка",
            "у мене питання по гарантії",
        ],
    )
    def test_genuine_out_of_scope_still_transfers(self, last_turn: str) -> None:
        """Fitting-only scope: off-topic questions belong to a human."""
        history = _hist("Олексій", "Дніпро", last_turn)
        result = _should_block_false_transfer(
            {"reason": "non_fitting_scope", "summary": "..."}, history
        )
        assert result is None

    def test_escalation_also_allows_other_reasons(self) -> None:
        history = _hist("Дніпро", "нічого не працює", "переключи вже")
        result = _should_block_false_transfer(
            {"reason": "negative_emotion", "summary": "..."}, history
        )
        assert result is None

    def test_storage_question_is_in_scope(self) -> None:
        """«шини на зберіганні» is a checklist question, not an escape hatch."""
        history = _hist("Дніпро", "шини у вас на зберіганні")
        result = _should_block_false_transfer(
            {"reason": "non_fitting_scope", "summary": "..."}, history
        )
        assert result is not None


class TestLoopBreaker:
    """Blocking is a nudge, not a cage — after 2 rejections the LLM wins.

    Without this, a default-deny guard can trap the caller forever; Wave 13's
    PRICE handler repeated 5× in production for exactly this reason.
    """

    @staticmethod
    def _with_blocks(history: list[dict], count: int) -> list[dict]:
        out = list(history)
        for i in range(count):
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": f"t{i}",
                            "content": "⛔ HALLUCINATION_GUARD: заблокований бекендом.",
                        }
                    ],
                }
            )
        return out

    def test_first_two_attempts_blocked(self) -> None:
        base = _hist("Олексій")
        for prior in (0, 1):
            history = self._with_blocks(base, prior)
            assert _should_block_false_transfer(
                {"reason": "customer_request", "summary": "..."}, history
            ) is not None

    def test_third_attempt_allowed(self) -> None:
        history = self._with_blocks(_hist("Олексій"), 2)
        assert _should_block_false_transfer(
            {"reason": "customer_request", "summary": "..."}, history
        ) is None

    def test_cap_applies_across_different_reasons(self) -> None:
        """347317e4 probed a different reason each time — the cap counts all."""
        history = self._with_blocks(_hist("Олексій"), 2)
        assert _should_block_false_transfer(
            {"reason": "complex_question", "summary": "..."}, history
        ) is None


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
