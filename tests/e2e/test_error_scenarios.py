"""E2E tests for error scenarios and edge cases.

Tests graceful handling of connection drops, timeouts, and error conditions.

Run against staging: pytest tests/e2e/test_error_scenarios.py -m e2e
"""

from __future__ import annotations

import asyncio
import os

import pytest

from tests.helpers.audiosocket_client import AudioSocketTestClient

AUDIOSOCKET_HOST = os.environ.get("E2E_AUDIOSOCKET_HOST", "127.0.0.1")
AUDIOSOCKET_PORT = int(os.environ.get("E2E_AUDIOSOCKET_PORT", "9092"))


@pytest.mark.e2e
class TestConnectionErrors:
    """Test error handling for connection issues."""

    @pytest.mark.asyncio
    async def test_immediate_disconnect(self) -> None:
        """Client connects and immediately disconnects — server should handle gracefully."""
        client = AudioSocketTestClient()
        try:
            await client.connect(AUDIOSOCKET_HOST, AUDIOSOCKET_PORT)
            # Close without hangup (simulates network failure)
        finally:
            await client.close()

        # Server should not crash — verify by connecting again
        client2 = AudioSocketTestClient()
        try:
            await client2.connect(AUDIOSOCKET_HOST, AUDIOSOCKET_PORT)
            pkt = await client2.read_packet(timeout=3.0)
            # Server is still alive if we got any response or timeout
            await client2.hangup()
        finally:
            await client2.close()

    @pytest.mark.asyncio
    async def test_connection_refused(self) -> None:
        """Connecting to a non-existent server raises ConnectionRefusedError."""
        client = AudioSocketTestClient()
        with pytest.raises((ConnectionRefusedError, OSError)):
            await client.connect("127.0.0.1", 59999)  # unlikely to be in use

    @pytest.mark.asyncio
    async def test_large_audio_burst(self) -> None:
        """Send a burst of audio frames rapidly — server should not crash."""
        client = AudioSocketTestClient()
        try:
            await client.connect(AUDIOSOCKET_HOST, AUDIOSOCKET_PORT)

            # Send 500 frames (10 seconds) without delay
            for _ in range(500):
                await client.send_audio_frame(b"\x00" * 640)

            # Server should still respond
            await asyncio.sleep(1.0)
            await client.hangup()
        finally:
            await client.close()
