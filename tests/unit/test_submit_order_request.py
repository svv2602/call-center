"""Sales order in one call: ``submit_order_request`` (wave 2-D).

Goldset №2/№3: under sales the model stopped after ``create_order_draft`` —
«менеджер зателефонує» with no request (ТШ) or a transfer (ПК); prompt rules
did not hold. Under ``sales_enabled`` the three-step chain is hidden and one
tool hands the request over; its completeness is checked in code
(default-deny, the first missing field comes back as a question and nothing
reaches 1C). With sales off the chain stays — the flag-off snapshot is
``test_sales_scope_switch.py::TestFlagOffIsIncumbent``.

The handler is a closure inside ``_build_tool_router``: every test drives the
**registered** handler through the real router.
"""

from __future__ import annotations

import json
import re
import uuid
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest

from scripts.configure_tenants import (
    PROKOLESO_CONFIG_PATCH,
    PROKOLESO_ENABLED_TOOLS,
    TVOYA_SHINA_CONFIG_PATCH,
)
from src.agent.network_policy import NetworkPolicy
from src.agent.prompts import ORDER_REQUEST_CREATED_TEXT, ORDER_REQUEST_FAILED_TEXT
from src.agent.tools import (
    ALL_TOOLS,
    ORDER_CHAIN_TOOLS,
    ORDER_FIELD_LABELS,
    SUBMIT_ORDER_TOOL,
    filter_tools_by_state,
)
from src.core.call_session import CallSession
from src.main import _SCENARIO_TOOLS, _build_tool_router, _scenario_tool_names
from src.onec_client.client import OneCClient
from src.sandbox.agent_runner import _register_live_tools
from src.sandbox.mock_tools import MOCK_RESPONSES, build_mock_tool_router
from src.store_client.client import StoreClient

TSH_ON = NetworkPolicy.from_tenant_config({**TVOYA_SHINA_CONFIG_PATCH, "sales_enabled": True})
PK_ON = NetworkPolicy.from_tenant_config({**PROKOLESO_CONFIG_PATCH, "sales_enabled": True})
TSH_OFF = NetworkPolicy.from_tenant_config({**TVOYA_SHINA_CONFIG_PATCH, "sales_enabled": False})
PK_OFF = NetworkPolicy.from_tenant_config({**PROKOLESO_CONFIG_PATCH, "sales_enabled": False})
ON = [TSH_ON, PK_ON]
OFF = [TSH_OFF, PK_OFF]

CALLER = "+380000000001"
POINT = "pp-offered"


def _onec(create: Any = None) -> Any:
    onec = create_autospec(OneCClient, instance=True)
    if isinstance(create, BaseException):
        onec.create_order_1c.side_effect = create
    else:
        onec.create_order_1c.return_value = create or {"success": True}
    onec.get_pickup_points.return_value = {
        "data": [{"id": POINT, "point": "адреса пункту", "point_type": "", "City": "Київ"}]
    }
    return onec


def _store() -> Any:
    store = create_autospec(StoreClient, instance=True)
    store.get_pickup_points.return_value = {"total": 0, "points": []}
    return store


def _pickup_args(**over: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "items": [{"product_id": "sku-under-test", "quantity": 2}],
        "delivery_type": "pickup",
        "pickup_point_id": POINT,
        "recipient_name": "Отримувач Тестовий",
        "payment_method": "cod",
    }
    args.update(over)
    return args


def _delivery_args(**over: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "items": [{"product_id": "sku-under-test", "quantity": 1}],
        "delivery_type": "delivery",
        "city": "Київ",
        "address": "відділення перевізника",
        "recipient_name": "Отримувач Тестовий",
        "payment_method": "card_on_delivery",
    }
    args.update(over)
    return args


async def _submit(
    args: dict[str, Any],
    *,
    onec: Any,
    store: Any,
    caller_phone: str | None = CALLER,
    offer_points: bool = True,
) -> tuple[Any, CallSession]:
    session = CallSession(uuid.uuid4())
    session.caller_phone = caller_phone
    with (
        patch("src.main._onec_client", onec),
        patch("src.main._call_logger", None),
        patch("src.main._redis", None),
    ):
        router = _build_tool_router(session, store_client=store)
        if offer_points:
            await router.execute("get_pickup_points", {"city": "Київ"})
        result = await router.execute(SUBMIT_ORDER_TOOL, dict(args))
    return result, session


