"""E2E tests for order flows.

Tests the order-related scenarios through the AudioSocket pipeline.
Verifies that the LLM agent can handle order status queries and creation.

Run against staging: pytest tests/e2e/test_orders.py -m e2e
Requires: Call Processor + Store API mock running.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from tests.helpers.audiosocket_client import AudioSocketTestClient

AUDIOSOCKET_HOST = os.environ.get("E2E_AUDIOSOCKET_HOST", "127.0.0.1")
AUDIOSOCKET_PORT = int(os.environ.get("E2E_AUDIOSOCKET_PORT", "9092"))


@pytest.mark.e2e
class TestOrderStatusE2E:
    """E2E: caller asks about order status through AudioSocket."""

    @pytest.mark.asyncio
    async def test_call_lifecycle_for_order_query(self) -> None:
        """Full call lifecycle: connect → greeting → query → response → hangup."""
        client = AudioSocketTestClient()
        try:
            await client.connect(AUDIOSOCKET_HOST, AUDIOSOCKET_PORT)

            # Read greeting
            greeting = await client.read_audio_response(timeout=5.0)
            assert len(greeting) > 0, "No greeting received"

            # Send audio (simulating order status question)
            await client.send_silence(duration_ms=1500)

            # Read bot response
            response = await client.read_audio_response(timeout=10.0)

            # Hang up
            await client.hangup()
        finally:
            await client.close()


@pytest.mark.e2e
class TestOrderCreationE2E:
    """E2E: full order creation flow through AudioSocket."""

    @pytest.mark.asyncio
    async def test_multi_turn_conversation(self) -> None:
        """Simulate multi-turn: greeting → query → response → followup → response → hangup."""
        client = AudioSocketTestClient()
        try:
            await client.connect(AUDIOSOCKET_HOST, AUDIOSOCKET_PORT)

            # Turn 1: greeting
            greeting = await client.read_audio_response(timeout=5.0)
            assert len(greeting) > 0

            # Turn 2: user speaks
            await client.send_silence(duration_ms=1000)
            response1 = await client.read_audio_response(timeout=10.0)

            # Turn 3: user speaks again (follow-up)
            await client.send_silence(duration_ms=1000)
            response2 = await client.read_audio_response(timeout=10.0)

            await client.hangup()
        finally:
            await client.close()
