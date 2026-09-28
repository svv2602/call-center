"""Sales for acceptance testers on a live line: ``config.sales_preview_callers``.

While ``sales_enabled`` is off, a caller whose number is in
``tenants.config.sales_preview_callers`` gets the sales scope for that call
(``NetworkPolicy.sales_preview``) and the 1C request of such a call is marked
TEST. Every other caller — and any empty or garbage list — gets exactly the
policy built without the list (the flag-off snapshot stays
``test_sales_scope_switch.py::TestFlagOffIsIncumbent``).
"""

from __future__ import annotations

import ast
import inspect
import logging
import uuid
from typing import Any
from unittest.mock import create_autospec, patch

import pytest

from scripts.configure_tenants import PROKOLESO_CONFIG_PATCH, TVOYA_SHINA_CONFIG_PATCH
from src.agent.network_policy import NetworkPolicy, mask_phone, phone_key
from src.agent.tool_result_compressor import AUDIT_ORDER_NUMBER_KEY, compress_tool_result
from src.agent.tools import SUBMIT_ORDER_TOOL
from src.core.call_session import CallSession
from src.main import _build_tool_router, _default_scenario, _scenario_tool_names
from src.onec_client.client import OneCClient
from src.store_client.client import StoreClient

TESTER = "+380000001234"
EXTENSION = "7770"
STRANGER = "+380000009999"
PATCHES = [PROKOLESO_CONFIG_PATCH, TVOYA_SHINA_CONFIG_PATCH]


def _cfg(patch_: dict[str, Any], callers: Any) -> dict[str, Any]:
    return {**patch_, "sales_preview_callers": callers}


# ── policy ─────────────────────────────────────────────────────────────────


class TestPreviewCaller:
    @pytest.mark.parametrize("patch_", PATCHES)
    @pytest.mark.parametrize(
        "listed", [TESTER, "380000001234", "0000001234", "+38 (000) 000-12-34"]
    )
    @pytest.mark.parametrize("caller", [TESTER, "380000001234", "0000001234"])
    def test_listed_number_in_any_format_gets_sales(
        self, patch_: dict[str, Any], listed: str, caller: str
    ) -> None:
        policy = NetworkPolicy.from_tenant_config(_cfg(patch_, [listed]), caller_phone=caller)
        assert policy.sales_enabled is True
        assert policy.sales_preview is True

    @pytest.mark.parametrize("patch_", PATCHES)
    def test_preview_policy_is_the_sales_on_policy_plus_the_marker(
        self, patch_: dict[str, Any]
    ) -> None:
        preview = NetworkPolicy.from_tenant_config(_cfg(patch_, [TESTER]), caller_phone=TESTER)
        sales_on = NetworkPolicy.from_tenant_config({**patch_, "sales_enabled": True})
        assert preview == NetworkPolicy(**{**sales_on.__dict__, "sales_preview": True})

    @pytest.mark.parametrize(
        "prod_config",
        [
            # prod 2026-09-28, before `configure_tenants --config-only`: no network_policy
            {"excluded_station_ids": ["st-1"], "agent_provider_override": "provider-x"},
            {},
            {"network_policy": "garbage"},
        ],
    )
    def test_preview_without_a_written_network_policy(self, prod_config: dict[str, Any]) -> None:
        policy = NetworkPolicy.from_tenant_config(
            {**prod_config, "sales_preview_callers": [EXTENSION]}, caller_phone=EXTENSION
        )
        assert policy == NetworkPolicy(sales_enabled=True, sales_preview=True)

    def test_internal_extension_matches_exactly(self) -> None:
        cfg = _cfg(TVOYA_SHINA_CONFIG_PATCH, [EXTENSION, "7771"])
        assert NetworkPolicy.from_tenant_config(cfg, caller_phone="7771").sales_preview
        assert NetworkPolicy.from_tenant_config(cfg, caller_phone=EXTENSION).sales_preview
        # a full number ending in the extension's digits is another caller
        assert not NetworkPolicy.from_tenant_config(cfg, caller_phone="380000007770").sales_enabled
        assert not NetworkPolicy.from_tenant_config(cfg, caller_phone="777").sales_enabled

    def test_sales_already_on_is_no_preview(self) -> None:
        cfg = {**_cfg(TVOYA_SHINA_CONFIG_PATCH, [TESTER]), "sales_enabled": True}
        policy = NetworkPolicy.from_tenant_config(cfg, caller_phone=TESTER)
        assert policy.sales_enabled is True
        assert policy.sales_preview is False  # a real sale — no TEST mark

    def test_scope_follows_the_preview(self) -> None:
        cfg = _cfg(PROKOLESO_CONFIG_PATCH, [TESTER])
        preview = NetworkPolicy.from_tenant_config(cfg, caller_phone=TESTER)
        assert _default_scenario(preview) == "sales"
        assert SUBMIT_ORDER_TOOL in (_scenario_tool_names("sales", preview) or set())

    def test_log_masks_the_number(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger="src.agent.network_policy"):
            NetworkPolicy.from_tenant_config(
                _cfg(TVOYA_SHINA_CONFIG_PATCH, [TESTER]), caller_phone=TESTER
            )
        assert "sales preview for caller ***1234" in caplog.text
        assert "000001234" not in caplog.text

    def test_helpers(self) -> None:
        assert phone_key("+380 67 123 45 67") == phone_key("0671234567") == "671234567"
        assert phone_key("7770") == "7770"
        assert phone_key("") is None and phone_key("abc") is None and phone_key(None) is None
        assert mask_phone("+380671234567") == "***4567"
        assert mask_phone(None) == "***"


