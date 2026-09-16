"""A guard refusal the model re-asks inside one turn is not run a second time.

The measurement behind this: over 21 days 47 refusals across 18 calls were
verbatim repeats of a call the same turn had already been refused. `past_krok_2`
supplied 29 of them and averaged 3.7 refusals per call, where every other guard
sits at ~1.0 — the model obeys them first time. The repeats are what turn a
correct refusal into a dead turn: five rounds of the same wall of text leave
nothing to say, and the caller hears the exhaustion fallback.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.llm.models import ToolCallDelta, ToolCallEnd, ToolCallStart
from src.monitoring.metrics import guard_refusal_repeated_total
from tests.unit.test_streaming_loop import _build_loop, _done, _text_stream, _tool_stream

REFUSAL = {
    "error": True,
    "action_required": "call_get_fitting_slots",
    "reason": "past_krok_2",
    "message": "⛔ Регресія Кроку 1. Станцію вже обрано.",
}


def _counter(tool_name: str, reason: str) -> float:
    return guard_refusal_repeated_total.labels(tool_name=tool_name, reason=reason)._value.get()


def _same_call_twice(args: dict[str, Any]) -> list[list[Any]]:
    """Two rounds asking for get_fitting_stations with identical args, then text."""
    return [
        _tool_stream("", "tc1", "get_fitting_stations", args),
        _tool_stream("", "tc2", "get_fitting_stations", args),
        _text_stream("Готово."),
    ]


def _two_tool_stream(
    id_a: str, name_a: str, args_a: dict[str, Any], id_b: str, name_b: str
) -> list[Any]:
    """One round asking for two different tools at once."""
    return [
        ToolCallStart(id=id_a, name=name_a),
        ToolCallDelta(id=id_a, arguments_chunk=json.dumps(args_a)),
        ToolCallEnd(id=id_a),
        ToolCallStart(id=id_b, name=name_b),
        ToolCallDelta(id=id_b, arguments_chunk=json.dumps({"tire_diameter": 16})),
        ToolCallEnd(id=id_b),
        _done(stop_reason="tool_use"),
    ]


def _tool_result_texts(history: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for msg in history:
        content = msg.get("content")
        if isinstance(content, list):
            out.extend(
                str(part.get("content", "")) for part in content if part.get("type") == "tool_result"
            )
    return out


class TestARefusedCallIsNotRunAgainInTheSameTurn:
    @pytest.mark.asyncio
    async def test_the_tool_runs_once_for_two_identical_requests(self):
        loop, _, tool_router, _ = _build_loop(_same_call_twice({"city": "Дніпро"}))
        handler = AsyncMock(return_value=REFUSAL)
        tool_router.register("get_fitting_stations", handler)

        await loop.run_turn("перенеси на другу", [])

        assert handler.await_count == 1

    @pytest.mark.asyncio
    async def test_the_second_answer_is_not_the_guard_message_again(self):
        # Repeating the refusal verbatim is the thing that did not work in
        # production, so the point of the fix is that the second answer differs.
        history: list[dict[str, Any]] = []
        loop, _, tool_router, _ = _build_loop(_same_call_twice({"city": "Дніпро"}))
        tool_router.register("get_fitting_stations", AsyncMock(return_value=REFUSAL))

        await loop.run_turn("перенеси на другу", history)

        answers = _tool_result_texts(history)
        assert len(answers) == 2
        assert "Регресія Кроку 1" in answers[0]
        assert "Регресія Кроку 1" not in answers[1]
        assert "вже викликав" in answers[1]

    @pytest.mark.asyncio
    async def test_the_counter_moves_under_the_refusing_tool_and_reason(self):
        before = _counter("get_fitting_stations", "past_krok_2")
        loop, _, tool_router, _ = _build_loop(_same_call_twice({"city": "Дніпро"}))
        tool_router.register("get_fitting_stations", AsyncMock(return_value=REFUSAL))

        await loop.run_turn("перенеси на другу", [])

        assert _counter("get_fitting_stations", "past_krok_2") == before + 1

    @pytest.mark.asyncio
    async def test_a_refusal_carrying_no_reason_is_still_registered(self):
        # `book_fitting` refuses 43 times in 21 days with no `reason` key at all;
        # keying the register on the reason would have let every one of them
        # through. The label falls back, the suppression does not.
        refusal = {"error": True, "message": "Неможливо записати без: vehicle_info."}
        args = {"date": "2026-09-17", "time": "14:00"}
        loop, _, tool_router, _ = _build_loop(
            [
                _tool_stream("", "tc1", "book_fitting", args),
                _tool_stream("", "tc2", "book_fitting", args),
                _text_stream("Готово."),
            ]
        )
        handler = AsyncMock(return_value=refusal)
        tool_router.register("book_fitting", handler)

        await loop.run_turn("записуй", [])

        assert handler.await_count == 1


class TestWhatTheRegisterMustNotSwallow:
    @pytest.mark.asyncio
    async def test_the_same_tool_with_different_args_still_runs(self):
        loop, _, tool_router, _ = _build_loop(
            [
                _tool_stream("", "tc1", "get_fitting_stations", {"city": "Дніпро"}),
                _tool_stream("", "tc2", "get_fitting_stations", {"city": "Київ"}),
                _text_stream("Готово."),
            ]
        )
        handler = AsyncMock(return_value=REFUSAL)
        tool_router.register("get_fitting_stations", handler)

        await loop.run_turn("а в Києві?", [])

        assert handler.await_count == 2

    @pytest.mark.asyncio
    async def test_a_transient_failure_may_be_retried(self):
        # A timeout answers with a string, not `error is True`. Retrying that is
        # legitimate, and only 3 such retries happened in 21 days anyway — the
        # register is not allowed to cost us one.
        loop, _, tool_router, _ = _build_loop(_same_call_twice({"city": "Дніпро"}))
        handler = AsyncMock(return_value={"error": "Сервіс тимчасово не відповідає"})
        tool_router.register("get_fitting_stations", handler)

        await loop.run_turn("перенеси", [])

        assert handler.await_count == 2

    @pytest.mark.asyncio
    async def test_a_call_that_succeeded_may_be_repeated(self):
        loop, _, tool_router, _ = _build_loop(_same_call_twice({"city": "Дніпро"}))
        handler = AsyncMock(return_value={"total": 1, "stations": []})
        tool_router.register("get_fitting_stations", handler)

        await loop.run_turn("перенеси", [])

        assert handler.await_count == 2

    @pytest.mark.asyncio
    async def test_the_next_turn_starts_with_an_empty_register(self):
        # The caller gets to change the facts between turns — a city named out
        # loud defeats the cross-city guard — so a refusal must not outlive the
        # turn that earned it.
        args = {"city": "Дніпро"}
        loop, _, tool_router, _ = _build_loop(
            [
                _tool_stream("", "tc1", "get_fitting_stations", args),
                _text_stream("Секунду."),
                _tool_stream("", "tc2", "get_fitting_stations", args),
                _text_stream("Готово."),
            ]
        )
        handler = AsyncMock(return_value=REFUSAL)
        tool_router.register("get_fitting_stations", handler)

        await loop.run_turn("перенеси", [])
        await loop.run_turn("та перенеси ж", [])

        assert handler.await_count == 2


class TestARoundOfNothingButRepeatsEndsTheTurn:
    @pytest.mark.asyncio
    async def test_the_remaining_rounds_are_not_spent(self):
        # The scarce resource is the round budget, not the tool call: a guard
        # refusal executes in 0ms, so the 106 seconds `fb854e33` lost were LLM
        # round-trips. Suppressing the call without reclaiming the round would
        # have saved nothing measurable.
        args = {"city": "Дніпро"}
        loop, router, tool_router, _ = _build_loop(
            [_tool_stream("", f"tc{i}", "get_fitting_stations", args) for i in range(5)]
        )
        tool_router.register("get_fitting_stations", AsyncMock(return_value=REFUSAL))

        result = await loop.run_turn("перенеси", [])

        assert result.tool_calls_made == 2
        assert router.call_count == 2

    @pytest.mark.asyncio
    async def test_a_round_that_also_asks_something_new_is_a_continuation(self):
        # `8e5fe347` interleaved a working `get_fitting_price` between refusals
        # and recovered. Cutting the turn on *any* repeat would take that away.
        args = {"city": "Дніпро"}
        loop, router, tool_router, _ = _build_loop(
            [
                _tool_stream("", "tc1", "get_fitting_stations", args),
                _two_tool_stream("tc2", "get_fitting_stations", args, "tc3", "get_fitting_price"),
                _text_stream("Ціна така."),
            ]
        )
        tool_router.register("get_fitting_stations", AsyncMock(return_value=REFUSAL))
        price = AsyncMock(return_value={"prices": [{"price": 354}]})
        tool_router.register("get_fitting_price", price)

        await loop.run_turn("а скільки коштує", [])

        assert price.await_count == 1
        assert router.call_count == 3

    @pytest.mark.asyncio
    async def test_the_caller_is_not_left_in_silence(self):
        args = {"city": "Дніпро"}
        loop, router, tool_router, _ = _build_loop(
            [_tool_stream("", f"tc{i}", "get_fitting_stations", args) for i in range(5)]
        )
        tool_router.register("get_fitting_stations", AsyncMock(return_value=REFUSAL))
        assert router.call_count == 0

        result = await loop.run_turn("перенеси", [])

        assert result.spoken_text.strip()


class TestTheRegisterKeysOnArgumentsNotOnOrder:
    @pytest.mark.asyncio
    async def test_the_same_arguments_in_a_different_order_are_the_same_call(self):
        forward = json.dumps({"city": "Дніпро", "query": "Речпорт"})
        reversed_ = json.dumps({"query": "Речпорт", "city": "Дніпро"})
        assert forward != reversed_
        loop, _, tool_router, _ = _build_loop(
            [
                _tool_stream("", "tc1", "get_fitting_stations", json.loads(forward)),
                _tool_stream("", "tc2", "get_fitting_stations", json.loads(reversed_)),
                _text_stream("Готово."),
            ]
        )
        handler = AsyncMock(return_value=REFUSAL)
        tool_router.register("get_fitting_stations", handler)

        await loop.run_turn("перенеси", [])

        assert handler.await_count == 1
