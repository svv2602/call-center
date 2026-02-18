"""Call Center AI — Application entry point."""

import asyncio
import contextlib
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

import aiohttp
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis

from src.agent.agent import LLMAgent, ToolRouter
from src.agent.prompt_manager import PromptManager
from src.agent.tool_loader import get_tools_with_overrides
from src.api.admin_users import router as admin_users_router
from src.api.analytics import router as analytics_router
from src.api.auth import router as auth_router
from src.api.export import router as export_router
from src.api.knowledge import router as knowledge_router
from src.api.llm_config import router as llm_config_router
from src.api.middleware.audit import AuditMiddleware
from src.api.middleware.rate_limit import RateLimitMiddleware
from src.api.middleware.security_headers import SecurityHeadersMiddleware
from src.api.operators import router as operators_router
from src.api.prompts import router as prompts_router
from src.api.scraper import router as scraper_router
from src.api.system import router as system_router
from src.api.training_dialogues import router as training_dialogues_router
from src.api.training_safety import router as training_safety_router
from src.api.training_templates import router as training_templates_router
from src.api.training_tools import router as training_tools_router
from src.api.vehicles import router as vehicles_router
from src.api.websocket import router as websocket_router
from src.config import Settings, get_settings
from src.core.audio_socket import AudioSocketConnection, AudioSocketServer
from src.core.call_session import CallSession, CallState, SessionStore
from src.core.pipeline import CallPipeline
from src.events.publisher import publish_event
from src.logging.pii_vault import PIIVault
from src.logging.structured_logger import setup_logging
from src.monitoring.metrics import active_calls, calls_total, get_metrics
from src.onec_client.client import OneCClient
from src.onec_client.sync import CatalogSyncService
from src.store_client.client import StoreClient
from src.stt.base import STTConfig
from src.stt.google_stt import GoogleSTTEngine
from src.tts.google_tts import GoogleTTSEngine

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Call Center AI",
    description="AI-powered call center for tire shop",
    version="0.1.0",
)
app.include_router(admin_users_router)
app.include_router(analytics_router)
app.include_router(auth_router)
app.include_router(export_router)
app.include_router(knowledge_router)
app.include_router(llm_config_router)
app.include_router(operators_router)
app.include_router(prompts_router)
app.include_router(scraper_router)
app.include_router(system_router)
app.include_router(training_dialogues_router)
app.include_router(training_safety_router)
app.include_router(training_templates_router)
app.include_router(training_tools_router)
app.include_router(vehicles_router)
app.include_router(websocket_router)
# Middleware order (last added = outermost = runs first):
# SecurityHeaders → RateLimit → CORS → Audit
app.add_middleware(AuditMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",")
    if os.environ.get("CORS_ALLOWED_ORIGINS")
    else [],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)


# Admin UI: serve from dist/ (production build) or root (dev with Vite proxy)
_admin_dist = Path("admin-ui/dist")
_admin_root = Path("admin-ui")
_admin_dir = _admin_dist if _admin_dist.is_dir() else _admin_root

if (_admin_dir / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=_admin_dir / "assets"), name="admin-assets")


@app.get("/admin")
async def admin_ui() -> FileResponse:
    """Serve the admin UI."""
    return FileResponse(str(_admin_dir / "index.html"))


# Module-level references for health checks and shared components
_audio_server: AudioSocketServer | None = None
_redis: Redis | None = None
_store_client: StoreClient | None = None
_tts_engine: GoogleTTSEngine | None = None
_onec_client: OneCClient | None = None
_sync_service: CatalogSyncService | None = None
_sync_task: asyncio.Task | None = None  # type: ignore[type-arg]
_db_engine: Any = None
_llm_router: Any = None  # LLMRouter when FF_LLM_ROUTING_ENABLED=true


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