class TestNotListedIsIncumbent:
    """Not in the list / empty / garbage → the policy built without the list."""

    @pytest.mark.parametrize("patch_", [*PATCHES, {}])
    @pytest.mark.parametrize(
        ("callers", "caller"),
        [
            ([TESTER], STRANGER),
            ([TESTER], None),
            ([TESTER], ""),
            ([TESTER], "unknown"),
            ([], TESTER),
            (None, TESTER),
            (TESTER, TESTER),  # a string, not a list
            ({"a": TESTER}, TESTER),
            ([None, 380000001234, "", "abc"], TESTER),  # no valid item
            (["380000001234"], "3800000012345"),  # 13 digits: last 9 differ
        ],
    )
    def test_equals_policy_without_list(
        self, patch_: dict[str, Any], callers: Any, caller: str | None
    ) -> None:
        without = NetworkPolicy.from_tenant_config(dict(patch_))
        got = NetworkPolicy.from_tenant_config(_cfg(patch_, callers), caller_phone=caller)
        assert got == without
        assert got.sales_enabled is False and got.sales_preview is False

    def test_garbage_list_warns_and_never_raises(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="src.agent.network_policy"):
            NetworkPolicy.from_tenant_config({"sales_preview_callers": 42}, caller_phone=TESTER)
        assert "sales_preview_callers" in caplog.text

    def test_no_caller_argument_is_the_old_signature(self) -> None:
        cfg = _cfg(TVOYA_SHINA_CONFIG_PATCH, [TESTER])
        assert NetworkPolicy.from_tenant_config(cfg) == NetworkPolicy.from_tenant_config(
            dict(TVOYA_SHINA_CONFIG_PATCH)
        )


# ── TEST mark on the 1C request ────────────────────────────────────────────


def _onec() -> Any:
    onec = create_autospec(OneCClient, instance=True)
    onec.create_order_1c.return_value = {"success": True}
    onec.get_pickup_points.return_value = {
        "data": [{"id": "pp-1", "point": "адреса", "point_type": "", "City": "Київ"}]
    }
    return onec


async def _submit(policy: NetworkPolicy) -> tuple[dict[str, Any], CallSession]:
    onec = _onec()
    store = create_autospec(StoreClient, instance=True)
    store.get_pickup_points.return_value = {"total": 0, "points": []}
    session = CallSession(uuid.uuid4())
    session.caller_phone = TESTER
    with (
        patch("src.main._onec_client", onec),
        patch("src.main._call_logger", None),
        patch("src.main._redis", None),
    ):
        router = _build_tool_router(session, store_client=store, network_policy=policy)
        await router.execute("get_pickup_points", {"city": "Київ"})
        result = await router.execute(
            SUBMIT_ORDER_TOOL,
            {
                "items": [{"product_id": "sku-1", "quantity": 4}],
                "delivery_type": "pickup",
                "pickup_point_id": "pp-1",
                "recipient_name": "Отримувач Тестовий",
                "payment_method": "cod",
            },
        )
    assert result["status"] == "request_created"
    # the audit row carries the 1C number; the model never sees it
    assert result[AUDIT_ORDER_NUMBER_KEY] == session.order_id
    assert AUDIT_ORDER_NUMBER_KEY not in compress_tool_result(SUBMIT_ORDER_TOOL, result)
    assert session.order_id not in compress_tool_result(SUBMIT_ORDER_TOOL, result)
    onec.create_order_1c.assert_awaited_once()
    return onec.create_order_1c.await_args.kwargs, session


