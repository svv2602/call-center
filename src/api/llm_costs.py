"""Admin API for LLM cost analysis.

Manage model pricing, provider catalog, and compare costs across providers/task types.
"""

from __future__ import annotations

import logging
from datetime import date  # noqa: TC003
from typing import Any
from uuid import UUID  # noqa: TC003

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.api.auth import require_permission
from src.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/llm-costs", tags=["llm-costs"])

_engine: AsyncEngine | None = None

_perm_r = Depends(require_permission("analytics:read"))
_perm_w = Depends(require_permission("llm_config:write"))

# Module-level Query defaults to satisfy B008
_q_date_from = Query(None)
_q_date_to = Query(None)
_q_task_type = Query(None)
_q_tenant_id = Query(None)
_q_provider_type = Query(None)
_q_search = Query(None)


async def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database.url, pool_pre_ping=True)
    return _engine


# --- Pydantic models ---


class PricingCreate(BaseModel):
    provider_key: str
    model_name: str
    display_name: str
    input_price_per_1m: float
    output_price_per_1m: float


class PricingUpdate(BaseModel):
    model_name: str | None = None
    display_name: str | None = None
    input_price_per_1m: float | None = None
    output_price_per_1m: float | None = None
    include_in_comparison: bool | None = None


class CatalogAddRequest(BaseModel):
    model_key: str
    provider_key: str
    display_name: str | None = None
    include_in_comparison: bool = True


class CatalogDismissRequest(BaseModel):
    model_keys: list[str]


# --- Pricing CRUD ---


@router.get("/pricing")
async def list_pricing(_: Any = _perm_r) -> dict[str, Any]:
    """List all model pricing entries."""
    engine = await _get_engine()
    async with engine.begin() as conn:
        rows = await conn.execute(
            text("""
                SELECT id, provider_key, model_name, display_name,
                       input_price_per_1m, output_price_per_1m,
                       is_system, provider_type, include_in_comparison,
                       catalog_model_key, created_at, updated_at
                FROM llm_model_pricing
                ORDER BY is_system DESC, display_name
            """)
        )
        items = [
            {
                "id": str(r.id),
                "provider_key": r.provider_key,
                "model_name": r.model_name,
                "display_name": r.display_name,
                "input_price_per_1m": float(r.input_price_per_1m),
                "output_price_per_1m": float(r.output_price_per_1m),
                "is_system": r.is_system,
                "provider_type": r.provider_type,
                "include_in_comparison": r.include_in_comparison,
                "catalog_model_key": r.catalog_model_key,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ]
    return {"items": items}


@router.post("/pricing", status_code=201)
async def create_pricing(body: PricingCreate, _: Any = _perm_w) -> dict[str, Any]:
    """Add a custom model pricing entry (is_system=false)."""
    engine = await _get_engine()
    async with engine.begin() as conn:
        # Check for duplicate provider_key
        dup = await conn.execute(
            text("SELECT id FROM llm_model_pricing WHERE provider_key = :pk"),
            {"pk": body.provider_key},
        )
        if dup.first() is not None:
            raise HTTPException(409, f"Provider key '{body.provider_key}' already exists")

        result = await conn.execute(
            text("""
                INSERT INTO llm_model_pricing
                    (provider_key, model_name, display_name,
                     input_price_per_1m, output_price_per_1m, is_system)
                VALUES (:pk, :mn, :dn, :ip, :op, false)
                RETURNING id
            """),
            {
                "pk": body.provider_key,
                "mn": body.model_name,
                "dn": body.display_name,
                "ip": body.input_price_per_1m,
                "op": body.output_price_per_1m,
            },
        )
        row = result.first()
    return {"id": str(row.id), "message": "Created"}


@router.patch("/pricing/{pricing_id}")
async def update_pricing(
    pricing_id: UUID, body: PricingUpdate, _: Any = _perm_w
) -> dict[str, Any]:
    """Update pricing for a model (system or custom)."""
    sets: list[str] = []
    params: dict[str, Any] = {"pid": str(pricing_id)}

    if body.model_name is not None:
        sets.append("model_name = :mn")
        params["mn"] = body.model_name
    if body.display_name is not None:
        sets.append("display_name = :dn")
        params["dn"] = body.display_name
    if body.input_price_per_1m is not None:
        sets.append("input_price_per_1m = :ip")
        params["ip"] = body.input_price_per_1m
    if body.output_price_per_1m is not None:
        sets.append("output_price_per_1m = :op")
        params["op"] = body.output_price_per_1m
    if body.include_in_comparison is not None:
        sets.append("include_in_comparison = :ic")
        params["ic"] = body.include_in_comparison

    if not sets:
        raise HTTPException(400, "No fields to update")

    sets.append("updated_at = now()")

    engine = await _get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            text(f"UPDATE llm_model_pricing SET {', '.join(sets)} WHERE id = :pid"),
            params,
        )
        if result.rowcount == 0:
            raise HTTPException(404, "Pricing entry not found")
    return {"message": "Updated"}


