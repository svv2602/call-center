"""Call Center AI — Application entry point."""

import asyncio
import logging
import signal
import sys

import uvicorn
from fastapi import FastAPI
from redis.asyncio import Redis

from fastapi.responses import Response

from src.api.analytics import router as analytics_router
from src.api.prompts import router as prompts_router
from src.config import Settings, get_settings
from src.core.audio_socket import AudioSocketConnection, AudioSocketServer, PacketType
from src.core.call_session import CallSession, CallState, SessionStore
from src.logging.structured_logger import setup_logging
from src.monitoring.metrics import active_calls, calls_total, get_metrics

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Call Center AI",
    description="AI-powered call center for tire shop",
    version="0.1.0",
)
app.include_router(analytics_router)
app.include_router(prompts_router)

# Module-level references for health checks
_audio_server: AudioSocketServer | None = None
_redis: Redis | None = None  # type: ignore[type-arg]


@app.get("/health")
async def health_check() -> dict[str, object]:
    """Health check endpoint."""
    redis_ok = False
    if _redis is not None:
        try:
            await _redis.ping()
            redis_ok = True
        except Exception:
            pass

    return {
        "status": "ok",
        "active_calls": _audio_server.active_connections if _audio_server else 0,
        "redis": "connected" if redis_ok else "disconnected",
    }


@app.get("/metrics")
async def metrics_endpoint() -> Response:
    """Prometheus metrics endpoint."""
    return Response(content=get_metrics(), media_type="text/plain; charset=utf-8")


async def handle_call(conn: AudioSocketConnection) -> None:
    """Handle a single AudioSocket call from Asterisk.

    This is the main call loop: reads audio packets and manages the
    session lifecycle. STT/LLM/TTS pipeline will be integrated in
    later phases.
    """
    session = CallSession(conn.channel_uuid)
    session.transition_to(CallState.GREETING)

    if _redis is not None:
        store = SessionStore(_redis)
        await store.save(session)

    active_calls.inc()
    logger.info("Call started: %s", conn.channel_uuid)

    session.transition_to(CallState.LISTENING)

    # TODO: Play greeting TTS (phase-04, phase-06)
    # TODO: Initialize STT streaming (phase-03)

    try:
        while not conn.is_closed:
            packet = await conn.read_audio_packet()
            if packet is None:
                break

            if packet.type == PacketType.HANGUP:
                logger.info("Hangup received: %s", conn.channel_uuid)
                break

            if packet.type == PacketType.AUDIO:
                # TODO: Feed audio to STT streaming (phase-03)
                # TODO: Process STT results → LLM → TTS (phase-06)
                pass

            if packet.type == PacketType.ERROR:
                logger.warning(
                    "Error packet from Asterisk: %s, payload=%s",
                    conn.channel_uuid,
                    packet.payload,
                )
                break

    except asyncio.CancelledError:
        logger.info("Call cancelled (shutdown): %s", conn.channel_uuid)

    # Cleanup
    session.transition_to(CallState.ENDED)
    if _redis is not None:
        store = SessionStore(_redis)
        await store.delete(conn.channel_uuid)

    active_calls.dec()
    calls_total.labels(
        status="transferred" if session.transferred else "completed"
    ).inc()

    logger.info(
        "Call ended: %s, duration=%ds, turns=%d",
        conn.channel_uuid,
        session.duration_seconds,
        len(session.dialog_history),
    )


async def start_api_server(settings: Settings) -> None:
    """Start the FastAPI server for health checks and metrics."""
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=settings.prometheus_port,
        log_level=settings.logging.level.lower(),
    )
    server = uvicorn.Server(config)
    await server.serve()


async def main() -> None:
    """Main application entry point."""
    global _audio_server, _redis

    settings = get_settings()

    # Configure structured logging
    setup_logging(level=settings.logging.level, format_type=settings.logging.format)

    logger.info("Starting Call Center AI v0.1.0")

    # Initialize Redis
    _redis = Redis.from_url(settings.redis.url, decode_responses=False)
    try:
        await _redis.ping()
        logger.info("Redis connected: %s", settings.redis.url)
    except Exception:
        logger.warning("Redis unavailable — sessions will be in-memory only")
        await _redis.aclose()
        _redis = None

    # Start AudioSocket server
    _audio_server = AudioSocketServer(
        host=settings.audio_socket.host,
        port=settings.audio_socket.port,
        on_connection=handle_call,
    )
    await _audio_server.start()

    # Graceful shutdown
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    # Start API server (health checks, metrics)
    api_task = asyncio.create_task(start_api_server(settings))

    # TODO: Initialize STT, TTS, Agent (phases 03-05)

    logger.info(
        "Call Center AI started — AudioSocket:%d, API:%d",
        settings.audio_socket.port,
        settings.prometheus_port,
    )

    # Wait for shutdown
    await stop_event.wait()

    logger.info("Shutting down...")
    await _audio_server.stop()
    api_task.cancel()

    if _redis is not None:
        await _redis.aclose()
        logger.info("Redis connection closed")

    logger.info("Call Center AI stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
