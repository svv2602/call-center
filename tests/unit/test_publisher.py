"""Unit tests for event publisher."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from src.events.publisher import CHANNEL, publish_event


class TestPublishEvent:
    """Test event publishing to Redis Pub/Sub."""

    @pytest.mark.asyncio
    async def test_publishes_to_redis(self) -> None:
        mock_redis = AsyncMock()

        async def _mock_get_redis() -> AsyncMock:
            return mock_redis

        with patch("src.events.publisher._get_redis", _mock_get_redis):
            await publish_event("call:started", {"call_id": "abc-123"})

        mock_redis.publish.assert_called_once()
        args = mock_redis.publish.call_args
        assert args[0][0] == CHANNEL
        payload = json.loads(args[0][1])
        assert payload["type"] == "call:started"
        assert payload["data"]["call_id"] == "abc-123"
        assert "timestamp" in payload

    @pytest.mark.asyncio
    async def test_publishes_without_data(self) -> None:
        mock_redis = AsyncMock()

        async def _mock_get_redis() -> AsyncMock:
            return mock_redis

        with patch("src.events.publisher._get_redis", _mock_get_redis):
            await publish_event("dashboard:metrics_updated")

        args = mock_redis.publish.call_args
        payload = json.loads(args[0][1])
        assert payload["type"] == "dashboard:metrics_updated"
        assert payload["data"] == {}

    @pytest.mark.asyncio
    async def test_redis_failure_silent(self) -> None:
        """Publishing should not raise on Redis failure."""

        async def _mock_get_redis_fail() -> None:
            raise Exception("connection refused")

        with patch("src.events.publisher._get_redis", _mock_get_redis_fail):
            # Should not raise
            await publish_event("call:ended", {"call_id": "xyz"})
