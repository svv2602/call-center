"""Admin API for network promotions (``promotions`` table, migration 063).

A promotion is data the admin enters by hand: the network, a title, the text
the bot speaks, a mandatory validity window and ``overrides`` — which standard
network conditions it beats. Owner decisions 2026-09-28.

The write path is strict and mirrors the table's CHECKs so the admin gets a
422 naming the field instead of a database error:
- ``valid_to`` is required and ``>= valid_from``;
- ``bot_text`` is non-blank and at most 600 characters;
- ``overrides`` is default-deny: only the four known keys, each with its own
  shape; anything else is rejected, never silently dropped;
- ``mention_brands`` / ``extended_warranty_brands`` are stored lowercase;
- ``tenant_id`` must name an existing network.

Every write bumps ``PROMOS_CACHE_REDIS_KEY`` so the in-process promotions
cache of every call processor reloads.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import text

from src.agent.network_policy import SERVICE_LABELS
from src.agent.prompt_manager import PROMOS_CACHE_REDIS_KEY
from src.api.auth import require_permission
from src.api.database import get_engine as _get_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/promotions", tags=["promotions"])

_perm_r = Depends(require_permission("promotions:read"))
_perm_w = Depends(require_permission("promotions:write"))

KYIV_TZ = ZoneInfo("Europe/Kyiv")
BOT_TEXT_MAX = 600
#: A draft made from an article leaves room for the admin to add conditions.
DRAFT_BOT_TEXT_MAX = 500
STATUSES = ("active", "upcoming", "expired", "all")
OVERRIDE_KEYS = frozenset(
    {"free_delivery", "extended_warranty_brands", "discount", "partner_service"}
)

_COLUMNS = (
    "id, tenant_id, title, bot_text, valid_from, valid_to, overrides, "
    "mention_brands, active, source_article_id, created_by, created_at, updated_at"
)


async def _get_redis() -> Any:
    from src.core.redis_client import get_redis

    return await get_redis()


def _today_kyiv() -> date:
    return datetime.now(KYIV_TZ).date()


# ─── Validation ───────────────────────────────────────────


def _lower_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        msg = f"{field} must be a list of strings"
        raise ValueError(msg)
    out: list[str] = []
    for v in value:
        item = v.strip().lower()
        if item and item not in out:
            out.append(item)
    return out


def validate_overrides(value: Any) -> dict[str, Any]:
    """Default-deny check of ``overrides``: every key and every value shape."""
    if not isinstance(value, dict):
        msg = "overrides must be an object"
        raise ValueError(msg)
    unknown = sorted(set(value) - OVERRIDE_KEYS)
    if unknown:
        msg = f"unknown overrides key(s): {', '.join(unknown)}; allowed: {', '.join(sorted(OVERRIDE_KEYS))}"
        raise ValueError(msg)
    out: dict[str, Any] = {}
    for key in ("free_delivery", "discount"):
        if key in value:
            if not isinstance(value[key], bool):
                msg = f"overrides.{key} must be a boolean"
                raise ValueError(msg)
            out[key] = value[key]
    if "extended_warranty_brands" in value:
        out["extended_warranty_brands"] = _lower_list(
            value["extended_warranty_brands"], "overrides.extended_warranty_brands"
        )
    if "partner_service" in value:
        ps = value["partner_service"]
        if not isinstance(ps, dict) or set(ps) != {"service", "network_label"}:
            msg = "overrides.partner_service must be {service, network_label}"
            raise ValueError(msg)
        if ps["service"] not in SERVICE_LABELS:
            msg = (
                f"overrides.partner_service.service '{ps['service']}' is not one of: "
                f"{', '.join(sorted(SERVICE_LABELS))}"
            )
            raise ValueError(msg)
        label = ps["network_label"]
        if not isinstance(label, str) or not label.strip():
            msg = "overrides.partner_service.network_label must be a non-empty string"
            raise ValueError(msg)
        out["partner_service"] = {"service": ps["service"], "network_label": label.strip()}
    return out


def _check_bot_text(value: str) -> str:
    value = value.strip()
    if not value:
        msg = "bot_text must not be blank"
        raise ValueError(msg)
    if len(value) > BOT_TEXT_MAX:
        msg = f"bot_text is {len(value)} characters, max {BOT_TEXT_MAX}"
        raise ValueError(msg)
    return value


def _check_title(value: str) -> str:
    value = value.strip()
    if not value:
        msg = "title must not be blank"
        raise ValueError(msg)
    return value


class _PromotionFields(BaseModel):
    """Shared validators; every field is optional here."""

    @field_validator("title", check_fields=False)
    @classmethod
    def _title(cls, v: str | None) -> str | None:
        return None if v is None else _check_title(v)

    @field_validator("bot_text", check_fields=False)
    @classmethod
    def _bot_text(cls, v: str | None) -> str | None:
        return None if v is None else _check_bot_text(v)

    @field_validator("overrides", check_fields=False, mode="before")
    @classmethod
    def _overrides(cls, v: Any) -> Any:
        return None if v is None else validate_overrides(v)

    @field_validator("mention_brands", check_fields=False, mode="before")
    @classmethod
    def _brands(cls, v: Any) -> Any:
        return None if v is None else _lower_list(v, "mention_brands")


class PromotionCreate(_PromotionFields):
    tenant_id: UUID
    title: str = Field(max_length=300)
    bot_text: str
    valid_from: date
    valid_to: date = Field(description="Required: a promotion without an end date is a 422.")
    overrides: dict[str, Any] = Field(default_factory=dict)
    mention_brands: list[str] = Field(default_factory=list)
    active: bool = True

    @model_validator(mode="after")
    def _range(self) -> PromotionCreate:
        if self.valid_to < self.valid_from:
            msg = "valid_to must be on or after valid_from"
            raise ValueError(msg)
        return self


class PromotionUpdate(_PromotionFields):
    tenant_id: UUID | None = None
    title: str | None = Field(default=None, max_length=300)
    bot_text: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    overrides: dict[str, Any] | None = None
    mention_brands: list[str] | None = None
    active: bool | None = None

    @model_validator(mode="after")
    def _range(self) -> PromotionUpdate:
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            msg = "valid_to must be on or after valid_from"
            raise ValueError(msg)
        return self


class PromotionFromArticle(_PromotionFields):
    """Body of «створити з статті».

    Dates are not in the article and are never guessed: both are required, a
    body without them is a 422 naming the missing field. ``title``/``bot_text``
    default to the article; ``tenant_id`` defaults to the article's network and
    is required when the article is shared (``tenant_id IS NULL``).
    """

    valid_from: date = Field(description="Required: the article carries no dates.")
    valid_to: date = Field(description="Required: the article carries no dates.")
    tenant_id: UUID | None = None
    title: str | None = Field(default=None, max_length=300)
    bot_text: str | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)
    mention_brands: list[str] = Field(default_factory=list)
    active: bool = True

    @model_validator(mode="after")
    def _range(self) -> PromotionFromArticle:
        if self.valid_to < self.valid_from:
            msg = "valid_to must be on or after valid_from"
            raise ValueError(msg)
        return self


# ─── Helpers ──────────────────────────────────────────────

_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_MD_MARKUP = re.compile(r"^\s{0,3}(?:#{1,6}\s*|[-*+]\s+|>\s*)|[*_`]{1,3}", re.MULTILINE)


def draft_bot_text(content: str, limit: int = DRAFT_BOT_TEXT_MAX) -> str:
    """Article text squeezed into a speakable draft: no links, no markup, ≤ limit."""
    s = _MD_LINK.sub(r"\1", content or "")
    s = _URL.sub("", s)
    s = _MD_MARKUP.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) <= limit:
        return s
    cut = s[: limit - 1]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,.;:—-") + "…"


def _serialize(row: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in dict(row._mapping).items():
        if isinstance(value, UUID):
            out[key] = str(value)
        elif isinstance(value, (date, datetime)):
            out[key] = value.isoformat()
        elif key == "overrides" and isinstance(value, str):
            out[key] = json.loads(value)
        elif key == "mention_brands":
            out[key] = list(value or [])
        else:
            out[key] = value
    return out


async def _invalidate_cache() -> None:
    """Bump the promotions cache stamp read by ``fetch_tenant_promotions``."""
    try:
        redis = await _get_redis()
        await redis.set(PROMOS_CACHE_REDIS_KEY, str(time.time()))
    except Exception:
        logger.warning("Could not invalidate promotions cache", exc_info=True)


async def _ensure_tenant(conn: Any, tenant_id: UUID | str) -> None:
    result = await conn.execute(
        text("SELECT id FROM tenants WHERE id = CAST(:id AS uuid)"), {"id": str(tenant_id)}
    )
    if result.first() is None:
        raise HTTPException(status_code=422, detail=f"tenant_id {tenant_id} does not exist")


def _conflict(exc: Exception, title: str | None) -> HTTPException | None:
    msg = str(exc).lower()
    if "unique" in msg or "duplicate" in msg:
        return HTTPException(
            status_code=409, detail=f"Promotion '{title}' already exists for this network"
        )
    return None


async def _insert(conn: Any, values: dict[str, Any]) -> dict[str, Any]:
    result = await conn.execute(
        text(f"""
            INSERT INTO promotions
                (tenant_id, title, bot_text, valid_from, valid_to, overrides,
                 mention_brands, active, source_article_id, created_by)
            VALUES
                (CAST(:tenant_id AS uuid), :title, :bot_text, :valid_from, :valid_to,
                 CAST(:overrides AS jsonb), :mention_brands, :active,
                 CAST(:source_article_id AS uuid), CAST(:created_by AS uuid))
            RETURNING {_COLUMNS}
        """),
        values,
    )
    row = result.first()
    if row is None:
        msg = "Expected row from INSERT RETURNING"
        raise RuntimeError(msg)
    return _serialize(row)


def _creator(user: dict[str, Any]) -> str | None:
    # Env-configured admins log in without a DB row and carry no user_id.
    user_id = user.get("user_id")
    return str(user_id) if user_id else None


# ─── Endpoints ────────────────────────────────────────────


@router.get("")
async def list_promotions(
    tenant_id: UUID | None = Query(None),  # noqa: B008
    status: str = Query("all", pattern=f"^({'|'.join(STATUSES)})$"),
    _: dict[str, Any] = _perm_r,
) -> dict[str, Any]:
    """List promotions; ``status`` is computed from Kyiv dates and ``active``.

    active — enabled and today within [valid_from, valid_to];
    upcoming — enabled and starts after today;
    expired — ended before today (enabled or not);
    all — everything, disabled ones included.
    """
    conditions: list[str] = []
    params: dict[str, Any] = {}
    if tenant_id is not None:
        conditions.append("tenant_id = CAST(:tenant_id AS uuid)")
        params["tenant_id"] = str(tenant_id)
    if status != "all":
        params["today"] = _today_kyiv()
        if status == "active":
            conditions.append("active AND valid_from <= :today AND valid_to >= :today")
        elif status == "upcoming":
            conditions.append("active AND valid_from > :today")
        else:
            conditions.append("valid_to < :today")
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    engine = await _get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            text(f"SELECT {_COLUMNS} FROM promotions {where} ORDER BY valid_to, title"),
            params,
        )
        items = [_serialize(r) for r in result]
    return {"items": items, "total": len(items)}


@router.post("/from-article/{article_id}")
async def create_from_article(
    article_id: UUID,
    request: PromotionFromArticle,
    user: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Create a promotion from a knowledge-base article (the admin supplies the dates)."""
    engine = await _get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT id, title, content, promo_summary, tenant_id
                FROM knowledge_articles WHERE id = CAST(:id AS uuid)
            """),
            {"id": str(article_id)},
        )
        article = result.first()
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")

    tenant_id = request.tenant_id or article.tenant_id
    if tenant_id is None:
        raise HTTPException(
            status_code=422,
            detail="The article is shared by all networks: pass tenant_id for the promotion",
        )
    title = request.title or _check_title(article.title or "")[:300]
    bot_text = request.bot_text or draft_bot_text(article.promo_summary or article.content or "")
    if not bot_text:
        raise HTTPException(status_code=422, detail="The article has no text: pass bot_text")
    bot_text = _check_bot_text(bot_text)

    values = {
        "tenant_id": str(tenant_id),
        "title": title,
        "bot_text": bot_text,
        "valid_from": request.valid_from,
        "valid_to": request.valid_to,
        "overrides": json.dumps(request.overrides, ensure_ascii=False),
        "mention_brands": request.mention_brands,
        "active": request.active,
        "source_article_id": str(article_id),
        "created_by": _creator(user),
    }
    try:
        async with engine.begin() as conn:
            await _ensure_tenant(conn, tenant_id)
            promotion = await _insert(conn, values)
    except HTTPException:
        raise
    except Exception as exc:
        if (conflict := _conflict(exc, title)) is not None:
            raise conflict from exc
        raise
    await _invalidate_cache()
    return {"promotion": promotion}


@router.get("/{promotion_id}")
async def get_promotion(promotion_id: UUID, _: dict[str, Any] = _perm_r) -> dict[str, Any]:
    engine = await _get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            text(f"SELECT {_COLUMNS} FROM promotions WHERE id = CAST(:id AS uuid)"),
            {"id": str(promotion_id)},
        )
        row = result.first()
    if row is None:
        raise HTTPException(status_code=404, detail="Promotion not found")
    return {"promotion": _serialize(row)}


@router.post("")
async def create_promotion(
    request: PromotionCreate, user: dict[str, Any] = _perm_w
) -> dict[str, Any]:
    values = {
        "tenant_id": str(request.tenant_id),
        "title": request.title,
        "bot_text": request.bot_text,
        "valid_from": request.valid_from,
        "valid_to": request.valid_to,
        "overrides": json.dumps(request.overrides, ensure_ascii=False),
        "mention_brands": request.mention_brands,
        "active": request.active,
        "source_article_id": None,
        "created_by": _creator(user),
    }
    engine = await _get_engine()
    try:
        async with engine.begin() as conn:
            await _ensure_tenant(conn, request.tenant_id)
            promotion = await _insert(conn, values)
    except HTTPException:
        raise
    except Exception as exc:
        if (conflict := _conflict(exc, request.title)) is not None:
            raise conflict from exc
        raise
    await _invalidate_cache()
    return {"promotion": promotion}


@router.patch("/{promotion_id}")
async def update_promotion(
    promotion_id: UUID, request: PromotionUpdate, _: dict[str, Any] = _perm_w
) -> dict[str, Any]:
    patch = request.model_dump(exclude_unset=True)
    # An explicit null on a NOT NULL column is a 422, not a database error.
    nulls = sorted(k for k, v in patch.items() if v is None)
    if nulls:
        raise HTTPException(status_code=422, detail=f"Fields cannot be null: {', '.join(nulls)}")
    if not patch:
        raise HTTPException(status_code=400, detail="No fields to update")

    engine = await _get_engine()
    try:
        async with engine.begin() as conn:
            current = (
                await conn.execute(
                    text(
                        "SELECT valid_from, valid_to FROM promotions WHERE id = CAST(:id AS uuid)"
                    ),
                    {"id": str(promotion_id)},
                )
            ).first()
            if current is None:
                raise HTTPException(status_code=404, detail="Promotion not found")
            valid_from = patch.get("valid_from", current.valid_from)
            valid_to = patch.get("valid_to", current.valid_to)
            if valid_to < valid_from:
                raise HTTPException(
                    status_code=422, detail="valid_to must be on or after valid_from"
                )
            if "tenant_id" in patch:
                await _ensure_tenant(conn, patch["tenant_id"])

            sets: list[str] = []
            params: dict[str, Any] = {"id": str(promotion_id)}
            for key, value in patch.items():
                if key == "tenant_id":
                    sets.append("tenant_id = CAST(:tenant_id AS uuid)")
                    params[key] = str(value)
                elif key == "overrides":
                    sets.append("overrides = CAST(:overrides AS jsonb)")
                    params[key] = json.dumps(value, ensure_ascii=False)
                else:
                    sets.append(f"{key} = :{key}")
                    params[key] = value
            sets.append("updated_at = now()")
            result = await conn.execute(
                text(f"""
                    UPDATE promotions SET {", ".join(sets)}
                    WHERE id = CAST(:id AS uuid)
                    RETURNING {_COLUMNS}
                """),
                params,
            )
            row = result.first()
    except HTTPException:
        raise
    except Exception as exc:
        if (conflict := _conflict(exc, patch.get("title"))) is not None:
            raise conflict from exc
        raise
    if row is None:
        raise HTTPException(status_code=404, detail="Promotion not found")
    await _invalidate_cache()
    return {"promotion": _serialize(row)}


@router.delete("/{promotion_id}")
async def delete_promotion(promotion_id: UUID, _: dict[str, Any] = _perm_w) -> dict[str, Any]:
    engine = await _get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            text("DELETE FROM promotions WHERE id = CAST(:id AS uuid) RETURNING id"),
            {"id": str(promotion_id)},
        )
        row = result.first()
    if row is None:
        raise HTTPException(status_code=404, detail="Promotion not found")
    await _invalidate_cache()
    return {"message": "Promotion deleted", "id": str(promotion_id)}
