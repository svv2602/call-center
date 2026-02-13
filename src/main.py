"""Call Center AI — Application entry point."""

import asyncio
import logging
import signal
import sys

import uvicorn
from fastapi import FastAPI

from src.config import get_settings

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Call Center AI",
    description="AI-powered call center for tire shop",
    version="0.1.0",
)


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}


async def start_api_server(settings: "src.config.Settings") -> None:
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
    settings = get_settings()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, settings.logging.level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    logger.info("Starting Call Center AI v0.1.0")
    logger.info("AudioSocket: %s:%d", settings.audio_socket.host, settings.audio_socket.port)
    logger.info("API/Metrics: port %d", settings.prometheus_port)

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

    # TODO: Start AudioSocket server (phase-02)
    # TODO: Initialize STT, TTS, Agent, Pipeline (phases 03-06)

    logger.info("Call Center AI started, waiting for calls...")

    # Wait for shutdown
    await stop_event.wait()

    logger.info("Shutting down...")
    api_task.cancel()

    # TODO: Close connections (Redis, PostgreSQL, AudioSocket)

    logger.info("Call Center AI stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
