"""Multi-car booking, wave 3-D — the wiring (FF_MULTI_CAR_BOOKING).

Owner request 2026-10-27: Твоя шина books several cars in one call, one after
another (R01); city, station and name are not asked again (R03); a fourth car
is a fleet and goes to an operator (R04); only a caller who says it starts the
flow (R05); behind a flag (R06); by code, not a prompt line (R07); Про Колесо
has no `book_fitting` — nothing opens there (R08); with the flag off the
detector only logs `multi_car_would_continue` (R10).

The state (`CallSession.next_car_pending`, `start_next_car`, …) is wave 2-C;
the booking handler that records `fitting_bookings` is wave 3-F (`main.py`).
Here bookings are recorded through `session.record_fitting_booking`, the
same call the handler makes.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import uuid
from typing import Any, ClassVar

import pytest

from src.agent.agent import MULTI_CAR_OVER_CAP_PHRASE, LLMAgent
from src.agent.fitting_fsm import NEXT_CAR_FIELDS, TRANSITIONS, FsmEngine, FsmEvent, FsmState
from src.agent.prompts import _render_fitting_progress
from src.agent.streaming_loop import StreamingAgentLoop
from src.core.call_session import CallSession
from src.llm.models import LLMResponse, Usage
from src.llm.router import LLMRouter
from src.monitoring.metrics import false_booking_claim_total, multi_car_booking_total
from src.stt.base import Transcript
from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection
from tests.unit.mocks.mock_llm_router import MockLLMRouter
from tests.unit.mocks.mock_tts import MockTTSEngine
from tests.unit.test_operator_only_intents import TS_OFF, _Call
from tests.unit.test_pipeline_fsm_wire import Harness
from tests.unit.test_vehicle_lookup_gate import _stream_events

_PIPE_LOG = "src.core.pipeline"


def _tool(name: str) -> dict[str, Any]:
    return {"name": name, "description": "", "input_schema": {"type": "object"}}


#: Твоя шина books fittings; Про Колесо has no `book_fitting` (R08).
TS_TOOLS = [
    _tool(n)
    for n in (
        "transfer_to_operator",
        "get_fitting_stations",
        "get_fitting_slots",
        "reserve_fitting_slot",
        "book_fitting",
        "update_customer_profile",
    )
]
PK_TOOLS = [_tool(n) for n in ("transfer_to_operator", "search_tires", "get_fitting_price")]

BOOKING_TOOLS = {"book_fitting", "get_fitting_slots", "reserve_fitting_slot"}


@pytest.fixture
def flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FF_MULTI_CAR_BOOKING", "true")


@pytest.fixture
def flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FF_MULTI_CAR_BOOKING", raising=False)


def _harness(tools: list[dict[str, Any]] | None) -> Harness:
    h = Harness()
    if tools is not None:
        # The pipeline reads the loop's tool list (`_books_fitting`).
        h.streaming_loop._tools = tools
    return h


def _first_car_booked(session: CallSession, *, booking_id: str = "guid-1") -> None:
    """What the session holds after car 1's confirmed booking (F + 2-C)."""
    session.fitting_customer_name = "Олена"
    session.selected_fitting_station_id = "st-1"
    session.fitting_storage_choice = "own"
    session.selected_fitting_date = "2026-10-28"
    session.selected_fitting_time = "10:00"
    session.fitting_plate = "білий"
    session.fitting_vehicle_brand = "Toyota"
    session.fitting_booked = True
    session.fsm_state = FsmState.BOOK.value
    session.fsm_filled_fields.update(
        {
            "intent": "fitting",
            "city": "Дніпро",
            "station_id": "st-1",
            "name": "Олена",
            "storage_choice": "own",
            "date": "2026-10-28",
            "time": "10:00",
            "color": "білий",
            "brand": "Toyota",
            "confirmed": True,
        }
    )
    session.record_fitting_booking(
        booking_id=booking_id,
        station_id="st-1",
        date="2026-10-28",
        time="10:00",
        vehicle="Toyota",
        color="білий",
    )


def _t(text: str) -> Transcript:
    return Transcript(text=text, is_final=True, confidence=0.95, language="uk-UA")


def _progress(h: Harness) -> dict[str, Any]:
    return h.pipeline._build_fitting_progress(None, krok8_confirmed=False)


def _counter(event: str) -> float:
    return multi_car_booking_total.labels(event=event)._value.get()


# ── FSM: row 49 ───────────────────────────────────────────────────────


