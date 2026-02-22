"""Sandbox agent testing API endpoints.

Chat-based interface for testing the AI agent with mock or live tools,
prompt iteration, turn rating, and conversation management.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID  # noqa: TC003 - FastAPI needs UUID at runtime for path params

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.api.auth import require_permission
from src.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/sandbox", tags=["sandbox"])

_engine: AsyncEngine | None = None
_perm_r = Depends(require_permission("sandbox:read"))
_perm_w = Depends(require_permission("sandbox:write"))
_perm_d = Depends(require_permission("sandbox:delete"))


async def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database.url, pool_pre_ping=True)
    return _engine


# ── Pydantic models ──────────────────────────────────────────


class ConversationCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    prompt_version_id: UUID | None = None
    tool_mode: str = "mock"
    model: str | None = None
    tags: list[str] = Field(default_factory=list)
    scenario_type: str | None = None
    tenant_id: UUID | None = None


class ConversationUpdate(BaseModel):
    title: str | None = None
    tags: list[str] | None = None
    status: str | None = None
    is_baseline: bool | None = None


class SendMessage(BaseModel):
    message: str = Field(..., min_length=1)
    parent_turn_id: UUID | None = None


class RateTurn(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    comment: str | None = None


class TurnGroupCreate(BaseModel):
    turn_ids: list[UUID] = Field(..., min_length=1)
    intent_label: str = Field(..., min_length=1, max_length=200)
    pattern_type: str = "positive"
    rating: int | None = Field(None, ge=1, le=5)
    rating_comment: str | None = None
    correction: str | None = None
    tags: list[str] = Field(default_factory=list)


class TurnGroupUpdate(BaseModel):
    intent_label: str | None = None
    pattern_type: str | None = None
    rating: int | None = Field(None, ge=1, le=5)
    rating_comment: str | None = None
    correction: str | None = None
    tags: list[str] | None = None


class BulkDeleteRequest(BaseModel):
    conversation_ids: list[UUID] = Field(..., min_length=1)


class ExportPatternRequest(BaseModel):
    guidance_note: str = Field(..., min_length=1)


class PatternUpdate(BaseModel):
    guidance_note: str | None = None
    is_active: bool | None = None
    tags: list[str] | None = None


# ── Agent phrases (read-only reference) ──────────────────────


@router.get("/agent-phrases")
async def get_agent_phrases(_: dict[str, Any] = _perm_r) -> dict[str, Any]:
    """Return hardcoded agent phrases and active DB templates.

    Hardcoded phrases are fallbacks from prompts.py.
    DB templates (response_templates) override them at runtime.
    """
    from src.agent.prompts import (
        ERROR_TEXT,
        FAREWELL_ORDER_TEXT,
        FAREWELL_TEXT,
        GREETING_TEXT,
        SILENCE_PROMPT_TEXT,
        TRANSFER_TEXT,
        WAIT_AVAILABILITY_POOL,
        WAIT_DEFAULT_POOL,
        WAIT_FITTING_POOL,
        WAIT_KNOWLEDGE_POOL,
        WAIT_ORDER_POOL,
        WAIT_SEARCH_POOL,
        WAIT_STATUS_POOL,
        WAIT_TEXT,
    )

    # Load DB templates (these override hardcoded at runtime)
    db_templates: dict[str, list[dict[str, Any]]] = {}
    try:
        engine = await _get_engine()
        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    "SELECT template_key, title, content, is_active, variant_number "
                    "FROM response_templates ORDER BY template_key, variant_number"
                )
            )
            for row in result:
                db_templates.setdefault(row.template_key, []).append(
                    {
                        "title": row.title,
                        "content": row.content,
                        "is_active": row.is_active,
                        "variant_number": row.variant_number,
                    }
                )
    except Exception:
        logger.warning("Failed to load DB templates for phrases tab", exc_info=True)

    return {
        "fixed": [
            {"key": "greeting", "label": "Приветствие", "text": GREETING_TEXT},
            {"key": "farewell", "label": "Прощание", "text": FAREWELL_TEXT},
            {"key": "farewell_order", "label": "Прощание (заказ)", "text": FAREWELL_ORDER_TEXT},
            {"key": "transfer", "label": "Переключение на оператора", "text": TRANSFER_TEXT},
            {"key": "error", "label": "Техническая ошибка", "text": ERROR_TEXT},
            {"key": "silence_prompt", "label": "Тишина (проверка)", "text": SILENCE_PROMPT_TEXT},
            {"key": "wait_default", "label": "Ожидание (общее)", "text": WAIT_TEXT},
        ],
        "wait_pools": [
            {"key": "search", "label": "Поиск шин", "phrases": WAIT_SEARCH_POOL},
            {"key": "availability", "label": "Проверка наличия", "phrases": WAIT_AVAILABILITY_POOL},
            {"key": "order", "label": "Оформление заказа", "phrases": WAIT_ORDER_POOL},
            {"key": "status", "label": "Статус заказа", "phrases": WAIT_STATUS_POOL},
            {"key": "fitting", "label": "Запись на шиномонтаж", "phrases": WAIT_FITTING_POOL},
            {
                "key": "knowledge",
                "label": "Консультация (база знаний)",
                "phrases": WAIT_KNOWLEDGE_POOL,
            },
            {"key": "default", "label": "По умолчанию (fallback)", "phrases": WAIT_DEFAULT_POOL},
        ],
        "db_templates": db_templates,
    }


# ── Available models ─────────────────────────────────────────

SANDBOX_MODELS = [
    {
        "id": "claude-sonnet-4-5-20250929",
        "label": "Claude Sonnet 4.5",
        "speed": "slow",
        "quality": "best",
    },
    {
        "id": "claude-haiku-4-5-20251001",
        "label": "Claude Haiku 4.5",
        "speed": "fast",
        "quality": "good",
    },
]


@router.get("/models")
async def list_models(_: dict[str, Any] = _perm_r) -> dict[str, Any]:
    """List available LLM models for sandbox conversations.

    Returns static Anthropic models plus any active providers from the
    LLM Router (marked with source='router').
    """
    models = [dict(m) for m in SANDBOX_MODELS]

    # Add router providers if available
    try:
        from src.llm import get_router

        llm_router = get_router()
        if llm_router is not None:
            router_models = llm_router.get_available_models()
            # Deduplicate: skip router providers whose model ID already exists
            existing_ids = {m["id"] for m in models}
            for rm in router_models:
                if rm["id"] not in existing_ids:
                    models.append(
                        {
                            "id": rm["id"],
                            "label": rm["label"],
                            "speed": "",
                            "quality": "",
                            "source": "router",
                            "model": rm.get("model", ""),
                            "type": rm.get("type", ""),
                        }
                    )
    except Exception:
        logger.debug("LLM router not available for sandbox models", exc_info=True)

    return {"models": models}


# ── Conversation CRUD ────────────────────────────────────────


@router.get("/conversations")
async def list_conversations(
    status: str | None = Query(None),
    search: str | None = Query(None),
    scenario_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: dict[str, Any] = _perm_r,
) -> dict[str, Any]:
    """List sandbox conversations with filters."""
    engine = await _get_engine()

    conditions = ["1=1"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    if status:
        conditions.append("c.status = :status")
        params["status"] = status
    if search:
        conditions.append("c.title ILIKE :search")
        params["search"] = f"%{search}%"
    if scenario_type:
        conditions.append("c.scenario_type = :scenario_type")
        params["scenario_type"] = scenario_type

    where = " AND ".join(conditions)

    async with engine.begin() as conn:
        count_result = await conn.execute(
            text(f"SELECT COUNT(*) FROM sandbox_conversations c WHERE {where}"),
            params,
        )
        total = count_result.scalar()

        result = await conn.execute(
            text(f"""
                SELECT c.id, c.title, c.prompt_version_id, c.prompt_version_name,
                       c.tool_mode, c.model, c.tags, c.scenario_type, c.status, c.is_baseline,
                       c.metadata, c.created_at, c.updated_at,
                       (SELECT COUNT(*) FROM sandbox_turns t WHERE t.conversation_id = c.id) AS turns_count,
                       (SELECT AVG(t.rating) FROM sandbox_turns t
                        WHERE t.conversation_id = c.id AND t.rating IS NOT NULL) AS avg_rating
                FROM sandbox_conversations c
                WHERE {where}
                ORDER BY c.updated_at DESC
                LIMIT :limit OFFSET :offset
            """),
            params,
        )
        items = [dict(row._mapping) for row in result]

    return {"total": total, "items": items}


@router.post("/conversations")
async def create_conversation(
    request: ConversationCreate, user: dict[str, Any] = _perm_w
) -> dict[str, Any]:
    """Create a new sandbox conversation."""
    engine = await _get_engine()

    user_id = user.get("user_id")

    async with engine.begin() as conn:
        # Resolve prompt version name if ID provided (same transaction as INSERT)
        prompt_version_name = None
        if request.prompt_version_id:
            pv_result = await conn.execute(
                text("SELECT name FROM prompt_versions WHERE id = :id"),
                {"id": str(request.prompt_version_id)},
            )
            pv_row = pv_result.first()
            if not pv_row:
                raise HTTPException(status_code=400, detail="Prompt version not found")
            prompt_version_name = pv_row.name

        result = await conn.execute(
            text("""
                INSERT INTO sandbox_conversations
                    (title, prompt_version_id, prompt_version_name, tool_mode, model, tags,
                     scenario_type, created_by, tenant_id)
                VALUES
                    (:title, :prompt_version_id, :prompt_version_name, :tool_mode, :model, :tags,
                     :scenario_type, :created_by, CAST(:tenant_id AS uuid))
                RETURNING id, title, prompt_version_id, prompt_version_name, tool_mode, model, tags,
                          scenario_type, status, is_baseline, metadata, tenant_id, created_at, updated_at
            """),
            {
                "title": request.title,
                "prompt_version_id": str(request.prompt_version_id)
                if request.prompt_version_id
                else None,
                "prompt_version_name": prompt_version_name,
                "tool_mode": request.tool_mode,
                "model": request.model,
                "tags": request.tags,
                "scenario_type": request.scenario_type,
                "created_by": user_id,
                "tenant_id": str(request.tenant_id) if request.tenant_id else None,
            },
        )
        row = result.first()
        if row is None:
            msg = "Expected row from INSERT RETURNING"
            raise RuntimeError(msg)

    return {"item": dict(row._mapping), "message": "Conversation created"}


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: UUID, _: dict[str, Any] = _perm_r) -> dict[str, Any]:
    """Get conversation with all turns and tool calls."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        # Conversation
        conv_result = await conn.execute(
            text("""
                SELECT id, title, prompt_version_id, prompt_version_name,
                       tool_mode, model, tags, scenario_type, status, is_baseline,
                       metadata, created_at, updated_at
                FROM sandbox_conversations
                WHERE id = :id
            """),
            {"id": str(conversation_id)},
        )
        conv_row = conv_result.first()
        if not conv_row:
            raise HTTPException(status_code=404, detail="Conversation not found")

        # Turns
        turns_result = await conn.execute(
            text("""
                SELECT id, parent_turn_id, turn_number, speaker, content,
                       llm_latency_ms, input_tokens, output_tokens, model,
                       rating, rating_comment, branch_label, created_at
                FROM sandbox_turns
                WHERE conversation_id = :conv_id
                ORDER BY turn_number, created_at
            """),
            {"conv_id": str(conversation_id)},
        )
        turns = [dict(row._mapping) for row in turns_result]

        # Tool calls for all turns in this conversation
        turn_ids = [str(t["id"]) for t in turns]
        tool_calls_by_turn: dict[str, list[dict[str, Any]]] = {tid: [] for tid in turn_ids}

        if turn_ids:
            tc_result = await conn.execute(
                text("""
                    SELECT id, turn_id, tool_name, tool_args, tool_result,
                           duration_ms, is_mock, created_at
                    FROM sandbox_tool_calls
                    WHERE turn_id = ANY(:turn_ids)
                    ORDER BY created_at
                """),
                {"turn_ids": turn_ids},
            )
            for tc_row in tc_result:
                tc_dict = dict(tc_row._mapping)
                tid = str(tc_dict.pop("turn_id"))
                tool_calls_by_turn.setdefault(tid, []).append(tc_dict)

        # Attach tool calls to turns
        for turn in turns:
            turn["tool_calls"] = tool_calls_by_turn.get(str(turn["id"]), [])

    return {"item": dict(conv_row._mapping), "turns": turns}


