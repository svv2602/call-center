"""Call Center AI — Application entry point."""

import asyncio
import logging
import signal
import sys

import uvicorn
from fastapi import FastAPI
from redis.asyncio import Redis

from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from src.agent.agent import LLMAgent, ToolRouter
from src.api.analytics import router as analytics_router
from src.api.auth import router as auth_router
from src.api.knowledge import router as knowledge_router
from src.api.prompts import router as prompts_router
from src.config import Settings, get_settings
from src.core.audio_socket import AudioSocketConnection, AudioSocketServer, PacketType
from src.core.call_session import CallSession, CallState, SessionStore
from src.core.pipeline import CallPipeline
from src.logging.structured_logger import setup_logging
from src.monitoring.metrics import active_calls, calls_total, get_metrics
from src.stt.base import STTConfig
from src.stt.google_stt import GoogleSTTEngine
from src.store_client.client import StoreClient
from src.tts.google_tts import GoogleTTSEngine

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Call Center AI",
    description="AI-powered call center for tire shop",
    version="0.1.0",
)
app.include_router(analytics_router)
app.include_router(auth_router)
app.include_router(knowledge_router)
app.include_router(prompts_router)


@app.get("/admin")
async def admin_ui() -> FileResponse:
    """Serve the admin UI."""
    return FileResponse("admin-ui/index.html")

# Module-level references for health checks and shared components
_audio_server: AudioSocketServer | None = None
_redis: Redis | None = None  # type: ignore[type-arg]
_store_client: StoreClient | None = None
_tts_engine: GoogleTTSEngine | None = None


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

    Creates per-call STT engine and LLM agent, then delegates to
    CallPipeline which orchestrates the STT → LLM → TTS loop.
    """
    settings = get_settings()
    session = CallSession(conn.channel_uuid)

    if _redis is not None:
        store = SessionStore(_redis)
        await store.save(session)

    active_calls.inc()
    logger.info("Call started: %s", conn.channel_uuid)

    try:
        # Per-call STT engine (each call gets its own streaming session)
        stt = GoogleSTTEngine()
        stt_config = STTConfig(
            language_code=settings.google_stt.language_code,
            alternative_languages=settings.google_stt.alternative_language_list,
        )

        # Per-call tool router and LLM agent
        router = _build_tool_router(session)
        agent = LLMAgent(
            api_key=settings.anthropic.api_key,
            model=settings.anthropic.model,
            tool_router=router,
        )

        # Run the pipeline (greeting → listen → STT → LLM → TTS loop)
        pipeline = CallPipeline(conn, stt, _tts_engine, agent, session, stt_config)
        await pipeline.run()

    except asyncio.CancelledError:
        logger.info("Call cancelled (shutdown): %s", conn.channel_uuid)
    except Exception:
        logger.exception("Unhandled error in call: %s", conn.channel_uuid)

    # Cleanup
    if session.state != CallState.ENDED:
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


def _build_tool_router(session: CallSession) -> ToolRouter:
    """Build a ToolRouter with all canonical tools registered."""
    router = ToolRouter()

    assert _store_client is not None, "StoreClient must be initialized before handling calls"

    router.register("search_tires", _store_client.search_tires)
    router.register("check_availability", _store_client.check_availability)
    router.register("get_order_status", _store_client.search_orders)
    router.register("create_order_draft", _store_client.create_order)
    router.register("update_order_delivery", _store_client.update_delivery)
    router.register("confirm_order", _store_client.confirm_order)
    router.register("get_fitting_stations", _store_client.get_fitting_stations)
    router.register("get_fitting_slots", _store_client.get_fitting_slots)
    router.register("book_fitting", _store_client.book_fitting)
    router.register("search_knowledge_base", _store_client.search_knowledge_base)

    async def transfer_to_operator(**_: object) -> dict[str, str]:
        session.transferred = True
        return {"status": "transferring", "message": "З'єдную з оператором"}

    router.register("transfer_to_operator", transfer_to_operator)

    return router


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
    global _audio_server, _redis, _store_client, _tts_engine

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

    # Initialize shared components (TTS is shared across calls, StoreClient too)
    _store_client = StoreClient(
        base_url=settings.store_api.url,
        api_key=settings.store_api.key,
        timeout=settings.store_api.timeout,
    )
    await _store_client.open()
    logger.info("StoreClient initialized: %s", settings.store_api.url)

    _tts_engine = GoogleTTSEngine()
    await _tts_engine.initialize()
    logger.info("TTS engine initialized")

    # Start API server (health checks, metrics)
    api_task = asyncio.create_task(start_api_server(settings))

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

    if _store_client is not None:
        await _store_client.close()
        logger.info("StoreClient closed")

    if _redis is not None:
        await _redis.aclose()
        logger.info("Redis connection closed")

    logger.info("Call Center AI stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