@app.get("/health/ready")
async def readiness_check() -> dict[str, object]:
    """Readiness probe — checks external dependencies (STT, LLM, TTS).

    Per deployment.md: verifies Google STT reachable, Claude API reachable,
    TTS initialized, Store API reachable, Redis connected.
    """
    checks: dict[str, str] = {}

    # Redis
    if _redis is not None:
        try:
            await _redis.ping()
            checks["redis"] = "connected"
        except Exception:
            checks["redis"] = "disconnected"
    else:
        checks["redis"] = "not_initialized"

    # Store API — lightweight HEAD request to base URL
    if _store_client is not None and _store_client._session is not None:
        try:
            async with _store_client._session.get(
                f"{_store_client._base_url}/health",
                timeout=aiohttp.ClientTimeout(total=3),
            ) as resp:
                checks["store_api"] = "reachable" if resp.status < 500 else "error"
        except Exception:
            checks["store_api"] = "unreachable"
    else:
        checks["store_api"] = "not_initialized"

    # TTS engine
    checks["tts_engine"] = "initialized" if _tts_engine is not None else "not_initialized"

    # Claude API — lightweight models list call
    settings = get_settings()
    if settings.anthropic.api_key:
        try:
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=settings.anthropic.api_key)
            await asyncio.wait_for(client.models.list(limit=1), timeout=3.0)
            checks["claude_api"] = "reachable"
        except Exception:
            checks["claude_api"] = "unreachable"
    else:
        checks["claude_api"] = "no_api_key"

    # 1C API
    if _onec_client is not None and _onec_client._session is not None:
        try:
            # Lightweight stock request to check 1C connectivity
            resp = await asyncio.wait_for(_onec_client.get_stock("ProKoleso"), timeout=5.0)
            checks["onec_api"] = "reachable" if resp.get("success") else "error"
        except Exception:
            checks["onec_api"] = "unreachable"
    else:
        checks["onec_api"] = "not_configured"

    # Google STT — check credentials file exists
    import os

    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    checks["google_stt"] = (
        "credentials_present" if (creds_path and os.path.isfile(creds_path)) else "no_credentials"
    )

    all_ok = all(
        v in ("connected", "reachable", "initialized", "credentials_present", "not_configured")
        for v in checks.values()
    )

    return {
        "status": "ready" if all_ok else "not_ready",
        **checks,
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
    await publish_event("call:started", {"call_id": str(conn.channel_uuid)})

    try:
        # Per-call STT engine (each call gets its own streaming session)
        stt = GoogleSTTEngine()
        stt_config = STTConfig(
            language_code=settings.google_stt.language_code,
            alternative_languages=settings.google_stt.alternative_language_list,
        )

        # Load DB templates and tool overrides (if DB is available)
        templates = None
        tools = None
        if _db_engine is not None:
            pm = PromptManager(_db_engine)
            templates = await pm.get_active_templates()
            tools = await get_tools_with_overrides(_db_engine)

        # Per-call tool router, PII vault, and LLM agent
        router = _build_tool_router(session)
        vault = PIIVault()
        agent = LLMAgent(
            api_key=settings.anthropic.api_key,
            model=settings.anthropic.model,
            tool_router=router,
            pii_vault=vault,
            tools=tools,
            llm_router=_llm_router,
        )

        # Run the pipeline (greeting → listen → STT → LLM → TTS loop)
        assert _tts_engine is not None, "TTS engine must be initialized before handling calls"
        pipeline = CallPipeline(conn, stt, _tts_engine, agent, session, stt_config, templates)
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
    status = "transferred" if session.transferred else "completed"
    calls_total.labels(status=status).inc()
    await publish_event(
        "call:ended",
        {
            "call_id": str(conn.channel_uuid),
            "status": status,
            "duration_seconds": session.duration_seconds,
        },
    )

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

    router.register("get_vehicle_tire_sizes", _store_client.get_vehicle_tire_sizes)
    router.register("search_tires", _store_client.search_tires)
    router.register("check_availability", _store_client.check_availability)
    router.register("get_order_status", _store_client.search_orders)
    router.register("create_order_draft", _store_client.create_order)
    router.register("update_order_delivery", _store_client.update_delivery)
    router.register("confirm_order", _store_client.confirm_order)
    router.register("get_fitting_stations", _store_client.get_fitting_stations)
    router.register("get_fitting_slots", _store_client.get_fitting_slots)
    router.register("book_fitting", _store_client.book_fitting)
    router.register("cancel_fitting", _store_client.cancel_fitting)
    router.register("get_fitting_price", _store_client.get_fitting_price)
    router.register("search_knowledge_base", _store_client.search_knowledge_base)

    async def transfer_to_operator(**_: object) -> dict[str, str]:
        session.transferred = True
        await publish_event("call:transferred", {"call_id": str(session.call_id)})
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


async def _periodic_sync(sync_service: CatalogSyncService, interval_minutes: int) -> None:
    """Run incremental catalog sync periodically."""
    while True:
        await asyncio.sleep(interval_minutes * 60)
        try:
            await sync_service.incremental_sync()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Periodic catalog sync failed")


async def main() -> None:
    """Main application entry point."""
    global _audio_server, _redis, _store_client, _tts_engine
    global _onec_client, _sync_service, _sync_task, _db_engine, _llm_router

    settings = get_settings()

    # Validate configuration before anything else
    validation = settings.validate_required()
    if not validation.ok:
        for err in validation.errors:
            hint = f" Hint: {err.hint}" if err.hint else ""
            print(f"\u274c {err.field}: {err.message}.{hint}")
        print(f"\n{len(validation.errors)} configuration error(s). Fix them and restart.")
        sys.exit(1)

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

    # Initialize LLM Router (when feature flag is enabled)
    if settings.feature_flags.llm_routing_enabled:
        try:
            from src.llm.router import LLMRouter

            _llm_router = LLMRouter()
            await _llm_router.initialize(redis=_redis)
            logger.info("LLM router initialized (multi-provider routing enabled)")

            # Share router with Celery tasks
            from src.tasks.prompt_optimizer import set_llm_router as set_optimizer_router
            from src.tasks.quality_evaluator import set_llm_router as set_evaluator_router

            set_evaluator_router(_llm_router)
            set_optimizer_router(_llm_router)
        except Exception:
            logger.warning("LLM router init failed — falling back to direct Anthropic", exc_info=True)
            _llm_router = None
    else:
        logger.info("LLM routing disabled (FF_LLM_ROUTING_ENABLED=false)")

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

    # Initialize 1C client and DB engine (MVP: tire catalog + stock)
    if settings.onec.username:
        try:
            from sqlalchemy.ext.asyncio import create_async_engine

            _db_engine = create_async_engine(settings.database.url, pool_size=5, max_overflow=5)
            logger.info("Database engine created: %s", settings.database.url.split("@")[-1])

            _onec_client = OneCClient(
                base_url=settings.onec.url,
                username=settings.onec.username,
                password=settings.onec.password,
                timeout=settings.onec.timeout,
            )
            await _onec_client.open()
            logger.info("OneCClient initialized: %s", settings.onec.url)

            # Full sync at startup (non-fatal if 1C is unreachable)
            _sync_service = CatalogSyncService(
                onec_client=_onec_client,
                db_engine=_db_engine,
                redis=_redis,
                stock_cache_ttl=settings.onec.stock_cache_ttl,
            )
            try:
                await _sync_service.full_sync()
                logger.info("Initial catalog sync completed")
            except Exception:
                logger.warning(
                    "Initial catalog sync failed — will retry periodically", exc_info=True
                )

            # Start periodic incremental sync
            _sync_task = asyncio.create_task(
                _periodic_sync(_sync_service, settings.onec.sync_interval_minutes)
            )
        except Exception:
            logger.warning(
                "1C integration init failed — MVP tools will use fallback HTTP", exc_info=True
            )
            _onec_client = None
            _db_engine = None
    else:
        logger.info("1C integration not configured (ONEC_USERNAME empty)")

    # Initialize shared components (TTS is shared across calls, StoreClient too)
    _store_client = StoreClient(
        base_url=settings.store_api.url,
        api_key=settings.store_api.key,
        timeout=settings.store_api.timeout,
        db_engine=_db_engine,
        redis=_redis,
        stock_cache_ttl=settings.onec.stock_cache_ttl,
    )
    await _store_client.open()
    logger.info("StoreClient initialized: %s", settings.store_api.url)

    try:
        _tts_engine = GoogleTTSEngine()
        await _tts_engine.initialize()
        logger.info("TTS engine initialized")
    except Exception:
        logger.warning(
            "TTS engine unavailable (no Google credentials?) — calls will not work, but API is running"
        )
        _tts_engine = None

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

    # Cancel periodic sync
    if _sync_task is not None:
        _sync_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _sync_task
        logger.info("Periodic sync stopped")

    if _llm_router is not None:
        await _llm_router.close()
        logger.info("LLM router closed")

    if _store_client is not None:
        await _store_client.close()
        logger.info("StoreClient closed")

    if _onec_client is not None:
        await _onec_client.close()
        logger.info("OneCClient closed")

    if _db_engine is not None:
        await _db_engine.dispose()
        logger.info("Database engine disposed")

    if _redis is not None:
        await _redis.aclose()
        logger.info("Redis connection closed")

    logger.info("Call Center AI stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
