"""Call Center AI — Application entry point."""

import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiohttp
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis
from sqlalchemy import text

from src.agent.agent import LLMAgent, ToolRouter
from src.agent.prompt_manager import (
    PromptManager,
    fetch_tenant_promotions,
    format_few_shot_section,
    format_promotions_context,
    format_safety_rules_section,
    get_few_shot_examples,
    get_pronunciation_rules,
    get_safety_rules_for_prompt,
    inject_pronunciation_rules,
)
from src.agent.prompts import (
    GREETING_TEXT_KNOWN,
    assemble_prompt,
    format_caller_history,
    format_customer_profile,
    format_storage_context,
)
from src.agent.tool_loader import get_tools_with_overrides
from src.api.admin_users import router as admin_users_router
from src.api.analytics import router as analytics_router
from src.api.auth import router as auth_router
from src.api.customers import router as customers_router
from src.api.export import router as export_router
from src.api.fitting_hints import router as fitting_hints_router
from src.api.knowledge import router as knowledge_router
from src.api.llm_config import router as llm_config_router
from src.api.llm_costs import router as llm_costs_router
from src.api.middleware.audit import AuditMiddleware
from src.api.middleware.rate_limit import RateLimitMiddleware
from src.api.middleware.security_headers import SecurityHeadersMiddleware
from src.api.notifications import router as notifications_router
from src.api.onec_data import router as onec_data_router
from src.api.operators import router as operators_router
from src.api.prompts import router as prompts_router
from src.api.pronunciation import router as pronunciation_router
from src.api.sandbox import router as sandbox_router
from src.api.scraper import router as scraper_router
from src.api.stt_config import router as stt_config_router
from src.api.system import router as system_router
from src.api.task_schedules import router as task_schedules_router
from src.api.tenants import router as tenants_router
from src.api.test_phones import router as test_phones_router
from src.api.training_dialogues import router as training_dialogues_router
from src.api.training_safety import router as training_safety_router
from src.api.training_templates import router as training_templates_router
from src.api.training_tools import router as training_tools_router
from src.api.tts_config import router as tts_config_router
from src.api.vehicles import router as vehicles_router
from src.api.websocket import router as websocket_router
from src.config import Settings, get_settings
from src.core.audio_socket import AudioSocketConnection, AudioSocketServer
from src.core.call_session import CallSession, CallState, SessionStore
from src.core.pipeline import CallPipeline
from src.events.publisher import publish_event
from src.logging.pii_vault import PIIVault
from src.logging.structured_logger import setup_logging
from src.monitoring.cost_tracker import CostBreakdown
from src.monitoring.metrics import (
    active_calls,
    call_duration_seconds,
    call_scenario_total,
    calls_resolved_by_bot_total,
    calls_total,
    fittings_booked_total,
    get_metrics,
    orders_created_total,
)
from src.onec_client.client import OneCClient
from src.onec_client.soap import OneCSOAPClient
from src.store_client.client import StoreClient
from src.stt.base import STTConfig
from src.stt.google_stt import GoogleSTTEngine
from src.tts.base import TTSConfig
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
app.include_router(customers_router)
app.include_router(export_router)
app.include_router(fitting_hints_router)
app.include_router(knowledge_router)
app.include_router(llm_config_router)
app.include_router(llm_costs_router)
app.include_router(notifications_router)
app.include_router(onec_data_router)
app.include_router(operators_router)
app.include_router(pronunciation_router)
app.include_router(prompts_router)
app.include_router(sandbox_router)
app.include_router(scraper_router)
app.include_router(stt_config_router)
app.include_router(system_router)
app.include_router(task_schedules_router)
app.include_router(tenants_router)
app.include_router(test_phones_router)
app.include_router(tts_config_router)
app.include_router(training_dialogues_router)
app.include_router(training_safety_router)
app.include_router(training_templates_router)
app.include_router(training_tools_router)
app.include_router(vehicles_router)
app.include_router(websocket_router)
# Middleware order (last added = outermost = runs first):
# SecurityHeaders → RateLimit → CORS → Audit
app.add_middleware(AuditMiddleware)
_DEFAULT_CORS_ORIGINS = [
    "http://localhost:5173",  # Vite dev server
    "http://localhost:8080",  # Backend (same-origin, but explicit)
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",")
    if os.environ.get("CORS_ALLOWED_ORIGINS")
    else _DEFAULT_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)


@app.on_event("shutdown")
async def _dispose_api_engines() -> None:
    """Dispose all module-level SQLAlchemy engines cached by API routers."""
    from src.api import (
        admin_users,
        analytics,
        auth,
        customers,
        export,
        knowledge,
        operators,
        prompts,
        sandbox,
        scraper,
        system,
        tenants,
        training_dialogues,
        training_safety,
        training_templates,
        training_tools,
        vehicles,
    )
    from src.api.middleware import audit

    modules = [
        admin_users,
        analytics,
        auth,
        customers,
        export,
        knowledge,
        operators,
        prompts,
        sandbox,
        scraper,
        system,
        tenants,
        training_dialogues,
        training_safety,
        training_templates,
        training_tools,
        vehicles,
        audit,
    ]
    for mod in modules:
        engine = getattr(mod, "_engine", None)
        if engine is not None:
            await engine.dispose()
    logger.info("API router engines disposed")


# Admin UI: serve from dist/ (production build) or root (dev with Vite proxy)
_admin_dist = Path("admin-ui/dist")
_admin_root = Path("admin-ui")
_admin_dir = _admin_dist if _admin_dist.is_dir() else _admin_root

if (_admin_dir / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=_admin_dir / "assets"), name="admin-assets")


@app.get("/admin")
async def admin_ui() -> FileResponse:
    """Serve the admin UI."""
    return FileResponse(
        str(_admin_dir / "index.html"),
        headers={"Cache-Control": "no-cache"},
    )


# Module-level references for health checks and shared components
_audio_server: AudioSocketServer | None = None
_redis: Redis | None = None
_store_client: StoreClient | None = None
_tts_engine: GoogleTTSEngine | None = None
_onec_client: OneCClient | None = None
_soap_client: OneCSOAPClient | None = None
_embedding_task: asyncio.Task | None = None  # type: ignore[type-arg]
_db_engine: Any = None
_call_logger: Any = None  # CallLogger for persisting calls to PostgreSQL
_llm_router: Any = None  # LLMRouter when FF_LLM_ROUTING_ENABLED=true
_asyncpg_pool: Any = None  # asyncpg pool for PatternSearch (pgvector)
_knowledge_search: Any = None  # KnowledgeSearch (pgvector) for search_knowledge_base tool
_search_embedding_gen: Any = None  # EmbeddingGenerator shared by KnowledgeSearch

_SENTINEL = object()  # sentinel for optional pre-fetched values


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
    """Readiness probe — checks external dependencies in parallel.

    Per deployment.md: verifies Google STT reachable, Claude API reachable,
    TTS initialized, Store API reachable, Redis connected.
    All independent checks run via asyncio.gather for 5-10x faster response.
    """

    async def _check_redis() -> tuple[str, str]:
        if _redis is None:
            return ("redis", "not_initialized")
        try:
            await _redis.ping()
            return ("redis", "connected")
        except Exception:
            return ("redis", "disconnected")

    async def _check_postgresql() -> tuple[str, str]:
        if _db_engine is None:
            return ("postgresql", "not_initialized")
        try:
            async with _db_engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
            return ("postgresql", "connected")
        except Exception:
            return ("postgresql", "disconnected")

    async def _check_asyncpg() -> tuple[str, str]:
        if _asyncpg_pool is None:
            return ("asyncpg_pool", "not_initialized")
        try:
            async with _asyncpg_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return ("asyncpg_pool", "connected")
        except Exception:
            return ("asyncpg_pool", "disconnected")

    async def _check_store_api() -> tuple[str, str]:
        if _store_client is None or _store_client._session is None:
            return ("store_api", "not_initialized")
        try:
            async with _store_client._session.get(
                f"{_store_client._base_url}/health",
                timeout=aiohttp.ClientTimeout(total=3),
            ) as resp:
                return ("store_api", "reachable" if resp.status < 500 else "error")
        except Exception:
            return ("store_api", "unreachable")

    async def _check_tts() -> tuple[str, str]:
        return ("tts_engine", "initialized" if _tts_engine is not None else "not_initialized")

    async def _check_llm() -> tuple[str, str]:
        if _llm_router is not None and _llm_router._providers:
            try:
                provider = next(iter(_llm_router._providers.values()))
                healthy = await asyncio.wait_for(provider.health_check(), timeout=5.0)
                return ("llm_api", "reachable" if healthy else "unreachable")
            except Exception:
                return ("llm_api", "unreachable")
        settings = get_settings()
        return ("llm_api", "no_api_key" if not settings.anthropic.api_key else "not_initialized")

    async def _check_onec() -> tuple[str, str]:
        if _onec_client is None or _onec_client._session is None:
            return ("onec_api", "not_configured")
        try:
            onec_resp: dict[str, Any] = await asyncio.wait_for(
                _onec_client.get_stock("ProKoleso"), timeout=5.0
            )
            return ("onec_api", "reachable" if onec_resp.get("success") else "error")
        except Exception:
            return ("onec_api", "unreachable")

    async def _check_stt_creds() -> tuple[str, str]:
        creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
        return (
            "google_stt",
            "credentials_present"
            if (creds_path and os.path.isfile(creds_path))
            else "no_credentials",
        )

    results = await asyncio.gather(
        _check_redis(),
        _check_postgresql(),
        _check_asyncpg(),
        _check_store_api(),
        _check_tts(),
        _check_llm(),
        _check_onec(),
        _check_stt_creds(),
        return_exceptions=True,
    )

    checks: dict[str, str] = {}
    for r in results:
        if isinstance(r, BaseException):
            continue
        checks[r[0]] = r[1]

    all_ok = all(
        v in ("connected", "reachable", "initialized", "credentials_present", "not_configured")
        for v in checks.values()
    )

    return {
        "status": "ready" if all_ok else "not_ready",
        **checks,
    }