class TestTestMark:
    @pytest.mark.asyncio
    async def test_preview_request_is_marked_test(self) -> None:
        policy = NetworkPolicy.from_tenant_config(
            _cfg(TVOYA_SHINA_CONFIG_PATCH, [TESTER]), caller_phone=TESTER
        )
        kw, session = await _submit(policy)
        assert kw["order_number"] == "AI-TEST-1"
        assert kw["customer_name"] == "TEST Отримувач Тестовий"
        assert session.order_id == "AI-TEST-1"

    @pytest.mark.asyncio
    async def test_real_sales_request_is_not_marked(self) -> None:
        policy = NetworkPolicy.from_tenant_config(
            {**_cfg(TVOYA_SHINA_CONFIG_PATCH, [TESTER]), "sales_enabled": True},
            caller_phone=TESTER,
        )
        kw, _ = await _submit(policy)
        assert kw["order_number"] == "AI-1"
        assert kw["customer_name"] == "Отримувач Тестовий"


class TestTestMarkOnStoreFallback:
    """1C down → the request goes draft → delivery → confirm via the Store API."""

    @staticmethod
    async def _fallback(policy: NetworkPolicy) -> Any:
        onec = _onec()
        onec.create_order_1c.side_effect = RuntimeError("1C down")
        store = create_autospec(StoreClient, instance=True)
        store.get_pickup_points.return_value = {"total": 0, "points": []}
        store.create_order.return_value = {"order_id": "o-1"}
        store.update_delivery.return_value = {}
        store.confirm_order.return_value = {"order_id": "o-1", "status": "confirmed"}
        session = CallSession(uuid.uuid4())
        session.caller_phone = TESTER
        with (
            patch("src.main._onec_client", onec),
            patch("src.main._call_logger", None),
            patch("src.main._redis", None),
        ):
            router = _build_tool_router(session, store_client=store, network_policy=policy)
            await router.execute("get_pickup_points", {"city": "Київ"})
            await router.execute(
                SUBMIT_ORDER_TOOL,
                {
                    "items": [{"product_id": "sku-1", "quantity": 4}],
                    "delivery_type": "pickup",
                    "pickup_point_id": "pp-1",
                    "recipient_name": "Отримувач Тестовий",
                    "payment_method": "cod",
                },
            )
        store.create_order.assert_awaited_once()
        return store

    @pytest.mark.asyncio
    async def test_preview_fallback_order_is_marked_test(self) -> None:
        policy = NetworkPolicy.from_tenant_config(
            _cfg(TVOYA_SHINA_CONFIG_PATCH, [TESTER]), caller_phone=TESTER
        )
        store = await self._fallback(policy)
        assert store.create_order.await_args.kwargs["customer_name"] == "TEST Отримувач Тестовий"
        assert store.confirm_order.await_args.kwargs["customer_name"] == "TEST Отримувач Тестовий"

    @pytest.mark.asyncio
    async def test_real_sales_fallback_order_is_not_marked(self) -> None:
        policy = NetworkPolicy.from_tenant_config(
            {**_cfg(TVOYA_SHINA_CONFIG_PATCH, [TESTER]), "sales_enabled": True},
            caller_phone=TESTER,
        )
        store = await self._fallback(policy)
        assert store.create_order.await_args.kwargs["customer_name"] == "Отримувач Тестовий"


# ── wiring in handle_call ──────────────────────────────────────────────────


def test_live_call_passes_the_caller_to_the_policy() -> None:
    """handle_call cannot be unit-run; pin the call site."""
    import src.main as main

    tree = ast.parse(inspect.getsource(main.handle_call))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "from_tenant_config"
    ]
    assert len(calls) == 1
    caller_kw = next((kw.value for kw in calls[0].keywords if kw.arg == "caller_phone"), None)
    assert caller_kw is not None, "caller_phone not passed to NetworkPolicy.from_tenant_config"
    names = {n.id for n in ast.walk(caller_kw) if isinstance(n, ast.Name)}
    assert "caller_id" in names  # resolved before the policy; session.caller_phone is set later
