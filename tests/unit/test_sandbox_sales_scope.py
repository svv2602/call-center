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