@app.post("/internal/caller-id")
async def store_caller_id(data: dict[str, str]) -> dict[str, str]:
    """Store CallerID and called extension in Redis for a call UUID.

    Called by Asterisk dialplan before AudioSocket:
      System(curl -s -X POST http://...:8080/internal/caller-id
        -H 'Content-Type: application/json'
        -d '{"uuid":"${CALL_UUID}","number":"${CALLERID(num)}","exten":"${CALLED_EXTEN}"}')
    """
    call_uuid = data.get("uuid", "").strip()
    number = data.get("number", "").strip()
    exten = data.get("exten", "").strip()
    if not call_uuid:
        return {"status": "ignored"}
    if _redis is not None:
        if number:
            await _redis.set(f"call:caller:{call_uuid}", number, ex=120)
        if exten:
            await _redis.set(f"call:exten:{call_uuid}", exten, ex=120)
    return {"status": "ok"}


@app.get("/metrics")
async def metrics_endpoint() -> Response:
    """Prometheus metrics endpoint."""
    return Response(content=get_metrics(), media_type="text/plain; charset=utf-8")


_VALID_IVR_INTENTS = {"tire_search", "order_status", "fitting", "consultation"}

# Tools allowed per IVR scenario (None = all tools, no filtering)
_SCENARIO_TOOLS: dict[str, set[str]] = {
    "tire_search": {
        "get_vehicle_tire_sizes",
        "search_tires",
        "check_availability",
        "create_order_draft",
        "update_order_delivery",
        "confirm_order",
        "get_pickup_points",
        "search_knowledge_base",
        "transfer_to_operator",
    },
    "order_status": {
        "get_order_status",
        "search_knowledge_base",
        "transfer_to_operator",
    },
    "fitting": {
        "get_fitting_stations",
        "get_fitting_slots",
        "book_fitting",
        "cancel_fitting",
        "get_fitting_price",
        "get_customer_bookings",
        "find_storage",
        "search_knowledge_base",
        "transfer_to_operator",
    },
    "consultation": {
        "search_knowledge_base",
        "transfer_to_operator",
    },
}

# Scenario-specific focus appended to system prompt (Ukrainian)
_SCENARIO_EMPHASIS: dict[str, str] = {
    "tire_search": (
        "\n\n[IVR-фокус] Клієнт вже обрав підбір шин через IVR. "
        "Не питай чого він дзвонить — одразу з'ясовуй розмір та сезон."
    ),
    "order_status": (
        "\n\n[IVR-фокус] Клієнт дзвонить перевірити статус. "
        "Запитай номер замовлення або телефон, одразу виклич get_order_status."
    ),
    "fitting": (
        "\n\n[IVR-фокус] Клієнт дзвонить записатися на шиномонтаж. "
        "Запитай місто, потім пропонуй точки та час."
    ),
    "consultation": (
        "\n\n[IVR-фокус] Клієнт має питання. Слухай і шукай відповідь через search_knowledge_base."
    ),
}


async def _batch_redis_lookups(channel_uuid: str) -> tuple[str | None, str | None]:
    """Batch Redis lookups for call start: exten + caller_id.

    Uses a pipeline to fetch both values in a single round-trip.
    Returns (exten, caller_id) — either may be None.
    """
    if _redis is None:
        return None, None
    try:
        pipe = _redis.pipeline(transaction=False)
        pipe.get(f"call:exten:{channel_uuid}")
        pipe.get(f"call:caller:{channel_uuid}")
        results = await pipe.execute()
        exten = results[0].decode().strip() if results[0] else None
        caller = results[1].decode().strip() if results[1] else None
        return exten or None, caller or None
    except Exception:
        logger.debug("Redis pipeline failed, falling back", exc_info=True)
        return None, None


async def _resolve_caller_id(
    channel_uuid: str, *, prefetched: str | None = _SENTINEL
) -> str | None:
    """Resolve caller phone number from Redis.

    Asterisk dialplan stores CallerID before AudioSocket:
      same => n,Set(CALL_UUID=${UUID()})
      same => n,System(redis-cli -h <host> SET call:caller:${CALL_UUID} ${CALLERID(num)} EX 120)
      same => n,AudioSocket(${CALL_UUID},...)

    Fallback: try ARI channel variable CALLER_NUMBER.
    """
    # Use pre-fetched value from Redis pipeline if available
    if prefetched is not _SENTINEL:
        if prefetched:
            logger.info("CallerID from Redis pipeline: %s for call %s", prefetched, channel_uuid)
            return prefetched
    else:
        # Standalone Redis read (no pipeline)
        if _redis is not None:
            try:
                raw = await _redis.get(f"call:caller:{channel_uuid}")
                if raw:
                    value = raw.decode() if isinstance(raw, bytes) else str(raw)
                    if value.strip():
                        logger.info(
                            "CallerID from Redis: %s for call %s", value.strip(), channel_uuid
                        )
                        return value.strip()
            except Exception:
                logger.debug("Redis CallerID lookup failed", exc_info=True)

    # Fallback: ARI channel variable
    settings = get_settings()
    if settings.ari.url:
        try:
            from src.core.asterisk_ari import AsteriskARIClient

            ari = AsteriskARIClient(
                url=settings.ari.url,
                user=settings.ari.user,
                password=settings.ari.password,
            )
            await ari.open()
            try:
                raw_ari = await ari.get_channel_variable(channel_uuid, "CALLER_NUMBER")
                if raw_ari and raw_ari.strip():
                    logger.info("CallerID from ARI: %s for call %s", raw_ari.strip(), channel_uuid)
                    return raw_ari.strip()
            finally:
                await ari.close()
        except Exception:
            logger.debug("ARI CALLER_NUMBER lookup failed", exc_info=True)

    return None


async def _resolve_ivr_intent(channel_uuid: str) -> str | None:
    """Resolve IVR intent from Asterisk channel variable IVR_INTENT.

    Returns one of _VALID_IVR_INTENTS or None if not set / invalid.
    """
    settings = get_settings()
    if not settings.ari.url:
        return None

    try:
        from src.core.asterisk_ari import AsteriskARIClient

        ari = AsteriskARIClient(
            url=settings.ari.url,
            user=settings.ari.user,
            password=settings.ari.password,
        )
        await ari.open()
        try:
            raw = await ari.get_channel_variable(channel_uuid, "IVR_INTENT")
        finally:
            await ari.close()

        if raw and raw.strip().lower() in _VALID_IVR_INTENTS:
            return raw.strip().lower()
    except Exception:
        logger.debug("ARI IVR_INTENT lookup failed", exc_info=True)

    return None


