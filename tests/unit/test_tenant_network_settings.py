"""«Умови мережі» — the admin write path of ``tenants.config`` network policy.

Invariants:
- the form endpoint merges: every ``config`` key other than ``sales_enabled``
  / ``network_policy`` survives a save; ``network_policy`` is replaced whole;
- the write path is strict: every value outside the enums of
  ``src.agent.network_policy`` is a 422 naming it (the lenient runtime parser
  would silently turn it into "promise nothing");
- a written policy that leaves out a service the tenant has tools for is a
  422 unless confirmed — an empty ``{}`` must not silently cut fitting;
- the sandbox sees the tenant's policy: the tenant SELECT carries ``config``
  and the sandbox ``search_tires`` ranks by the policy like the live router.
"""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.agent.network_policy import (
    BANK_LABELS,
    DELIVERY_MODES,
    PAYMENT_LABELS,
    RECOMMEND_COUNT_MAX,
    RECOMMEND_COUNT_MIN,
    SERVICE_LABELS,
    NetworkPolicy,
)
from src.api.auth import create_jwt
from src.api.tenants import router

_SECRET = "test-secret"
_FITTING_TOOLS = ["search_tires", "get_fitting_stations", "book_fitting"]
_FULL_POLICY: dict[str, Any] = {
    "services": ["fitting", "storage"],
    "delivery_mode": "carrier_tariff",
    "delivery_carriers": ["Нова Пошта"],
    "delivery_eta_text": "1-3 дні",
    "pickup_available": True,
    "payment_methods": ["cod", "card", "installments"],
    "cod_fee_text": "за тарифом перевізника",
    "installment_banks": ["monobank", "privatbank"],
    "extended_warranty_brands": ["premiorri"],
    "brand_priority": ["premiorri", "kormoran"],
    "recommend_count": 3,
}


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_jwt({'sub': 'admin', 'role': 'admin'}, _SECRET)}"}


class _Row:
    def __init__(self, mapping: dict[str, Any]) -> None:
        self._mapping = mapping


class _Result:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def first(self) -> _Row | None:
        return _Row(self._row) if self._row is not None else None


