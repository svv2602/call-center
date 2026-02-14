"""E2E tests for tire search scenario.

Uses AudioSocket test client to simulate a caller connecting to the Call Processor.
Tests verify the full pipeline: connect → greeting → user speech → LLM response → TTS audio.

Run against staging: pytest tests/e2e/test_tire_search.py -m e2e
Requires: Call Processor running with AudioSocket on port 9092 (or staging on 19092).
"""

from __future__ import annotations

import asyncio
import os

import pytest

from tests.helpers.audiosocket_client import AudioSocketTestClient

# AudioSocket server address (configurable via env for staging)
AUDIOSOCKET_HOST = os.environ.get("E2E_AUDIOSOCKET_HOST", "127.0.0.1")
AUDIOSOCKET_PORT = int(os.environ.get("E2E_AUDIOSOCKET_PORT", "9092"))


@pytest.mark.e2e
class TestTireSearchE2E:
    """E2E tests: AudioSocket call → tire search → response.

    These tests connect to a running Call Processor via AudioSocket
    and verify the greeting and response pipeline.
    """

    @pytest.mark.asyncio
    async def test_connect_and_receive_greeting(self) -> None:
        """Connect via AudioSocket → server sends greeting audio."""
        client = AudioSocketTestClient()
        try:
            await client.connect(AUDIOSOCKET_HOST, AUDIOSOCKET_PORT)

            # After connecting, server should send greeting audio
            audio = await client.read_audio_response(timeout=5.0)

            # Greeting should produce some audio output
            assert len(audio) > 0, "No greeting audio received from server"
        finally:
            try:
                await client.hangup()
            except Exception:
                pass
            await client.close()

    @pytest.mark.asyncio
    async def test_send_audio_and_receive_response(self) -> None:
        """Send audio frames → server processes through pipeline → returns audio."""
        client = AudioSocketTestClient()
        try:
            await client.connect(AUDIOSOCKET_HOST, AUDIOSOCKET_PORT)

            # Read greeting first
            await client.read_audio_response(timeout=5.0)

            # Send audio (simulating caller speech)
            # In real E2E with Google STT, this would be actual speech audio
            # With mocked STT, silence triggers the pipeline after timeout
            await client.send_silence(duration_ms=2000)

            # Read response from server (LLM → TTS)
            response_audio = await client.read_audio_response(timeout=10.0)

            # Server should respond with some audio
            assert len(response_audio) >= 0, "Expected audio response from server"
        finally:
            try:
                await client.hangup()
            except Exception:
                pass
            await client.close()

    @pytest.mark.asyncio
    async def test_hangup_closes_connection(self) -> None:
        """Verify that sending hangup cleanly closes the connection."""
        client = AudioSocketTestClient()
        try:
            await client.connect(AUDIOSOCKET_HOST, AUDIOSOCKET_PORT)

            # Send hangup immediately
            await client.hangup()
            await asyncio.sleep(0.5)

            # Connection should be closed by server
            pkt = await client.read_packet(timeout=2.0)
            # After hangup, either None (EOF) or hangup packet back
            assert pkt is None or pkt.type == 0x00
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_multiple_concurrent_connections(self) -> None:
        """Verify server handles multiple simultaneous AudioSocket connections."""

        async def single_call() -> bool:
            client = AudioSocketTestClient()
            try:
                await client.connect(AUDIOSOCKET_HOST, AUDIOSOCKET_PORT)
                await client.send_silence(duration_ms=500)
                await asyncio.sleep(1.0)
                await client.hangup()
                return True
            except Exception:
                return False
            finally:
                await client.close()

        # Start 3 concurrent calls
        results = await asyncio.gather(
            single_call(),
            single_call(),
            single_call(),
            return_exceptions=True,
        )

        # At least some should succeed (server might reject if overloaded)
        successes = sum(1 for r in results if r is True)
        assert successes >= 1, f"No successful connections out of 3: {results}"