class TestFsmNextCar:
    def test_book_goes_to_storage_and_clears_only_the_car(self) -> None:
        session = CallSession(uuid.uuid4())
        _first_car_booked(session)
        to = FsmEngine(session).start_next_car(2)
        assert to is FsmState.STORAGE
        assert session.fsm_state == "STORAGE"
        filled = session.fsm_filled_fields
        assert not set(NEXT_CAR_FIELDS) & set(filled)
        # R03: city, station and name are not asked again.
        assert filled == {
            "intent": "fitting",
            "city": "Дніпро",
            "station_id": "st-1",
            "name": "Олена",
        }
        last = session.fsm_history[-1]
        assert (last["from"], last["to"], last["event"]) == ("BOOK", "STORAGE", "tool_success")
        assert last["payload"]["row"] == 49

    def test_row_49_wins_over_row_27_for_its_guard(self) -> None:
        from src.agent.fitting_fsm import find_transitions

        rows = find_transitions(FsmState.BOOK, FsmEvent.TOOL_SUCCESS, "next_car")
        assert [r.row for r in rows][:2] == [49, 27]
        # Without the guard row 27 stays what it was.
        assert find_transitions(FsmState.BOOK, FsmEvent.TOOL_SUCCESS)[0].row == 27
        assert next(r for r in TRANSITIONS if r.row == 27).to_state is FsmState.DONE

    def test_before_storage_nothing_to_rewind(self) -> None:
        session = CallSession(uuid.uuid4())
        session.fsm_state = FsmState.CITY.value
        assert FsmEngine(session).start_next_car(2) is None
        assert session.fsm_state == "CITY"


# ── Flag off: HEAD behaviour + the R10 log ────────────────────────────


class TestFlagOff:
    async def test_detector_only_logs(self, flag_off: None, caplog) -> None:
        h = _harness(TS_TOOLS)
        with caplog.at_level(logging.WARNING, logger=_PIPE_LOG):
            await h.run("хочу записати дві машини на шиномонтаж")
        assert "multi_car_would_continue" in caplog.text
        assert h.session.cars_expected is None
        kwargs = h.llm_kwargs[0]
        assert "next_car_pending" not in kwargs
        assert "multi_car_over_cap" not in kwargs

    async def test_four_cars_flag_off_no_transfer(self, flag_off: None) -> None:
        h = _harness(TS_TOOLS)
        await h.run("у мене чотири машини")
        assert "multi_car_over_cap" not in h.llm_kwargs[0]
        assert h.session.cars_expected is None

    def test_after_a_booking_nothing_moves(self, flag_off: None) -> None:
        h = _harness(TS_TOOLS)
        _first_car_booked(h.session)
        h.session.cars_expected = 2  # even if something had written it
        before = dict(h.session.fsm_filled_fields)
        h.pipeline._run_multi_car_step(_t("і ще одну машину запишіть"))
        assert h.session.fitting_car_number == 1
        assert h.session.fsm_state == "BOOK"
        assert h.session.fsm_filled_fields == before
        progress = _progress(h)
        assert progress["booked"] is True
        assert not {"car_number", "cars_total", "booked_cars"} & set(progress)
        assert "ЗАПИС СТВОРЕНО" in _render_fitting_progress(progress)

    def test_progress_keys_are_heads(self, flag_off: None) -> None:
        h = _harness(TS_TOOLS)
        h.session.cars_expected = 2
        assert not {"car_number", "cars_total", "booked_cars"} & set(_progress(h))


# ── Флаг on, Твоя шина ────────────────────────────────────────────────