@router.patch("/conversations/{conversation_id}")
async def update_conversation(
    conversation_id: UUID,
    request: ConversationUpdate,
    _: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Update conversation metadata."""
    engine = await _get_engine()

    updates: list[str] = []
    params: dict[str, Any] = {"id": str(conversation_id)}

    if request.title is not None:
        updates.append("title = :title")
        params["title"] = request.title
    if request.tags is not None:
        updates.append("tags = :tags")
        params["tags"] = request.tags
    if request.status is not None:
        if request.status not in ("active", "archived"):
            raise HTTPException(status_code=400, detail="Status must be 'active' or 'archived'")
        updates.append("status = :status")
        params["status"] = request.status
    if request.is_baseline is not None:
        updates.append("is_baseline = :is_baseline")
        params["is_baseline"] = request.is_baseline

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates.append("updated_at = now()")
    set_clause = ", ".join(updates)

    async with engine.begin() as conn:
        result = await conn.execute(
            text(f"""
                UPDATE sandbox_conversations
                SET {set_clause}
                WHERE id = :id
                RETURNING id, title, status, is_baseline, tags, updated_at
            """),
            params,
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Conversation not found")

    return {"item": dict(row._mapping), "message": "Conversation updated"}


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: UUID, _: dict[str, Any] = _perm_d
) -> dict[str, Any]:
    """Delete a conversation and all its turns/tool_calls (CASCADE).

    Blocks deletion if the conversation is referenced by regression runs
    (suggests archiving instead).
    """
    engine = await _get_engine()
    cid = str(conversation_id)

    async with engine.begin() as conn:
        # Check if conversation is referenced by regression runs
        refs = await conn.execute(
            text("""
                SELECT id FROM sandbox_regression_runs
                WHERE source_conversation_id = :cid OR new_conversation_id = :cid
                LIMIT 1
            """),
            {"cid": cid},
        )
        if refs.first():
            raise HTTPException(
                status_code=409,
                detail="Conversation is referenced by regression runs. Archive it instead.",
            )

        result = await conn.execute(
            text("DELETE FROM sandbox_conversations WHERE id = :id RETURNING id, title"),
            {"id": cid},
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Conversation not found")

    return {"message": f"Conversation '{row.title}' deleted"}


@router.post("/conversations/bulk-delete")
async def bulk_delete_conversations(
    request: BulkDeleteRequest, _: dict[str, Any] = _perm_d
) -> dict[str, Any]:
    """Delete multiple conversations at once, skipping those referenced by regression runs."""
    engine = await _get_engine()
    ids = [str(cid) for cid in request.conversation_ids]

    async with engine.begin() as conn:
        # Find IDs that have regression run references
        refs_result = await conn.execute(
            text("""
                SELECT DISTINCT id FROM (
                    SELECT source_conversation_id AS id FROM sandbox_regression_runs
                    WHERE source_conversation_id = ANY(:ids)
                    UNION
                    SELECT new_conversation_id AS id FROM sandbox_regression_runs
                    WHERE new_conversation_id = ANY(:ids)
                ) sub
            """),
            {"ids": ids},
        )
        protected_ids = {str(row.id) for row in refs_result}

        deletable_ids = [cid for cid in ids if cid not in protected_ids]
        skipped_ids = [cid for cid in ids if cid in protected_ids]

        deleted_count = 0
        if deletable_ids:
            del_result = await conn.execute(
                text(
                    "DELETE FROM sandbox_conversations WHERE id = ANY(:ids) RETURNING id"
                ),
                {"ids": deletable_ids},
            )
            deleted_count = del_result.rowcount

    return {
        "deleted": deleted_count,
        "skipped": len(skipped_ids),
        "skipped_ids": skipped_ids,
    }


# ── Send message ─────────────────────────────────────────────


@router.post("/conversations/{conversation_id}/send")
async def send_message(
    conversation_id: UUID,
    request: SendMessage,
    _: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Send a customer message and get the agent response.

    If parent_turn_id is provided, branches from that turn's history
    instead of the latest turn.
    """
    from src.sandbox.agent_runner import create_sandbox_agent, process_sandbox_turn

    engine = await _get_engine()

    # Load conversation
    async with engine.begin() as conn:
        conv_result = await conn.execute(
            text("""
                SELECT id, prompt_version_id, tool_mode, model, status, tenant_id
                FROM sandbox_conversations
                WHERE id = :id
            """),
            {"id": str(conversation_id)},
        )
        conv = conv_result.first()
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if conv.status != "active":
            raise HTTPException(status_code=400, detail="Conversation is archived")

    # Load tenant config if set
    tenant: dict[str, Any] | None = None
    if conv.tenant_id:
        async with engine.begin() as conn:
            tenant_result = await conn.execute(
                text("""
                    SELECT slug, name, network_id, enabled_tools, prompt_suffix
                    FROM tenants WHERE id = :id AND is_active = true
                """),
                {"id": str(conv.tenant_id)},
            )
            tenant_row = tenant_result.first()
            if tenant_row:
                tenant = dict(tenant_row._mapping)

    # Determine conversation history
    history: list[dict[str, Any]] = []
    parent_turn_id = request.parent_turn_id
    is_mock = conv.tool_mode == "mock"

    if parent_turn_id:
        # Branch: load history from the specified parent turn
        async with engine.begin() as conn:
            turn_result = await conn.execute(
                text("""
                    SELECT conversation_history FROM sandbox_turns
                    WHERE id = :id AND conversation_id = :conv_id
                """),
                {"id": str(parent_turn_id), "conv_id": str(conversation_id)},
            )
            parent_row = turn_result.first()
            if not parent_row:
                raise HTTPException(status_code=404, detail="Parent turn not found")
            if parent_row.conversation_history:
                history = parent_row.conversation_history
    else:
        # Continue: load history from the latest agent turn
        async with engine.begin() as conn:
            last_turn = await conn.execute(
                text("""
                    SELECT conversation_history FROM sandbox_turns
                    WHERE conversation_id = :conv_id AND speaker = 'agent'
                    ORDER BY turn_number DESC, created_at DESC
                    LIMIT 1
                """),
                {"conv_id": str(conversation_id)},
            )
            last_row = last_turn.first()
            if last_row and last_row.conversation_history:
                history = last_row.conversation_history

    # Determine next turn number
    async with engine.begin() as conn:
        max_turn_result = await conn.execute(
            text("""
                SELECT COALESCE(MAX(turn_number), 0) AS max_turn
                FROM sandbox_turns WHERE conversation_id = :conv_id
            """),
            {"conv_id": str(conversation_id)},
        )
        max_turn = max_turn_result.scalar() or 0

    customer_turn_number = max_turn + 1
    agent_turn_number = max_turn + 2

    # Determine if the model is a router provider key
    provider_override = None
    llm_router = None
    model_for_agent = conv.model

    try:
        from src.llm import get_router

        llm_router = get_router()
        if llm_router is not None and conv.model and conv.model in llm_router.providers:
            provider_override = conv.model
    except Exception:
        logger.debug("LLM router not available for sandbox", exc_info=True)

    # Create sandbox agent and process
    prompt_version_id = conv.prompt_version_id

    # Pass live infrastructure for "live" tool mode
    onec_client = None
    redis_client = None
    knowledge_search = None
    store_client = None
    if conv.tool_mode == "live":
        try:
            # Use sys.modules to get the actual running main module
            # (avoids __main__ vs src.main module identity issue)
            import sys

            main_mod = sys.modules.get("__main__") or sys.modules.get("src.main")
            if main_mod:
                onec_client = getattr(main_mod, "_onec_client", None)
                redis_client = getattr(main_mod, "_redis", None)
                knowledge_search = getattr(main_mod, "_knowledge_search", None)
                store_client = getattr(main_mod, "_store_client", None)
        except Exception:
            logger.debug("Could not get live clients from main module", exc_info=True)

    agent = await create_sandbox_agent(
        engine,
        prompt_version_id=prompt_version_id,
        tool_mode=conv.tool_mode,
        model=model_for_agent,
        llm_router=llm_router,
        provider_override=provider_override,
        onec_client=onec_client,
        redis_client=redis_client,
        tenant=tenant,
        knowledge_search=knowledge_search,
        tenant_id=str(conv.tenant_id) if conv.tenant_id else "",
        store_client=store_client,
    )

    result = await process_sandbox_turn(agent, request.message, history, is_mock=is_mock)

    # Save turns and tool calls
    async with engine.begin() as conn:
        # Customer turn
        cust_result = await conn.execute(
            text("""
                INSERT INTO sandbox_turns
                    (conversation_id, parent_turn_id, turn_number, speaker, content)
                VALUES
                    (:conv_id, :parent_turn_id, :turn_number, 'customer', :content)
                RETURNING id, turn_number, speaker, content, created_at
            """),
            {
                "conv_id": str(conversation_id),
                "parent_turn_id": str(parent_turn_id) if parent_turn_id else None,
                "turn_number": customer_turn_number,
                "content": request.message,
            },
        )
        cust_row = cust_result.first()
        if cust_row is None:
            msg = "Expected row from INSERT RETURNING"
            raise RuntimeError(msg)
        customer_turn = dict(cust_row._mapping)

        # Agent turn
        agent_result = await conn.execute(
            text("""
                INSERT INTO sandbox_turns
                    (conversation_id, parent_turn_id, turn_number, speaker, content,
                     llm_latency_ms, input_tokens, output_tokens, model, conversation_history)
                VALUES
                    (:conv_id, :parent_turn_id, :turn_number, 'agent', :content,
                     :latency_ms, :input_tokens, :output_tokens, :model, :history)
                RETURNING id, turn_number, speaker, content, llm_latency_ms,
                          input_tokens, output_tokens, model, created_at
            """),
            {
                "conv_id": str(conversation_id),
                "parent_turn_id": str(customer_turn["id"]),
                "turn_number": agent_turn_number,
                "content": result.response_text,
                "latency_ms": result.latency_ms,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "model": result.model,
                "history": json.dumps(result.updated_history),
            },
        )
        agent_row = agent_result.first()
        if agent_row is None:
            msg = "Expected row from INSERT RETURNING"
            raise RuntimeError(msg)
        agent_turn = dict(agent_row._mapping)

        # Tool calls
        tool_calls_saved = []
        for tc in result.tool_calls:
            tc_result = await conn.execute(
                text("""
                    INSERT INTO sandbox_tool_calls
                        (turn_id, tool_name, tool_args, tool_result, duration_ms, is_mock)
                    VALUES
                        (:turn_id, :tool_name, :tool_args, :tool_result, :duration_ms, :is_mock)
                    RETURNING id, tool_name, tool_args, tool_result, duration_ms, is_mock
                """),
                {
                    "turn_id": str(agent_turn["id"]),
                    "tool_name": tc.tool_name,
                    "tool_args": json.dumps(tc.tool_args),
                    "tool_result": json.dumps(tc.tool_result)
                    if not isinstance(tc.tool_result, str)
                    else json.dumps({"result": tc.tool_result}),
                    "duration_ms": tc.duration_ms,
                    "is_mock": tc.is_mock,
                },
            )
            tc_row = tc_result.first()
            if tc_row is None:
                msg = "Expected row from INSERT RETURNING"
                raise RuntimeError(msg)
            tool_calls_saved.append(dict(tc_row._mapping))

        # Update conversation timestamp
        await conn.execute(
            text("UPDATE sandbox_conversations SET updated_at = now() WHERE id = :id"),
            {"id": str(conversation_id)},
        )

    agent_turn["tool_calls"] = tool_calls_saved

    return {
        "customer_turn": customer_turn,
        "agent_turn": agent_turn,
        "latency_ms": result.latency_ms,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "model": result.model,
    }


# ── Rate turn ────────────────────────────────────────────────


@router.patch("/turns/{turn_id}/rate")
async def rate_turn(
    turn_id: UUID,
    request: RateTurn,
    _: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Rate an agent turn (1-5 stars + optional comment)."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                UPDATE sandbox_turns
                SET rating = :rating, rating_comment = :comment
                WHERE id = :id AND speaker = 'agent'
                RETURNING id, rating, rating_comment
            """),
            {
                "id": str(turn_id),
                "rating": request.rating,
                "comment": request.comment,
            },
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Agent turn not found")

    return {"item": dict(row._mapping), "message": "Rating saved"}


# ── Phase 2: Branching + Tree ────────────────────────────────


@router.get("/conversations/{conversation_id}/tree")
async def get_conversation_tree(
    conversation_id: UUID, _: dict[str, Any] = _perm_r
) -> dict[str, Any]:
    """Get the full turn tree structure using a recursive CTE."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                WITH RECURSIVE tree AS (
                    SELECT id, parent_turn_id, turn_number, speaker, content,
                           branch_label, rating, created_at, 0 AS depth
                    FROM sandbox_turns
                    WHERE conversation_id = :conv_id AND parent_turn_id IS NULL
                    UNION ALL
                    SELECT t.id, t.parent_turn_id, t.turn_number, t.speaker, t.content,
                           t.branch_label, t.rating, t.created_at, tree.depth + 1
                    FROM sandbox_turns t
                    JOIN tree ON t.parent_turn_id = tree.id
                )
                SELECT * FROM tree ORDER BY depth, turn_number, created_at
            """),
            {"conv_id": str(conversation_id)},
        )
        nodes = [dict(row._mapping) for row in result]

    return {"tree": nodes}


@router.patch("/turns/{turn_id}/label")
async def update_turn_label(
    turn_id: UUID,
    label: str = Query(..., max_length=200),
    _: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Set or update a branch label on a turn."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                UPDATE sandbox_turns SET branch_label = :label
                WHERE id = :id RETURNING id, branch_label
            """),
            {"id": str(turn_id), "label": label},
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Turn not found")

    return {"item": dict(row._mapping)}


