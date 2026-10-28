"""The 1C and Store API circuit breakers open and recover.

aiobreaker adds ``timeout_duration`` to a datetime when a breaker opens: an int
there raised ``TypeError`` on the fifth failure in a row and left the breaker
OPEN for good — every later call raised ``TypeError`` until a restart, and the
``except CircuitBreakerError → 503`` branch never ran.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import MagicMock

import aiohttp
import pytest
from aiobreaker import CircuitBreaker, CircuitBreakerError

from src.onec_client import client as onec_module
from src.store_client.client import StoreClient


async def _boom() -> None:
    raise ValueError("down")


async def _ok() -> str:
    return "ok"


def _trip(breaker: CircuitBreaker) -> list[type[BaseException]]:
    """Fail ``fail_max`` times, then call once more; the exception types seen."""

    async def run() -> list[type[BaseException]]:
        seen: list[type[BaseException]] = []
        for _ in range(breaker.fail_max + 1):
            try:
                await breaker.call_async(_boom)
            except Exception as exc:
                seen.append(type(exc))
        return seen

    return asyncio.run(run())


def _breakers() -> list[CircuitBreaker]:
    return [onec_module._onec_breaker, StoreClient("http://store.invalid", "k")._breaker]


@pytest.mark.parametrize("breaker", _breakers(), ids=["onec", "store"])
def test_timeout_is_a_timedelta(breaker: CircuitBreaker) -> None:
    assert isinstance(breaker.timeout_duration, timedelta)


@pytest.mark.parametrize("which", ["onec", "store"])
def test_opens_with_circuit_breaker_error_and_recovers(which: str) -> None:
    template = dict(zip(["onec", "store"], _breakers(), strict=True))[which]
    # A fresh breaker of the same configuration: the module one is shared.
    breaker = CircuitBreaker(
        fail_max=template.fail_max, timeout_duration=template.timeout_duration
    )
    seen = _trip(breaker)
    assert TypeError not in seen
    assert seen[-1] is CircuitBreakerError

    # Past the timeout the breaker lets a call through again.
    breaker.timeout_duration = timedelta(0)
    assert asyncio.run(breaker.call_async(_ok)) == "ok"


def test_onec_maps_an_open_breaker_to_503(monkeypatch: pytest.MonkeyPatch) -> None:
    breaker = CircuitBreaker(fail_max=1, timeout_duration=timedelta(seconds=30))
    monkeypatch.setattr(onec_module, "_onec_breaker", breaker)
    _trip(breaker)
    client = onec_module.OneCClient("http://onec.invalid", "u", "p")
    # Never reached: the open breaker refuses before any HTTP.
    client._session = MagicMock(spec=aiohttp.ClientSession)

    async def run() -> None:
        await client._request("GET", "/x")

    with pytest.raises(onec_module.OneCAPIError) as err:
        asyncio.run(run())
    assert err.value.status == 503
