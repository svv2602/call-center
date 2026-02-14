"""E2E tests for order flows.

These tests require a running Asterisk + Store API + Call Processor.
Run with: pytest tests/e2e/ -v --timeout=60
"""

from __future__ import annotations

import pytest


@pytest.mark.skip(reason="E2E — requires full stack running")
class TestOrderStatusE2E:
    """E2E: caller asks about order status."""

    def test_caller_asks_order_status(self) -> None:
        """SIP call → 'Де мій заказ?' → bot reports order status."""

    def test_caller_with_multiple_orders(self) -> None:
        """SIP call → multiple orders found → bot lists them."""


@pytest.mark.skip(reason="E2E — requires full stack running")
class TestOrderCreationE2E:
    """E2E: full order creation flow."""

    def test_full_order_flow(self) -> None:
        """SIP call → tire search → select → order draft → delivery → confirm."""

    def test_order_cancellation_mid_flow(self) -> None:
        """SIP call → start order → 'скасуй' → order cancelled."""