# ── completeness: default-deny, one field at a time ──────────────────────


_INCOMPLETE: list[tuple[str, dict[str, Any], str]] = [
    ("no items", _pickup_args(items=None), "items"),
    ("empty items", _pickup_args(items=[]), "items"),
    ("zero quantity", _pickup_args(items=[{"product_id": "sku", "quantity": 0}]), "items"),
    ("no product id", _pickup_args(items=[{"product_id": " ", "quantity": 1}]), "items"),
    ("bool quantity", _pickup_args(items=[{"product_id": "sku", "quantity": True}]), "items"),
    ("no delivery type", _pickup_args(delivery_type=None), "delivery_type"),
    ("unknown delivery type", _pickup_args(delivery_type="courier"), "delivery_type"),
    ("pickup without point", _pickup_args(pickup_point_id=""), "pickup_point_id"),
    ("point not offered", _pickup_args(pickup_point_id="pp-invented"), "pickup_point_id"),
    ("delivery without city", _delivery_args(city=""), "city"),
    ("delivery without address", _delivery_args(address="  "), "address"),
    ("no recipient", _pickup_args(recipient_name=""), "recipient_name"),
    ("no payment", _pickup_args(payment_method=None), "payment_method"),
    ("payment not a 1C code", _pickup_args(payment_method="installments"), "payment_method"),
]


class TestIncompleteRequestIsAQuestion:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("args", "field"), [(a, f) for _, a, f in _INCOMPLETE], ids=[c for c, _, _ in _INCOMPLETE]
    )
    async def test_missing_field_is_asked_and_nothing_is_sent(
        self, args: dict[str, Any], field: str
    ) -> None:
        onec, store = _onec(), _store()
        result, session = await _submit(args, onec=onec, store=store)

        assert result["status"] == "missing_field"
        assert result["field"] == field
        assert ORDER_FIELD_LABELS[field] in result["message"]
        assert "error" not in result  # a question, not a failure the bot transfers on
        onec.create_order_1c.assert_not_awaited()
        store.create_order.assert_not_awaited()
        store.confirm_order.assert_not_awaited()
        assert session.order_id is None

    @pytest.mark.asyncio
    async def test_point_is_accepted_only_after_get_pickup_points(self) -> None:
        onec = _onec()
        result, _ = await _submit(_pickup_args(), onec=onec, store=_store(), offer_points=False)
        assert result["field"] == "pickup_point_id"
        onec.create_order_1c.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_phone_and_no_caller_id_is_asked(self) -> None:
        onec = _onec()
        result, _ = await _submit(_pickup_args(), onec=onec, store=_store(), caller_phone=None)
        assert result["field"] == "phone"
        onec.create_order_1c.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_one_field_at_a_time(self) -> None:
        # everything but the items is missing too — only the first is named
        result, _ = await _submit({"items": []}, onec=_onec(), store=_store())
        assert result["field"] == "items"
        others = [lbl for f, lbl in ORDER_FIELD_LABELS.items() if f != "items"]
        assert not [lbl for lbl in others if lbl in result["message"]]


# ── complete request: the same path as confirm_order ───────────────────────