@router.delete("/pricing/{pricing_id}")
async def delete_pricing(pricing_id: UUID, _: Any = _perm_w) -> dict[str, Any]:
    """Delete a custom pricing entry. System entries cannot be deleted."""
    engine = await _get_engine()
    async with engine.begin() as conn:
        row = await conn.execute(
            text("SELECT is_system FROM llm_model_pricing WHERE id = :pid"),
            {"pid": str(pricing_id)},
        )
        entry = row.first()
        if entry is None:
            raise HTTPException(404, "Pricing entry not found")
        if entry.is_system:
            raise HTTPException(403, "Cannot delete system pricing entry")

        await conn.execute(
            text("DELETE FROM llm_model_pricing WHERE id = :pid"),
            {"pid": str(pricing_id)},
        )
    return {"message": "Deleted"}


@router.post("/pricing/sync-system")
async def sync_system_pricing(_: Any = _perm_w) -> dict[str, Any]:
    """Synchronize system pricing entries from LLM routing config.

    Inserts missing system models, updates model_name for existing ones.
    """
    from src.llm.models import DEFAULT_ROUTING_CONFIG

    # Map provider_key → (model_name, display_name, input_price, output_price)
    known_prices: dict[str, tuple[str, str, float, float]] = {
        "anthropic-sonnet": ("claude-sonnet-4-5-20250929", "Claude Sonnet 4.5", 3.0, 15.0),
        "anthropic-haiku": ("claude-haiku-4-5-20251001", "Claude Haiku 4.5", 1.0, 5.0),
        "openai-gpt41-mini": ("gpt-4.1-mini", "GPT-4.1 Mini", 0.4, 1.6),
        "openai-gpt41-nano": ("gpt-4.1-nano", "GPT-4.1 Nano", 0.1, 0.4),
        "deepseek-chat": ("deepseek-chat", "DeepSeek Chat", 0.27, 1.1),
        "gemini-flash": ("gemini-2.5-flash", "Gemini 2.5 Flash", 0.3, 2.5),
        "openai-gpt5-mini": ("gpt-5-mini", "GPT-5 Mini", 0.25, 2.0),
        "openai-gpt5-nano": ("gpt-5-nano", "GPT-5 Nano", 0.05, 0.4),
    }

    providers = DEFAULT_ROUTING_CONFIG.get("providers", {})
    synced = 0
    engine = await _get_engine()

    async with engine.begin() as conn:
        for key, cfg in providers.items():
            model = cfg.get("model", key)
            prices = known_prices.get(key)
            if prices is None:
                display = key
                inp, out = 0.0, 0.0
            else:
                model, display, inp, out = prices

            await conn.execute(
                text("""
                    INSERT INTO llm_model_pricing
                        (provider_key, model_name, display_name,
                         input_price_per_1m, output_price_per_1m, is_system)
                    VALUES (:pk, :mn, :dn, :ip, :op, true)
                    ON CONFLICT (provider_key) DO UPDATE SET
                        model_name = EXCLUDED.model_name,
                        updated_at = now()
                """),
                {"pk": key, "mn": model, "dn": display, "ip": inp, "op": out},
            )
            synced += 1

    return {"message": f"Synced {synced} system models"}


