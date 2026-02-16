"""Load test for Call Center AI using Locust.

Simulates concurrent AudioSocket connections and HTTP API monitoring.

Profiles:
  Normal: locust -f tests/load/locustfile.py --host=http://localhost:8080 -u 20 -r 2 -t 30m
  Peak:   locust -f tests/load/locustfile.py --host=http://localhost:8080 -u 50 -r 5 -t 15m
  Stress: locust -f tests/load/locustfile.py --host=http://localhost:8080 -u 100 -r 10 -t 10m

NFR targets:
  - p95 < 2 sec (normal, 20 concurrent), < 3 sec (peak, 50 concurrent)
  - 0% errors (normal), < 5% errors (peak)
  - Stress: graceful degradation (new calls rejected, active calls continue)
"""

from __future__ import annotations

import asyncio
import os
import struct
import time
import uuid

from locust import HttpUser, between, events, task

AUDIOSOCKET_HOST = os.environ.get("LOAD_AUDIOSOCKET_HOST", "127.0.0.1")
AUDIOSOCKET_PORT = int(os.environ.get("LOAD_AUDIOSOCKET_PORT", "9092"))
AUDIO_FRAME_BYTES = 640
CALL_DURATION_SECONDS = float(os.environ.get("LOAD_CALL_DURATION", "30"))


def _run_async(coro):  # type: ignore[no-untyped-def]
    """Run an async coroutine in a new event loop (for Locust sync tasks)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _simulate_call(host: str, port: int, duration: float) -> dict:
    """Simulate a single AudioSocket call.

    Returns metrics: success, duration_ms, frames_sent, frames_received, error.
    """
    channel_uuid = uuid.uuid4()
    frames_sent = 0
    frames_received = 0
    start = time.monotonic()

    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5.0)

        # Send UUID
        uuid_pkt = struct.pack("!BH", 0x01, 16) + channel_uuid.bytes
        writer.write(uuid_pkt)
        await writer.drain()

        # Run call for specified duration
        call_start = time.monotonic()
        silence = b"\x00" * AUDIO_FRAME_BYTES

        while time.monotonic() - call_start < duration:
            # Send audio frame
            pkt = struct.pack("!BH", 0x10, AUDIO_FRAME_BYTES) + silence
            writer.write(pkt)
            await writer.drain()
            frames_sent += 1

            # Non-blocking read of response
            try:
                header = await asyncio.wait_for(reader.readexactly(3), timeout=0.05)
                length = struct.unpack("!H", header[1:3])[0]
                if length > 0:
                    await asyncio.wait_for(reader.readexactly(length), timeout=0.1)
                if header[0] == 0x10:
                    frames_received += 1
                elif header[0] == 0x00:  # server hung up
                    break
            except (TimeoutError, asyncio.IncompleteReadError):
                pass

            # Simulate real-time 20ms frame pacing
            await asyncio.sleep(0.02)

        # Hangup
        writer.write(struct.pack("!BH", 0x00, 0))
        await writer.drain()
        writer.close()

        elapsed_ms = (time.monotonic() - start) * 1000
        return {
            "success": True,
            "duration_ms": elapsed_ms,
            "frames_sent": frames_sent,
            "frames_received": frames_received,
        }

    except Exception as e:
        elapsed_ms = (time.monotonic() - start) * 1000
        return {
            "success": False,
            "duration_ms": elapsed_ms,
            "frames_sent": frames_sent,
            "frames_received": frames_received,
            "error": str(e),
        }


class CallCenterUser(HttpUser):
    """Locust user simulating AudioSocket calls + HTTP health monitoring."""

    wait_time = between(2, 5)

    @task(3)
    def make_audiosocket_call(self) -> None:
        """Simulate a full AudioSocket call."""
        start = time.monotonic()
        result = _run_async(
            _simulate_call(AUDIOSOCKET_HOST, AUDIOSOCKET_PORT, CALL_DURATION_SECONDS)
        )
        elapsed = (time.monotonic() - start) * 1000

        if result["success"]:
            events.request.fire(
                request_type="AudioSocket",
                name="call",
                response_time=elapsed,
                response_length=result["frames_received"],
                exception=None,
                context={},
            )
        else:
            events.request.fire(
                request_type="AudioSocket",
                name="call",
                response_time=elapsed,
                response_length=0,
                exception=Exception(result.get("error", "unknown")),
                context={},
            )

    @task(1)
    def health_check(self) -> None:
        """Check /health endpoint."""
        self.client.get("/health")

    @task(1)
    def metrics_check(self) -> None:
        """Check /metrics endpoint (Prometheus)."""
        self.client.get("/metrics")
