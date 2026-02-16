"""Unit tests for rate limiting middleware."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.middleware.rate_limit import (
    _get_client_ip,
    _get_endpoint_limit,
)


class TestGetClientIP:
    """Test client IP extraction."""

    def test_direct_connection(self) -> None:
        request = AsyncMock()
        request.headers = {}
        request.client.host = "192.168.1.1"
        assert _get_client_ip(request) == "192.168.1.1"

    def test_forwarded_for_single(self) -> None:
        request = AsyncMock()
        request.headers = {"X-Forwarded-For": "10.0.0.1"}
        request.client.host = "192.168.1.1"
        assert _get_client_ip(request) == "10.0.0.1"

    def test_forwarded_for_chain(self) -> None:
        request = AsyncMock()
        request.headers = {"X-Forwarded-For": "10.0.0.1, 172.16.0.1, 192.168.1.1"}
        request.client.host = "127.0.0.1"
        assert _get_client_ip(request) == "10.0.0.1"

    def test_no_client(self) -> None:
        request = AsyncMock()
        request.headers = {}
        request.client = None
        assert _get_client_ip(request) == "unknown"


class TestGetEndpointLimit:
    """Test endpoint-specific rate limit overrides."""

    def test_export_endpoint(self) -> None:
        result = _get_endpoint_limit("/analytics/calls/export", "GET")
        assert result is not None
        limit, window = result
        assert limit == 10
        assert window == 60

    def test_summary_export_endpoint(self) -> None:
        result = _get_endpoint_limit("/analytics/summary/export", "GET")
        assert result is not None
        assert result[0] == 10

    def test_knowledge_mutation(self) -> None:
        result = _get_endpoint_limit("/knowledge", "POST")
        assert result is not None
        assert result[0] == 30

    def test_knowledge_get_no_limit(self) -> None:
        result = _get_endpoint_limit("/knowledge", "GET")
        assert result is None

    def test_regular_endpoint_no_override(self) -> None:
        result = _get_endpoint_limit("/auth/login", "POST")
        assert result is None

    def test_health_no_override(self) -> None:
        result = _get_endpoint_limit("/health", "GET")
        assert result is None


class TestCheckLimit:
    """Test the sliding window rate limit check."""

    @pytest.mark.asyncio
    async def test_under_limit_allows(self) -> None:
        from src.api.middleware.rate_limit import _check_limit

        # pipeline() is sync, but execute() is async
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[None, None, 5, True])
        mock_redis = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe

        async def _mock_get_redis() -> MagicMock:
            return mock_redis

        with patch("src.api.middleware.rate_limit._get_redis", _mock_get_redis):
            blocked, remaining, _reset_ts = await _check_limit("test_key", 100, 60)

        assert blocked is False
        assert remaining == 95

    @pytest.mark.asyncio
    async def test_over_limit_blocks(self) -> None:
        from src.api.middleware.rate_limit import _check_limit

        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[None, None, 101, True])
        mock_redis = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe

        async def _mock_get_redis() -> MagicMock:
            return mock_redis

        with patch("src.api.middleware.rate_limit._get_redis", _mock_get_redis):
            blocked, remaining, _reset_ts = await _check_limit("test_key", 100, 60)

        assert blocked is True
        assert remaining == 0

    @pytest.mark.asyncio
    async def test_redis_failure_allows(self) -> None:
        from src.api.middleware.rate_limit import _check_limit

        async def _mock_get_redis_fail() -> None:
            raise Exception("connection refused")

        with patch("src.api.middleware.rate_limit._get_redis", _mock_get_redis_fail):
            blocked, remaining, _reset_ts = await _check_limit("test_key", 100, 60)

        assert blocked is False
        assert remaining == 100