async def _resolve_tenant(
    channel_uuid: str,
    db_engine: Any,
    *,
    prefetched_exten: str | None = _SENTINEL,
) -> dict[str, Any] | None:
    """Resolve tenant for the current call.

    Three-level resolution:
    1. Try ARI channel variable TENANT_SLUG (direct slug mapping, backward compat).
    2. Try ARI channel variable CALLED_EXTEN → lookup tenant by extensions[] in DB.
    3. Fallback to the first active tenant in DB.
    Returns tenant dict or None.
    """
    slug: str | None = None
    called_exten: str | None = None

    tenant_columns = (
        "id, slug, name, network_id, agent_name, greeting, "
        "enabled_tools, prompt_suffix, config, is_active, extensions"
    )

    # 1. Primary: use pre-fetched exten from Redis pipeline, or read directly
    if prefetched_exten is not _SENTINEL:
        called_exten = prefetched_exten
        if called_exten:
            logger.info(
                "CALLED_EXTEN from Redis pipeline: %s for call %s", called_exten, channel_uuid
            )
    elif _redis is not None:
        try:
            raw = await _redis.get(f"call:exten:{channel_uuid}")
            if raw:
                called_exten = raw.decode() if isinstance(raw, bytes) else str(raw)
                called_exten = called_exten.strip() or None
                if called_exten:
                    logger.info(
                        "CALLED_EXTEN from Redis: %s for call %s", called_exten, channel_uuid
                    )
        except Exception:
            logger.debug("Redis CALLED_EXTEN lookup failed", exc_info=True)

    # 2. Fallback: try ARI channel variables (best-effort)
    if not called_exten:
        settings = get_settings()
        if settings.ari.url:
            try:
                from src.core.asterisk_ari import AsteriskARIClient

                ari = AsteriskARIClient(
                    url=settings.ari.url,
                    user=settings.ari.user,
                    password=settings.ari.password,
                )
                await ari.open()
                try:
                    slug = await ari.get_channel_variable(channel_uuid, "TENANT_SLUG")
                    if not slug:
                        called_exten = await ari.get_channel_variable(channel_uuid, "CALLED_EXTEN")
                finally:
                    await ari.close()
            except Exception:
                logger.debug("ARI tenant lookup failed", exc_info=True)

    if db_engine is None:
        return None

    try:
        async with db_engine.begin() as conn:
            if slug:
                result = await conn.execute(
                    text(f"""
                        SELECT {tenant_columns}
                        FROM tenants
                        WHERE slug = :slug AND is_active = true
                    """),
                    {"slug": slug},
                )
            elif called_exten:
                # Extension-based lookup
                result = await conn.execute(
                    text(f"""
                        SELECT {tenant_columns}
                        FROM tenants
                        WHERE :exten = ANY(extensions) AND is_active = true
                        LIMIT 1
                    """),
                    {"exten": called_exten},
                )
            else:
                # Fallback: first active tenant
                result = await conn.execute(
                    text(f"""
                        SELECT {tenant_columns}
                        FROM tenants
                        WHERE is_active = true
                        ORDER BY created_at
                        LIMIT 1
                    """)
                )
            row = result.first()
            if row:
                return dict(row._mapping)
    except Exception:
        logger.debug("Tenant DB lookup failed", exc_info=True)

    return None


