"""The sandbox (and the goldset on top of it) builds a sales call like a live one.

With ``sales_enabled`` the live call gets the ``sales`` scenario frame and loses
the tools of services the network does not offer (``main.handle_call``). The
sandbox assembled the fitting-only prompt and kept every tool, so a goldset
case with sales on was graded against a bot that was told to transfer.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts.configure_tenants import PROKOLESO_CONFIG_PATCH, TVOYA_SHINA_CONFIG_PATCH
from src.agent.network_policy import SERVICE_TOOLS

_FITTING_ONLY_MARKER = "ТІЛЬКИ 4 сценарії"


async def _agent(config: dict[str, Any]) -> Any:
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
    tenant = {"network_id": "Tshina", "enabled_tools": [], "config": config}
    with patch("src.sandbox.agent_runner.get_settings") as settings:
        settings.return_value = MagicMock(
            anthropic=MagicMock(api_key="k", model="claude-sonnet-4-5-20250929"),
        )
        return await create_sandbox_agent(engine, tool_mode="mock", tenant=tenant)


def _tool_names(agent: Any) -> set[str]:
    return {t["name"] for t in agent._tools}


@pytest.mark.asyncio
async def test_prokoleso_sales_gets_sales_frame_and_no_service_tools() -> None:
    agent = await _agent({**PROKOLESO_CONFIG_PATCH, "sales_enabled": True})
    assert _FITTING_ONLY_MARKER not in agent._system_prompt
    assert not _tool_names(agent) & (SERVICE_TOOLS["fitting"] | SERVICE_TOOLS["storage"])
    assert "search_tires" in _tool_names(agent)


@pytest.mark.asyncio
async def test_tvoya_shina_sales_keeps_fitting_tools() -> None:
    agent = await _agent({**TVOYA_SHINA_CONFIG_PATCH, "sales_enabled": True})
    assert _FITTING_ONLY_MARKER not in agent._system_prompt
    assert "book_fitting" in _tool_names(agent)


@pytest.mark.asyncio
async def test_sales_off_is_the_fitting_only_sandbox() -> None:
    agent = await _agent(dict(PROKOLESO_CONFIG_PATCH))
    assert _FITTING_ONLY_MARKER in agent._system_prompt
    assert "book_fitting" in _tool_names(agent)


@pytest.mark.asyncio
async def test_sales_prompt_is_modular_so_fitting_loads_on_demand() -> None:
    # Wave 2-D: the fitting module is added per turn only to a modular prompt;
    # without the flag the goldset would never see it under sales.
    on = await _agent({**TVOYA_SHINA_CONFIG_PATCH, "sales_enabled": True})
    off = await _agent(dict(TVOYA_SHINA_CONFIG_PATCH))
    assert on._is_modular is True
    assert off._is_modular is False


# Prod tenants carry explicit enabled_tools lists that predate the sales tools
# (2026-09-28: neither network listed search_disks; Про Колесо had no pickup).
_PK_PROD_TOOLS = [
    "get_vehicle_tire_sizes",
    "search_tires",
    "check_availability",
    "transfer_to_operator",
    "get_order_status",
    "create_order_draft",
    "update_order_delivery",
    "confirm_order",
    "search_knowledge_base",
    "create_callback_request",
    "update_customer_profile",
]


async def _agent_with_list(config: dict[str, Any], enabled: list[str]) -> Any:
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
    tenant = {"network_id": "ProKoleso", "enabled_tools": enabled, "config": config}
    with patch("src.sandbox.agent_runner.get_settings") as settings:
        settings.return_value = MagicMock(
            anthropic=MagicMock(api_key="k", model="claude-sonnet-4-5-20250929"),
        )
        return await create_sandbox_agent(engine, tool_mode="mock", tenant=tenant)


@pytest.mark.asyncio
async def test_old_tenant_list_gets_the_sales_core_under_sales() -> None:
    agent = await _agent_with_list(
        {**PROKOLESO_CONFIG_PATCH, "sales_enabled": True}, _PK_PROD_TOOLS
    )
    names = _tool_names(agent)
    assert {"search_disks", "get_pickup_points", "submit_order_request"} <= names
    assert not names & (SERVICE_TOOLS["fitting"] | SERVICE_TOOLS["storage"])


@pytest.mark.asyncio
async def test_old_tenant_list_unchanged_with_sales_off() -> None:
    agent = await _agent_with_list(dict(PROKOLESO_CONFIG_PATCH), _PK_PROD_TOOLS)
    names = _tool_names(agent)
    assert "search_disks" not in names and "get_pickup_points" not in names


def test_sales_tenant_allowlist_adds_core_and_swaps_chain() -> None:
    from src.agent.tools import SALES_CORE_TOOLS, sales_tenant_allowlist

    out = sales_tenant_allowlist({"transfer_to_operator", "create_order_draft", "confirm_order"})
    assert out >= SALES_CORE_TOOLS
    assert "create_order_draft" not in out and "confirm_order" not in out
    assert "transfer_to_operator" in out


def test_live_call_applies_the_sales_core_to_the_tenant_list() -> None:
    """handle_call cannot be unit-run; pin the call site (as test_network_policy does)."""
    import ast
    import inspect

    import src.main as main

    tree = ast.parse(inspect.getsource(main.handle_call))
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "sales_tenant_allowlist" in calls
