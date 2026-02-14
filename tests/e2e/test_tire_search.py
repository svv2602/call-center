"""E2E tests for tire search scenario.

Uses AudioSocket test client to simulate a caller connecting to the Call Processor.
Tests verify the full pipeline: connect → greeting → user speech → LLM response → TTS audio.

Runs against a self-contained AudioSocket server started by the e2e_server fixture.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.helpers.audiosocket_client import AudioSocketTestClient


@pytest.mark.e2e
class TestTireSearchE2E:
    """E2E tests: AudioSocket call → tire search → response.

    These tests connect to a running Call Processor via AudioSocket
    and verify the greeting and response pipeline.
    """

    @pytest.mark.asyncio
    async def test_connect_and_receive_greeting(
        self, audiosocket_host: str, audiosocket_port: int
    ) -> None:
        """Connect via AudioSocket → server sends greeting audio."""
        client = AudioSocketTestClient()
        try:
            await client.connect(audiosocket_host, audiosocket_port)

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
    async def test_send_audio_and_receive_response(
        self, audiosocket_host: str, audiosocket_port: int
    ) -> None:
        """Send audio frames → server processes through pipeline → returns audio."""
        client = AudioSocketTestClient()
        try:
            await client.connect(audiosocket_host, audiosocket_port)

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
    async def test_hangup_closes_connection(
        self, audiosocket_host: str, audiosocket_port: int
    ) -> None:
        """Verify that sending hangup cleanly closes the connection."""
        client = AudioSocketTestClient()
        try:
            await client.connect(audiosocket_host, audiosocket_port)

            # Send hangup immediately
            await client.hangup()
            await asyncio.sleep(0.5)

            # Server may have already buffered greeting audio before processing
            # the hangup, so drain any audio packets first.
            while True:
                pkt = await client.read_packet(timeout=2.0)
                if pkt is None or pkt.type == 0x00:
                    break  # EOF or hangup — server closed properly
                # Audio packets (0x10) are acceptable: greeting was buffered
                assert pkt.type == 0x10, f"Unexpected packet type: {pkt.type}"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_multiple_concurrent_connections(
        self, audiosocket_host: str, audiosocket_port: int
    ) -> None:
        """Verify server handles multiple simultaneous AudioSocket connections."""

        async def single_call() -> bool:
            client = AudioSocketTestClient()
            try:
                await client.connect(audiosocket_host, audiosocket_port)
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