class TestCompleteRequest:
    @pytest.mark.asyncio
    async def test_pickup_request_goes_to_1c_with_the_collected_fields(self) -> None:
        onec = _onec({"success": True, "number": "onec-internal-number"})
        result, session = await _submit(_pickup_args(), onec=onec, store=_store())

        onec.create_order_1c.assert_awaited_once()
        kw = onec.create_order_1c.await_args.kwargs
        assert kw["items"] == [{"product_id": "sku-under-test", "quantity": 2}]
        assert kw["delivery_type"] == "pickup"
        assert kw["pickup_point_id"] == POINT
        assert kw["customer_name"] == "Отримувач Тестовий"
        assert kw["payment_method"] == "cod"
        assert kw["customer_phone"] == CALLER  # caller id when no other number named
        assert result["status"] == "request_created"
        assert result["message"] == ORDER_REQUEST_CREATED_TEXT
        # the AI-N number is logged and kept in the session, never shown to the LLM
        assert session.order_id
        # The number is in the raw result for the audit row only; what the
        # model reads (`compress_tool_result`) never names it.
        from src.agent.tool_result_compressor import compress_tool_result

        dumped = compress_tool_result(SUBMIT_ORDER_TOOL, result, sales_enabled=True)
        assert session.order_id not in dumped and "onec-internal-number" not in dumped
        assert "onec-internal-number" not in json.dumps(result, ensure_ascii=False)

    @pytest.mark.asyncio
    async def test_delivery_request_carries_city_address_and_named_phone(self) -> None:
        onec = _onec()
        result, _ = await _submit(_delivery_args(phone="+380000000002"), onec=onec, store=_store())
        kw = onec.create_order_1c.await_args.kwargs
        assert (kw["delivery_city"], kw["delivery_address"]) == ("Київ", "відділення перевізника")
        assert kw["customer_phone"] == "+380000000002"
        assert kw["payment_method"] == "card_on_delivery"
        assert result["status"] == "request_created"

    @pytest.mark.asyncio
    async def test_1c_and_store_failure_is_request_failed(self) -> None:
        store = _store()
        store.create_order.side_effect = RuntimeError("store down")
        result, session = await _submit(
            _pickup_args(), onec=_onec(RuntimeError("1C down")), store=store
        )
        assert result["status"] == "request_failed"
        assert result["message"] == ORDER_REQUEST_FAILED_TEXT
        assert session.order_id is None

    @pytest.mark.asyncio
    async def test_1c_failure_falls_back_to_the_store_chain(self) -> None:
        store = _store()
        store.create_order.return_value = {"order_id": "store-order"}
        store.update_delivery.return_value = {"order_id": "store-order"}
        store.confirm_order.return_value = {"order_id": "store-order", "order_number": "N"}
        result, _ = await _submit(_pickup_args(), onec=_onec(RuntimeError("1C down")), store=store)
        assert result["status"] == "request_created"
        assert store.update_delivery.await_args.kwargs["order_id"] == "store-order"
        assert store.confirm_order.await_args.kwargs["order_id"] == "store-order"
        assert store.confirm_order.await_args.kwargs["payment_method"] == "cod"

    @pytest.mark.asyncio
    async def test_store_without_an_order_id_is_request_failed(self) -> None:
        store = _store()
        store.create_order.return_value = {"order_id": None}
        result, _ = await _submit(_pickup_args(), onec=_onec(RuntimeError("1C down")), store=store)
        assert result["status"] == "request_failed"
        store.confirm_order.assert_not_awaited()


# ── which tools the model sees ──────────────────────────────────────────────


class TestToolSets:
    @pytest.mark.parametrize("pol", ON)
    def test_sales_sees_submit_not_the_chain(self, pol: NetworkPolicy) -> None:
        names = _scenario_tool_names("sales", pol)
        assert names is not None
        assert SUBMIT_ORDER_TOOL in names
        assert not names & ORDER_CHAIN_TOOLS
        assert not _SCENARIO_TOOLS["sales"] & ORDER_CHAIN_TOOLS

    @pytest.mark.parametrize("pol", ON)
    def test_ivr_scenario_under_sales_orders_through_submit(self, pol: NetworkPolicy) -> None:
        # an IVR scenario gets the sales order module under sales — so its tools follow
        names = _scenario_tool_names("tire_search", pol)
        assert names is not None
        assert SUBMIT_ORDER_TOOL in names and not names & ORDER_CHAIN_TOOLS

    @pytest.mark.parametrize("pol", OFF)
    def test_sales_off_keeps_the_chain_and_never_shows_submit(self, pol: NetworkPolicy) -> None:
        for scenario in _SCENARIO_TOOLS:
            if scenario == "sales":
                continue
            names = _scenario_tool_names(scenario, pol) or set()
            assert SUBMIT_ORDER_TOOL not in names, scenario
        assert (_scenario_tool_names("tire_search", PK_OFF) or set()) >= ORDER_CHAIN_TOOLS

    def test_accepted_request_hides_submit(self) -> None:
        tools = [t for t in ALL_TOOLS if t["name"] in {SUBMIT_ORDER_TOOL, "search_tires"}]
        left = {t["name"] for t in filter_tools_by_state(tools, order_stage="confirmed")}
        assert left == {"search_tires"}
        kept = {t["name"] for t in filter_tools_by_state(tools, order_stage=None)}
        assert SUBMIT_ORDER_TOOL in kept

    def test_schema_has_no_literal_numbers_or_ids(self) -> None:
        tool = next(t for t in ALL_TOOLS if t["name"] == SUBMIT_ORDER_TOOL)
        text = json.dumps(tool, ensure_ascii=False).replace("1С", "")  # the ERP's name
        assert not re.search(r"\d", text)
        assert set(tool["input_schema"]["properties"]["payment_method"]["enum"]) == {
            "cod",
            "online",
            "card_on_delivery",
        }


