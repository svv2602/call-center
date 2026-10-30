"""A Nova Poshta branch is not one of our pickup points (call f06909f9, 2026-10-29).

The sales prompt sent the model to ``get_pickup_points(city, query="Нова
пошта …")``; the tool filters the network's own points, found nothing, and the
bot told the caller there were no Nova Poshta branches in Dnipro. The branch
is checked against the Nova Poshta API by `submit_order_request`.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import create_autospec, patch

import pytest

from src.agent.network_policy import NetworkPolicy
from src.agent.prompts import _MOD_ORDER_FLOW, _MOD_ORDER_FLOW_SALES
from src.core.call_session import CallSession
from src.main import _build_tool_router
from src.store_client.client import StoreClient


def _router() -> tuple[Any, Any]:
    store = create_autospec(StoreClient, instance=True)
    store.get_pickup_points.return_value = {"total": 1, "points": [{"id": "p1"}]}
    session = CallSession(uuid.uuid4())
    policy = NetworkPolicy(sales_enabled=True, recommend_count=3)
    return _build_tool_router(session, store_client=store, network_policy=policy), store


async def _points(router: Any, **args: Any) -> dict[str, Any]:
    with patch("src.main._call_logger", None), patch("src.main._redis", None):
        return await router.execute("get_pickup_points", args)


class TestTool:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "query", ["Нова пошта", "Нова пошта відділення 5", "новая почта", "НП"]
    )
    async def test_a_nova_poshta_query_says_it_is_not_here(self, query: str) -> None:
        router, store = _router()
        result = await _points(router, city="Дніпро", query=query)
        assert result["points"] == []
        assert "Нової пошти" in result["message"]
        assert "Не кажи, що їх немає" in result["message"]
        store.get_pickup_points.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_our_own_points_are_still_searched(self) -> None:
        router, store = _router()
        with patch("src.main._onec_client", None):
            result = await _points(router, city="Дніпро", query="")
        assert result["points"] == [{"id": "p1"}]
        store.get_pickup_points.assert_awaited_once()


class TestPrompt:
    def test_sales_prompt_does_not_search_branches_among_our_points(self) -> None:
        line = next(s for s in _MOD_ORDER_FLOW_SALES.splitlines() if "Нова пошта →" in s)
        assert "get_pickup_points тут НЕ" in line
        assert "submit_order_request" in line

    def test_the_fitting_only_prompt_is_unchanged(self) -> None:
        assert 'get_pickup_points(city, query="Нова пошта [адреса]")' in _MOD_ORDER_FLOW