# --- Catalog endpoints ---


@router.get("/catalog")
async def list_catalog(
    provider_type: str | None = _q_provider_type,
    search: str | None = _q_search,
    _: Any = _perm_r,
) -> dict[str, Any]:
    """List models from the pricing catalog with optional filters."""
    conditions = ["1=1"]
    params: dict[str, Any] = {}

    if provider_type:
        conditions.append("c.provider_type = :pt")
        params["pt"] = provider_type
    if search:
        conditions.append("(c.model_key ILIKE :s OR c.display_name ILIKE :s)")
        params["s"] = f"%{search}%"

    where = " AND ".join(conditions)

    engine = await _get_engine()
    async with engine.begin() as conn:
        rows = await conn.execute(
            text(f"""
                SELECT c.model_key, c.provider_type, c.display_name,
                       c.input_price_per_1m, c.output_price_per_1m,
                       c.max_input_tokens, c.max_output_tokens,
                       c.is_new, c.synced_at,
                       CASE WHEN p.id IS NOT NULL THEN true ELSE false END AS is_added
                FROM llm_pricing_catalog c
                LEFT JOIN llm_model_pricing p ON p.catalog_model_key = c.model_key
                WHERE {where}
                ORDER BY c.provider_type, c.display_name
            """),
            params,
        )
        items = [
            {
                "model_key": r.model_key,
                "provider_type": r.provider_type,
                "display_name": r.display_name,
                "input_price_per_1m": float(r.input_price_per_1m),
                "output_price_per_1m": float(r.output_price_per_1m),
                "max_input_tokens": r.max_input_tokens,
                "max_output_tokens": r.max_output_tokens,
                "is_new": r.is_new,
                "synced_at": r.synced_at.isoformat() if r.synced_at else None,
                "is_added": r.is_added,
            }
            for r in rows
        ]
    return {"items": items}


@router.get("/catalog/new-count")
async def catalog_new_count(_: Any = _perm_r) -> dict[str, Any]:
    """Count of new (unreviewed) models in the catalog."""
    engine = await _get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            text("SELECT COUNT(*) AS cnt FROM llm_pricing_catalog WHERE is_new = true")
        )
        count = result.scalar() or 0
    return {"count": count}


@router.get("/catalog/sync-status")
async def catalog_sync_status(_: Any = _perm_r) -> dict[str, Any]:
    """Get last catalog sync timestamp from Redis."""
    import redis.asyncio as aioredis

    settings = get_settings()
    try:
        r = aioredis.from_url(settings.redis.url)
        val = await r.get("llm_pricing:last_sync_at")
        await r.close()
        return {"last_sync_at": val.decode() if val else None}
    except Exception:
        logger.warning("Failed to read catalog sync status from Redis", exc_info=True)
        return {"last_sync_at": None}


@router.post("/catalog/add", status_code=201)
async def catalog_add(body: CatalogAddRequest, _: Any = _perm_w) -> dict[str, Any]:
    """Add a model from the catalog to 'My Models' (llm_model_pricing)."""
    engine = await _get_engine()
    async with engine.begin() as conn:
        # Check catalog entry exists
        cat = await conn.execute(
            text("""
                SELECT model_key, provider_type, display_name,
                       input_price_per_1m, output_price_per_1m
                FROM llm_pricing_catalog WHERE model_key = :mk
            """),
            {"mk": body.model_key},
        )
        cat_row = cat.first()
        if cat_row is None:
            raise HTTPException(404, f"Catalog model '{body.model_key}' not found")

        # Check for duplicate provider_key
        dup = await conn.execute(
            text("SELECT id FROM llm_model_pricing WHERE provider_key = :pk"),
            {"pk": body.provider_key},
        )
        if dup.first() is not None:
            raise HTTPException(409, f"Provider key '{body.provider_key}' already exists")

        display = body.display_name or cat_row.display_name or body.model_key

        result = await conn.execute(
            text("""
                INSERT INTO llm_model_pricing
                    (provider_key, model_name, display_name,
                     input_price_per_1m, output_price_per_1m,
                     is_system, provider_type, include_in_comparison, catalog_model_key)
                VALUES (:pk, :mn, :dn, :ip, :op, false, :pt, :ic, :cmk)
                RETURNING id
            """),
            {
                "pk": body.provider_key,
                "mn": body.model_key,
                "dn": display,
                "ip": float(cat_row.input_price_per_1m),
                "op": float(cat_row.output_price_per_1m),
                "pt": cat_row.provider_type,
                "ic": body.include_in_comparison,
                "cmk": body.model_key,
            },
        )
        row = result.first()

        # Mark as not new in catalog
        await conn.execute(
            text("UPDATE llm_pricing_catalog SET is_new = false WHERE model_key = :mk"),
            {"mk": body.model_key},
        )

    return {"id": str(row.id), "message": "Model added to pricing"}