# ── wiring: the live router, the sandbox ──────────────────────────────────


class TestWiring:
    @pytest.mark.asyncio
    async def test_live_router_registers_submit(self) -> None:
        # an unregistered tool never reaches the 1C mock
        onec = _onec()
        await _submit(_pickup_args(), onec=onec, store=_store())
        onec.create_order_1c.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sandbox_mock_answers_like_a_request(self) -> None:
        router = build_mock_tool_router()
        result = await router.execute(SUBMIT_ORDER_TOOL, _pickup_args())
        assert result == MOCK_RESPONSES[SUBMIT_ORDER_TOOL]
        assert result["status"] == "request_created"

    @pytest.mark.asyncio
    async def test_sandbox_live_checks_completeness_and_sends_nothing(self) -> None:
        router = build_mock_tool_router()
        onec = _onec()
        _register_live_tools(router, onec_client=onec, redis_client=None, network="Tshina")
        missing = await router.execute(SUBMIT_ORDER_TOOL, _pickup_args())
        assert missing["field"] == "pickup_point_id"  # nothing offered yet
        await router.execute("get_pickup_points", {"city": "Київ"})
        # the sandbox has no caller id — the phone is a field like any other
        assert (await router.execute(SUBMIT_ORDER_TOOL, _pickup_args()))["field"] == "phone"
        done = await router.execute(SUBMIT_ORDER_TOOL, _pickup_args(phone="+380000000003"))
        assert done["status"] == "request_created"
        onec.create_order_1c.assert_not_awaited()


async def _sandbox_agent(config: dict[str, Any], enabled_tools: list[str]) -> Any:
    from src.sandbox.agent_runner import create_sandbox_agent

    engine = MagicMock(spec=["begin"])
    conn = MagicMock(spec=["execute"])
    empty = MagicMock(spec=["first", "__iter__", "scalar"])
    empty.first.return_value = None
    empty.__iter__.return_value = iter([])
    conn.execute = AsyncMock(return_value=empty)

    @asynccontextmanager
    async def _begin() -> Any:
        yield conn

    engine.begin = _begin
    tenant = {"network_id": "Tshina", "enabled_tools": enabled_tools, "config": config}
    with patch("src.sandbox.agent_runner.get_settings") as settings:
        settings.return_value = MagicMock(
            anthropic=MagicMock(api_key="k", model="claude-sonnet-4-5-20250929"),
        )
        return await create_sandbox_agent(engine, tool_mode="mock", tenant=tenant)


class TestSandboxToolSet:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("enabled", [[], list(PROKOLESO_ENABLED_TOOLS)])
    async def test_sales_sandbox_gets_submit_not_the_chain(self, enabled: list[str]) -> None:
        agent = await _sandbox_agent({**PROKOLESO_CONFIG_PATCH, "sales_enabled": True}, enabled)
        names = {t["name"] for t in agent._tools}
        assert SUBMIT_ORDER_TOOL in names
        assert not names & ORDER_CHAIN_TOOLS

    @pytest.mark.asyncio
    async def test_sales_off_sandbox_keeps_the_chain(self) -> None:
        agent = await _sandbox_agent(dict(PROKOLESO_CONFIG_PATCH), [])
        names = {t["name"] for t in agent._tools}
        assert SUBMIT_ORDER_TOOL not in names
        assert names >= ORDER_CHAIN_TOOLS