async def handle_call(conn: AudioSocketConnection) -> None:
    """Handle a single AudioSocket call from Asterisk.

    Creates per-call STT engine and LLM agent, then delegates to
    CallPipeline which orchestrates the STT → LLM → TTS loop.
    """
    settings = get_settings()
    session = CallSession(conn.channel_uuid)
    uuid_str = str(conn.channel_uuid)

    # Batch Redis lookups (single round-trip) then resolve all in parallel
    exten, caller_from_redis = await _batch_redis_lookups(uuid_str)
    tenant, ivr_intent, caller_id = await asyncio.gather(
        _resolve_tenant(uuid_str, _db_engine, prefetched_exten=exten),
        _resolve_ivr_intent(uuid_str),
        _resolve_caller_id(uuid_str, prefetched=caller_from_redis),
    )

    tenant_store_client: StoreClient | None = None
    if tenant:
        session.tenant_id = str(tenant["id"])
        session.tenant_slug = tenant["slug"]
        session.network_id = tenant.get("network_id")
        logger.info(
            "Tenant resolved: %s (%s) for call %s",
            tenant["slug"],
            tenant["name"],
            conn.channel_uuid,
        )
    if ivr_intent:
        session.scenario = ivr_intent
        session.active_scenarios.add(ivr_intent)
        logger.info("IVR intent resolved: %s for call %s", ivr_intent, conn.channel_uuid)
    if caller_id:
        session.caller_id = caller_id
        session.caller_phone = caller_id
        logger.info("CallerID resolved: %s for call %s", caller_id, conn.channel_uuid)

    # Upsert customer (fast indexed lookup, ~5ms)
    if _call_logger is not None and session.caller_id:
        try:
            session.customer_id = await _call_logger.upsert_customer(session.caller_id)
        except Exception:
            logger.debug("upsert_customer failed", exc_info=True)

    if _redis is not None:
        store = SessionStore(_redis)
        await store.save(session)

    active_calls.inc()
    logger.info("Call started: %s", conn.channel_uuid)
    await publish_event("call:started", {"call_id": str(conn.channel_uuid)})

    # Persist call start to PostgreSQL
    if _call_logger is not None:
        try:
            await _call_logger.log_call_start(
                call_id=conn.channel_uuid,
                caller_id=session.caller_id,
                customer_id=getattr(session, "customer_id", None),
                started_at=datetime.now(UTC),
                prompt_version="default",
                tenant_id=session.tenant_id,
            )
        except Exception:
            logger.warning("log_call_start failed", exc_info=True)

    # Per-call cost tracker (created outside try block so it's always available for cleanup)
    cost = CostBreakdown(llm_model=settings.anthropic.model)

    _embedding_gen = None
    try:
        # Per-call STT engine (each call gets its own streaming session)
        stt = GoogleSTTEngine(project_id=settings.google_stt.project_id)
        phrase_hints: tuple[str, ...] = ()
        if _redis is not None:
            try:
                from src.stt.phrase_hints import get_all_phrases_flat

                phrase_hints = await get_all_phrases_flat(_redis)
            except Exception:
                logger.debug("Failed to load STT phrase hints", exc_info=True)
        stt_config = STTConfig(
            language_code=settings.google_stt.language_code,
            alternative_languages=settings.google_stt.alternative_language_list,
            phrase_hints=phrase_hints,
        )

        # Load DB templates, tool overrides, and active prompt in parallel
        templates = None
        tools = None
        system_prompt = None
        prompt_version_name = None
        if _db_engine is not None:
            pm = PromptManager(_db_engine)
            templates, tools, active_prompt = await asyncio.gather(
                pm.get_active_templates(),
                get_tools_with_overrides(_db_engine, redis=_redis),
                pm.get_active_prompt(),
            )
            if active_prompt.get("id") is not None:
                system_prompt = active_prompt["system_prompt"]
                prompt_version_name = active_prompt["name"]

            # A/B test: may override prompt with assigned variant
            try:
                from src.agent.ab_testing import ABTestManager

                ab_manager = ABTestManager(_db_engine)
                assignment = await ab_manager.assign_variant(str(conn.channel_uuid))
                if assignment is not None:
                    variant = await pm.get_version(assignment["prompt_version_id"])
                    if variant:
                        system_prompt = variant["system_prompt"]
                        prompt_version_name = assignment["variant_name"]
                        logger.info(
                            "A/B test override: call=%s, variant=%s (%s)",
                            conn.channel_uuid,
                            assignment["variant_label"],
                            assignment["variant_name"],
                        )
            except Exception:
                logger.warning("A/B test assignment failed, using default prompt", exc_info=True)

        # Load few-shot, safety rules, pronunciation, and promotions in parallel
        few_shot_context = None
        safety_context = None
        promotions_context = None

        async def _load_few_shot() -> dict:
            if _db_engine is None:
                return {}
            try:
                return await get_few_shot_examples(_db_engine, _redis)
            except Exception:
                logger.debug("Few-shot examples loading failed", exc_info=True)
                return {}

        async def _load_safety() -> list:
            if _db_engine is None:
                return []
            try:
                return await get_safety_rules_for_prompt(_db_engine, _redis)
            except Exception:
                logger.debug("Safety rules loading failed", exc_info=True)
                return []

        async def _load_pronunciation() -> str:
            if _redis is None:
                return ""
            try:
                return await get_pronunciation_rules(_redis)
            except Exception:
                logger.debug("Pronunciation rules loading failed", exc_info=True)
                return ""

        async def _load_promotions() -> list:
            if _db_engine is None or not session.tenant_id:
                return []
            try:
                return await fetch_tenant_promotions(
                    _db_engine, str(session.tenant_id), redis=_redis
                )
            except Exception:
                logger.debug("Tenant promotions loading failed", exc_info=True)
                return []

        async def _load_caller_history() -> list:
            if _call_logger is None or not session.caller_id:
                return []
            try:
                # Test phone override — skip history if configured
                if _redis is not None:
                    raw = await _redis.get("test:phones")
                    if raw:
                        from src.utils.phone import normalize_phone_ua

                        phones = json.loads(
                            raw.decode() if isinstance(raw, bytes) else raw
                        )
                        normalized = normalize_phone_ua(session.caller_id)
                        if phones.get(normalized) == "no_history":
                            logger.info(
                                "Test phone %s: skipping history", session.caller_id
                            )
                            return []
                return await _call_logger.get_caller_history(
                    session.caller_id, tenant_id=session.tenant_id
                )
            except Exception:
                logger.debug("Caller history loading failed", exc_info=True)
                return []

        async def _load_storage_contracts() -> dict:
            if _onec_client is None or not session.caller_id:
                return {}
            try:
                return await _onec_client.find_storage(phone=session.caller_id)
            except Exception:
                logger.debug("Storage contracts preload failed", exc_info=True)
                return {}

        async def _load_customer_profile() -> dict | None:
            if _call_logger is None or not session.caller_id:
                return None
            try:
                return await _call_logger.get_customer_profile(session.caller_id)
            except Exception:
                logger.debug("Customer profile loading failed", exc_info=True)
                return None

        (
            few_shot_examples,
            safety_rules_extra,
            pron_rules,
            promos,
            caller_history_raw,
            storage_raw,
            customer_profile_raw,
        ) = await asyncio.gather(
            _load_few_shot(),
            _load_safety(),
            _load_pronunciation(),
            _load_promotions(),
            _load_caller_history(),
            _load_storage_contracts(),
            _load_customer_profile(),
        )
        few_shot_context = format_few_shot_section(
            few_shot_examples, scenario_type=session.scenario
        )
        safety_context = format_safety_rules_section(safety_rules_extra)
        promotions_context = format_promotions_context(promos)
        caller_history_text = format_caller_history(caller_history_raw)
        storage_context_text = format_storage_context(storage_raw)
        customer_profile_text = format_customer_profile(customer_profile_raw)
        profile_name = (
            customer_profile_raw.get("name") if customer_profile_raw else None
        )

        # Modular prompt assembly: if no DB/A-B prompt, assemble from modules
        is_modular = False
        if system_prompt is None:
            system_prompt = assemble_prompt(
                scenario=session.scenario,
                include_pronunciation=False,  # added separately via inject_pronunciation_rules
                compact=(session.scenario is None),  # lightweight router when no IVR
            )
            is_modular = True

        # Inject pronunciation rules into system prompt
        if pron_rules:
            system_prompt = inject_pronunciation_rules(system_prompt, pron_rules)

        # Apply tenant overrides (tools filter, greeting, prompt suffix)
        if tenant:
            if tenant.get("enabled_tools"):
                allowed = set(tenant["enabled_tools"])
                if tools:
                    tools = [t for t in tools if t["name"] in allowed]
            if tenant.get("greeting") and templates:
                templates = dict(templates)  # copy to avoid mutating shared dict
                templates["greeting"] = tenant["greeting"]
            if tenant.get("prompt_suffix") and system_prompt:
                system_prompt = system_prompt + "\n\n" + tenant["prompt_suffix"]

        # Personalized greeting for known customers (after tenant override)
        if profile_name and templates:
            templates = dict(templates)  # copy to avoid mutating shared dict
            current_greeting = templates.get("greeting", "")
            # Replace "Як можу до вас звертатися?" with personalized version
            if "Як можу до вас звертатися?" in current_greeting:
                templates["greeting"] = current_greeting.replace(
                    "Як можу до вас звертатися?",
                    f"{profile_name}, чим можу допомогти?",
                )
            else:
                # Tenant custom greeting without name question — prepend name
                templates["greeting"] = GREETING_TEXT_KNOWN.replace(
                    "{customer_name}", profile_name
                )
            logger.info(
                "Known customer greeting: %s for call %s",
                profile_name,
                conn.channel_uuid,
            )

        # Apply IVR scenario-based tool filtering
        if session.scenario and session.scenario in _SCENARIO_TOOLS:
            allowed_scenario = _SCENARIO_TOOLS[session.scenario]
            if tools:
                tools = [t for t in tools if t["name"] in allowed_scenario]
            logger.info(
                "Scenario tool filter: %s → %d tools for call %s",
                session.scenario,
                len(tools) if tools else 0,
                conn.channel_uuid,
            )

        # Apply IVR scenario emphasis to system prompt
        if session.scenario and system_prompt:
            emphasis = _SCENARIO_EMPHASIS.get(session.scenario)
            if emphasis:
                system_prompt = system_prompt + emphasis

        # Create per-tenant StoreClient if tenant has custom store config
        if tenant and tenant.get("config"):
            tenant_config = tenant["config"] if isinstance(tenant["config"], dict) else {}
            if tenant_config.get("store_api_url"):
                tenant_store_client = StoreClient(
                    base_url=tenant_config["store_api_url"],
                    api_key=tenant_config.get("store_api_key", ""),
                    timeout=settings.store_api.timeout,
                    db_engine=_db_engine,
                    redis=_redis,
                )
                await tenant_store_client.open()
                logger.info("Per-tenant StoreClient: %s", tenant_config["store_api_url"])

        # Per-call tool router, PII vault, and LLM agent
        router = _build_tool_router(session, store_client=tenant_store_client)

        # Wire tool call logging into the router
        if _call_logger is not None:
            _tool_turn_counter = [0]  # mutable counter for tool turn tracking

            async def _on_tool_execute(
                name: str,
                args: dict[str, Any],
                result: Any,
                duration_ms: int,
                success: bool,
            ) -> None:
                _tool_turn_counter[0] += 1
                # Track tool calls for OPT-2 (lazy tool filtering) and OPT-3 (module expansion)
                session.tools_called.add(name)
                if name == "book_fitting" and success:
                    session.fitting_booked = True
                await _call_logger.log_tool_call(
                    call_id=conn.channel_uuid,
                    turn_number=_tool_turn_counter[0],
                    tool_name=name,
                    tool_args=args if isinstance(args, dict) else {},
                    tool_result=result if isinstance(result, dict) else {"result": str(result)},
                    duration_ms=duration_ms,
                    success=success,
                )

            router.set_execute_hook(_on_tool_execute)

        vault = PIIVault()
        tenant_agent_name = tenant.get("agent_name") if tenant else None
        agent = LLMAgent(
            api_key=settings.anthropic.api_key,
            model=settings.anthropic.model,
            tool_router=router,
            pii_vault=vault,
            tools=tools,
            llm_router=_llm_router,
            system_prompt=system_prompt,
            prompt_version_name=prompt_version_name,
            few_shot_context=few_shot_context,
            safety_context=safety_context,
            promotions_context=promotions_context,
            is_modular=is_modular,
            agent_name=tenant_agent_name,
        )

        # Initialize pattern search (if asyncpg pool available)
        pattern_search = None
        if _asyncpg_pool is not None:
            try:
                from src.knowledge.embeddings import EmbeddingGenerator
                from src.sandbox.patterns import PatternSearch

                _embedding_gen = EmbeddingGenerator(settings.openai.api_key)
                await _embedding_gen.open()
                pattern_search = PatternSearch(_asyncpg_pool, _embedding_gen)
            except Exception:
                logger.debug("Pattern search init failed, continuing without", exc_info=True)

        # Single shared barge-in event for the entire call
        barge_in_event = asyncio.Event()

        # Create streaming loop if FF enabled and LLM router available
        streaming_loop = None
        ff = settings.feature_flags
        if ff.streaming_llm and _llm_router is not None:
            from src.agent.streaming_loop import StreamingAgentLoop

            streaming_loop = StreamingAgentLoop(
                llm_router=_llm_router,
                tool_router=router,
                tts=_tts_engine,  # type: ignore[arg-type]
                conn=conn,
                barge_in_event=barge_in_event,
                tools=tools,
                system_prompt=system_prompt,
                pii_vault=vault,
                few_shot_context=few_shot_context,
                safety_context=safety_context,
                promotions_context=promotions_context,
                is_modular=is_modular,
                agent_name=tenant_agent_name,
            )

        # Run the pipeline (greeting → listen → STT → LLM → TTS loop)
        assert _tts_engine is not None, "TTS engine must be initialized before handling calls"

        # Set context vars so LLM router can associate usage with call/tenant
        from src.llm.router import llm_call_id_var, llm_tenant_id_var

        llm_call_id_var.set(conn.channel_uuid)
        llm_tenant_id_var.set(session.tenant_id)

        pipeline = CallPipeline(
            conn,
            stt,
            _tts_engine,
            agent,
            session,
            stt_config,
            templates,
            pattern_search=pattern_search,
            streaming_loop=streaming_loop,
            barge_in_event=barge_in_event,
            agent_name=tenant_agent_name,
            call_logger=_call_logger,
            cost_breakdown=cost,
            caller_history=caller_history_text,
            storage_context=storage_context_text,
            customer_profile=customer_profile_text,
        )
        await pipeline.run()

    except asyncio.CancelledError:
        logger.info("Call cancelled (shutdown): %s", conn.channel_uuid)
    except Exception:
        logger.exception("Unhandled error in call: %s", conn.channel_uuid)

    # Cleanup per-tenant StoreClient
    if tenant_store_client is not None:
        with contextlib.suppress(Exception):
            await tenant_store_client.close()

    # Cleanup
    if _embedding_gen is not None:
        with contextlib.suppress(Exception):
            await _embedding_gen.close()
    if session.state != CallState.ENDED:
        session.transition_to(CallState.ENDED)
    if _redis is not None:
        store = SessionStore(_redis)
        await store.delete(conn.channel_uuid)

    active_calls.dec()
    status = "transferred" if session.transferred else "completed"
    calls_total.labels(status=status).inc()
    call_duration_seconds.observe(session.duration_seconds)
    if session.scenario:
        call_scenario_total.labels(scenario=session.scenario).inc()
    if not session.transferred:
        calls_resolved_by_bot_total.inc()
    await publish_event(
        "call:ended",
        {
            "call_id": str(conn.channel_uuid),
            "status": status,
            "duration_seconds": session.duration_seconds,
        },
    )

    # Finalize call cost: add STT usage and record Prometheus metrics
    cost.add_stt_usage(session.duration_seconds)
    cost.record_metrics()

    # Persist call end to PostgreSQL
    if _call_logger is not None:
        try:
            await _call_logger.log_call_end(
                call_id=conn.channel_uuid,
                ended_at=datetime.now(UTC),
                duration_seconds=session.duration_seconds,
                scenario=session.scenario,
                transferred=session.transferred,
                transfer_reason=session.transfer_reason if session.transferred else None,
                cost_breakdown=cost.to_dict(),
                total_cost_usd=cost.total_cost,
            )
        except Exception:
            logger.warning("log_call_end failed", exc_info=True)

    # Dispatch async quality evaluation (non-blocking, Celery task)
    try:
        from src.tasks.quality_evaluator import evaluate_call_quality

        evaluate_call_quality.delay(str(conn.channel_uuid))
    except Exception:
        logger.warning("Quality evaluation dispatch failed", exc_info=True)

    logger.info(
        "Call ended: %s, duration=%ds, turns=%d",
        conn.channel_uuid,
        session.duration_seconds,
        len(session.dialog_history),
    )