class TestTwoCars:
    async def test_named_count_reaches_the_loop(self, flag_on: None) -> None:
        h = _harness(TS_TOOLS)
        await h.run("хочу записати дві машини на шиномонтаж")
        assert h.session.cars_expected == 2
        kwargs = h.llm_kwargs[0]
        assert kwargs["next_car_pending"] is True
        block = _render_fitting_progress(kwargs["fitting_progress"])
        assert "Авто 1 з 2" in block

    async def test_second_car_starts_after_the_first_booking(self, flag_on: None) -> None:
        h = _harness(TS_TOOLS)
        h.session.cars_expected = 2
        _first_car_booked(h.session)
        started = _counter("started")
        await h.run("так, давайте другу")
        s = h.session
        assert s.fitting_car_number == 2
        assert _counter("started") == started + 1
        assert s.fsm_state == "STORAGE"
        # Car fields cleared, the caller's kept (R03).
        assert s.selected_fitting_date is None and s.fitting_plate is None
        assert s.fitting_customer_name == "Олена"
        assert s.fsm_filled_fields["city"] == "Дніпро"
        kwargs = h.llm_kwargs[0]
        assert kwargs["next_car_pending"] is True
        progress = kwargs["fitting_progress"]
        # The block of car 2 is a booking in progress, not «ЗАПИС СТВОРЕНО».
        assert progress["booked"] is False
        assert progress["date"] is None and progress["plate"] is None
        assert progress["customer_name"] == "Олена"
        block = _render_fitting_progress(progress)
        assert "ЗАПИС СТВОРЕНО" not in block
        assert "Авто 2 з 2" in block
        assert "білий Toyota — 2026-10-28 о 10:00" in block
        assert "Попереднє авто записано на 2026-10-28 о 10:00" in block

    async def test_all_cars_booked_closes_with_the_list(self, flag_on: None) -> None:
        h = _harness(TS_TOOLS)
        h.session.cars_expected = 2
        _first_car_booked(h.session)
        h.pipeline._run_multi_car_step(_t("так"))
        h.session.record_fitting_booking(
            booking_id="guid-2",
            station_id="st-1",
            date="2026-10-28",
            time="10:20",
            vehicle="Skoda",
            color="сірий",
        )
        booked = _counter("booked")
        h.pipeline._count_multi_car_bookings()
        assert _counter("booked") == booked + 1
        await h.run("дякую")
        kwargs = h.llm_kwargs[0]
        assert "next_car_pending" not in kwargs  # both booked → tools close
        progress = kwargs["fitting_progress"]
        assert progress["booked"] is True
        block = _render_fitting_progress(progress)
        assert "ЗАПИС СТВОРЕНО" in block
        assert "2. сірий Skoda — 2026-10-28 о 10:20" in block
        assert h.session.fitting_car_number == 2

    def test_more_after_a_booking_opens_the_next_car(self, flag_on: None) -> None:
        h = _harness(TS_TOOLS)
        _first_car_booked(h.session)
        h.pipeline._run_multi_car_step(_t("а можна ще одну машину записати?"))
        assert h.session.cars_expected == 2
        assert h.session.fitting_car_number == 2

    def test_no_word_from_the_caller_no_next_car_r05(self, flag_on: None) -> None:
        h = _harness(TS_TOOLS)
        _first_car_booked(h.session)
        h.pipeline._run_multi_car_step(_t("дякую, до побачення"))
        assert h.session.cars_expected is None
        assert h.session.fitting_car_number == 1
        assert h.pipeline._next_car_pending() is False


# ── Loop-breakers ─────────────────────────────────────────────────────


class TestLoopBreaker:
    def test_one_booking_starts_one_car(self, flag_on: None) -> None:
        h = _harness(TS_TOOLS)
        h.session.cars_expected = 3
        _first_car_booked(h.session)
        h.pipeline._run_multi_car_step(_t("так"))
        h.session.fitting_plate = "сірий"  # car 2's answer
        # The same booking again (same GUID — deduped) and more turns: no new car.
        h.session.record_fitting_booking(
            booking_id="guid-1",
            station_id="st-1",
            date="2026-10-28",
            time="10:00",
            vehicle="Toyota",
            color="білий",
        )
        h.pipeline._run_multi_car_step(_t("так"))
        h.pipeline._run_multi_car_step(_t("ще раз так"))
        assert h.session.fitting_car_number == 2
        assert h.session.fitting_plate == "сірий"

    def test_naming_the_car_in_hand_does_not_add_a_car(self, flag_on: None) -> None:
        h = _harness(TS_TOOLS)
        h.session.cars_expected = 2
        _first_car_booked(h.session)
        h.pipeline._run_multi_car_step(_t("так"))
        assert h.session.fitting_car_number == 2
        h.pipeline._run_multi_car_step(_t("друга машина — шкода, сіра"))
        assert h.session.cars_expected == 2

    def test_over_cap_once_per_call(self, flag_on: None) -> None:
        h = _harness(TS_TOOLS)
        h.session.cars_expected = 4
        assert h.pipeline._multi_car_over_cap_now() is True
        assert h.pipeline._multi_car_over_cap_now() is False


# ── Про Колесо (R08) and default-deny ─────────────────────────────────


class TestProkoleso:
    async def test_no_book_fitting_nothing_opens(self, flag_on: None) -> None:
        h = _harness(PK_TOOLS)
        await h.run("хочу записати дві машини на шиномонтаж")
        assert h.session.cars_expected is None
        assert "next_car_pending" not in h.llm_kwargs[0]

    async def test_no_tool_list_is_deny(self, flag_on: None) -> None:
        h = _harness(None)
        await h.run("у мене чотири машини")
        assert h.session.cars_expected is None
        assert "multi_car_over_cap" not in h.llm_kwargs[0]


