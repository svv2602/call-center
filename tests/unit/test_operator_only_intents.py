"""Operator-only requests: the code transfers the call itself (wave 2-D).

R01 — military (УБД) discount, a VAT invoice for a company, a company code
(ЄДРПОУ) → operator; R02 — at Твоя шина, buying tyres and fitting them in one
call → operator (ПроКолесо has no fitting: that refusal is `network_facts`');
R04 — at once, with what the call collected in the ``summary``; R11 — nothing
is armed with sales off.

The loop tests run the real tool router (`src.main._build_tool_router`) with
the real `transfer_to_operator` executor and assert WHOSE transfer it was: the
log line of this path, the counter, and the code's phrase — the model may call
`transfer_to_operator` by itself, and the default-deny guard sits next door.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, create_autospec

import pytest

import src.main as main_module
from scripts.configure_tenants import PROKOLESO_CONFIG_PATCH, TVOYA_SHINA_CONFIG_PATCH
from src.agent.agent import LLMAgent
from src.agent.network_policy import NetworkPolicy
from src.agent.operator_only_intents import (
    COMPANY_CODE,
    MILITARY,
    ORDER_PLUS_FITTING,
    PHRASES,
    VAT_INVOICE,
    OperatorOnlyGate,
    detect_operator_only_intent,
)
from src.agent.streaming_loop import StreamingAgentLoop
from src.core.asterisk_ami import AsteriskAMIClient
from src.core.call_session import CallSession
from src.core.pipeline import merge_tire_query
from src.llm.models import LLMResponse, Usage
from src.llm.router import LLMRouter
from src.monitoring.metrics import operator_only_transfer_total
from src.store_client.client import StoreClient
from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection
from tests.unit.mocks.mock_tts import MockTTSEngine
from tests.unit.test_vehicle_lookup_gate import _Snapshotting, _stream_events

#: No ``search_tires``: a complete request would make the code search first
#: (`tire_search_gate`) — not this wave's path; the summary reads the query.
_TOOLS = [
    {"name": n, "description": "", "input_schema": {"type": "object"}}
    for n in ("transfer_to_operator", "get_vehicle_tire_sizes", "create_callback_request")
]

_LOG = "src.agent.operator_only_intents"


def _policy(patch: dict[str, Any], sales: bool = True) -> NetworkPolicy:
    return NetworkPolicy.from_tenant_config({**patch, "sales_enabled": sales})


TS = _policy(TVOYA_SHINA_CONFIG_PATCH)
PK = _policy(PROKOLESO_CONFIG_PATCH)
TS_OFF = _policy(TVOYA_SHINA_CONFIG_PATCH, sales=False)
PK_OFF = _policy(PROKOLESO_CONFIG_PATCH, sales=False)


# ── The predicate ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("я УБД, є знижка?", MILITARY),
        ("у мене статус убд", MILITARY),
        ("а для військових є щось?", MILITARY),
        ("я військовий, скидка є?", MILITARY),
        ("є знижки для ЗСУ?", MILITARY),
        ("у мене посвідчення учасника бойових дій", MILITARY),
        ("скидка военным есть?", MILITARY),
        ("потрібен рахунок з ПДВ", VAT_INVOICE),
        ("нам з ПДВ треба", VAT_INVOICE),
        ("рахунок на фірму виставите?", VAT_INVOICE),
        ("оплата від підприємства по безготівці", VAT_INVOICE),
        ("мы плательщики НДС, нужен счет", VAT_INVOICE),
        ("счет на ООО можно?", VAT_INVOICE),
        ("ось наш ЄДРПОУ", COMPANY_CODE),
        ("едрпоу продиктую", COMPANY_CODE),
        ("код фірми скажу", COMPANY_CODE),
    ],
)
def test_both_networks_transfer_r01(text: str, intent: str) -> None:
    assert detect_operator_only_intent(text, TS) == intent
    assert detect_operator_only_intent(text, PK) == intent


@pytest.mark.parametrize(
    "text",
    [
        "хочу купити шини і одразу поставити",
        "купити резину і встановити у вас",
        "замовити шини з монтажем",
        "хочу придбати гуму і перевзутися",
        "купить резину и сразу установить",
    ],
)
def test_order_plus_fitting_tshina_only_r02(text: str) -> None:
    assert detect_operator_only_intent(text, TS) == ORDER_PLUS_FITTING
    assert detect_operator_only_intent(text, PK) is None


def test_order_plus_fitting_across_turns() -> None:
    assert detect_operator_only_intent("а можна одразу і поставити?", TS, searched=True) == (
        ORDER_PLUS_FITTING
    )
    assert detect_operator_only_intent("а шини у вас купити можна?", TS, in_fitting=True) == (
        ORDER_PLUS_FITTING
    )
    assert detect_operator_only_intent("а можна одразу і поставити?", TS) is None
    assert detect_operator_only_intent("а можна одразу і поставити?", PK, searched=True) is None


@pytest.mark.parametrize(
    "text",
    [
        "безготівка карткою можна?",
        "ПДВ включено в ціну?",
        "ціна з ПДВ?",
        "відправте на військову частину А1234",
        "доставка на Нову пошту, військова частина 1234",
        "код з СМС 1234",
        "мій чоловік військовий, а шини мені",
        "бонусна картка в мене є",
        "55 профіль, а я поставлю 60",
        "хочу купити шини 205/55 R16",
        "оплата карткою на сайті",
        "за рахунок клієнта доставка?",
        "",
    ],
)
def test_default_deny_negatives(text: str) -> None:
    for policy in (TS, PK):
        assert detect_operator_only_intent(text, policy) is None
        assert detect_operator_only_intent(text, policy, searched=True) is None


@pytest.mark.parametrize(
    "text",
    [
        "хочу записатися на шиномонтаж",
        "купив шини, треба поставити",
        "замовити шиномонтаж на завтра",
    ],
)
def test_a_fitting_booking_alone_is_not_an_order(text: str) -> None:
    # The fitting booking is the bot's own flow — also after a `search_tires`:
    # a caller who asked a price and then books with his own tyres orders
    # nothing (wave 2-D acceptance; the bare «fits after a search» branch sent
    # him to the operator instead of the booking).
    assert detect_operator_only_intent(text, TS) is None
    assert detect_operator_only_intent(text, TS, searched=True) is None


@pytest.mark.parametrize(
    "text",
    [
        "давайте тоді ці шини і одразу на монтаж",
        "а їх можна у вас і поставити?",
        "а можна заодно і перевзути?",
    ],
)
def test_fitting_the_offered_tyres_is_order_plus_fitting(text: str) -> None:
    assert detect_operator_only_intent(text, TS, searched=True) == ORDER_PLUS_FITTING
    assert detect_operator_only_intent(text, TS) is None


@pytest.mark.parametrize(
    "text",
    [
        "запишіть мене на перевзування одразу, шини свої",
        "а можна одразу записатись на шиномонтаж, у мене шини на зберіганні",
        "сразу запишите на шиномонтаж, резина своя",
    ],
)
def test_own_tyres_after_a_search_stay_a_booking(text: str) -> None:
    assert detect_operator_only_intent(text, TS, searched=True) is None


@pytest.mark.parametrize("text", ["я УБД, є знижка?", "потрібен рахунок з ПДВ", "ЄДРПОУ"])
def test_sales_off_never_fires_r11(text: str) -> None:
    assert detect_operator_only_intent(text, TS_OFF) is None
    assert detect_operator_only_intent(text, PK_OFF) is None
    assert detect_operator_only_intent(text, None) is None


def test_gate_once_per_call() -> None:
    gate = OperatorOnlyGate(TS)
    kw: dict[str, Any] = {"tools": _TOOLS, "history": [], "tools_called": None, "in_fitting": False}
    assert gate.plan("я УБД", **kw) == MILITARY
    assert gate.plan("я УБД", **kw) is None
    assert gate.plan("ЄДРПОУ", **kw) is None


def test_gate_needs_the_transfer_tool() -> None:
    gate = OperatorOnlyGate(TS)
    tools = [t for t in _TOOLS if t["name"] != "transfer_to_operator"]
    assert gate.plan("я УБД", tools=tools, history=[], tools_called=None, in_fitting=False) is None


# ── Both loops, the real executor ─────────────────────────────────────


class _Call:
    """The real tool router of a call, with an AMI that accepts or refuses."""

    def __init__(self, policy: NetworkPolicy, monkeypatch: pytest.MonkeyPatch, *, ami_ok: bool):
        self.session = CallSession(uuid.uuid4())
        ami = create_autospec(AsteriskAMIClient, instance=True)
        ami.redirect.return_value = ami_ok
        redis = MagicMock(spec=["get"])
        redis.get = AsyncMock(return_value=b"SIP/trunk1058-00000042")

        async def _publish(_event: str, _data: dict[str, Any] | None = None) -> None:
            return None

        monkeypatch.setattr(main_module, "_ami_client", ami)
        monkeypatch.setattr(main_module, "_redis", redis)
        monkeypatch.setattr(main_module, "publish_event", _publish)
        self.router = main_module._build_tool_router(
            self.session,
            store_client=create_autospec(StoreClient, instance=True),
            network_policy=policy,
        )
        self.audited: list[tuple[str, dict[str, Any], Any]] = []

        async def _hook(name: str, args: dict[str, Any], result: Any, *_: Any) -> None:
            self.audited.append((name, dict(args), result))

        self.router.set_execute_hook(_hook)

    def transfers(self) -> list[tuple[dict[str, Any], Any]]:
        return [(a, r) for n, a, r in self.audited if n == "transfer_to_operator"]


def _stream(call: _Call, policy: NetworkPolicy, turns: list[str], reply: str = "Добре."):
    history: list[dict[str, Any]] = []
    conn = MockAudioSocketConnection()
    loop = StreamingAgentLoop(
        llm_router=_Snapshotting(_stream_events([], reply)),
        tool_router=call.router,
        tts=MockTTSEngine(),
        conn=conn,
        barge_in_event=asyncio.Event(),
        tools=_TOOLS,
        system_prompt="Test system prompt",
        network_policy=policy,
    )
    out: list[tuple[str, int]] = []
    for text in turns:
        llm = _Snapshotting(_stream_events([], reply))
        loop._llm_router = llm
        progress = None
        if policy.sales_enabled:
            call.session.tire_query = merge_tire_query(call.session.tire_query, text)
            progress = dict(call.session.tire_query) or None
        res = asyncio.run(loop.run_turn(text, history, tire_progress=progress))
        out.append((res.spoken_text, len(llm.seen)))
    return out, history


def _text(call: _Call, policy: NetworkPolicy, turns: list[str], reply: str = "Добре."):
    history: list[dict[str, Any]] = []
    agent = LLMAgent(
        api_key="test-key",
        system_prompt="base",
        llm_router=create_autospec(LLMRouter, instance=True),
        tool_router=call.router,
        tools=_TOOLS,
        network_policy=policy,
    )
    out: list[tuple[str, int]] = []
    for text in turns:
        seen: list[Any] = []

        async def _complete(_task: Any, messages: Any, *_a: Any, _s=seen, **_k: Any):
            _s.append(copy.deepcopy(messages))
            return LLMResponse(text=reply, usage=Usage(1, 1), provider="test")

        agent._llm_router.complete = AsyncMock(side_effect=_complete)
        spoken, history = asyncio.run(agent.process_message(text, history))
        out.append((spoken, len(seen)))
    return out, history


LOOPS = pytest.mark.parametrize("run", [_stream, _text], ids=["streaming", "text"])


def _count(intent: str) -> float:
    return operator_only_transfer_total.labels(intent=intent)._value.get()


@LOOPS
@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("я УБД, є знижка?", MILITARY),
        ("потрібен рахунок з ПДВ", VAT_INVOICE),
        ("ЄДРПОУ", COMPANY_CODE),
    ],
)
@pytest.mark.parametrize("policy", [TS, PK], ids=["tshina", "prokoleso"])
def test_code_transfers_in_the_same_turn(
    run: Any, text: str, intent: str, policy: NetworkPolicy, monkeypatch, caplog
) -> None:
    call = _Call(policy, monkeypatch, ami_ok=True)
    before = _count(intent)
    with caplog.at_level(logging.INFO, logger=_LOG):
        out, history = run(call, policy, [text])
    assert out == [(PHRASES[intent], 0)]  # the code's phrase, no LLM round
    assert [a["reason"] for a, _ in call.transfers()] == ["complex_question"]
    assert call.session.transfer_redirect_initiated()
    assert _count(intent) == before + 1
    assert f"Operator-only intent={intent}" in caplog.text
    assert history[-1] == {
        "role": "assistant",
        "content": [{"type": "text", "text": PHRASES[intent]}],
    }


@LOOPS
def test_summary_carries_what_the_call_collected_r04(run: Any, monkeypatch) -> None:
    call = _Call(TS, monkeypatch, ami_ok=True)
    run(call, TS, ["потрібні зимові шини 205/55 R16 Michelin, чотири штуки", "я УБД, є знижка?"])
    ((args, _),) = call.transfers()
    summary = args["summary"]
    assert "205/55 R16" in summary
    assert "michelin" in summary.lower()
    assert "Кількість: 4" in summary
    assert "зимові" in summary
    assert "УБД" in summary


@LOOPS
def test_order_plus_fitting_tshina_transfers_prokoleso_does_not(run: Any, monkeypatch) -> None:
    text = "хочу купити шини і одразу поставити"
    ts_call = _Call(TS, monkeypatch, ami_ok=True)
    out, _ = run(ts_call, TS, [text])
    assert out == [(PHRASES[ORDER_PLUS_FITTING], 0)]
    assert len(ts_call.transfers()) == 1
    pk_call = _Call(PK, monkeypatch, ami_ok=True)
    out, _ = run(pk_call, PK, [text])
    assert pk_call.transfers() == []
    assert out[0][0] != PHRASES[ORDER_PLUS_FITTING]


@LOOPS
@pytest.mark.parametrize("policy", [TS_OFF, PK_OFF], ids=["tshina", "prokoleso"])
def test_sales_off_is_untouched_r11(run: Any, policy: NetworkPolicy, monkeypatch) -> None:
    call = _Call(policy, monkeypatch, ami_ok=True)
    out, _ = run(call, policy, ["я УБД, є знижка?"])
    assert call.transfers() == []
    assert out == [("Добре.", 1)]


@LOOPS
def test_a_refused_transfer_goes_to_the_model_once_per_call(run: Any, monkeypatch) -> None:
    call = _Call(TS, monkeypatch, ami_ok=False)
    out, _ = run(call, TS, ["я УБД, є знижка?", "мій номер 0671234567, я УБД"])
    # refused: the model speaks the executor's message, the phrase opened it
    assert out[0][1] == 1
    assert out[0][0].startswith(PHRASES[MILITARY])
    assert len(call.transfers()) == 1  # the callback turn does not re-transfer
    assert out[1][1] == 1
    assert not call.session.transfer_redirect_initiated()


@LOOPS
def test_a_refused_transfer_is_not_swallowed_by_the_vague_buy_question(
    run: Any, monkeypatch
) -> None:
    # «хочу купити резину» alone is `vague_tyre_buy`'s whole-reply question;
    # after a refused transfer the caller must hear the executor's message.
    # No code keeps them apart: `names_a_car` is default-deny and reads
    # «УБД / ПДВ / ЄДРПОУ» as car words, so the vague gate stands down. This
    # pins that, should the car reader ever learn those words.
    call = _Call(TS, monkeypatch, ami_ok=False)
    out, _ = run(call, TS, ["хочу купити резину, я УБД, є знижка?"])
    assert len(call.transfers()) == 1
    assert out[0][1] == 1
    assert out[0][0].startswith(PHRASES[MILITARY])
