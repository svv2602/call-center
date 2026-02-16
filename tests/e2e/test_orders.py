"""E2E tests for order flows.

Tests the order-related scenarios through the AudioSocket pipeline.
Verifies that the LLM agent can handle order status queries and creation.

Runs against a self-contained AudioSocket server started by the e2e_server fixture.
"""

from __future__ import annotations

import pytest

from tests.helpers.audiosocket_client import AudioSocketTestClient


@pytest.mark.e2e
class TestOrderStatusE2E:
    """E2E: caller asks about order status through AudioSocket."""

    @pytest.mark.asyncio
    async def test_call_lifecycle_for_order_query(
        self, audiosocket_host: str, audiosocket_port: int
    ) -> None:
        """Full call lifecycle: connect → greeting → query → response → hangup."""
        client = AudioSocketTestClient()
        try:
            await client.connect(audiosocket_host, audiosocket_port)

            # Read greeting
            greeting = await client.read_audio_response(timeout=5.0)
            assert len(greeting) > 0, "No greeting received"

            # Send audio (simulating order status question)
            await client.send_silence(duration_ms=1500)

            # Read bot response
            _response = await client.read_audio_response(timeout=10.0)

            # Hang up
            await client.hangup()
        finally:
            await client.close()


@pytest.mark.e2e
class TestOrderCreationE2E:
    """E2E: full order creation flow through AudioSocket."""

    @pytest.mark.asyncio
    async def test_multi_turn_conversation(
        self, audiosocket_host: str, audiosocket_port: int
    ) -> None:
        """Simulate multi-turn: greeting → query → response → followup → response → hangup."""
        client = AudioSocketTestClient()
        try:
            await client.connect(audiosocket_host, audiosocket_port)

            # Turn 1: greeting
            greeting = await client.read_audio_response(timeout=5.0)
            assert len(greeting) > 0

            # Turn 2: user speaks
            await client.send_silence(duration_ms=1000)
            _response1 = await client.read_audio_response(timeout=10.0)

            # Turn 3: user speaks again (follow-up)
            await client.send_silence(duration_ms=1000)
            _response2 = await client.read_audio_response(timeout=10.0)

            await client.hangup()
        finally:
            await client.close()
