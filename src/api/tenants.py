"""Admin API for tenant management.

Tenants represent retail networks (Prokoleso, Tvoya Shina, Technoopttorg)
sharing the same Asterisk/AI infrastructure. Each tenant has its own
Store API config, enabled tools, greeting, and prompt customization.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from uuid import UUID  # noqa: TC003 - FastAPI needs UUID at runtime for path params

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from src.agent.network_policy import (
    BANK_LABELS,
    DELIVERY_MODES,
    ORDER_FINISH_MODES,
    PAYMENT_LABELS,
    RECOMMEND_COUNT_MAX,
    RECOMMEND_COUNT_MIN,
    SERVICE_LABELS,
    SERVICE_TOOLS,
)
from src.agent.tools import ALL_TOOLS
from src.api.auth import require_permission
from src.api.database import get_engine as _get_engine
from src.core.working_hours import validate_schema as _validate_working_hours

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/tenants", tags=["tenants"])

# Module-level dependencies to satisfy B008 lint rule
_perm_r = Depends(require_permission("tenants:read"))
_perm_w = Depends(require_permission("tenants:write"))
_perm_d = Depends(require_permission("tenants:delete"))

# Canonical tool names for validation
_VALID_TOOL_NAMES = frozenset(t["name"] for t in ALL_TOOLS)


# ─── Pydantic models ──────────────────────────────────────


class TenantCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=50, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=200)
    network_id: str = Field(min_length=1, max_length=50)
    agent_name: str = Field(default="Олена", max_length=100)
    greeting: str | None = None
    enabled_tools: list[str] = []
    extensions: list[str] = []
    prompt_suffix: str | None = None
    config: dict[str, Any] = {}
    working_hours: dict[str, Any] | None = None
    is_active: bool = True
    # Not stored: the owner's explicit "this network provides none of the
    # services its tools serve" (see `_check_service_coverage`).
    confirm_no_services: bool = False


class TenantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    network_id: str | None = Field(default=None, min_length=1, max_length=50)
    agent_name: str | None = Field(default=None, max_length=100)
    greeting: str | None = None
    enabled_tools: list[str] | None = None
    extensions: list[str] | None = None
    prompt_suffix: str | None = None
    config: dict[str, Any] | None = None
    working_hours: dict[str, Any] | None = None
    is_active: bool | None = None
    confirm_no_services: bool = False


class NetworkSettingsUpdate(BaseModel):
    """The «Умови мережі» form: two ``config`` keys, merged into the rest."""

    sales_enabled: bool = False
    network_policy: dict[str, Any]
    confirm_no_services: bool = False


# ─── Validation helpers ───────────────────────────────────


def _validate_tools(tools: list[str]) -> None:
    """Raise 400 if any tool name is not in the canonical list."""
    invalid = [t for t in tools if t not in _VALID_TOOL_NAMES]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid tool names: {', '.join(invalid)}. "
            f"Valid tools: {', '.join(sorted(_VALID_TOOL_NAMES))}",
        )


_EXTENSION_RE = re.compile(r"^\d{1,10}$")


def _validate_extensions(extensions: list[str]) -> None:
    """Raise 400 if any extension is not a numeric string (1-10 digits)."""
    invalid = [e for e in extensions if not _EXTENSION_RE.match(e)]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid extensions: {', '.join(invalid)}. "
            "Extensions must be numeric strings (1-10 digits).",
        )


async def _check_extension_uniqueness(
    conn: Any, extensions: list[str], exclude_tenant_id: str | None = None
) -> None:
    """Raise 409 if any extension is already assigned to another tenant."""
    if not extensions:
        return
    params: dict[str, Any] = {"extensions": extensions}
    exclude_clause = ""
    if exclude_tenant_id:
        exclude_clause = "AND id != :exclude_id"
        params["exclude_id"] = exclude_tenant_id
    result = await conn.execute(
        text(f"""
            SELECT slug, extensions FROM tenants
            WHERE extensions && CAST(:extensions AS text[])
              AND is_active = true {exclude_clause}
        """),
        params,
    )
    conflict = result.first()
    if conflict:
        raise HTTPException(
            status_code=409,
            detail=f"Extension(s) already assigned to tenant '{conflict._mapping['slug']}'",
        )


# ─── Network policy validation ────────────────────────────
#
# `NetworkPolicy.from_tenant_config` is lenient on purpose — a live call must
# never fail on bad config, so garbage silently becomes "promise nothing".
# The admin API is the other side: a typo saved here would silently switch a
# service or a payment method off in every call. So the write path is strict
# and answers 422 with every problem named.

_POLICY_ENUM_LISTS: dict[str, dict[str, str]] = {
    "services": SERVICE_LABELS,
    "payment_methods": PAYMENT_LABELS,
    "installment_banks": BANK_LABELS,
}
_POLICY_NAME_LISTS: tuple[str, ...] = (
    "delivery_carriers",
    "extended_warranty_brands",
    "brand_priority",
)
_POLICY_TEXTS: tuple[str, ...] = ("delivery_eta_text", "cod_fee_text")
_POLICY_KEYS: frozenset[str] = frozenset(
    {
        *_POLICY_ENUM_LISTS,
        *_POLICY_NAME_LISTS,
        *_POLICY_TEXTS,
        "delivery_mode",
        "pickup_available",
        "recommend_count",
        "order_finish",
    }
)

#: Tools that serve each network service — single source in ``network_policy``.
_SERVICE_TOOLS = SERVICE_TOOLS


def _list_errors(raw: dict[str, Any], key: str, *, allowed: dict[str, str] | None) -> list[str]:
    value = raw.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        return [f"{key} must be a list"]
    errors: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{key}: {item!r} is not a non-empty string")
        elif allowed is not None and item.strip().lower() not in allowed:
            errors.append(f"{key}: {item!r} is not one of {list(allowed)}")
    return errors


def _policy_errors(raw: Any) -> list[str]:
    """Every problem of a ``config.network_policy`` value (empty = valid)."""
    if not isinstance(raw, dict):
        return [f"network_policy must be an object, got {type(raw).__name__}"]
    errors: list[str] = []

    unknown = sorted(set(raw) - _POLICY_KEYS)
    if unknown:
        errors.append(f"network_policy: unknown keys {unknown}; allowed {sorted(_POLICY_KEYS)}")

    mode = raw.get("delivery_mode", "unknown")
    if mode not in DELIVERY_MODES:
        errors.append(f"delivery_mode {mode!r} is not one of {list(DELIVERY_MODES)}")

    finish = raw.get("order_finish", ORDER_FINISH_MODES[0])
    if finish not in ORDER_FINISH_MODES:
        errors.append(f"order_finish {finish!r} is not one of {list(ORDER_FINISH_MODES)}")

    for key, allowed in _POLICY_ENUM_LISTS.items():
        errors.extend(_list_errors(raw, key, allowed=allowed))
    for key in _POLICY_NAME_LISTS:
        errors.extend(_list_errors(raw, key, allowed=None))

    for key in _POLICY_TEXTS:
        value = raw.get(key)
        if value is not None and not isinstance(value, str):
            errors.append(f"{key} must be a string or null")

    if not isinstance(raw.get("pickup_available", False), bool):
        errors.append("pickup_available must be true or false")

    count = raw.get("recommend_count", RECOMMEND_COUNT_MAX)
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or not RECOMMEND_COUNT_MIN <= count <= RECOMMEND_COUNT_MAX
    ):
        errors.append(
            f"recommend_count {count!r} must be an integer "
            f"{RECOMMEND_COUNT_MIN}..{RECOMMEND_COUNT_MAX}"
        )
    return errors


def _config_policy_errors(config: dict[str, Any]) -> list[str]:
    """Problems of the policy keys of a whole ``tenants.config``."""
    errors: list[str] = []
    if "sales_enabled" in config and not isinstance(config["sales_enabled"], bool):
        errors.append("sales_enabled must be true or false")
    if "network_policy" in config:
        errors.extend(_policy_errors(config["network_policy"]))
    return errors


def _raise_policy_errors(errors: list[str]) -> None:
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))


def _uncovered_services(config: dict[str, Any], enabled_tools: list[str] | None) -> list[str]:
    """Services whose tools this tenant has but the written policy leaves out.

    A written ``network_policy`` (or ``sales_enabled``) makes a live call read
    a service missing from ``services`` as "not provided": the claim guard
    refuses it and the sales scope strips its tools. An empty policy ``{}``
    would therefore silently cut fitting from a fitting network. Default-deny
    over the whole service enum; empty ``enabled_tools`` means every tool.
    """
    raw = config.get("network_policy")
    if not isinstance(raw, dict) and config.get("sales_enabled") is not True:
        return []
    services = raw.get("services") if isinstance(raw, dict) else None
    offered = {
        s.strip().lower()
        for s in (services if isinstance(services, list) else [])
        if isinstance(s, str)
    }
    tools = set(enabled_tools) if enabled_tools else set(_VALID_TOOL_NAMES)
    return [
        service
        for service in SERVICE_LABELS
        if service not in offered and tools & _SERVICE_TOOLS.get(service, frozenset())
    ]


def _check_service_coverage(
    config: dict[str, Any], enabled_tools: list[str] | None, *, confirmed: bool
) -> None:
    """422 unless every service the tenant's tools serve is listed or confirmed."""
    if confirmed:
        return
    missing = _uncovered_services(config, enabled_tools)
    if missing:
        raise HTTPException(
            status_code=422,
            detail=(
                f"network_policy.services lacks {missing}, but the tenant has tools "
                "for them — the bot would refuse these services. Add them to "
                "services, or confirm that the network does not provide them "
                "(confirm_no_services=true)."
            ),
        )