# ── 4+ cars (R04) ─────────────────────────────────────────────────────


class TestOverCap:
    async def test_pipeline_hands_the_transfer_once(self, flag_on: None) -> None:
        h = _harness(TS_TOOLS)
        await h.run("мені треба записати чотири машини", "алло")
        assert h.session.cars_expected == 4
        assert h.llm_kwargs[0]["multi_car_over_cap"] is True
        assert "multi_car_over_cap" not in h.llm_kwargs[1]


def _stream_loop(call: _Call, tools: list[dict[str, Any]]):
    class _Router(MockLLMRouter):
        seen_tools: ClassVar[list[list[str]]] = []

        async def complete_stream(self, task: Any, messages: Any, **kwargs: Any):  # type: ignore[override]
            type(self).seen_tools.append([t["name"] for t in kwargs.get("tools") or []])
            async for e in super().complete_stream(task, messages, **kwargs):
                yield e

    _Router.seen_tools = []
    loop = StreamingAgentLoop(
        llm_router=_Router(_stream_events([], "Добре.")),
        tool_router=call.router,
        tts=MockTTSEngine(),
        conn=MockAudioSocketConnection(),
        barge_in_event=asyncio.Event(),
        tools=tools,
        system_prompt="Test system prompt",
        network_policy=TS_OFF,
    )

    async def run(text: str, **kw: Any) -> tuple[str, list[str]]:
        res = await loop.run_turn(text, [], **kw)
        return res.spoken_text, (_Router.seen_tools[-1] if _Router.seen_tools else [])

    return run


def _text_agent(call: _Call, tools: list[dict[str, Any]]):
    from unittest.mock import AsyncMock, create_autospec

    agent = LLMAgent(
        api_key="test-key",
        system_prompt="base",
        llm_router=create_autospec(LLMRouter, instance=True),
        tool_router=call.router,
        tools=tools,
        network_policy=TS_OFF,
    )
    seen: list[list[str]] = []

    async def _complete(_task: Any, messages: Any, *_a: Any, **k: Any):
        copy.deepcopy(messages)
        seen.append([t["name"] for t in k.get("tools") or []])
        return LLMResponse(text="Добре.", usage=Usage(1, 1), provider="test")

    agent._llm_router.complete = AsyncMock(side_effect=_complete)

    async def run(text: str, **kw: Any) -> tuple[str, list[str]]:
        spoken, _ = await agent.process_message(text, [], **kw)
        return spoken, (seen[-1] if seen else [])

    return run


LOOPS = pytest.mark.parametrize("make", [_stream_loop, _text_agent], ids=["streaming", "text"])


@LOOPS
async def test_loop_keeps_booking_tools_for_the_next_car(make: Any, monkeypatch) -> None:
    call = _Call(TS_OFF, monkeypatch, ami_ok=True)
    run = make(call, TS_TOOLS)
    _, closed = await run("так", fitting_booked=True)
    assert not BOOKING_TOOLS & set(closed)  # HEAD: booked → stripped
    _, opened = await run("так", fitting_booked=True, next_car_pending=True)
    assert opened and set(opened) >= BOOKING_TOOLS


@LOOPS
async def test_loop_transfers_four_cars_by_code(make: Any, monkeypatch) -> None:
    call = _Call(TS_OFF, monkeypatch, ami_ok=True)
    run = make(call, TS_TOOLS)
    before = _counter("over_cap")
    spoken, seen = await run("у мене чотири машини", multi_car_over_cap=True)
    assert spoken == MULTI_CAR_OVER_CAP_PHRASE
    assert seen == []  # no LLM round
    assert [a["reason"] for a, _ in call.transfers()] == ["complex_question"]
    assert "більше ніж трьох" in call.transfers()[0][0]["summary"]
    assert _counter("over_cap") == before + 1


@LOOPS
async def test_loop_without_book_fitting_does_not_transfer(make: Any, monkeypatch) -> None:
    call = _Call(TS_OFF, monkeypatch, ami_ok=True)
    run = make(call, PK_TOOLS)
    spoken, _ = await run("у мене чотири машини", multi_car_over_cap=True)
    assert spoken != MULTI_CAR_OVER_CAP_PHRASE
    assert call.transfers() == []


# ── False booking claim for car N>1 ───────────────────────────────────


