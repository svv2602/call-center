"""/health/ready asks the 1C circuit breaker, not 1C itself.

The probe used to pull the whole ProKoleso stock (get_stock) on every call.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiobreaker import CircuitBreakerState

import src.main as main


def _client() -> MagicMock:
    client = MagicMock()
    client._session = object()
    client.get_stock = AsyncMock(return_value={"success": True})
    return client


@pytest.mark.asyncio
async def test_ready_does_not_request_1c() -> None:
    client = _client()
    with patch.object(main, "_onec_client", client):
        result = await main.readiness_check()
    assert result["onec_api"] == "reachable"
    client.get_stock.assert_not_awaited()


@pytest.mark.asyncio
async def test_open_breaker_is_not_ready() -> None:
    breaker = MagicMock(current_state=CircuitBreakerState.OPEN)
    with (
        patch.object(main, "_onec_client", _client()),
        patch.object(main, "_onec_breaker", breaker),
    ):
        result = await main.readiness_check()
    assert result["onec_api"] == "circuit_open"
    assert result["status"] == "not_ready"
