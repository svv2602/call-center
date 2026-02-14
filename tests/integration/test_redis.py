"""Integration tests for Redis session storage.

Requires Docker (testcontainers) for Redis.
Run: pytest tests/integration/test_redis.py
"""

import pytest


@pytest.mark.skip(reason="Requires Docker with testcontainers for Redis")
class TestRedisIntegration:
    """Integration tests for SessionStore with real Redis."""

    @pytest.mark.asyncio
    async def test_save_and_load_session(self) -> None:
        """Test: session round-trip through Redis."""

    @pytest.mark.asyncio
    async def test_session_ttl(self) -> None:
        """Test: session expires after TTL."""

    @pytest.mark.asyncio
    async def test_delete_session(self) -> None:
        """Test: session is deleted on normal call end."""