class TestFalseClaim:
    def test_car_two_claimed_without_its_booking(self, flag_on: None, caplog) -> None:
        h = _harness(TS_TOOLS)
        h.session.cars_expected = 2
        _first_car_booked(h.session)
        h.pipeline._run_multi_car_step(_t("так"))
        before = false_booking_claim_total._value.get()
        with caplog.at_level(logging.ERROR, logger=_PIPE_LOG):
            h.pipeline._flag_false_booking_claim("Ви записані на завтра.")
        assert false_booking_claim_total._value.get() == before + 1
        assert "FALSE BOOKING CLAIM" in caplog.text

    def test_car_two_booked_is_silent(self, flag_on: None) -> None:
        h = _harness(TS_TOOLS)
        h.session.cars_expected = 2
        _first_car_booked(h.session)
        h.pipeline._run_multi_car_step(_t("так"))
        h.session.record_fitting_booking(
            booking_id="guid-2",
            station_id="st-1",
            date="2026-10-28",
            time="10:20",
            vehicle="Skoda",
            color="сірий",
        )
        before = false_booking_claim_total._value.get()
        h.pipeline._flag_false_booking_claim("Ви записані на завтра.")
        assert false_booking_claim_total._value.get() == before

    def test_single_car_unchanged(self, flag_off: None) -> None:
        h = _harness(TS_TOOLS)
        h.session.fitting_booked = True  # no bookings list — F not involved
        before = false_booking_claim_total._value.get()
        h.pipeline._flag_false_booking_claim("Ви записані на завтра.")
        assert false_booking_claim_total._value.get() == before


# ── Call sites: the blocking (text) path and the «booked» counter ─────


def _blocking(tools: list[dict[str, Any]]) -> tuple[Harness, list[dict[str, Any]]]:
    """The same pipeline on the non-streaming path (`LLMAgent.process_message`)."""
    from unittest.mock import MagicMock

    h = Harness()
    seen: list[dict[str, Any]] = []
    agent = MagicMock(
        spec=[
            "process_message",
            "_tools",
            "last_input_tokens",
            "last_output_tokens",
            "last_provider_key",
            "last_cached_input_tokens",
        ]
    )
    agent._tools = tools
    agent.last_input_tokens = 0
    agent.last_output_tokens = 0
    agent.last_provider_key = ""
    agent.last_cached_input_tokens = 0

    async def _process(**kwargs: Any) -> tuple[str, list[dict[str, Any]]]:
        seen.append(kwargs)
        return "Добре.", []

    agent.process_message = _process
    h.pipeline._agent = agent
    h.pipeline._streaming_loop = None
    return h, seen


class TestCallSites:
    async def test_blocking_path_gets_next_car_and_over_cap(self, flag_on: None) -> None:
        h, seen = _blocking(TS_TOOLS)
        await h.run("мені треба записати чотири машини")
        assert seen[0]["next_car_pending"] is True
        assert seen[0]["multi_car_over_cap"] is True

    async def test_blocking_path_flag_off_heads_kwargs(self, flag_off: None) -> None:
        h, seen = _blocking(TS_TOOLS)
        await h.run("мені треба записати чотири машини")
        assert "next_car_pending" not in seen[0]
        assert "multi_car_over_cap" not in seen[0]

    @pytest.mark.parametrize("path", ["streaming", "blocking"])
    async def test_second_booking_counted_after_the_turn(self, flag_on: None, path: str) -> None:
        h = _harness(TS_TOOLS) if path == "streaming" else _blocking(TS_TOOLS)[0]
        h.session.cars_expected = 2
        _first_car_booked(h.session)
        h.pipeline._run_multi_car_step(_t("так"))
        h.session.record_fitting_booking(
            booking_id="guid-2",
            station_id="st-1",
            date="2026-10-28",
            time="10:20",
            vehicle="Skoda",
            color="сірий",
        )
        before = _counter("booked")
        await h.run("дякую")
        assert _counter("booked") == before + 1


def test_no_book_fitting_never_starts_a_car_even_with_a_count(flag_on: None) -> None:
    """Default-deny on the start itself, not only on the detector: a count
    that reached the session another way (a Redis-restored session) does not
    open a next car where the network has no `book_fitting` (R08)."""
    h = _harness(PK_TOOLS)
    h.session.cars_expected = 2
    _first_car_booked(h.session)
    h.pipeline._run_multi_car_step(_t("так"))
    assert h.session.fitting_car_number == 1
    assert h.session.fsm_state == "BOOK"