# ── Phase 2: Auto-customer ───────────────────────────────────


class AutoCustomerRequest(BaseModel):
    persona: str = "neutral"
    context_hint: str | None = None


@router.post("/conversations/{conversation_id}/auto-customer")
async def auto_customer(
    conversation_id: UUID,
    request: AutoCustomerRequest,
    _: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Generate a customer reply using Haiku (editable before sending)."""
    from src.sandbox.auto_customer import generate_customer_reply

    engine = await _get_engine()

    # Load latest conversation history
    async with engine.begin() as conn:
        last_turn = await conn.execute(
            text("""
                SELECT conversation_history FROM sandbox_turns
                WHERE conversation_id = :conv_id AND speaker = 'agent'
                ORDER BY turn_number DESC, created_at DESC
                LIMIT 1
            """),
            {"conv_id": str(conversation_id)},
        )
        row = last_turn.first()
        history = row.conversation_history if row and row.conversation_history else []

    reply = await generate_customer_reply(
        conversation_history=history,
        persona=request.persona,
        context_hint=request.context_hint,
    )

    return {"suggested_message": reply, "persona": request.persona}


# ── Phase 2: Scenario starters CRUD ─────────────────────────


class StarterCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    first_message: str = Field(..., min_length=1)
    scenario_type: str | None = None
    tags: list[str] = Field(default_factory=list)
    customer_persona: str = "neutral"
    description: str | None = None
    mock_overrides: dict[str, Any] | None = None
    sort_order: int = 0


class StarterUpdate(BaseModel):
    title: str | None = None
    first_message: str | None = None
    scenario_type: str | None = None
    tags: list[str] | None = None
    customer_persona: str | None = None
    description: str | None = None
    mock_overrides: dict[str, Any] | None = None
    is_active: bool | None = None
    sort_order: int | None = None


@router.get("/scenario-starters")
async def list_starters(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: dict[str, Any] = _perm_r,
) -> dict[str, Any]:
    """List all scenario starters."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        count_result = await conn.execute(text("SELECT COUNT(*) FROM sandbox_scenario_starters"))
        total = count_result.scalar() or 0

        result = await conn.execute(
            text("""
                SELECT id, title, first_message, scenario_type, tags,
                       customer_persona, description, is_active, sort_order,
                       created_at, updated_at
                FROM sandbox_scenario_starters
                ORDER BY sort_order, created_at
                LIMIT :limit OFFSET :offset
            """),
            {"limit": limit, "offset": offset},
        )
        items = [dict(row._mapping) for row in result]

    return {"items": items, "total": total}


@router.post("/scenario-starters")
async def create_starter(request: StarterCreate, _: dict[str, Any] = _perm_w) -> dict[str, Any]:
    """Create a new scenario starter."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                INSERT INTO sandbox_scenario_starters
                    (title, first_message, scenario_type, tags, customer_persona,
                     description, mock_overrides, sort_order)
                VALUES
                    (:title, :first_message, :scenario_type, :tags, :customer_persona,
                     :description, :mock_overrides, :sort_order)
                RETURNING id, title, first_message, scenario_type, tags,
                          customer_persona, description, is_active, sort_order, created_at
            """),
            {
                "title": request.title,
                "first_message": request.first_message,
                "scenario_type": request.scenario_type,
                "tags": request.tags,
                "customer_persona": request.customer_persona,
                "description": request.description,
                "mock_overrides": json.dumps(request.mock_overrides)
                if request.mock_overrides
                else None,
                "sort_order": request.sort_order,
            },
        )
        row = result.first()
        if row is None:
            msg = "Expected row from INSERT RETURNING"
            raise RuntimeError(msg)

    return {"item": dict(row._mapping), "message": "Starter created"}


