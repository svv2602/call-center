"""Chaos tests: Redis failure scenarios.

Verifies that all Redis-dependent components degrade gracefully when Redis
is unavailable (ConnectionError) or times out (TimeoutError). Every service
using Redis must fail-open: requests continue to be served, events are
silently dropped, and blacklist checks default to "not blacklisted".
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# 1. Rate limiter: Redis unavailable -> fail-open (request allowed)
# ---------------------------------------------------------------------------
@pytest.mark.chaos
async def test_rate_limiter_fail_open_on_connection_error() -> None:
    """When Redis is down, _check_limit returns (False, limit, reset_ts).

    The middleware must NOT block requests when it cannot reach Redis.
    """
    from src.api.middleware.rate_limit import _check_limit

    async def _redis_unavailable() -> None:
        raise ConnectionError("Connection refused")

    with patch("src.api.middleware.rate_limit._get_redis", _redis_unavailable):
        blocked, remaining, reset_ts = await _check_limit("rl:ip:10.0.0.1", 100, 60)

    assert blocked is False, "Request must be allowed when Redis is unavailable"
    assert remaining == 100
    assert isinstance(reset_ts, int)


# ---------------------------------------------------------------------------
# 2. Login rate limit: Redis unavailable -> allows request (not blocked)
# ---------------------------------------------------------------------------
@pytest.mark.chaos
async def test_login_rate_limit_allows_on_connection_error() -> None:
    """When Redis is down, _check_rate_limit returns False (not blocked).

    Login flow must not reject users just because the rate-limit backend
    is unreachable.
    """
    from src.api.auth import _check_rate_limit

    async def _redis_unavailable() -> None:
        raise ConnectionError("Connection refused")

    with patch("src.api.auth._get_redis", _redis_unavailable):
        is_blocked = await _check_rate_limit("10.0.0.1", "admin")

    assert is_blocked is False, "Login must not be blocked when Redis is unavailable"


# ---------------------------------------------------------------------------
# 3. Pub/Sub: Redis unavailable -> publish_event does not raise
# ---------------------------------------------------------------------------
@pytest.mark.chaos
async def test_pubsub_silent_fail_on_connection_error() -> None:
    """publish_event must swallow ConnectionError and never propagate it.

    Callers of publish_event (e.g. call start/end handlers) must not
    crash because of a Redis outage.
    """
    from src.events.publisher import publish_event

    async def _redis_unavailable() -> None:
        raise ConnectionError("Connection refused")

    with patch("src.events.publisher._get_redis", _redis_unavailable):
        # Must complete without raising
        await publish_event("call:started", {"call_id": "chaos-test-001"})


# ---------------------------------------------------------------------------
# 4. JWT blacklist: Redis unavailable -> is_token_blacklisted returns False
# ---------------------------------------------------------------------------
@pytest.mark.chaos
async def test_jwt_blacklist_allows_on_connection_error() -> None:
    """When Redis is down, is_token_blacklisted returns False.

    This is a deliberate fail-open decision: it is better to accept a
    potentially-revoked token than to lock out every authenticated user
    during a Redis outage.
    """
    from src.api.auth import is_token_blacklisted

    async def _redis_unavailable() -> None:
        raise ConnectionError("Connection refused")

    with patch("src.api.auth._get_redis", _redis_unavailable):
        result = await is_token_blacklisted("some-jti-value")

    assert result is False, "Token must not be treated as blacklisted when Redis is down"


# ---------------------------------------------------------------------------
# 5. TimeoutError behaves the same as ConnectionError (all components)
# ---------------------------------------------------------------------------
@pytest.mark.chaos
async def test_rate_limiter_fail_open_on_timeout() -> None:
    """_check_limit must also handle TimeoutError gracefully."""
    from src.api.middleware.rate_limit import _check_limit

    async def _redis_timeout() -> None:
        raise TimeoutError("Redis operation timed out")

    with patch("src.api.middleware.rate_limit._get_redis", _redis_timeout):
        blocked, remaining, _reset_ts = await _check_limit("rl:ip:10.0.0.2", 100, 60)

    assert blocked is False
    assert remaining == 100


@pytest.mark.chaos
async def test_login_rate_limit_allows_on_timeout() -> None:
    """_check_rate_limit must also handle TimeoutError gracefully."""
    from src.api.auth import _check_rate_limit

    async def _redis_timeout() -> None:
        raise TimeoutError("Redis operation timed out")

    with patch("src.api.auth._get_redis", _redis_timeout):
        is_blocked = await _check_rate_limit("10.0.0.3", "operator")

    assert is_blocked is False


@pytest.mark.chaos
async def test_pubsub_silent_fail_on_timeout() -> None:
    """publish_event must swallow TimeoutError without propagating."""
    from src.events.publisher import publish_event

    async def _redis_timeout() -> None:
        raise TimeoutError("Redis operation timed out")

    with patch("src.events.publisher._get_redis", _redis_timeout):
        await publish_event("operator:status_changed", {"operator_id": "op-1"})


@pytest.mark.chaos
async def test_jwt_blacklist_allows_on_timeout() -> None:
    """is_token_blacklisted must also handle TimeoutError gracefully."""
    from src.api.auth import is_token_blacklisted

    async def _redis_timeout() -> None:
        raise TimeoutError("Redis operation timed out")

    with patch("src.api.auth._get_redis", _redis_timeout):
        result = await is_token_blacklisted("another-jti-value")

    assert result is False


# ---------------------------------------------------------------------------
# Bonus: blacklist_token also degrades gracefully
# ---------------------------------------------------------------------------
@pytest.mark.chaos
async def test_blacklist_token_silent_fail_on_connection_error() -> None:
    """blacklist_token must not raise when Redis is unavailable.

    The logout endpoint calls blacklist_token; a Redis outage should not
    cause logout to return 500.
    """
    from src.api.auth import blacklist_token

    async def _redis_unavailable() -> None:
        raise ConnectionError("Connection refused")

    with patch("src.api.auth._get_redis", _redis_unavailable):
        # Must complete without raising
        await blacklist_token("jti-to-blacklist", 3600)