def _resolve_date(value: str) -> str:
    """Resolve 'today'/'tomorrow'/relative strings to YYYY-MM-DD.

    The LLM may pass literal 'today', 'tomorrow', 'послезавтра', or a
    proper YYYY-MM-DD date. We normalise everything to YYYY-MM-DD so the
    SOAP layer receives a valid date.
    """
    if not value:
        return ""
    low = value.strip().lower()
    today = datetime.now(tz=UTC).date()
    if low in {"today", "сьогодні", "сегодня"}:
        return today.isoformat()
    if low in {"tomorrow", "завтра"}:
        return (today + timedelta(days=1)).isoformat()
    if low in {"послезавтра", "afterTomorrow", "після завтра", "післязавтра"}:
        return (today + timedelta(days=2)).isoformat()
    # Already a date string — return as-is
    return value.strip()


# Mapping of alternative / Russian / old city names → canonical 1C names.
_CITY_ALIASES: dict[str, str] = {
    "днепропетровск": "дніпро",
    "дніпропетровськ": "дніпро",
    "днепр": "дніпро",
    "днипро": "дніпро",
    "запорожье": "запоріжжя",
    "запорiжжя": "запоріжжя",
    "киев": "київ",
    "кiев": "київ",
    "харьков": "харків",
    "харкiв": "харків",
    "черкассы": "черкаси",
    "черкаси": "черкаси",
}


def _normalize_city(name: str) -> str:
    """Normalize city name: lowercase + resolve aliases."""
    low = name.strip().lower()
    return _CITY_ALIASES.get(low, low)