class _TenantTable:
    """One ``tenants`` row; answers SELECT with the listed columns, records UPDATE."""

    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row
        self.updates: list[dict[str, Any]] = []

    async def execute(self, clause: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = " ".join(str(clause).split())
        if sql.upper().startswith("SELECT"):
            if self.row is None:
                return _Result(None)
            cols = re.search(r"SELECT (.*?) FROM", sql, re.IGNORECASE)
            assert cols is not None
            names = [c.strip() for c in cols.group(1).split(",")]
            return _Result({n: self.row[n] for n in names if n in self.row})
        if sql.upper().startswith("UPDATE"):
            self.updates.append(dict(params or {}))
            return _Result({"id": (params or {}).get("id")})
        return _Result(None)

    def engine(self) -> Any:
        engine = MagicMock(spec=["begin"])

        @asynccontextmanager
        async def _begin() -> Any:
            yield self

        engine.begin = _begin
        return engine


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _put(client: TestClient, table: _TenantTable, body: dict[str, Any]) -> Any:
    with (
        patch("src.api.auth.get_settings") as settings,
        patch("src.api.tenants._get_engine", new_callable=AsyncMock) as get_engine,
    ):
        settings.return_value.admin.jwt_secret = _SECRET
        get_engine.return_value = table.engine()
        return client.put(
            f"/admin/tenants/{uuid4()}/network-settings", json=body, headers=_headers()
        )


def _patch(client: TestClient, table: _TenantTable, body: dict[str, Any]) -> Any:
    with (
        patch("src.api.auth.get_settings") as settings,
        patch("src.api.tenants._get_engine", new_callable=AsyncMock) as get_engine,
    ):
        settings.return_value.admin.jwt_secret = _SECRET
        get_engine.return_value = table.engine()
        return client.patch(f"/admin/tenants/{uuid4()}", json=body, headers=_headers())


def _post(client: TestClient, table: _TenantTable, body: dict[str, Any]) -> Any:
    with (
        patch("src.api.auth.get_settings") as settings,
        patch("src.api.tenants._get_engine", new_callable=AsyncMock) as get_engine,
    ):
        settings.return_value.admin.jwt_secret = _SECRET
        get_engine.return_value = table.engine()
        return client.post("/admin/tenants", json=body, headers=_headers())


def _tenant(config: dict[str, Any], tools: list[str] | None = None) -> _TenantTable:
    return _TenantTable({"config": config, "enabled_tools": tools or []})


def _saved_config(table: _TenantTable) -> dict[str, Any]:
    assert len(table.updates) == 1
    return json.loads(table.updates[0]["config"])


# ── Merge ──────────────────────────────────────────────────


class TestNetworkSettingsMerge:
    def test_other_config_keys_survive(self, client: TestClient) -> None:
        other = {
            "store_api_url": "https://store.example/api",
            "excluded_station_ids": ["s-1", "s-2"],
            "agent_provider_override": "openai-gpt41-mini",
        }
        table = _tenant({**other, "sales_enabled": False})

        resp = _put(client, table, {"sales_enabled": True, "network_policy": _FULL_POLICY})

        assert resp.status_code == 200, resp.text
        saved = _saved_config(table)
        for key, value in other.items():
            assert saved[key] == value
        assert saved["sales_enabled"] is True
        assert saved["network_policy"]["services"] == ["fitting", "storage"]

    def test_json_string_config_is_merged_too(self, client: TestClient) -> None:
        table = _tenant(json.dumps({"store_api_url": "https://store.example/api"}))  # type: ignore[arg-type]

        resp = _put(client, table, {"network_policy": _FULL_POLICY})

        assert resp.status_code == 200, resp.text
        assert _saved_config(table)["store_api_url"] == "https://store.example/api"

    def test_policy_is_replaced_whole(self, client: TestClient) -> None:
        table = _tenant(
            {"network_policy": {**_FULL_POLICY, "cod_fee_text": "20 грн"}, "sales_enabled": True},
            tools=_FITTING_TOOLS,
        )
        new_policy = {"services": ["fitting"], "delivery_mode": "free"}

        resp = _put(client, table, {"sales_enabled": False, "network_policy": new_policy})

        assert resp.status_code == 200, resp.text
        saved = _saved_config(table)
        assert saved["network_policy"] == new_policy
        assert saved["sales_enabled"] is False

    def test_saved_policy_parses_to_what_the_form_sent(self, client: TestClient) -> None:
        table = _tenant({})

        resp = _put(client, table, {"sales_enabled": True, "network_policy": _FULL_POLICY})

        assert resp.status_code == 200, resp.text
        policy = NetworkPolicy.from_tenant_config(_saved_config(table))
        assert policy.sales_enabled is True
        assert policy.configured is True
        assert policy.services == frozenset({"fitting", "storage"})
        assert policy.payment_methods == ("cod", "card", "installments")
        assert policy.installment_banks == ("monobank", "privatbank")
        assert policy.brand_priority == ("premiorri", "kormoran")
        assert policy.recommend_count == 3

    def test_network_fact_texts_are_accepted_and_saved(self, client: TestClient) -> None:
        # configure_tenants writes them; the form sends them back untouched
        # (`_netState.policy`) — a 422 here would make the form unsaveable.
        table = _tenant({})
        texts = {
            "warranty_text": "гарантія виробника",
            "returns_text": "повернення — уточнить менеджер",
            "tracking_text": "ТТН надійде в SMS",
        }

        resp = _put(
            client, table, {"sales_enabled": False, "network_policy": {**_FULL_POLICY, **texts}}
        )

        assert resp.status_code == 200, resp.text
        policy = NetworkPolicy.from_tenant_config(_saved_config(table))
        assert policy.warranty_text == texts["warranty_text"]
        assert policy.returns_text == texts["returns_text"]
        assert policy.tracking_text == texts["tracking_text"]

    def test_missing_tenant_is_404(self, client: TestClient) -> None:
        table = _TenantTable(None)

        resp = _put(client, table, {"network_policy": _FULL_POLICY})

        assert resp.status_code == 404
        assert table.updates == []


# ── Strict validation ─────────────────────────────────────


def _bad_policies() -> list[tuple[str, dict[str, Any], str]]:
    return [
        ("delivery_mode", {**_FULL_POLICY, "delivery_mode": "courier"}, "courier"),
        ("payment", {**_FULL_POLICY, "payment_methods": ["cod", "crypto"]}, "crypto"),
        ("bank", {**_FULL_POLICY, "installment_banks": ["oschadbank"]}, "oschadbank"),
        ("service", {**_FULL_POLICY, "services": ["fitting", "storage", "wash"]}, "wash"),
        ("unknown_key", {**_FULL_POLICY, "free_delivery": True}, "free_delivery"),
        ("count_high", {**_FULL_POLICY, "recommend_count": 9}, "recommend_count"),
        ("count_low", {**_FULL_POLICY, "recommend_count": 1}, "recommend_count"),
        ("count_bool", {**_FULL_POLICY, "recommend_count": True}, "recommend_count"),
        ("count_str", {**_FULL_POLICY, "recommend_count": "3"}, "recommend_count"),
        ("pickup_str", {**_FULL_POLICY, "pickup_available": "yes"}, "pickup_available"),
        ("list_type", {**_FULL_POLICY, "brand_priority": "premiorri"}, "brand_priority"),
        ("empty_name", {**_FULL_POLICY, "delivery_carriers": [" "]}, "delivery_carriers"),
        ("text_type", {**_FULL_POLICY, "cod_fee_text": 20}, "cod_fee_text"),
        ("order_finish", {**_FULL_POLICY, "order_finish": "auto_confirm"}, "auto_confirm"),
    ]


class TestStrictValidation:
    @pytest.mark.parametrize(
        ("policy", "needle"),
        [(p, n) for _, p, n in _bad_policies()],
        ids=[i for i, _, _ in _bad_policies()],
    )
    def test_form_rejects_and_names_the_problem(
        self, client: TestClient, policy: dict[str, Any], needle: str
    ) -> None:
        table = _tenant({"store_api_url": "x"})

        resp = _put(client, table, {"sales_enabled": True, "network_policy": policy})

        assert resp.status_code == 422
        assert needle in resp.json()["detail"]
        assert table.updates == []

    @pytest.mark.parametrize(
        ("policy", "needle"),
        [(p, n) for _, p, n in _bad_policies()],
        ids=[i for i, _, _ in _bad_policies()],
    )
    def test_raw_json_patch_rejects_too(
        self, client: TestClient, policy: dict[str, Any], needle: str
    ) -> None:
        table = _tenant({})

        resp = _patch(client, table, {"config": {"network_policy": policy}})

        assert resp.status_code == 422
        assert needle in resp.json()["detail"]
        assert table.updates == []

    def test_create_rejects_too(self, client: TestClient) -> None:
        table = _tenant({})
        body = {
            "slug": "net",
            "name": "Net",
            "network_id": "Net",
            "config": {"network_policy": {**_FULL_POLICY, "recommend_count": 9}},
        }

        resp = _post(client, table, body)

        assert resp.status_code == 422
        assert "recommend_count" in resp.json()["detail"]

    def test_sales_enabled_must_be_bool(self, client: TestClient) -> None:
        resp = _patch(client, _tenant({}), {"config": {"sales_enabled": "true"}})

        assert resp.status_code == 422
        assert "sales_enabled" in resp.json()["detail"]

    @pytest.mark.parametrize("count", range(RECOMMEND_COUNT_MIN, RECOMMEND_COUNT_MAX + 1))
    def test_every_allowed_count_is_accepted(self, client: TestClient, count: int) -> None:
        table = _tenant({})

        resp = _put(client, table, {"network_policy": {**_FULL_POLICY, "recommend_count": count}})

        assert resp.status_code == 200, resp.text
        assert _saved_config(table)["network_policy"]["recommend_count"] == count

    def test_every_enum_member_is_accepted(self, client: TestClient) -> None:
        """The form offers the whole enum of `network_policy`; none may 422."""
        for mode in DELIVERY_MODES:
            policy = {
                "services": list(SERVICE_LABELS),
                "delivery_mode": mode,
                "payment_methods": list(PAYMENT_LABELS),
                "installment_banks": list(BANK_LABELS),
            }
            table = _tenant({})
            resp = _put(client, table, {"network_policy": policy})
            assert resp.status_code == 200, (mode, resp.text)

    def test_names_are_normalised_like_the_runtime_parser(self, client: TestClient) -> None:
        table = _tenant({})
        policy = {"services": [" Fitting ", "STORAGE"], "payment_methods": ["Card", "card"]}

        resp = _put(client, table, {"network_policy": policy})

        assert resp.status_code == 200, resp.text
        saved = _saved_config(table)["network_policy"]
        assert saved["services"] == ["fitting", "storage"]
        assert saved["payment_methods"] == ["card"]


# ── Empty-policy trap ─────────────────────────────────────


class TestServiceCoverage:
    def test_empty_policy_on_a_fitting_tenant_is_refused(self, client: TestClient) -> None:
        table = _tenant({"store_api_url": "x"}, tools=_FITTING_TOOLS)

        resp = _put(client, table, {"network_policy": {}})

        assert resp.status_code == 422
        assert "fitting" in resp.json()["detail"]
        assert table.updates == []

    def test_all_tools_tenant_counts_as_having_every_service(self, client: TestClient) -> None:
        table = _tenant({}, tools=[])  # empty enabled_tools = every tool

        resp = _put(client, table, {"network_policy": {"services": ["fitting"]}})

        assert resp.status_code == 422
        assert "storage" in resp.json()["detail"]

    def test_confirmed_no_services_is_saved(self, client: TestClient) -> None:
        table = _tenant({}, tools=_FITTING_TOOLS)

        resp = _put(client, table, {"network_policy": {}, "confirm_no_services": True})

        assert resp.status_code == 200, resp.text
        assert _saved_config(table)["network_policy"] == {}

    def test_tenant_without_service_tools_needs_no_confirmation(self, client: TestClient) -> None:
        table = _tenant({}, tools=["search_tires", "transfer_to_operator"])

        resp = _put(client, table, {"network_policy": {}})

        assert resp.status_code == 200, resp.text

    def test_raw_json_empty_policy_is_refused(self, client: TestClient) -> None:
        table = _tenant({}, tools=_FITTING_TOOLS)

        resp = _patch(client, table, {"config": {"store_api_url": "x", "network_policy": {}}})

        assert resp.status_code == 422
        assert "fitting" in resp.json()["detail"]
        assert table.updates == []

    def test_raw_json_sales_on_without_policy_is_refused(self, client: TestClient) -> None:
        """`sales_enabled` alone strips service tools in the live scope too."""
        table = _tenant({}, tools=_FITTING_TOOLS)

        resp = _patch(client, table, {"config": {"sales_enabled": True}})

        assert resp.status_code == 422

    def test_raw_json_without_policy_keys_is_untouched(self, client: TestClient) -> None:
        table = _tenant({}, tools=_FITTING_TOOLS)

        resp = _patch(client, table, {"config": {"store_api_url": "x"}})

        assert resp.status_code == 200, resp.text

    def test_create_with_empty_policy_is_refused(self, client: TestClient) -> None:
        body = {
            "slug": "net",
            "name": "Net",
            "network_id": "Net",
            "enabled_tools": ["book_fitting"],
            "config": {"network_policy": {}},
        }

        resp = _post(client, _tenant({}), body)

        assert resp.status_code == 422
        assert "fitting" in resp.json()["detail"]

    def test_service_tool_map_matches_the_live_scope(self) -> None:
        from src import main
        from src.api import tenants

        assert tenants._SERVICE_TOOLS == main._SERVICE_TOOLS
        assert set(tenants._SERVICE_TOOLS) == set(SERVICE_LABELS)


# ── Sandbox wiring ────────────────────────────────────────


class TestSandboxSeesPolicy:
    @pytest.mark.asyncio
    async def test_tenant_select_carries_config(self) -> None:
        from src.api.sandbox import _load_sandbox_tenant

        table = _TenantTable(
            {
                "slug": "tshina",
                "name": "Твоя Шина",
                "network_id": "Tshina",
                "enabled_tools": [],
                "prompt_suffix": None,
                "config": {"sales_enabled": True, "network_policy": _FULL_POLICY},
            }
        )

        tenant = await _load_sandbox_tenant(table, "t-1")

        assert tenant is not None
        policy = NetworkPolicy.from_tenant_config(tenant.get("config"))
        assert policy.sales_enabled is True
        assert policy.brand_priority == ("premiorri", "kormoran")

    @pytest.mark.asyncio
    async def test_json_string_config_is_decoded(self) -> None:
        from src.api.sandbox import _load_sandbox_tenant

        table = _TenantTable({"slug": "t", "config": json.dumps({"sales_enabled": True})})

        tenant = await _load_sandbox_tenant(table, "t-1")

        assert tenant is not None
        assert tenant["config"] == {"sales_enabled": True}

    @pytest.mark.asyncio
    async def test_live_search_tires_ranks_by_the_tenant_policy(self) -> None:
        """create_sandbox_agent(tenant=…) → search_tires gets the network's ranking."""
        from src.sandbox.agent_runner import create_sandbox_agent
        from src.store_client.client import StoreClient

        store = create_autospec(StoreClient, instance=True)
        store.search_tires.return_value = {"items": []}

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
        tenant = {
            "network_id": "Tshina",
            "config": {
                "network_policy": {
                    **_FULL_POLICY,
                    "brand_priority": ["premiorri"],
                    "recommend_count": 2,
                }
            },
        }

        with patch("src.sandbox.agent_runner.get_settings") as settings:
            settings.return_value = MagicMock(
                anthropic=MagicMock(api_key="k", model="claude-sonnet-4-5-20250929"),
            )
            agent = await create_sandbox_agent(
                engine, tool_mode="live", tenant=tenant, store_client=store
            )

        handler = agent._tool_router._handlers["search_tires"]
        await handler(width=205, brand_priority=["michelin"], recommend_count=9)

        kwargs = store.search_tires.await_args.kwargs
        assert kwargs["network"] == "Tshina"
        assert tuple(kwargs["brand_priority"]) == ("premiorri",)
        assert kwargs["recommend_count"] == 2
        assert kwargs["width"] == 205

        # search_disks: registered in the sandbox too, offer size from the policy.
        store.search_disks.return_value = {"items": []}
        disks = agent._tool_router._handlers["search_disks"]
        await disks(diameter=16, recommend_count=9, network="ProKoleso")
        disk_kwargs = store.search_disks.await_args.kwargs
        assert disk_kwargs["network"] == "Tshina"
        assert disk_kwargs["recommend_count"] == 2
        assert disk_kwargs["diameter"] == 16
