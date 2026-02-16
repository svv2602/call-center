"""Chaos tests: PostgreSQL and Store API failure scenarios.

Verifies that the system degrades gracefully when downstream services fail:
- Store API circuit breaker open -> StoreAPIError(503)
- Store API retries exhaust on 503/429
- Store API 500 error is NOT retried
- ToolRouter catches tool handler exceptions
- DB unavailable -> login falls back to env credentials
- DB unavailable -> failed-login audit does not crash
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aiobreaker import CircuitBreakerError

from src.agent.agent import ToolRouter
from src.store_client.client import _MAX_RETRIES, StoreAPIError, StoreClient


def _make_circuit_breaker_error() -> CircuitBreakerError:
    """Create a CircuitBreakerError with the required arguments."""
    return CircuitBreakerError(
        "Circuit breaker is open",
        reopen_time=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# 1. Circuit breaker open -> StoreAPIError with status 503
# ---------------------------------------------------------------------------
@pytest.mark.chaos
async def test_circuit_breaker_open_raises_store_api_error_503() -> None:
    """When the circuit breaker is open, _request must raise StoreAPIError(503).

    The circuit breaker opens after 5 consecutive failures. Subsequent
    calls are fast-failed with CircuitBreakerError, which _request
    translates into a user-friendly StoreAPIError.
    """
    client = StoreClient(base_url="http://store.local", api_key="test-key")
    client._session = AsyncMock()  # pretend session is open

    cb_error = _make_circuit_breaker_error()

    with (
        patch(
            "src.store_client.client._store_breaker.call_async",
            side_effect=cb_error,
        ),
        pytest.raises(StoreAPIError) as exc_info,
    ):
        await client._request("GET", "/api/v1/tires/search")

    assert exc_info.value.status == 503
    assert "недоступний" in exc_info.value.message.lower() or "503" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 2. Store API timeout (503) -> retries exhausted
# ---------------------------------------------------------------------------
@pytest.mark.chaos
async def test_store_api_retries_on_503_then_raises() -> None:
    """_request_with_retry must retry _MAX_RETRIES times for 503 status.

    Total call count should be _MAX_RETRIES + 1 (initial + retries).
    After exhausting retries the last StoreAPIError is re-raised.
    """
    client = StoreClient(base_url="http://store.local", api_key="test-key")
    client._session = AsyncMock()

    mock_do_request = AsyncMock(
        side_effect=StoreAPIError(503, "Service temporarily unavailable"),
    )

    with (
        patch.object(client, "_do_request", mock_do_request),
        patch("src.store_client.client.asyncio.sleep", new_callable=AsyncMock),
        pytest.raises(StoreAPIError) as exc_info,
    ):
        await client._request_with_retry("GET", "http://store.local/api/v1/tires/search", "req-001")

    assert exc_info.value.status == 503
    assert mock_do_request.call_count == _MAX_RETRIES + 1


# ---------------------------------------------------------------------------
# 3. Store API 500 error -> no retry (500 is not retryable)
# ---------------------------------------------------------------------------
@pytest.mark.chaos
async def test_store_api_no_retry_on_500() -> None:
    """500 Internal Server Error must NOT be retried.

    Only 429 (rate-limited) and 503 (unavailable) trigger retries.
    A 500 indicates a bug in the upstream service, and retrying would
    just waste time and resources.
    """
    client = StoreClient(base_url="http://store.local", api_key="test-key")
    client._session = AsyncMock()

    mock_do_request = AsyncMock(
        side_effect=StoreAPIError(500, "Internal server error"),
    )

    with (
        patch.object(client, "_do_request", mock_do_request),
        pytest.raises(StoreAPIError) as exc_info,
    ):
        await client._request_with_retry("GET", "http://store.local/api/v1/tires/search", "req-002")

    assert exc_info.value.status == 500
    assert mock_do_request.call_count == 1, "500 must not be retried"


# ---------------------------------------------------------------------------
# 4. ToolRouter catches handler exceptions gracefully
# ---------------------------------------------------------------------------
@pytest.mark.chaos
async def test_tool_router_catches_exception_returns_error() -> None:
    """ToolRouter.execute() must catch any exception from a handler and
    return {"error": str(exc)} instead of propagating.

    This is the primary graceful-degradation mechanism: even if the
    Store API or any other tool backend crashes, the LLM receives a
    structured error and can respond to the customer appropriately.
    """
    router = ToolRouter()

    async def exploding_handler(**kwargs: Any) -> dict[str, Any]:
        raise Exception("boom")

    router.register("exploding_tool", exploding_handler)

    result = await router.execute("exploding_tool", {"arg": "value"})

    assert isinstance(result, dict)
    assert "error" in result
    assert result["error"] == "boom"


# ---------------------------------------------------------------------------
# 5. DB unavailable -> login falls back to env credentials
# ---------------------------------------------------------------------------
@pytest.mark.chaos
async def test_login_falls_back_to_env_when_db_unavailable() -> None:
    """When _authenticate_via_db returns None (DB failure), the login
    endpoint falls back to env-configured credentials.

    This ensures admin access is preserved during DB outages.
    """
    from src.api.auth import LoginRequest, login

    mock_request = AsyncMock()
    mock_request.client.host = "127.0.0.1"

    login_data = LoginRequest(username="admin", password="secret")

    # Mock settings to provide env credentials and JWT config
    mock_settings = AsyncMock()
    mock_settings.admin.username = "admin"
    mock_settings.admin.password = "secret"
    mock_settings.admin.jwt_secret = "test-jwt-secret-key-for-chaos"
    mock_settings.admin.jwt_ttl_hours = 24

    with (
        patch("src.api.auth.get_settings", return_value=mock_settings),
        patch("src.api.auth._authenticate_via_db", new_callable=AsyncMock, return_value=None),
        patch("src.api.auth._check_rate_limit", new_callable=AsyncMock, return_value=False),
    ):
        result = await login(login_data, mock_request)

    assert "token" in result
    assert result["token_type"] == "bearer"
    assert result["expires_in"] == 86400


# ---------------------------------------------------------------------------
# 6. DB unavailable -> _log_failed_login does not crash
# ---------------------------------------------------------------------------
@pytest.mark.chaos
async def test_log_failed_login_swallows_db_error() -> None:
    """_log_failed_login must not propagate exceptions when DB is down.

    The login endpoint calls _log_failed_login after a failed attempt.
    If the audit log write fails, the login flow must continue normally
    (i.e. return 401 to the user, not 500).
    """
    from src.api.auth import _log_failed_login

    async def _engine_that_crashes() -> None:
        raise ConnectionError("DB connection refused")

    with patch("src.api.auth._get_engine", _engine_that_crashes):
        # Must complete without raising
        await _log_failed_login("unknown_user", "10.0.0.1")


# ---------------------------------------------------------------------------
# Bonus: ToolRouter handles StoreAPIError specifically
# ---------------------------------------------------------------------------
@pytest.mark.chaos
async def test_tool_router_catches_store_api_error() -> None:
    """ToolRouter.execute() must also catch StoreAPIError and return
    a structured error dict, so the LLM can inform the customer.
    """
    router = ToolRouter()

    async def handler_with_api_error(**kwargs: Any) -> dict[str, Any]:
        raise StoreAPIError(503, "Service temporarily unavailable")

    router.register("search_tires", handler_with_api_error)

    result = await router.execute("search_tires", {"brand": "Michelin"})

    assert isinstance(result, dict)
    assert "error" in result
    assert "503" in result["error"]