@router.get("/scenario-starters/{starter_id}")
async def get_starter(starter_id: UUID, _: dict[str, Any] = _perm_r) -> dict[str, Any]:
    """Get a single scenario starter by ID."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT id, title, first_message, scenario_type, tags,
                       customer_persona, description, is_active, sort_order,
                       created_at, updated_at
                FROM sandbox_scenario_starters
                WHERE id = :id
            """),
            {"id": str(starter_id)},
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Starter not found")

    return {"item": dict(row._mapping)}


@router.patch("/scenario-starters/{starter_id}")
async def update_starter(
    starter_id: UUID,
    request: StarterUpdate,
    _: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Update a scenario starter."""
    engine = await _get_engine()

    updates: list[str] = []
    params: dict[str, Any] = {"id": str(starter_id)}

    if request.title is not None:
        updates.append("title = :title")
        params["title"] = request.title
    if request.first_message is not None:
        updates.append("first_message = :first_message")
        params["first_message"] = request.first_message
    if request.scenario_type is not None:
        updates.append("scenario_type = :scenario_type")
        params["scenario_type"] = request.scenario_type
    if request.tags is not None:
        updates.append("tags = :tags")
        params["tags"] = request.tags
    if request.customer_persona is not None:
        updates.append("customer_persona = :customer_persona")
        params["customer_persona"] = request.customer_persona
    if request.description is not None:
        updates.append("description = :description")
        params["description"] = request.description
    if request.mock_overrides is not None:
        updates.append("mock_overrides = :mock_overrides")
        params["mock_overrides"] = json.dumps(request.mock_overrides)
    if request.is_active is not None:
        updates.append("is_active = :is_active")
        params["is_active"] = request.is_active
    if request.sort_order is not None:
        updates.append("sort_order = :sort_order")
        params["sort_order"] = request.sort_order

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates.append("updated_at = now()")
    set_clause = ", ".join(updates)

    async with engine.begin() as conn:
        result = await conn.execute(
            text(f"""
                UPDATE sandbox_scenario_starters
                SET {set_clause}
                WHERE id = :id
                RETURNING id, title, is_active, sort_order, updated_at
            """),
            params,
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Starter not found")

    return {"item": dict(row._mapping), "message": "Starter updated"}


@router.delete("/scenario-starters/{starter_id}")
async def delete_starter(starter_id: UUID, _: dict[str, Any] = _perm_d) -> dict[str, Any]:
    """Delete a scenario starter."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("DELETE FROM sandbox_scenario_starters WHERE id = :id RETURNING id, title"),
            {"id": str(starter_id)},
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Starter not found")

    return {"message": f"Starter '{row.title}' deleted"}


# ── Phase 3: Regression runs ────────────────────────────────


class ReplayRequest(BaseModel):
    new_prompt_version_id: UUID
    branch_turn_ids: list[UUID] | None = None


@router.post("/conversations/{conversation_id}/replay")
async def replay_conversation(
    conversation_id: UUID,
    request: ReplayRequest,
    user: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Replay a baseline conversation with a new prompt version."""
    from src.sandbox.regression import run_regression

    engine = await _get_engine()

    # Create regression run record
    user_id = user.get("user_id")
    async with engine.begin() as conn:
        run_result = await conn.execute(
            text("""
                INSERT INTO sandbox_regression_runs
                    (source_conversation_id, new_prompt_version_id, status, created_by, started_at)
                VALUES
                    (:source_id, :prompt_id, 'running', :created_by, now())
                RETURNING id
            """),
            {
                "source_id": str(conversation_id),
                "prompt_id": str(request.new_prompt_version_id),
                "created_by": user_id,
            },
        )
        run_row = run_result.first()
        assert run_row is not None
        run_id = str(run_row.id)

    # Run regression
    try:
        result = await run_regression(
            engine,
            source_conversation_id=conversation_id,
            new_prompt_version_id=request.new_prompt_version_id,
            branch_turn_ids=request.branch_turn_ids,
            created_by=user_id,
        )

        # Update regression run record
        summary = {
            "turn_diffs": [
                {
                    "turn_number": td.turn_number,
                    "customer_message": td.customer_message,
                    "source_response": td.source_response,
                    "new_response": td.new_response,
                    "source_rating": td.source_rating,
                    "diff_lines": td.diff_lines,
                }
                for td in result.turn_diffs
            ],
        }

        async with engine.begin() as conn:
            await conn.execute(
                text("""
                    UPDATE sandbox_regression_runs
                    SET status = :status, turns_compared = :turns, avg_source_rating = :avg_source,
                        avg_new_rating = :avg_new, score_diff = :score_diff,
                        new_conversation_id = :new_conv_id, summary = :summary,
                        error_message = :error, completed_at = now()
                    WHERE id = :id
                """),
                {
                    "id": run_id,
                    "status": "failed" if result.error else "completed",
                    "turns": result.turns_compared,
                    "avg_source": result.avg_source_rating,
                    "avg_new": result.avg_new_rating,
                    "score_diff": result.score_diff,
                    "new_conv_id": result.new_conversation_id,
                    "summary": json.dumps(summary),
                    "error": result.error,
                },
            )

        return {
            "run_id": run_id,
            "status": "failed" if result.error else "completed",
            "turns_compared": result.turns_compared,
            "new_conversation_id": result.new_conversation_id,
            "avg_source_rating": result.avg_source_rating,
            "error": result.error,
        }

    except Exception as exc:
        logger.exception("Regression run %s failed", run_id)
        async with engine.begin() as conn:
            await conn.execute(
                text("""
                    UPDATE sandbox_regression_runs
                    SET status = 'failed', error_message = :error, completed_at = now()
                    WHERE id = :id
                """),
                {"id": run_id, "error": str(exc)},
            )
        raise HTTPException(
            status_code=500, detail="Regression failed. Check server logs."
        ) from exc


@router.get("/regression-runs")
async def list_regression_runs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: dict[str, Any] = _perm_r,
) -> dict[str, Any]:
    """List regression runs with pagination."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        count_result = await conn.execute(text("SELECT COUNT(*) FROM sandbox_regression_runs"))
        total = count_result.scalar()

        result = await conn.execute(
            text("""
                SELECT r.id, r.source_conversation_id, r.new_prompt_version_id,
                       r.new_conversation_id, r.status, r.turns_compared,
                       r.avg_source_rating, r.avg_new_rating, r.score_diff,
                       r.error_message, r.started_at, r.completed_at, r.created_at,
                       sc.title AS source_title,
                       pv.name AS prompt_version_name
                FROM sandbox_regression_runs r
                LEFT JOIN sandbox_conversations sc ON r.source_conversation_id = sc.id
                LEFT JOIN prompt_versions pv ON r.new_prompt_version_id = pv.id
                ORDER BY r.created_at DESC
                LIMIT :limit OFFSET :offset
            """),
            {"limit": limit, "offset": offset},
        )
        items = [dict(row._mapping) for row in result]

    return {"total": total, "items": items}


@router.get("/regression-runs/{run_id}")
async def get_regression_run(run_id: UUID, _: dict[str, Any] = _perm_r) -> dict[str, Any]:
    """Get regression run detail with per-turn diffs."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT r.*, sc.title AS source_title,
                       pv.name AS prompt_version_name
                FROM sandbox_regression_runs r
                LEFT JOIN sandbox_conversations sc ON r.source_conversation_id = sc.id
                LEFT JOIN prompt_versions pv ON r.new_prompt_version_id = pv.id
                WHERE r.id = :id
            """),
            {"id": str(run_id)},
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Regression run not found")

    return {"item": dict(row._mapping)}


# ── Phase 4: Turn Groups (conversation fragment marking) ─────


@router.post("/conversations/{conversation_id}/turn-groups")
async def create_turn_group(
    conversation_id: UUID,
    request: TurnGroupCreate,
    user: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Create a turn group (marked conversation fragment)."""
    engine = await _get_engine()

    if request.pattern_type not in ("positive", "negative"):
        raise HTTPException(status_code=400, detail="pattern_type must be 'positive' or 'negative'")

    async with engine.begin() as conn:
        # Verify conversation exists
        conv_result = await conn.execute(
            text("SELECT id FROM sandbox_conversations WHERE id = :id"),
            {"id": str(conversation_id)},
        )
        if not conv_result.first():
            raise HTTPException(status_code=404, detail="Conversation not found")

        # Verify all turn_ids belong to this conversation
        turn_ids_str = [str(tid) for tid in request.turn_ids]
        valid_result = await conn.execute(
            text("""
                SELECT COUNT(*) FROM sandbox_turns
                WHERE id = ANY(:turn_ids) AND conversation_id = :conv_id
            """),
            {"turn_ids": turn_ids_str, "conv_id": str(conversation_id)},
        )
        valid_count = valid_result.scalar()
        if valid_count != len(request.turn_ids):
            raise HTTPException(
                status_code=400,
                detail=f"Some turn_ids do not belong to this conversation (found {valid_count}/{len(request.turn_ids)})",
            )

        user_id = user.get("user_id")
        result = await conn.execute(
            text("""
                INSERT INTO sandbox_turn_groups
                    (conversation_id, turn_ids, intent_label, pattern_type,
                     rating, rating_comment, correction, tags, created_by)
                VALUES
                    (:conv_id, :turn_ids, :intent_label, :pattern_type,
                     :rating, :rating_comment, :correction, :tags, :created_by)
                RETURNING id, conversation_id, turn_ids, intent_label, pattern_type,
                          rating, rating_comment, correction, tags, is_exported, created_at
            """),
            {
                "conv_id": str(conversation_id),
                "turn_ids": turn_ids_str,
                "intent_label": request.intent_label,
                "pattern_type": request.pattern_type,
                "rating": request.rating,
                "rating_comment": request.rating_comment,
                "correction": request.correction,
                "tags": request.tags,
                "created_by": user_id,
            },
        )
        row = result.first()

    assert row is not None
    return {"item": dict(row._mapping), "message": "Turn group created"}


@router.get("/conversations/{conversation_id}/turn-groups")
async def list_turn_groups(
    conversation_id: UUID,
    _: dict[str, Any] = _perm_r,
) -> dict[str, Any]:
    """List turn groups for a conversation."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT id, conversation_id, turn_ids, intent_label, pattern_type,
                       rating, rating_comment, correction, tags, is_exported, created_at
                FROM sandbox_turn_groups
                WHERE conversation_id = :conv_id
                ORDER BY created_at
            """),
            {"conv_id": str(conversation_id)},
        )
        items = [dict(row._mapping) for row in result]

    return {"items": items}


@router.patch("/turn-groups/{group_id}")
async def update_turn_group(
    group_id: UUID,
    request: TurnGroupUpdate,
    _: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Update a turn group."""
    engine = await _get_engine()

    updates: list[str] = []
    params: dict[str, Any] = {"id": str(group_id)}

    if request.intent_label is not None:
        updates.append("intent_label = :intent_label")
        params["intent_label"] = request.intent_label
    if request.pattern_type is not None:
        if request.pattern_type not in ("positive", "negative"):
            raise HTTPException(
                status_code=400, detail="pattern_type must be 'positive' or 'negative'"
            )
        updates.append("pattern_type = :pattern_type")
        params["pattern_type"] = request.pattern_type
    if request.rating is not None:
        updates.append("rating = :rating")
        params["rating"] = request.rating
    if request.rating_comment is not None:
        updates.append("rating_comment = :rating_comment")
        params["rating_comment"] = request.rating_comment
    if request.correction is not None:
        updates.append("correction = :correction")
        params["correction"] = request.correction
    if request.tags is not None:
        updates.append("tags = :tags")
        params["tags"] = request.tags

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    set_clause = ", ".join(updates)

    async with engine.begin() as conn:
        result = await conn.execute(
            text(f"""
                UPDATE sandbox_turn_groups
                SET {set_clause}
                WHERE id = :id
                RETURNING id, conversation_id, turn_ids, intent_label, pattern_type,
                          rating, rating_comment, correction, tags, is_exported, created_at
            """),
            params,
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Turn group not found")

    return {"item": dict(row._mapping), "message": "Turn group updated"}


@router.delete("/turn-groups/{group_id}")
async def delete_turn_group(
    group_id: UUID,
    _: dict[str, Any] = _perm_d,
) -> dict[str, Any]:
    """Delete a turn group."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("DELETE FROM sandbox_turn_groups WHERE id = :id RETURNING id"),
            {"id": str(group_id)},
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Turn group not found")

    return {"message": "Turn group deleted"}


# ── Phase 4: Pattern Bank ────────────────────────────────────


@router.post("/turn-groups/{group_id}/export")
async def export_turn_group(
    group_id: UUID,
    request: ExportPatternRequest,
    _: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Export a turn group to the conversation pattern bank."""
    from src.knowledge.embeddings import EmbeddingGenerator
    from src.sandbox.patterns import export_group_to_pattern

    engine = await _get_engine()
    settings = get_settings()

    generator = EmbeddingGenerator(settings.openai.api_key)
    await generator.open()

    try:
        pattern = await export_group_to_pattern(
            engine,
            generator,
            group_id,
            request.guidance_note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await generator.close()

    return {"item": pattern, "message": "Pattern exported"}


@router.get("/patterns")
async def list_patterns(
    pattern_type: str | None = Query(None),
    is_active: bool | None = Query(None),
    search: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: dict[str, Any] = _perm_r,
) -> dict[str, Any]:
    """List conversation patterns with filters."""
    engine = await _get_engine()

    conditions = ["1=1"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    if pattern_type:
        conditions.append("pattern_type = :pattern_type")
        params["pattern_type"] = pattern_type
    if is_active is not None:
        conditions.append("is_active = :is_active")
        params["is_active"] = is_active
    if search:
        conditions.append("(intent_label ILIKE :search OR guidance_note ILIKE :search)")
        params["search"] = f"%{search}%"

    where = " AND ".join(conditions)

    async with engine.begin() as conn:
        count_result = await conn.execute(
            text(f"SELECT COUNT(*) FROM conversation_patterns WHERE {where}"),
            params,
        )
        total = count_result.scalar()

        result = await conn.execute(
            text(f"""
                SELECT id, source_group_id, intent_label, pattern_type,
                       customer_messages, agent_messages, guidance_note,
                       scenario_type, tags, rating, is_active, times_used,
                       created_at, updated_at
                FROM conversation_patterns
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
            """),
            params,
        )
        items = [dict(row._mapping) for row in result]

    return {"total": total, "items": items}


@router.get("/patterns/{pattern_id}")
async def get_pattern(
    pattern_id: UUID,
    _: dict[str, Any] = _perm_r,
) -> dict[str, Any]:
    """Get pattern detail."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT id, source_group_id, intent_label, pattern_type,
                       customer_messages, agent_messages, guidance_note,
                       scenario_type, tags, rating, is_active, times_used,
                       created_at, updated_at
                FROM conversation_patterns
                WHERE id = :id
            """),
            {"id": str(pattern_id)},
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Pattern not found")

    return {"item": dict(row._mapping)}


@router.patch("/patterns/{pattern_id}")
async def update_pattern(
    pattern_id: UUID,
    request: PatternUpdate,
    _: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Update a conversation pattern."""
    engine = await _get_engine()

    updates: list[str] = []
    params: dict[str, Any] = {"id": str(pattern_id)}

    if request.guidance_note is not None:
        updates.append("guidance_note = :guidance_note")
        params["guidance_note"] = request.guidance_note
    if request.is_active is not None:
        updates.append("is_active = :is_active")
        params["is_active"] = request.is_active
    if request.tags is not None:
        updates.append("tags = :tags")
        params["tags"] = request.tags

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates.append("updated_at = now()")
    set_clause = ", ".join(updates)

    async with engine.begin() as conn:
        result = await conn.execute(
            text(f"""
                UPDATE conversation_patterns
                SET {set_clause}
                WHERE id = :id
                RETURNING id, intent_label, pattern_type, guidance_note,
                          is_active, tags, times_used, updated_at
            """),
            params,
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Pattern not found")

    return {"item": dict(row._mapping), "message": "Pattern updated"}


@router.delete("/patterns/{pattern_id}")
async def delete_pattern(
    pattern_id: UUID,
    _: dict[str, Any] = _perm_d,
) -> dict[str, Any]:
    """Delete a conversation pattern."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("DELETE FROM conversation_patterns WHERE id = :id RETURNING id"),
            {"id": str(pattern_id)},
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Pattern not found")

    return {"message": "Pattern deleted"}


@router.post("/patterns/search-test")
async def test_pattern_search(
    query: str = Query(..., min_length=1),
    top_k: int = Query(3, ge=1, le=10),
    min_similarity: float = Query(0.75, ge=0.0, le=1.0),
    _: dict[str, Any] = _perm_r,
) -> dict[str, Any]:
    """Test pattern search — find which patterns match a given query."""
    from src.knowledge.embeddings import EmbeddingGenerator

    settings = get_settings()
    engine = await _get_engine()

    generator = EmbeddingGenerator(settings.openai.api_key)
    await generator.open()

    try:
        embedding = await generator.generate_single(query)
        embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"

        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    SELECT id, intent_label, pattern_type, customer_messages,
                           guidance_note, rating, tags,
                           1 - (embedding <=> CAST(:embedding AS vector)) AS similarity
                    FROM conversation_patterns
                    WHERE is_active = true
                      AND embedding IS NOT NULL
                      AND 1 - (embedding <=> CAST(:embedding AS vector)) >= :min_sim
                    ORDER BY embedding <=> CAST(:embedding AS vector)
                    LIMIT :top_k
                """),
                {
                    "embedding": embedding_str,
                    "min_sim": min_similarity,
                    "top_k": top_k,
                },
            )
            items = [dict(row._mapping) for row in result]
    finally:
        await generator.close()

    return {"query": query, "results": items}
