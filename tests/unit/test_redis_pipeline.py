"""Unit tests for Redis pipeline batch lookups at call start."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestBatchRedisLookups:
    """Tests for _batch_redis_lookups()."""

    @pytest.mark.asyncio()
    async def test_both_values_returned(self) -> None:
        """Pipeline returns both exten and caller when present."""
        mock_pipe = MagicMock()
        mock_pipe.get = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[b"7770", b"+380441234567"])

        mock_redis = MagicMock()
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)

        with patch("src.main._redis", mock_redis):
            from src.main import _batch_redis_lookups

            exten, caller = await _batch_redis_lookups("test-uuid")

        assert exten == "7770"
        assert caller == "+380441234567"

    @pytest.mark.asyncio()
    async def test_partial_results(self) -> None:
        """Pipeline handles one None and one value."""
        mock_pipe = MagicMock()
        mock_pipe.get = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[b"7771", None])

        mock_redis = MagicMock()
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)

        with patch("src.main._redis", mock_redis):
            from src.main import _batch_redis_lookups

            exten, caller = await _batch_redis_lookups("test-uuid")

        assert exten == "7771"
        assert caller is None

    @pytest.mark.asyncio()
    async def test_redis_unavailable(self) -> None:
        """Returns (None, None) when Redis is not connected."""
        with patch("src.main._redis", None):
            from src.main import _batch_redis_lookups

            exten, caller = await _batch_redis_lookups("test-uuid")

        assert exten is None
        assert caller is None

    @pytest.mark.asyncio()
    async def test_pipeline_error_fallback(self) -> None:
        """Returns (None, None) on Redis error."""
        mock_pipe = MagicMock()
        mock_pipe.get = MagicMock()
        mock_pipe.execute = AsyncMock(side_effect=ConnectionError("Redis down"))

        mock_redis = MagicMock()
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)

        with patch("src.main._redis", mock_redis):
            from src.main import _batch_redis_lookups

            exten, caller = await _batch_redis_lookups("test-uuid")

        assert exten is None
        assert caller is None

    @pytest.mark.asyncio()
    async def test_empty_strings_treated_as_none(self) -> None:
        """Empty/whitespace Redis values are treated as None."""
        mock_pipe = MagicMock()
        mock_pipe.get = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[b"  ", b""])

        mock_redis = MagicMock()
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)

        with patch("src.main._redis", mock_redis):
            from src.main import _batch_redis_lookups

            exten, caller = await _batch_redis_lookups("test-uuid")

        assert exten is None
        assert caller is None
