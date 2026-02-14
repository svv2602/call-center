"""E2E tests for tire search scenario.

Requires running Asterisk + Call Processor + SIPp.
Run: pytest tests/e2e/test_tire_search.py
"""

import pytest


@pytest.mark.skip(reason="E2E: requires Asterisk + Call Processor + SIPp")
class TestTireSearchE2E:
    """E2E tests: SIP call → tire search → response."""

    @pytest.mark.asyncio
    async def test_tire_search_by_size(self) -> None:
        """SIP call: 'Шукаю шини 205/55 R16' → bot responds with options."""

    @pytest.mark.asyncio
    async def test_tire_search_by_vehicle(self) -> None:
        """SIP call: 'Шукаю шини для Toyota Camry 2020' → bot responds."""

    @pytest.mark.asyncio
    async def test_availability_check(self) -> None:
        """SIP call: ask about specific tire → bot checks availability."""

    @pytest.mark.asyncio
    async def test_operator_transfer(self) -> None:
        """SIP call: 'З'єднайте з оператором' → transfer happens."""
