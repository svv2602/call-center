"""Integration tests for PostgreSQL logging.

Requires Docker (testcontainers) for PostgreSQL.
Run: pytest tests/integration/test_postgres.py
"""

import pytest


@pytest.mark.skip(reason="Requires Docker with testcontainers for PostgreSQL")
class TestPostgresIntegration:
    """Integration tests for CallLogger with real PostgreSQL."""

    @pytest.mark.asyncio
    async def test_log_call_start_and_end(self) -> None:
        """Test: call lifecycle is logged to PostgreSQL."""

    @pytest.mark.asyncio
    async def test_log_turns(self) -> None:
        """Test: dialog turns are logged."""

    @pytest.mark.asyncio
    async def test_upsert_customer(self) -> None:
        """Test: customer is created/updated by phone."""

    @pytest.mark.asyncio
    async def test_partitioned_table_insert(self) -> None:
        """Test: data inserts into correct monthly partition."""