def _build_tool_router(session: CallSession, store_client: StoreClient | None = None) -> ToolRouter:
    """Build a ToolRouter with all canonical tools registered."""
    router = ToolRouter()

    client = store_client or _store_client
    assert client is not None, "StoreClient must be initialized before handling calls"

    router.register("get_vehicle_tire_sizes", client.get_vehicle_tire_sizes)

    async def _search_tires(**params: Any) -> dict[str, Any]:
        network = session.network_id or "ProKoleso"
        return await client.search_tires(network=network, **params)

    async def _check_availability(
        product_id: str = "", query: str = "", **kw: Any
    ) -> dict[str, Any]:
        network = session.network_id or "ProKoleso"
        return await client.check_availability(product_id, query, network=network, **kw)

    router.register("search_tires", _search_tires)
    router.register("check_availability", _check_availability)
    router.register("get_order_status", client.search_orders)

    async def _create_order_draft(**kwargs: Any) -> Any:
        """Save order draft to session (1C order is one-step, created on confirm)."""
        session.order_draft = {
            "items": kwargs.get("items", []),
            "customer_phone": kwargs.get("customer_phone", ""),
        }
        return {
            "order_id": f"DRAFT-{session.channel_uuid}",
            "status": "draft",
            "items": kwargs.get("items", []),
            "message": "Чорновик замовлення створено. Вкажіть спосіб доставки.",
        }

    async def _update_order_delivery(**kwargs: Any) -> Any:
        """Update session order_draft with delivery info."""
        if session.order_draft is not None:
            session.order_draft["delivery_type"] = kwargs.get("delivery_type", "pickup")
            session.order_draft["city"] = kwargs.get("city", "")
            session.order_draft["address"] = kwargs.get("address", "")
            session.order_draft["pickup_point_id"] = kwargs.get("pickup_point_id", "")
            return {
                "order_id": kwargs.get("order_id", f"DRAFT-{session.channel_uuid}"),
                "delivery_type": kwargs.get("delivery_type"),
                "status": "delivery_set",
                "message": "Доставку оновлено. Підтвердіть замовлення.",
            }
        # Fallback to Store API if no draft in session
        return await client.update_delivery(**kwargs)

    async def _confirm_order(**kwargs: Any) -> Any:
        """Confirm order: try 1C direct, fallback to Store API."""
        if session.order_draft is not None and _onec_client is not None:
            try:
                # Generate AI order number via Redis sequence
                order_seq = 1
                if _redis is not None:
                    order_seq = await _redis.incr("order:ai_sequence")
                order_number = f"AI-{order_seq}"

                network = session.network_id or "ProKoleso"
                draft = session.order_draft
                result = await _onec_client.create_order_1c(
                    order_number=order_number,
                    items=draft.get("items", []),
                    customer_phone=draft.get("customer_phone", ""),
                    payment_method=kwargs.get("payment_method", "cod"),
                    delivery_type=draft.get("delivery_type", "pickup"),
                    delivery_address=draft.get("address", ""),
                    delivery_city=draft.get("city", ""),
                    pickup_point_id=draft.get("pickup_point_id", ""),
                    customer_name=kwargs.get("customer_name", ""),
                    network=network,
                )
                session.order_id = order_number
                session.order_draft = None
                orders_created_total.inc()
                return {
                    "order_id": order_number,
                    "status": "confirmed",
                    "payment_method": kwargs.get("payment_method", "cod"),
                    "message": f"Замовлення {order_number} підтверджено.",
                    "onec_response": result,
                }
            except Exception:
                logger.warning(
                    "1C order creation failed for call %s, falling back to Store API",
                    session.channel_uuid,
                    exc_info=True,
                )
                # Fallback: use Store API 3-step flow
                session.order_draft = None

        # Store API fallback (or no draft)
        result = await client.confirm_order(**kwargs)
        if isinstance(result, dict) and result.get("id"):
            orders_created_total.inc()
        return result

    async def _book_fitting_with_metric(**kwargs: Any) -> Any:
        """Book fitting: try SOAP, fallback to Store API."""
        # Server-side validation: reject booking without required customer data
        customer_name = (kwargs.get("customer_name") or "").strip()
        auto_number = (kwargs.get("auto_number") or "").strip()
        missing: list[str] = []
        if not customer_name:
            missing.append("customer_name (ім'я клієнта)")
        if not auto_number:
            missing.append("auto_number (державний номер авто)")
        if missing:
            return {
                "error": True,
                "message": f"Неможливо записати без: {', '.join(missing)}. "
                "Поверніся до чеклісту і запитай у клієнта відсутні дані.",
            }

        if _soap_client is not None:
            try:
                result = await _soap_client.book_fitting(
                    person=kwargs.get("customer_name", ""),
                    phone=kwargs.get("customer_phone", ""),
                    station_id=kwargs.get("station_id", ""),
                    date=kwargs.get("date", ""),
                    time=kwargs.get("time", ""),
                    vehicle_info=kwargs.get("vehicle_info", ""),
                    auto_number=kwargs.get("auto_number", ""),
                    storage_contract=kwargs.get("storage_contract", ""),
                    tire_diameter=kwargs.get("tire_diameter", 0),
                    service_type=kwargs.get("service_type", "tire_change"),
                )
                if result.get("booking_id"):
                    fittings_booked_total.inc()
                    return result
                # SOAP returned error (Result=false or parse failure) — log and
                # fall through to Store API instead of returning error directly.
                logger.warning(
                    "SOAP book_fitting returned error for call %s: "
                    "status=%s, message=%s, station=%s, date=%s, time=%s",
                    session.channel_uuid,
                    result.get("status"),
                    result.get("message", "(no message)"),
                    kwargs.get("station_id"),
                    kwargs.get("date"),
                    kwargs.get("time"),
                )
            except Exception:
                logger.warning(
                    "SOAP book_fitting exception for call %s, falling back to Store API",
                    session.channel_uuid,
                    exc_info=True,
                )

        result = await client.book_fitting(**kwargs)
        if isinstance(result, dict) and result.get("id"):
            fittings_booked_total.inc()
        return result

    router.register("create_order_draft", _create_order_draft)
    router.register("update_order_delivery", _update_order_delivery)
    router.register("confirm_order", _confirm_order)

    async def _get_pickup_points(city: str = "") -> dict[str, Any]:
        network = session.network_id or "ProKoleso"
        cache_key = f"onec:points:{network}"

        result: dict[str, Any] | None = None

        # 1. Redis cache
        if _redis:
            try:
                raw = await _redis.get(cache_key)
                if raw:
                    all_points = json.loads(raw if isinstance(raw, str) else raw.decode())
                    if city:
                        all_points = [
                            p for p in all_points if city.lower() in p.get("city", "").lower()
                        ]
                    result = {"total": len(all_points), "points": all_points[:15]}
            except Exception:
                pass

        # 2. 1C API
        if result is None and _onec_client is not None:
            try:
                data = await _onec_client.get_pickup_points(network)
                raw_points = data.get("data", [])
                all_points = [
                    {
                        "id": p.get("id", ""),
                        "address": p.get("point", ""),
                        "type": p.get("point_type", ""),
                        "city": p.get("City", ""),
                    }
                    for p in raw_points
                ]
                if _redis:
                    await _redis.setex(cache_key, 3600, json.dumps(all_points, ensure_ascii=False))
                if city:
                    all_points = [
                        p for p in all_points if city.lower() in p.get("city", "").lower()
                    ]
                result = {"total": len(all_points), "points": all_points[:15]}
            except Exception:
                logger.warning("1C pickup points unavailable for %s", network, exc_info=True)

        # 3. Fallback to Store API
        if result is None:
            result = await client.get_pickup_points(city)

        # Merge pickup point hints from Redis
        if _redis:
            try:
                hints_raw = await _redis.get("pickup:point_hints")
                if hints_raw:
                    hints = json.loads(
                        hints_raw if isinstance(hints_raw, str) else hints_raw.decode()
                    )
                    for p in result.get("points", []):
                        pid = p.get("id", "")
                        if pid and pid in hints:
                            h = hints[pid]
                            if h.get("district"):
                                p["district"] = h["district"]
                            if h.get("landmarks"):
                                p["landmarks"] = h["landmarks"]
                            if h.get("description"):
                                p["description"] = h["description"]
            except Exception:
                pass

        return result

    router.register("get_pickup_points", _get_pickup_points)

    async def _get_fitting_stations(city: str = "", **_kwargs: Any) -> dict[str, Any]:
        cache_key = "onec:fitting_stations"

        # 1. Redis cache
        all_stations: list[dict[str, Any]] | None = None
        if _redis:
            try:
                raw = await _redis.get(cache_key)
                if raw:
                    all_stations = json.loads(raw if isinstance(raw, str) else raw.decode())
            except Exception:
                pass

        # 2. 1C REST API (preferred over SOAP)
        if all_stations is None and _onec_client is not None:
            try:
                rest_data = await _onec_client.get_fitting_stations_rest()
                raw_list = (
                    rest_data
                    if isinstance(rest_data, list)
                    else rest_data.get(
                        "data", rest_data.get("stations", rest_data.get("items", []))
                    )
                )
                if isinstance(raw_list, list) and raw_list:
                    all_stations = [
                        {
                            "station_id": s.get(
                                "StationID", s.get("station_id", s.get("id", ""))
                            ),
                            "name": s.get("StationName", s.get("name", "")),
                            "city": s.get("StationCity", s.get("city", "")),
                            "city_id": s.get("StationCityID", s.get("city_id", "")),
                            "address": s.get(
                                "StationAdress",
                                s.get("StationAddress", s.get("address", "")),
                            ),
                            "count_posts": int(s.get("StationCountPosts", 0)) or None,
                        }
                        for s in raw_list
                    ]
                    if _redis and all_stations:
                        await _redis.setex(
                            cache_key,
                            86400,
                            json.dumps(all_stations, ensure_ascii=False),
                        )
            except Exception:
                logger.warning(
                    "REST get_fitting_stations failed for call %s, trying SOAP",
                    session.channel_uuid,
                    exc_info=True,
                )

        # 3. 1C SOAP API (fallback)
        if all_stations is None and _soap_client is not None:
            try:
                all_stations = await _soap_client.get_stations()
                if _redis and all_stations is not None:
                    await _redis.setex(
                        cache_key, 86400, json.dumps(all_stations, ensure_ascii=False)
                    )
            except Exception:
                logger.warning(
                    "SOAP get_stations failed for call %s, falling back",
                    session.channel_uuid,
                    exc_info=True,
                )

        # 4. Return from cache/1C with city filter
        if all_stations is not None:
            filtered = all_stations
            if city:
                city_q = _normalize_city(city)
                filtered = [
                    s
                    for s in filtered
                    if city_q in _normalize_city(s.get("city", ""))
                    or _normalize_city(s.get("city", "")) in city_q
                ]

            # Merge station hints from Redis
            hints: dict[str, Any] = {}
            if _redis:
                try:
                    hints_raw = await _redis.get("fitting:station_hints")
                    if hints_raw:
                        hints = json.loads(
                            hints_raw if isinstance(hints_raw, str) else hints_raw.decode()
                        )
                except Exception:
                    pass

            stations_out = []
            for s in filtered[:20]:
                entry = {
                    "id": s.get("station_id", s.get("id", "")),
                    "name": s.get("name", ""),
                    "city": s.get("city", ""),
                    "address": s.get("address", ""),
                }
                sid = entry["id"]
                if sid and sid in hints:
                    h = hints[sid]
                    if h.get("district"):
                        entry["district"] = h["district"]
                    if h.get("landmarks"):
                        entry["landmarks"] = h["landmarks"]
                    if h.get("description"):
                        entry["description"] = h["description"]
                stations_out.append(entry)

            return {
                "total": len(filtered),
                "stations": stations_out,
            }

        # 5. Fallback to Store API
        return await client.get_fitting_stations(city)

    router.register("get_fitting_stations", _get_fitting_stations)

    async def _get_station_count_posts(station_id: str) -> int:
        """Look up count_posts for a station from the cached stations list.

        Returns the station's post count or 1 as a conservative fallback.
        """
        if not _redis or not station_id:
            return 1
        try:
            raw = await _redis.get("onec:fitting_stations")
            if raw:
                stations = json.loads(raw if isinstance(raw, str) else raw.decode())
                for s in stations:
                    sid = s.get("station_id", s.get("id", ""))
                    if sid == station_id:
                        return s.get("count_posts") or 1
        except Exception:
            pass
        return 1

    async def _get_fitting_slots(**kwargs: Any) -> Any:
        """Get fitting slots: try SOAP, fallback to Store API."""
        station_id = kwargs.get("station_id", "")
        today = datetime.now(tz=UTC).date().isoformat()
        date_from = _resolve_date(kwargs.get("date_from", "")) or today
        date_to = _resolve_date(kwargs.get("date_to", "")) or date_from

        if _soap_client is not None:
            try:
                slots = await _soap_client.get_station_schedule(
                    date_from=date_from,
                    date_to=date_to,
                    station_id=station_id,
                )
                count_posts = await _get_station_count_posts(station_id)
                for slot in slots:
                    qty = slot.pop("quantity", 0)
                    slot["available"] = count_posts - qty > 0
                return {"station_id": station_id, "slots": slots}
            except Exception:
                logger.warning(
                    "SOAP get_station_schedule failed for call %s, falling back to Store API",
                    session.channel_uuid,
                    exc_info=True,
                )
        try:
            return await client.get_fitting_slots(**kwargs)
        except Exception:
            logger.warning(
                "Store API get_fitting_slots also failed for call %s",
                session.channel_uuid,
                exc_info=True,
            )
            return {
                "station_id": station_id,
                "error": "Сервіс тимчасово недоступний. Спробуйте через хвилину.",
                "slots": [],
            }

    router.register("get_fitting_slots", _get_fitting_slots)
    router.register("book_fitting", _book_fitting_with_metric)

    async def _cancel_fitting(**kwargs: Any) -> Any:
        """Cancel fitting: try SOAP, fallback to Store API."""
        if _soap_client is not None:
            try:
                booking_id = kwargs.get("booking_id", "")
                result = await _soap_client.cancel_booking(booking_id)
                return {
                    "booking_id": booking_id,
                    "status": result.get("status", "cancelled"),
                    "message": result.get("message", "Запис скасовано"),
                }
            except Exception:
                logger.warning(
                    "SOAP cancel_booking failed for call %s, falling back to Store API",
                    session.channel_uuid,
                    exc_info=True,
                )
        return await client.cancel_fitting(**kwargs)

    router.register("cancel_fitting", _cancel_fitting)

    def _matches_diameter(price_item: dict[str, Any], diameter: int) -> bool:
        """Check if a price item matches the given tire diameter.

        Handles multiple formats from 1C data: R16, r16, Р16 (cyrillic),
        R 16, bare "16" with word boundaries.
        """
        import re

        d = str(diameter)
        for field in ("artikul", "service", "name", "description"):
            val = price_item.get(field, "")
            if not val:
                continue
            # Case-insensitive match: R16, r16, Р16 (cyrillic Р), R 16
            if re.search(rf"[RrРр]\s*{re.escape(d)}(?:\b|[^0-9]|$)", val):
                return True
            # Bare diameter as a standalone number (e.g. "16" in "16 дюймів")
            if re.search(rf"(?<![0-9]){re.escape(d)}(?![0-9])", val):
                return True
        return False

    async def _get_fitting_price(
        tire_diameter: int = 0,
        station_id: str = "",
        service_type: str = "",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        cache_key = "onec:fitting_prices"

        # 1. Redis cache
        all_prices: list[dict[str, Any]] | None = None
        if _redis:
            try:
                raw = await _redis.get(cache_key)
                if raw:
                    all_prices = json.loads(raw if isinstance(raw, str) else raw.decode())
            except Exception:
                pass

        # 2. 1C REST API
        if all_prices is None and _onec_client is not None:
            try:
                data = await _onec_client.get_fitting_prices()
                all_prices = data.get("data", [])
                if _redis and all_prices is not None:
                    await _redis.setex(cache_key, 3600, json.dumps(all_prices, ensure_ascii=False))
            except Exception:
                logger.warning(
                    "1C get_fitting_prices failed for call %s, falling back",
                    session.channel_uuid,
                    exc_info=True,
                )

        # 3. Return from cache/1C with filters
        if all_prices is not None:
            filtered = all_prices
            if station_id:
                filtered = [p for p in filtered if p.get("point_id") == station_id]
            if tire_diameter:
                by_diameter = [
                    p
                    for p in filtered
                    if _matches_diameter(p, tire_diameter)
                ]
                if by_diameter:
                    filtered = by_diameter
                elif filtered:
                    # Diameter filter matched nothing — return all with hint
                    logger.info(
                        "Fitting price: R%d filter empty, returning all %d items",
                        tire_diameter,
                        len(filtered),
                    )
                    return {
                        "prices": filtered,
                        "note": f"Цін за діаметром R{tire_diameter} не знайдено. "
                        "Ось загальний прайс на послуги шиномонтажу.",
                    }
            return {"prices": filtered}

        # 4. Fallback to Store API
        return await client.get_fitting_price(
            tire_diameter=tire_diameter,
            station_id=station_id,
            service_type=service_type,
        )

    router.register("get_fitting_price", _get_fitting_price)

    async def _get_customer_bookings(**kwargs: Any) -> dict[str, Any]:
        """Get customer bookings from SOAP service."""
        if _soap_client is not None:
            try:
                phone = kwargs.get("phone", "")
                station_id = kwargs.get("station_id", "")
                bookings = await _soap_client.get_customer_bookings(
                    phone=phone, station_id=station_id
                )
                return {"total": len(bookings), "bookings": bookings}
            except Exception:
                logger.warning(
                    "SOAP get_customer_bookings failed for call %s",
                    session.channel_uuid,
                    exc_info=True,
                )
        return {"total": 0, "bookings": [], "message": "Сервіс записів тимчасово недоступний"}

    router.register("get_customer_bookings", _get_customer_bookings)

    async def _find_storage(**kwargs: Any) -> dict[str, Any]:
        """Find storage contracts: 1C REST API."""
        if _onec_client is not None:
            try:
                data = await _onec_client.find_storage(
                    storage_number=kwargs.get("storage_number", ""),
                    phone=kwargs.get("phone", ""),
                )
                if isinstance(data, dict):
                    return data
                return {"result": data}
            except Exception:
                logger.warning(
                    "1C find_storage failed for call %s",
                    session.channel_uuid,
                    exc_info=True,
                )
        return {"error": "Сервіс зберігання тимчасово недоступний", "contracts": []}

    router.register("find_storage", _find_storage)

    async def _update_customer_profile(**kwargs: Any) -> dict[str, Any]:
        """Update customer profile with merge-patch semantics."""
        if _call_logger is None or not session.caller_phone:
            return {"status": "unavailable"}
        return await _call_logger.update_customer_profile(
            session.caller_phone,
            name=kwargs.get("name"),
            city=kwargs.get("city"),
            vehicles=kwargs.get("vehicles"),
            delivery_address=kwargs.get("delivery_address"),
        )

    router.register("update_customer_profile", _update_customer_profile)

    async def _search_knowledge(**kwargs: Any) -> dict[str, Any]:
        if _knowledge_search is not None:
            results = await _knowledge_search.search(
                query=kwargs.get("query", ""),
                category=kwargs.get("category", ""),
                tenant_id=session.tenant_id or "",
            )
            return {"total": len(results), "articles": results}
        return await client.search_knowledge_base(**kwargs)

    router.register("search_knowledge_base", _search_knowledge)

    async def transfer_to_operator(**kwargs: Any) -> dict[str, str]:
        session.transferred = True
        session.transfer_reason = str(kwargs.get("reason", ""))
        logger.warning(
            "Operator transfer requested for call %s (ARI not configured — "
            "flag set but no SIP transfer performed)",
            session.channel_uuid,
        )
        await publish_event("call:transferred", {"call_id": str(session.channel_uuid)})
        return {"status": "transferring", "message": "З'єдную з оператором"}

    router.register("transfer_to_operator", transfer_to_operator)

    # --- Auto-inject CallerID into tool calls that need phone ---
    # Tools use different param names: "customer_phone" or "phone".
    _PHONE_FIELDS: dict[str, str] = {
        "book_fitting": "customer_phone",
        "create_order_draft": "customer_phone",
        "get_order_status": "phone",
        "get_customer_bookings": "phone",
        "find_storage": "phone",
    }
    _original_execute = router.execute

    async def _execute_with_caller_id(name: str, args: dict[str, Any]) -> Any:
        phone_field = _PHONE_FIELDS.get(name)
        if phone_field and session.caller_phone:
            # Always override with real CallerID — LLM may hallucinate phone numbers
            current = args.get(phone_field, "")
            if current != session.caller_phone:
                args[phone_field] = session.caller_phone
                logger.info(
                    "Auto-injected CallerID %s into %s.%s for call %s (was: %s)",
                    session.caller_phone,
                    name,
                    phone_field,
                    session.channel_uuid,
                    current or "(empty)",
                )
        return await _original_execute(name, args)

    router.execute = _execute_with_caller_id  # type: ignore[assignment]

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


async def _periodic_embedding_check(interval_minutes: int = 5) -> None:
    """Periodically check for articles with pending embeddings and generate them.

    Runs inside the FastAPI process — no Celery worker needed.
    """
    from src.knowledge.embeddings import generate_embeddings_inline

    # Wait a bit before first check (let the server start up)
    await asyncio.sleep(30)

    while True:
        try:
            if _db_engine is None:
                await asyncio.sleep(interval_minutes * 60)
                continue

            async with _db_engine.begin() as conn:
                result = await conn.execute(
                    text("""
                        SELECT id, title FROM knowledge_articles
                        WHERE embedding_status = 'pending' AND active = true
                        ORDER BY updated_at NULLS FIRST
                        LIMIT 20
                    """)
                )
                pending = [(str(row.id), row.title) for row in result]

            if pending:
                logger.info("Embedding check: %d pending articles found", len(pending))
                for article_id, title in pending:
                    try:
                        result = await generate_embeddings_inline(article_id)
                        logger.info(
                            "Embedding generated: %s (%s, %s chunks)",
                            title,
                            result["status"],
                            result.get("chunks", "?"),
                        )
                    except Exception:
                        logger.exception("Embedding failed for article %s", article_id)

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Periodic embedding check failed")

        await asyncio.sleep(interval_minutes * 60)


async def main() -> None:
    """Main application entry point."""
    global _audio_server, _redis, _store_client, _tts_engine
    global _onec_client, _soap_client, _embedding_task, _db_engine, _llm_router, _asyncpg_pool
    global _knowledge_search, _search_embedding_gen

    settings = get_settings()

    # Validate configuration before anything else
    validation = settings.validate_required()
    if not validation.ok:
        for err in validation.errors:
            hint = f" Hint: {err.hint}" if err.hint else ""
            print(f"\u274c {err.field}: {err.message}.{hint}")
        print(f"\n{len(validation.errors)} configuration error(s). Fix them and restart.")
        sys.exit(1)

    # Block startup with default JWT secret when running in Docker (production)
    _in_docker = Path("/app/venv").exists()
    if _in_docker and settings.admin.jwt_secret == "change-me-in-production":
        print("\u274c ADMIN_JWT_SECRET is set to the insecure default.")
        print("  Set ADMIN_JWT_SECRET to a random string in your .env file.")
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

            # Pre-establish HTTP connections to reduce first-call latency
            await _llm_router.warmup()

            # Share router globally (avoids __main__ vs src.main module issue)
            from src.llm import set_router

            set_router(_llm_router)
        except Exception:
            logger.warning(
                "LLM router init failed — falling back to direct Anthropic", exc_info=True
            )
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

            _db_engine = create_async_engine(
                settings.database.url, pool_size=5, max_overflow=5, pool_pre_ping=True
            )
            logger.info("Database engine created: %s", settings.database.url.split("@")[-1])

            _onec_client = OneCClient(
                base_url=settings.onec.url,
                username=settings.onec.username,
                password=settings.onec.password,
                timeout=settings.onec.timeout,
            )
            await _onec_client.open()
            logger.info("OneCClient initialized: %s", settings.onec.url)

            # Initialize SOAP client for tire fitting service
            _soap_client = OneCSOAPClient(
                base_url=settings.onec.url,
                username=settings.onec.username,
                password=settings.onec.password,
                wsdl_path=settings.onec.soap_wsdl_path,
                timeout=settings.onec.soap_timeout,
            )
            await _soap_client.open()
            logger.info("OneCSOAPClient initialized: %s", _soap_client.endpoint_url)

            # Catalog sync is delegated to Celery (catalog_full_sync + catalog_incremental_sync)
            logger.info(
                "Catalog sync delegated to Celery (full daily 05:00, incremental every 5 min)"
            )
        except Exception:
            logger.warning(
                "1C integration init failed — MVP tools will use fallback HTTP", exc_info=True
            )
            _onec_client = None
            _soap_client = None
            _db_engine = None
    else:
        logger.info("1C integration not configured (ONEC_USERNAME empty)")

    # Initialize CallLogger for persisting real calls to PostgreSQL
    global _call_logger
    try:
        from src.logging.call_logger import CallLogger

        _call_logger = CallLogger(database_url=settings.database.url, redis=_redis)
        logger.info("CallLogger initialized")
    except Exception:
        logger.warning("CallLogger init failed — calls will not be persisted", exc_info=True)
        _call_logger = None

    # Initialize asyncpg pool for PatternSearch (pgvector direct queries)
    if settings.openai.api_key and _db_engine is not None:
        try:
            import asyncpg  # type: ignore[import-untyped]

            # Convert SQLAlchemy URL to asyncpg DSN (replace +asyncpg driver prefix)
            dsn = settings.database.url.replace("postgresql+asyncpg://", "postgresql://")
            _asyncpg_pool = await asyncpg.create_pool(
                dsn, min_size=1, max_size=3, command_timeout=30
            )
            logger.info("asyncpg pool created for pattern search")
        except Exception:
            logger.debug("asyncpg pool init failed — pattern search disabled", exc_info=True)
            _asyncpg_pool = None

    # Initialize KnowledgeSearch for search_knowledge_base tool (pgvector)
    if _asyncpg_pool is not None and settings.openai.api_key:
        try:
            from src.knowledge.embeddings import EmbeddingGenerator
            from src.knowledge.search import KnowledgeSearch

            _search_embedding_gen = EmbeddingGenerator(settings.openai.api_key)
            await _search_embedding_gen.open()
            _knowledge_search = KnowledgeSearch(_asyncpg_pool, _search_embedding_gen, redis=_redis)
            logger.info("KnowledgeSearch initialized (pgvector)")
        except Exception:
            logger.debug(
                "KnowledgeSearch init failed — tool will use StoreClient fallback", exc_info=True
            )
            _knowledge_search = None
            if _search_embedding_gen is not None:
                with contextlib.suppress(Exception):
                    await _search_embedding_gen.close()
                _search_embedding_gen = None

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
        # Read effective config: Redis (admin UI overrides) merged over env defaults
        from src.api.tts_config import _get_effective_config

        tts_cfg, tts_cfg_source = await _get_effective_config(_redis)
        _tts_engine = GoogleTTSEngine(
            config=TTSConfig(
                voice_name=tts_cfg.get("voice_name", settings.google_tts.voice),
                speaking_rate=tts_cfg.get("speaking_rate", settings.google_tts.speaking_rate),
                pitch=tts_cfg.get("pitch", settings.google_tts.pitch),
                break_comma_ms=tts_cfg.get("break_comma_ms", 100),
                break_period_ms=tts_cfg.get("break_period_ms", 200),
                break_exclamation_ms=tts_cfg.get("break_exclamation_ms", 250),
                break_colon_ms=tts_cfg.get("break_colon_ms", 200),
                break_semicolon_ms=tts_cfg.get("break_semicolon_ms", 150),
                break_em_dash_ms=tts_cfg.get("break_em_dash_ms", 150),
            )
        )
        await _tts_engine.initialize()
        from src.tts import set_engine as set_tts_engine

        set_tts_engine(_tts_engine)
        logger.info("TTS engine initialized (config source: %s)", tts_cfg_source)
    except Exception:
        logger.warning(
            "TTS engine unavailable (no Google credentials?) — calls will not work, but API is running"
        )
        _tts_engine = None

    # Start API server (health checks, metrics)
    api_task = asyncio.create_task(start_api_server(settings))

    # Start periodic embedding check (generates embeddings for pending articles)
    _embedding_task = asyncio.create_task(_periodic_embedding_check(interval_minutes=5))
    logger.info("Periodic embedding check started (every 5 min)")

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

    # Cancel periodic tasks
    if _embedding_task is not None:
        _embedding_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _embedding_task
        logger.info("Periodic embedding check stopped")

    if _llm_router is not None:
        await _llm_router.close()
        from src.llm import set_router

        set_router(None)
        logger.info("LLM router closed")

    if _store_client is not None:
        await _store_client.close()
        logger.info("StoreClient closed")

    if _onec_client is not None:
        await _onec_client.close()
        logger.info("OneCClient closed")

    if _soap_client is not None:
        await _soap_client.close()
        logger.info("OneCSOAPClient closed")

    if _search_embedding_gen is not None:
        await _search_embedding_gen.close()
        logger.info("Search embedding generator closed")

    if _asyncpg_pool is not None:
        await _asyncpg_pool.close()
        logger.info("asyncpg pool closed")

    if _call_logger is not None:
        await _call_logger.close()
        logger.info("CallLogger closed")

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
