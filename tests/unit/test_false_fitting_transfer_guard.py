"""Fitting callers are not let out to an operator by the false-transfer guard.

Measurement 2026-09-28 (`development-checklists/sales-residuals-2026-09-28/
FALSE-TRANSFERS-MEASURE.md`), sales off («тільки монтаж»):

- A: `non_fitting_scope` first attempts — the guard let through 700721ea
  («товар и шиномонтажу») and 75184393 («на шиномонтаж место в кредит»): an
  off-topic stem in the same STT-garbled turn as a fitting one.
- B: after Wave 16 six fitting callers still reached an operator with
  cannot_help / customer_request / complex_question — through the two-block exit
  (`_MAX_BLOCKS_PER_CALL`), the model changing `reason` on every refusal.
- Blocked transfers left no `call_tool_calls` row.

The caller turns below are the production turns of those calls, verbatim.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from src.agent import streaming_loop as sl
from src.agent.streaming_loop import (
    _FITTING_HOLD_MARKER,
    _GUARD_MARKER,
    _MAX_FITTING_BLOCKED_TURNS,
    _should_block_false_transfer,
    blocked_transfer_audit_result,
)
from src.monitoring.metrics import tool_audit_write_failures_total
from tests.unit.test_streaming_loop import _build_loop, _text_stream, _tool_stream

if TYPE_CHECKING:
    from collections.abc import Sequence

# ---------------------------------------------------------------------------
# Corpus A — caller turns of `non_fitting_scope` transfers (first attempt)
# ---------------------------------------------------------------------------

#: Fitting callers the transfer was wrong for (the measurement's «ложные»,
#: incl. 96515b6c and ac84e5da reclassified through `stt:corrections`).
CORPUS_A_FALSE: dict[str, list[str]] = {
    "96515b6c": ["Добрый день а можно мы не по раздутые колледство завтра напрямую"],
    "5bc4a958": ["шиномонтаж на Оболоне", "монет рыба в Киеве"],
    "529f390c": ["скидки будут А что вот шиномонтажу Харькове"],
    "853372e2": ["Анатолий", "Днепро на Победе на"],
    "7afbc049": ["здравствуйте Добрый день подскажите артист монтажу в Днепре"],
    "c9ab41f8": ["Добрый день мы не трогать надпись Тину на монтаж в Киеве"],
    "59aca32c": ["Добрий день хочу дізнатися вартість монтажу в Харкові"],
    "0921216e": ["запах на шиномонтаж"],
    "7bb81358": ["а добрый день а уточнить кильки будако что вот и монтаж на"],
    "6be52592": ["монтаж на Оболони", "именные потребности монтаж"],
    "b5b48797": ["весной сварки сна монтаж в Харькове"],
    "f749aefa": ["Добрый день я к буду в Арти монтажу в Черкассах"],
    "605bab15": ["Добрый день Можно ли знать монтаж в Днепре", "это начатый", "чоппер Настя ты"],
    "63365773": ["Александр", "запах на шиномонтаж место Черкассы"],
    "ea7775fb": ["Добрый день а когда можно записаться на шиномонтаж", "экрем"],
    "700721ea": [
        "Добрый день а мы не требует знать товар и шиномонтажу на два Колоса Volkswagen Tiguan"
    ],
    "4e5a15f3": ["запасайтесь нашим монтаж"],
    "0512ecad": ["Добрый день подключить под ласка сельский Корж шиномонтаж на запарийскому шоссе"],
    "fc2afa95": [
        "Добрый день А мне traba запасалась она монтажкой жить якобы есть монтажу эрви 17 у Днепре",
        "R18",
        "мне потребно держать монтажу там беляк каравану моменту зашлифовывает",
        "что в этой монтаж там",
    ],
    "ac84e5da": ["Добрый день а можно в кассоватый запуск"],
    "81cd713b": ["Добрый день а можно без надпися Вар то есть шиномонтажу на перемозі"],
    "f1283ce7": [
        "Александр",
        "запуск по черкасса",
        "так",
        "за собою",
        "завтрак вместо черкаши",
        "так",
        "так",
        "приложишь с собой",
        "Марина привожу с собой шины за собою",
        "над пятого",
        "чем мы выросли",
    ],
    "75184393": [
        "Илья",
        "и",
        "хочу записаться Украина Европа планета Земля",
        "и",
        "допустимое на шиномонтаж место в кредит",
    ],
}

#: The two the guard let through on HEAD — the same-turn fitting veto is theirs.
VETO_CASES = ("700721ea", "75184393")

#: A transfer that was right: the caller wants to buy tyres.
EE8D68A6 = [
    "узнать вартість",
    "205/517",
    "Сергій",
    "Днепр",
    "Речпорт",
    "там мне купить надо резину",
]

# ---------------------------------------------------------------------------
# Corpus B — transfers that went through after Wave 16 (third attempt)
# ---------------------------------------------------------------------------

CORPUS_B: dict[str, tuple[str, list[str]]] = {
    "97ddfd87": ("cannot_help", ["хочу записаться на монтаж", "діда"]),
    "c98213ba": ("cannot_help", ["записатися на шиномонтаж", "записатись на шиномонтаж", "Юрій"]),
    "37df2972": ("customer_request", ["записатися", "записатися", "Олексій"]),
    "24998707": (
        "customer_request",
        ["перенести запись", "на 18", "на 18 Надеюсь утро", "Надеюсь это", "900", "Евгения"],
    ),
    # Known misses of the lexical predicate (no «кошт»/«баланс» stem; «узнать»).
    "1eb11373": (
        "complex_question",
        ["стельки будакоштоватый балансу Ваня Колосова 16 размер", "Дима"],
    ),
    "045eb42a": ("cannot_help", ["узнать", "Антон"]),
    # Right transfers: order status, buying tyres, two explicit operator requests.
    "94d4b94b": (
        "customer_request",
        [
            "Алло так Добрий день а там я замовляв ціни в понеділок повинні відправити",
            "Пирятин",
            "Олександр",
        ],
    ),
    "ee8d68a6": ("non_fitting_scope", EE8D68A6),
    "b1631072": ("customer_request", ["меня с оператором живым живими оператором соединитесь"]),
    "9bbd2221": ("customer_request", ["стоїть у меня с оператором"]),
}
B_HELD = ("97ddfd87", "c98213ba", "37df2972", "24998707")
B_RELEASED = ("1eb11373", "045eb42a", "94d4b94b", "ee8d68a6", "b1631072", "9bbd2221")

REASONS = (
    "customer_request",
    "cannot_help",
    "negative_emotion",
    "complex_question",
    "non_fitting_scope",
    "fitting_service_unavailable",
    "",
)


def _block_msg(i: int) -> dict[str, Any]:
    return {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": f"{_GUARD_MARKER}: x"}
        ],
    }


def _hist(
    turns: Sequence[str], blocks_per_turn: dict[int, int] | None = None
) -> list[dict[str, Any]]:
    """Bot/caller history; `blocks_per_turn[i]` guard refusals after caller turn i
    (negative index counts from the end)."""
    per = {}
    for idx, n in (blocks_per_turn or {}).items():
        per[idx % len(turns)] = n
    out: list[dict[str, Any]] = []
    k = 0
    for i, text in enumerate(turns):
        out.append({"role": "assistant", "content": f"bot {i}"})
        out.append({"role": "user", "content": text})
        for _ in range(per.get(i, 0)):
            out.append({"role": "assistant", "content": [{"type": "tool_use", "id": f"t{k}"}]})
            out.append(_block_msg(k))
            k += 1
    return out


def _guard(reason: str, history: list[dict[str, Any]], **kw: Any) -> str | None:
    return _should_block_false_transfer({"reason": reason, "summary": "..."}, history, **kw)


# ---------------------------------------------------------------------------
# (a) first attempt
# ---------------------------------------------------------------------------


class TestCorpusAFirstAttempt:
    @pytest.mark.parametrize("call_id", sorted(CORPUS_A_FALSE))
    def test_every_false_first_attempt_is_blocked(self, call_id: str) -> None:
        msg = _guard("non_fitting_scope", _hist(CORPUS_A_FALSE[call_id]))
        assert msg is not None and _GUARD_MARKER in msg
        assert _FITTING_HOLD_MARKER not in msg

    def test_count_over_the_whole_corpus(self) -> None:
        blocked = [c for c, t in CORPUS_A_FALSE.items() if _guard("non_fitting_scope", _hist(t))]
        assert len(blocked) == len(CORPUS_A_FALSE) == 23

    @pytest.mark.parametrize("call_id", VETO_CASES)
    def test_veto_cases_blocked_on_the_second_attempt_too(self, call_id: str) -> None:
        assert _guard("non_fitting_scope", _hist(CORPUS_A_FALSE[call_id], {-1: 1})) is not None

    def test_buying_tyres_still_transfers(self) -> None:
        assert _guard("non_fitting_scope", _hist(EE8D68A6)) is None

    def test_veto_is_sales_off_only(self) -> None:
        # «рахун» stays out of scope under sales, so the stem decides there.
        turns = ["записатися на монтаж і рахунок"]
        assert _guard("non_fitting_scope", _hist(turns)) is not None
        assert _guard("non_fitting_scope", _hist(turns), sales_enabled=True) is None

    @pytest.mark.parametrize(
        "turn",
        [
            "шиномонтаж не працює, в кредит хочу",
            "запис на монтаж і товар, з оператором поговорити",
        ],
    )
    def test_veto_yields_to_complaint_or_operator(self, turn: str) -> None:
        assert _guard("non_fitting_scope", _hist([turn])) is None

    @pytest.mark.parametrize(
        "turn",
        ["скільки коштує доставка", "отменить заказ", "перенести доставку на завтра"],
    )
    def test_price_cancel_reschedule_stems_do_not_veto(self, turn: str) -> None:
        """Only a booking stem vetoes: «скільки»/«отмен»/«перенес» open order
        questions just as well (pinned incumbent verdict in test_sales_scope_switch)."""
        assert _guard("non_fitting_scope", _hist([turn])) is None

    def test_veto_reads_the_last_turn_only(self) -> None:
        turns = ["записатися на монтаж", "а ще питання по товару"]
        assert _guard("non_fitting_scope", _hist(turns)) is None


# ---------------------------------------------------------------------------
# (b) after the two-block exit
# ---------------------------------------------------------------------------


class TestCorpusBAfterTwoBlocks:
    @pytest.mark.parametrize("call_id", B_HELD)
    def test_fitting_callers_are_held(self, call_id: str) -> None:
        reason, turns = CORPUS_B[call_id]
        msg = _guard(reason, _hist(turns, {-1: 2}))
        assert msg is not None and _FITTING_HOLD_MARKER in msg

    @pytest.mark.parametrize("call_id", B_RELEASED)
    def test_the_rest_go_through(self, call_id: str) -> None:
        reason, turns = CORPUS_B[call_id]
        assert _guard(reason, _hist(turns, {-1: 2})) is None

    @pytest.mark.parametrize("reason", REASONS)
    def test_hold_covers_every_reason(self, reason: str) -> None:
        msg = _guard(reason, _hist(CORPUS_B["97ddfd87"][1], {-1: 2}))
        assert msg is not None and _FITTING_HOLD_MARKER in msg

    def test_sales_on_keeps_the_two_block_exit(self) -> None:
        reason, turns = CORPUS_B["97ddfd87"]
        assert _guard(reason, _hist(turns, {-1: 2}), sales_enabled=True) is None

    def test_no_fitting_evidence_keeps_the_two_block_exit(self) -> None:
        assert _guard("cannot_help", _hist(["Олексій", "Дніпро"], {-1: 2})) is None


class TestHoldCeiling:
    turns = ("хочу записатися на монтаж", "Олексій", "Дніпро", "так", "в п'ятницю", "14:20")

    def test_repeats_inside_one_caller_turn_do_not_spend_the_ceiling(self) -> None:
        msg = _guard("cannot_help", _hist(self.turns, {-1: 7}))
        assert msg is not None and f"{_FITTING_HOLD_MARKER} 1/" in msg

    def test_three_blocked_caller_turns_still_held(self) -> None:
        hist = _hist(self.turns, {1: 1, 2: 1, 3: 1})
        msg = _guard("cannot_help", hist)
        assert msg is not None and f"{_FITTING_HOLD_MARKER} 3/" in msg

    def test_ceiling_releases_at_four_blocked_caller_turns(self) -> None:
        assert _MAX_FITTING_BLOCKED_TURNS == 4
        hist = _hist(self.turns, {1: 1, 2: 1, 3: 1, 4: 1})
        assert _guard("cannot_help", hist) is None

    def test_ceiling_is_counted_in_caller_turns_not_attempts(self) -> None:
        # Six attempts over three caller turns: under the ceiling.
        hist = _hist(self.turns, {1: 2, 2: 2, 3: 2})
        assert _guard("complex_question", hist) is not None


class TestLoopBreakerReleases:
    base = ("хочу записаться на монтаж", "діда")

    @pytest.mark.parametrize(
        "new_turn",
        [
            "з'єднайте з оператором",
            "з'єднайте мене будь ласка",  # no «оператор» — TRANSFER stem
            "та ну вас, нічого не працює",
            "а купить шины у вас можно",
        ],
    )
    @pytest.mark.parametrize("reason", ["cannot_help", "customer_request", "complex_question"])
    def test_a_new_turn_asking_for_a_person_or_another_topic(
        self, new_turn: str, reason: str
    ) -> None:
        hist = _hist(self.base, {-1: 2})
        hist += [{"role": "assistant", "content": "bot"}, {"role": "user", "content": new_turn}]
        assert _guard(reason, hist) is None

    def test_explicit_operator_request_goes_through_at_once(self) -> None:
        hist = _hist(["хочу записатися на монтаж", "з'єднайте з оператором"])
        assert _guard("customer_request", hist) is None


# ---------------------------------------------------------------------------
# Audit row for a blocked transfer
# ---------------------------------------------------------------------------


async def _hook_signature(
    name: str, args: dict[str, Any], result: Any, duration_ms: int, success: bool
) -> None: ...


def _transfer_turn(reason: str = "customer_request") -> list[list[Any]]:
    return [
        _tool_stream("", "tc1", "transfer_to_operator", {"reason": reason, "summary": "s"}),
        _text_stream("Як до вас звертатися?"),
    ]


class TestBlockedTransferIsAudited:
    @pytest.mark.asyncio
    async def test_row_written_with_blocked_marker(self) -> None:
        loop, _, tool_router, _ = _build_loop(_transfer_turn())
        handler = AsyncMock(return_value={"transferred": True})
        tool_router.register("transfer_to_operator", handler)
        hook = AsyncMock(spec=_hook_signature)
        tool_router.set_execute_hook(hook)

        await loop.run_turn("Олексій", [])

        handler.assert_not_awaited()
        hook.assert_awaited_once()
        name, args, result, _duration, success = hook.await_args.args
        assert name == "transfer_to_operator"
        assert args["reason"] == "customer_request"
        assert result["blocked"] is True
        assert result["reason"] == "customer_request"
        assert result["guard"] == "false_transfer"
        assert _GUARD_MARKER in result["message"]
        assert success is True  # «did not raise» — the marker carries the refusal

    @pytest.mark.asyncio
    async def test_allowed_transfer_is_not_marked_blocked(self) -> None:
        loop, _, tool_router, _ = _build_loop(_transfer_turn())
        tool_router.register("transfer_to_operator", AsyncMock(return_value={"transferred": True}))
        hook = AsyncMock(spec=_hook_signature)
        tool_router.set_execute_hook(hook)

        await loop.run_turn("з'єднайте з оператором", [])

        hook.assert_awaited_once()
        assert "blocked" not in hook.await_args.args[2]

    @pytest.mark.asyncio
    async def test_failed_write_is_loud_and_the_turn_survives(self) -> None:
        metric = tool_audit_write_failures_total.labels(
            tool_name="transfer_to_operator", path="blocked"
        )
        before = metric._value.get()
        loop, _, tool_router, _ = _build_loop(_transfer_turn())
        tool_router.set_execute_hook(
            AsyncMock(spec=_hook_signature, side_effect=RuntimeError("db"))
        )

        result = await loop.run_turn("Олексій", [])

        assert metric._value.get() == before + 1
        assert "звертатися" in result.spoken_text

    def test_audit_result_flags_the_fitting_hold(self) -> None:
        reason, turns = CORPUS_B["97ddfd87"]
        msg = _guard(reason, _hist(turns, {-1: 2}))
        assert msg is not None
        assert blocked_transfer_audit_result({"reason": reason}, msg)["fitting_hold"] is True
        plain = _guard("customer_request", _hist(["Олексій"]))
        assert plain is not None
        assert blocked_transfer_audit_result({"reason": "x"}, plain)["fitting_hold"] is False


def test_fitting_intents_are_the_four_fitting_scenarios() -> None:
    assert frozenset({"BOOK", "PRICE", "CANCEL", "RESCHEDULE"}) == sl._FITTING_INTENTS