def _normalize_policy(raw: dict[str, Any]) -> dict[str, Any]:
    """A validated policy with enum names lowercased and strings stripped."""
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key in _POLICY_ENUM_LISTS and isinstance(value, list):
            out[key] = list(dict.fromkeys(v.strip().lower() for v in value))
        elif key in _POLICY_NAME_LISTS and isinstance(value, list):
            out[key] = list(dict.fromkeys(v.strip() for v in value))
        elif key in _POLICY_TEXTS and isinstance(value, str):
            out[key] = value.strip() or None
        else:
            out[key] = value
    return out


def _as_dict(value: Any) -> dict[str, Any]:
    """A JSONB column value as a dict (a driver may hand back a str)."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


# ─── CRUD endpoints ──────────────────────────────────────


@router.get("")
async def list_tenants(
    is_active: bool | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: dict[str, Any] = _perm_r,
) -> dict[str, Any]:
    """List all tenants with optional active filter and pagination."""
    engine = await _get_engine()

    conditions = ["1=1"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    if is_active is not None:
        conditions.append("is_active = :is_active")
        params["is_active"] = is_active

    where_clause = " AND ".join(conditions)

    async with engine.begin() as conn:
        count_result = await conn.execute(
            text(f"SELECT COUNT(*) FROM tenants WHERE {where_clause}"),
            params,
        )
        total = count_result.scalar() or 0

        result = await conn.execute(
            text(f"""
                SELECT id, slug, name, network_id, agent_name, greeting,
                       enabled_tools, extensions, prompt_suffix, config,
                       working_hours, is_active, created_at, updated_at
                FROM tenants
                WHERE {where_clause}
                ORDER BY created_at
                LIMIT :limit OFFSET :offset
            """),
            params,
        )
        tenants = [dict(row._mapping) for row in result]

    return {"tenants": tenants, "total": total}


@router.get("/{tenant_id}")
async def get_tenant(tenant_id: UUID, _: dict[str, Any] = _perm_r) -> dict[str, Any]:
    """Get a single tenant by ID."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT id, slug, name, network_id, agent_name, greeting,
                       enabled_tools, extensions, prompt_suffix, config,
                       working_hours, is_active, created_at, updated_at
                FROM tenants WHERE id = :id
            """),
            {"id": str(tenant_id)},
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Tenant not found")

    return {"tenant": dict(row._mapping)}


@router.post("", status_code=201)
async def create_tenant(request: TenantCreate, _: dict[str, Any] = _perm_w) -> dict[str, Any]:
    """Create a new tenant."""
    if request.enabled_tools:
        _validate_tools(request.enabled_tools)
    if request.extensions:
        _validate_extensions(request.extensions)
    try:
        _validate_working_hours(request.working_hours)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"working_hours: {exc}") from exc
    _raise_policy_errors(_config_policy_errors(request.config))
    _check_service_coverage(
        request.config, request.enabled_tools, confirmed=request.confirm_no_services
    )

    engine = await _get_engine()

    try:
        async with engine.begin() as conn:
            if request.extensions:
                await _check_extension_uniqueness(conn, request.extensions)
            result = await conn.execute(
                text("""
                    INSERT INTO tenants
                        (slug, name, network_id, agent_name, greeting,
                         enabled_tools, extensions, prompt_suffix, config,
                         working_hours, is_active)
                    VALUES (:slug, :name, :network_id, :agent_name, :greeting,
                            CAST(:enabled_tools AS text[]), CAST(:extensions AS text[]),
                            :prompt_suffix, CAST(:config AS jsonb),
                            CAST(:working_hours AS jsonb), :is_active)
                    RETURNING id
                """),
                {
                    "slug": request.slug,
                    "name": request.name,
                    "network_id": request.network_id,
                    "agent_name": request.agent_name,
                    "greeting": request.greeting,
                    "enabled_tools": request.enabled_tools,
                    "extensions": request.extensions,
                    "prompt_suffix": request.prompt_suffix,
                    "config": json.dumps(request.config),
                    "working_hours": (
                        json.dumps(request.working_hours)
                        if request.working_hours is not None
                        else None
                    ),
                    "is_active": request.is_active,
                },
            )
            row = result.first()
            tenant_id = str(row.id) if row else None
    except Exception as exc:
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise HTTPException(
                status_code=409, detail=f"Tenant with slug '{request.slug}' already exists"
            ) from exc
        raise

    logger.info("Created tenant: %s (slug=%s)", tenant_id, request.slug)
    return {"message": "Tenant created", "id": tenant_id}


@router.patch("/{tenant_id}")
async def update_tenant(
    tenant_id: UUID, request: TenantUpdate, _: dict[str, Any] = _perm_w
) -> dict[str, Any]:
    """Update a tenant (partial update)."""
    if request.enabled_tools is not None:
        _validate_tools(request.enabled_tools)
    if request.extensions is not None:
        _validate_extensions(request.extensions)
    if "working_hours" in request.model_fields_set:
        try:
            _validate_working_hours(request.working_hours)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"working_hours: {exc}") from exc
    if request.config is not None:
        _raise_policy_errors(_config_policy_errors(request.config))

    engine = await _get_engine()

    # Build dynamic SET clause
    updates: list[str] = []
    params: dict[str, Any] = {"id": str(tenant_id)}

    for field_name in ("name", "network_id", "agent_name", "is_active"):
        value = getattr(request, field_name, None)
        if value is not None:
            updates.append(f"{field_name} = :{field_name}")
            params[field_name] = value

    # Nullable text fields: distinguish "set to None" from "not provided"
    # Since these use the same None default, we check model_fields_set
    provided = request.model_fields_set
    if "greeting" in provided:
        updates.append("greeting = :greeting")
        params["greeting"] = request.greeting
    if "prompt_suffix" in provided:
        updates.append("prompt_suffix = :prompt_suffix")
        params["prompt_suffix"] = request.prompt_suffix

    if request.enabled_tools is not None:
        updates.append("enabled_tools = CAST(:enabled_tools AS text[])")
        params["enabled_tools"] = request.enabled_tools

    if request.extensions is not None:
        updates.append("extensions = CAST(:extensions AS text[])")
        params["extensions"] = request.extensions

    if request.config is not None:
        updates.append("config = CAST(:config AS jsonb)")
        params["config"] = json.dumps(request.config)

    if "working_hours" in provided:
        updates.append("working_hours = CAST(:working_hours AS jsonb)")
        params["working_hours"] = (
            json.dumps(request.working_hours) if request.working_hours is not None else None
        )

    if not updates:
        return {"message": "No changes"}

    updates.append("updated_at = now()")
    set_clause = ", ".join(updates)

    async with engine.begin() as conn:
        if request.extensions is not None:
            await _check_extension_uniqueness(conn, request.extensions, str(tenant_id))
        # Nothing uncovered even with every tool → no need to read the row.
        if (
            request.config is not None
            and not request.confirm_no_services
            and _uncovered_services(request.config, None)
        ):
            enabled_tools = request.enabled_tools
            if enabled_tools is None:
                current = await conn.execute(
                    text("SELECT enabled_tools FROM tenants WHERE id = :id"),
                    {"id": str(tenant_id)},
                )
                current_row = current.first()
                if not current_row:
                    raise HTTPException(status_code=404, detail="Tenant not found")
                enabled_tools = list(current_row._mapping["enabled_tools"] or [])
            _check_service_coverage(
                request.config, enabled_tools, confirmed=request.confirm_no_services
            )
        result = await conn.execute(
            text(f"""
                UPDATE tenants
                SET {set_clause}
                WHERE id = :id
                RETURNING id
            """),
            params,
        )
        if not result.first():
            raise HTTPException(status_code=404, detail="Tenant not found")

    logger.info("Updated tenant %s", tenant_id)
    return {"message": "Tenant updated"}


@router.put("/{tenant_id}/network-settings")
async def update_network_settings(
    tenant_id: UUID, request: NetworkSettingsUpdate, _: dict[str, Any] = _perm_w
) -> dict[str, Any]:
    """Save the «Умови мережі» form.

    Only ``sales_enabled`` and ``network_policy`` change; every other
    ``config`` key (``store_api_url``, ``excluded_station_ids``,
    ``agent_provider_override`` …) is kept. ``network_policy`` is replaced
    whole, so no stale key of an old policy survives.
    """
    _raise_policy_errors(_policy_errors(request.network_policy))
    patch = {
        "sales_enabled": request.sales_enabled,
        "network_policy": _normalize_policy(request.network_policy),
    }

    engine = await _get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            text("SELECT config, enabled_tools FROM tenants WHERE id = :id FOR UPDATE"),
            {"id": str(tenant_id)},
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Tenant not found")
        merged = {**_as_dict(row._mapping["config"]), **patch}
        _check_service_coverage(
            merged,
            list(row._mapping["enabled_tools"] or []),
            confirmed=request.confirm_no_services,
        )
        await conn.execute(
            text("""
                UPDATE tenants
                SET config = CAST(:config AS jsonb), updated_at = now()
                WHERE id = :id
            """),
            {"id": str(tenant_id), "config": json.dumps(merged)},
        )

    logger.info(
        "Updated network settings of tenant %s (sales_enabled=%s)",
        tenant_id,
        request.sales_enabled,
    )
    return {"message": "Network settings updated", "config": merged}


@router.delete("/{tenant_id}")
async def delete_tenant(tenant_id: UUID, _: dict[str, Any] = _perm_d) -> dict[str, Any]:
    """Soft-delete a tenant (set is_active=false)."""
    engine = await _get_engine()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                UPDATE tenants
                SET is_active = false, updated_at = now()
                WHERE id = :id
                RETURNING id
            """),
            {"id": str(tenant_id)},
        )
        if not result.first():
            raise HTTPException(status_code=404, detail="Tenant not found")

    logger.info("Soft-deleted tenant %s", tenant_id)
    return {"message": "Tenant deactivated"}