@router.post("/catalog/dismiss")
async def catalog_dismiss(body: CatalogDismissRequest, _: Any = _perm_w) -> dict[str, Any]:
    """Dismiss new models (set is_new=false). Models stay in catalog."""
    if not body.model_keys:
        raise HTTPException(400, "No model keys provided")

    engine = await _get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                UPDATE llm_pricing_catalog
                SET is_new = false
                WHERE model_key = ANY(:keys) AND is_new = true
            """),
            {"keys": body.model_keys},
        )
    return {"dismissed": result.rowcount}


@router.post("/catalog/sync")
async def catalog_sync_trigger(_: Any = _perm_w) -> dict[str, Any]:
    """Trigger manual catalog sync from LiteLLM."""
    from src.tasks.pricing_sync import sync_llm_pricing_catalog

    sync_llm_pricing_catalog.delay()
    return {"message": "Catalog sync started"}


# --- Usage analysis ---


@router.get("/usage/summary")
async def usage_summary(
    date_from: date | None = _q_date_from,
    date_to: date | None = _q_date_to,
    task_type: str | None = _q_task_type,
    tenant_id: str | None = _q_tenant_id,
    _: Any = _perm_r,
) -> dict[str, Any]:
    """Aggregate LLM usage by task_type and provider for a period."""
    conditions = ["1=1"]
    params: dict[str, Any] = {}

    if date_from:
        conditions.append("u.created_at >= :df")
        params["df"] = date_from
    if date_to:
        conditions.append("u.created_at < :dt + interval '1 day'")
        params["dt"] = date_to
    if task_type:
        conditions.append("u.task_type = :tt")
        params["tt"] = task_type
    if tenant_id:
        conditions.append("u.tenant_id = :tid")
        params["tid"] = tenant_id

    where = " AND ".join(conditions)

    engine = await _get_engine()
    async with engine.begin() as conn:
        rows = await conn.execute(
            text(f"""
                SELECT
                    u.task_type,
                    u.provider_key,
                    COUNT(*) AS call_count,
                    SUM(u.input_tokens)  AS total_input_tokens,
                    SUM(u.output_tokens) AS total_output_tokens,
                    AVG(u.latency_ms)    AS avg_latency_ms,
                    COALESCE(
                        SUM(u.input_tokens)  / 1000000.0 * p.input_price_per_1m +
                        SUM(u.output_tokens) / 1000000.0 * p.output_price_per_1m,
                        0
                    ) AS total_cost
                FROM llm_usage_log u
                LEFT JOIN llm_model_pricing p ON p.provider_key = u.provider_key
                WHERE {where}
                GROUP BY u.task_type, u.provider_key,
                         p.input_price_per_1m, p.output_price_per_1m
                ORDER BY total_cost DESC
            """),
            params,
        )
        items = [
            {
                "task_type": r.task_type,
                "provider_key": r.provider_key,
                "call_count": r.call_count,
                "total_input_tokens": int(r.total_input_tokens),
                "total_output_tokens": int(r.total_output_tokens),
                "avg_latency_ms": round(float(r.avg_latency_ms), 0) if r.avg_latency_ms else None,
                "total_cost": round(float(r.total_cost), 6),
            }
            for r in rows
        ]
    return {"items": items, "period": {"from": str(date_from), "to": str(date_to)}}


@router.get("/usage/model-comparison")
async def model_comparison(
    date_from: date | None = _q_date_from,
    date_to: date | None = _q_date_to,
    task_type: str | None = _q_task_type,
    tenant_id: str | None = _q_tenant_id,
    _: Any = _perm_r,
) -> dict[str, Any]:
    """Compare actual cost vs hypothetical cost on other models.

    Takes real token usage from llm_usage_log and recalculates
    what each model in llm_model_pricing would have cost.
    Only includes models with include_in_comparison=true.
    """
    conditions = ["1=1"]
    params: dict[str, Any] = {}

    if date_from:
        conditions.append("created_at >= :df")
        params["df"] = date_from
    if date_to:
        conditions.append("created_at < :dt + interval '1 day'")
        params["dt"] = date_to
    if task_type:
        conditions.append("task_type = :tt")
        params["tt"] = task_type
    if tenant_id:
        conditions.append("tenant_id = :tid")
        params["tid"] = tenant_id

    where = " AND ".join(conditions)

    engine = await _get_engine()
    async with engine.begin() as conn:
        # Aggregate actual usage
        agg = await conn.execute(
            text(f"""
                SELECT
                    provider_key AS actual_provider,
                    SUM(input_tokens)  AS total_input_tokens,
                    SUM(output_tokens) AS total_output_tokens,
                    COUNT(*) AS call_count
                FROM llm_usage_log
                WHERE {where}
                GROUP BY provider_key
                ORDER BY call_count DESC
            """),
            params,
        )
        usage_rows = agg.fetchall()

        if not usage_rows:
            return {
                "period": {"from": str(date_from), "to": str(date_to)},
                "task_type": task_type,
                "actual_provider": None,
                "actual_cost": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "comparisons": [],
            }

        # Sum across providers for total tokens
        total_input = sum(r.total_input_tokens for r in usage_rows)
        total_output = sum(r.total_output_tokens for r in usage_rows)
        actual_provider = usage_rows[0].actual_provider  # most-used provider

        # Get pricing for comparison (only include_in_comparison=true)
        pricing = await conn.execute(
            text("""
                SELECT provider_key, display_name,
                       input_price_per_1m, output_price_per_1m
                FROM llm_model_pricing
                WHERE include_in_comparison = true
                ORDER BY display_name
            """)
        )
        pricing_rows = pricing.fetchall()

        # Calculate actual cost from per-provider usage (use all pricing for actual calc)
        all_pricing = await conn.execute(
            text("""
                SELECT provider_key, input_price_per_1m, output_price_per_1m
                FROM llm_model_pricing
            """)
        )
        all_pricing_rows = all_pricing.fetchall()
        pricing_map = {r.provider_key: r for r in all_pricing_rows}

        actual_cost = 0.0
        for ur in usage_rows:
            pr = pricing_map.get(ur.actual_provider)
            if pr:
                actual_cost += (
                    ur.total_input_tokens / 1_000_000 * float(pr.input_price_per_1m)
                    + ur.total_output_tokens / 1_000_000 * float(pr.output_price_per_1m)
                )

        # Calculate hypothetical cost for each comparison model
        comparisons = []
        for pr in pricing_rows:
            cost = (
                total_input / 1_000_000 * float(pr.input_price_per_1m)
                + total_output / 1_000_000 * float(pr.output_price_per_1m)
            )
            comparisons.append(
                {
                    "provider_key": pr.provider_key,
                    "display_name": pr.display_name,
                    "cost": round(cost, 6),
                    "is_actual": pr.provider_key == actual_provider,
                }
            )

        # Sort by cost ascending
        comparisons.sort(key=lambda c: c["cost"])

    return {
        "period": {"from": str(date_from), "to": str(date_to)},
        "task_type": task_type,
        "actual_provider": actual_provider,
        "actual_cost": round(actual_cost, 6),
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "comparisons": comparisons,
    }
