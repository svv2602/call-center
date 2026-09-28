"""Network promotions from the ``promotions`` table (migration 063).

Used only when the network has ``sales_enabled``; otherwise the prompt keeps
the old free-text path (``prompt_manager.fetch_tenant_promotions`` over
``knowledge_articles``) byte for byte.

A promotion reaches the prompt only while it is live: ``active``, belongs to
the caller's network and ``valid_from <= today <= valid_to`` with ``today``
taken in Kyiv. The SQL filters, and every row is re-checked in Python
(default-deny): a row the query should not have returned — another network,
expired, not started, inactive — is dropped, not trusted.

The in-process cache is keyed by day, so an expired promotion never outlives
its last day, and is dropped on the same Redis signal as the old path
(``PROMOS_CACHE_REDIS_KEY``) when an admin edits promotions.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy import text

from src.agent.prompt_manager import PROMOS_CACHE_REDIS_KEY

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

KYIV_TZ = ZoneInfo("Europe/Kyiv")

# (tenant_id, iso day) → promotions live on that day
_cache: dict[tuple[str, str], list[ActivePromotion]] = {}
_cache_ts: float = 0.0


@dataclass(frozen=True)
class ActivePromotion:
    """One promotion live today for one network."""

    title: str
    bot_text: str
    valid_to: date
    overrides: dict[str, Any] = field(default_factory=dict)
    mention_brands: tuple[str, ...] = ()


@dataclass(frozen=True)
class PromoOverrides:
    """Which standard network conditions today's promotions beat.

    For the network-claim guard: a claim covered here is not a false one.

    ``free_delivery_brand_scopes`` holds one scope per live free-delivery
    promotion: its ``mention_brands`` as lower-case names, an empty set
    meaning every brand. The guard reads only the scopes; ``free_delivery``
    (true when there is at least one scope) is kept for compatibility.
    """

    free_delivery: bool = False
    free_delivery_brand_scopes: tuple[frozenset[str], ...] = ()
    discount: bool = False
    extended_warranty_brands: frozenset[str] = frozenset()
    partner_services: tuple[dict[str, str], ...] = ()


def kyiv_today() -> date:
    return datetime.now(KYIV_TZ).date()


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _is_live(row: dict[str, Any], tenant_id: str, today: date) -> bool:
    """Default-deny: every condition must hold explicitly."""
    if row.get("active") is not True:
        return False
    if str(row.get("tenant_id") or "") != tenant_id:
        return False
    valid_from = _as_date(row.get("valid_from"))
    valid_to = _as_date(row.get("valid_to"))
    if valid_from is None or valid_to is None:
        return False
    if not valid_from <= today <= valid_to:
        return False
    return bool((row.get("title") or "").strip()) and bool((row.get("bot_text") or "").strip())


def _to_promotion(row: dict[str, Any]) -> ActivePromotion:
    overrides = row.get("overrides")
    brands = row.get("mention_brands") or ()
    return ActivePromotion(
        title=row["title"].strip(),
        bot_text=row["bot_text"].strip(),
        valid_to=_as_date(row["valid_to"]),  # type: ignore[arg-type]  # checked by _is_live
        overrides=dict(overrides) if isinstance(overrides, dict) else {},
        mention_brands=tuple(b.strip() for b in brands if isinstance(b, str) and b.strip()),
    )


async def load_active_promotions(
    engine: AsyncEngine,
    tenant_id: str,
    *,
    redis: Any = None,
    today: date | None = None,
) -> list[ActivePromotion]:
    """Promotions of ``tenant_id`` live on ``today`` (Kyiv), ending soonest first."""
    global _cache_ts

    if not tenant_id:
        return []
    day = today or kyiv_today()

    if redis is not None:
        try:
            raw = await redis.get(PROMOS_CACHE_REDIS_KEY)
            remote_ts = float(raw) if raw else 0.0
        except Exception:
            remote_ts = 0.0
        if remote_ts > _cache_ts and _cache:
            _cache.clear()

    key = (tenant_id, day.isoformat())
    if key in _cache:
        return _cache[key]

    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    SELECT tenant_id, title, bot_text, valid_from, valid_to,
                           overrides, mention_brands, active
                    FROM promotions
                    WHERE tenant_id = CAST(:tid AS uuid)
                      AND active = true
                      AND valid_from <= :today
                      AND valid_to >= :today
                    ORDER BY valid_to, title
                """),
                {"tid": tenant_id, "today": day},
            )
            rows = [dict(r._mapping) for r in result]
    except Exception:
        logger.warning("Failed to load network promotions", exc_info=True)
        return []

    promos = sorted(
        (_to_promotion(r) for r in rows if _is_live(r, tenant_id, day)),
        key=lambda p: (p.valid_to, p.title),
    )
    # Yesterday's entries can only hold promotions that may have expired.
    for stale in [k for k in _cache if k[1] != key[1]]:
        del _cache[stale]
    _cache[key] = promos
    _cache_ts = time.time()
    return promos


_RULES = (
    "Акція главніша за стандартні умови мережі, але лише в межах своїх умов "
    "(бренди, розміри, спосіб покупки) і до дати завершення. "
    "Згадуй акцію, лише коли клієнт питає про акції або обрав бренд з її списку; "
    "не більше однієї акції у відповіді. "
    "Умови акції не вигадуй — тільки з тексту."
)


def format_promotions_block(promos: list[ActivePromotion]) -> str | None:
    """Ukrainian prompt block of today's network promotions, or ``None``."""
    if not promos:
        return None
    parts = ["\n## Актуальні акції мережі", _RULES]
    for p in promos:
        lines = [f"\n### {p.title}", p.bot_text]
        if p.mention_brands:
            lines.append("Бренди акції: " + ", ".join(p.mention_brands) + ".")
        lines.append(f"Діє до {p.valid_to.strftime('%d.%m.%Y')} включно.")
        parts.append("\n".join(lines))
    return "\n".join(parts)


def promo_overrides(promos: list[ActivePromotion]) -> PromoOverrides:
    """Union of what today's promotions override — default-deny per key."""
    scopes: list[frozenset[str]] = []
    discount = False
    brands: set[str] = set()
    services: list[dict[str, str]] = []
    for p in promos:
        o = p.overrides
        if o.get("free_delivery") is True:
            # A brand promotion covers its own brands only; no brands = all.
            brands_of = (b.strip().lower() for b in p.mention_brands if isinstance(b, str))
            scopes.append(frozenset(b for b in brands_of if b))
        discount = discount or o.get("discount") is True
        raw_brands = o.get("extended_warranty_brands")
        if isinstance(raw_brands, list):
            brands.update(b.strip() for b in raw_brands if isinstance(b, str) and b.strip())
        svc = o.get("partner_service")
        if isinstance(svc, dict):
            service = svc.get("service")
            label = svc.get("network_label")
            if isinstance(service, str) and service.strip():
                entry = {"service": service.strip()}
                if isinstance(label, str) and label.strip():
                    entry["network_label"] = label.strip()
                services.append(entry)
    return PromoOverrides(
        free_delivery=bool(scopes),
        free_delivery_brand_scopes=tuple(scopes),
        discount=discount,
        extended_warranty_brands=frozenset(brands),
        partner_services=tuple(services),
    )
