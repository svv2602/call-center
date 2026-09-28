"""Per-network sales/consultation policy — structured data, not prompt text.

Both networks (Твоя Шина, Про Колесо) share one system prompt, so any
network-specific condition written as prose leaks from one network into the
other. The conditions live in ``tenants.config`` instead:

- ``config["sales_enabled"]`` — master switch (default ``False``). While it is
  off the network block is not rendered and the assembled prompt is
  byte-for-byte the same as without this module ("fitting-only" scope stays).
- ``config["network_policy"]`` — one sub-object with delivery, payment,
  services, warranty and brand priority. Being one object, a JSONB
  ``config || patch`` merge replaces it whole — no stale keys survive.

Parsing is default-deny: a missing or malformed field never turns into a
promise. Unknown delivery mode → ``unknown`` ("уточнить менеджер", never
"безкоштовна"); unknown enum members are dropped; garbage types fall back to
the default with a ``logger.warning`` — a call never fails on bad config.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ── Enums (the whole set — renderers and guards iterate these) ──────────

DELIVERY_MODES: tuple[str, ...] = ("free", "carrier_tariff", "unknown")

SERVICE_LABELS: dict[str, str] = {
    "fitting": "шиномонтаж",
    "storage": "зберігання шин",
}

PAYMENT_LABELS: dict[str, str] = {
    "cod": "накладений платіж",
    "card": "оплата карткою",
    "prepay": "передоплата",
    "installments": "оплата частинами",
}

BANK_LABELS: dict[str, str] = {
    "monobank": "monobank",
    "privatbank": "ПриватБанк",
}

ORDER_FINISH_MODES: tuple[str, ...] = ("request_manager_callback",)

RECOMMEND_COUNT_MIN = 2
RECOMMEND_COUNT_MAX = 3


@dataclass(frozen=True)
class NetworkPolicy:
    """What a network offers. Defaults promise nothing.

    ``configured`` tells "the owner wrote this network's policy" (a dict under
    ``config["network_policy"]``) from "nothing was written" (key missing or
    garbage). Only a configured policy's empty ``services`` means "not
    provided"; an unconfigured one's means "unknown".
    """

    sales_enabled: bool = False
    configured: bool = False
    services: frozenset[str] = frozenset()
    delivery_mode: str = "unknown"
    delivery_carriers: tuple[str, ...] = ()
    delivery_eta_text: str | None = None
    pickup_available: bool = False
    payment_methods: tuple[str, ...] = ()
    cod_fee_text: str | None = None
    installment_banks: tuple[str, ...] = ()
    extended_warranty_brands: frozenset[str] = frozenset()
    brand_priority: tuple[str, ...] = ()
    recommend_count: int = RECOMMEND_COUNT_MAX
    order_finish: str = "request_manager_callback"

    @classmethod
    def from_tenant_config(cls, cfg: Any) -> NetworkPolicy:
        """Build a policy from ``tenants.config`` (a dict). Never raises."""
        if cfg is None:
            return cls()
        if not isinstance(cfg, dict):
            logger.warning("network_policy: tenant config is %s, not dict", type(cfg).__name__)
            return cls()

        sales_enabled = _parse_bool(cfg, "sales_enabled")

        raw = cfg.get("network_policy")
        if raw is None:
            return cls(sales_enabled=sales_enabled)
        if not isinstance(raw, dict):
            logger.warning(
                "network_policy: config.network_policy is %s, not dict", type(raw).__name__
            )
            return cls(sales_enabled=sales_enabled)

        delivery_mode = raw.get("delivery_mode", "unknown")
        if delivery_mode not in DELIVERY_MODES:
            logger.warning("network_policy: unknown delivery_mode %r → unknown", delivery_mode)
            delivery_mode = "unknown"

        order_finish = raw.get("order_finish", ORDER_FINISH_MODES[0])
        if order_finish not in ORDER_FINISH_MODES:
            logger.warning("network_policy: unknown order_finish %r → default", order_finish)
            order_finish = ORDER_FINISH_MODES[0]

        return cls(
            sales_enabled=sales_enabled,
            configured=True,
            services=frozenset(_parse_names(raw, "services", allowed=SERVICE_LABELS)),
            delivery_mode=delivery_mode,
            delivery_carriers=_parse_names(raw, "delivery_carriers", lower=False),
            delivery_eta_text=_parse_text(raw, "delivery_eta_text"),
            pickup_available=_parse_bool(raw, "pickup_available"),
            payment_methods=_parse_names(raw, "payment_methods", allowed=PAYMENT_LABELS),
            cod_fee_text=_parse_text(raw, "cod_fee_text"),
            installment_banks=_parse_names(raw, "installment_banks", allowed=BANK_LABELS),
            extended_warranty_brands=frozenset(_parse_names(raw, "extended_warranty_brands")),
            brand_priority=_parse_names(raw, "brand_priority"),
            recommend_count=_parse_recommend_count(raw),
            order_finish=order_finish,
        )


# ── Parsing helpers ─────────────────────────────────────────────────────


def _parse_bool(src: dict[str, Any], key: str) -> bool:
    """Only a real JSON ``true`` enables a flag; anything else is ``False``."""
    value = src.get(key, False)
    if isinstance(value, bool):
        return value
    logger.warning("network_policy: %s=%r is not bool → false", key, value)
    return False


def _parse_text(src: dict[str, Any], key: str) -> str | None:
    value = src.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        logger.warning("network_policy: %s=%r is not a string → ignored", key, value)
        return None
    return value.strip() or None


def _parse_names(
    src: dict[str, Any],
    key: str,
    *,
    allowed: dict[str, str] | None = None,
    lower: bool = True,
) -> tuple[str, ...]:
    """A list of non-empty strings, order kept, duplicates and unknowns dropped."""
    value = src.get(key)
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        logger.warning("network_policy: %s=%r is not a list → empty", key, value)
        return ()
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            logger.warning("network_policy: %s item %r is not a non-empty string", key, item)
            continue
        name = item.strip().lower() if lower else item.strip()
        if allowed is not None and name not in allowed:
            logger.warning("network_policy: %s item %r is unknown → dropped", key, name)
            continue
        if name not in out:
            out.append(name)
    return tuple(out)


def _parse_recommend_count(src: dict[str, Any]) -> int:
    value = src.get("recommend_count", RECOMMEND_COUNT_MAX)
    if isinstance(value, bool) or not isinstance(value, int):
        logger.warning("network_policy: recommend_count=%r is not int → default", value)
        return RECOMMEND_COUNT_MAX
    return max(RECOMMEND_COUNT_MIN, min(RECOMMEND_COUNT_MAX, value))


# ── Rendering ───────────────────────────────────────────────────────────


def _brand_label(name: str) -> str:
    return name[:1].upper() + name[1:]


def render_network_block(policy: NetworkPolicy | None) -> str | None:
    """Render the «Умови мережі» prompt section, or ``None`` when sales are off.

    Every value comes from the policy; nothing here is an example number that
    could leak into an answer. One line per item, no empty lines for empty
    fields.
    """
    if policy is None or not policy.sales_enabled:
        return None

    lines: list[str] = [
        "\n## Умови мережі",
        "Називай клієнту тільки умови з цього списку. Чого тут немає — «це уточнить менеджер».",
    ]

    if policy.order_finish == "request_manager_callback":
        lines.append(
            "- Замовлення: ти оформлюєш заявку, менеджер передзвонить і підтвердить її. "
            "Не кажи, що замовлення підтверджено."
        )
    lines.append("- Ціну називай за одну шину; можна замовити й окремі шини, не лише комплект.")

    carriers = f" ({', '.join(policy.delivery_carriers)})" if policy.delivery_carriers else ""
    if policy.delivery_mode == "free":
        lines.append(f"- Доставка: безкоштовна{carriers}.")
    elif policy.delivery_mode == "carrier_tariff":
        lines.append(
            f"- Доставка: за тарифами перевізника{carriers}. Вартість доставки не називай."
        )
    else:
        lines.append("- Доставка: умови й вартість не називай — це уточнить менеджер.")
    if policy.delivery_eta_text:
        lines.append(f"- Термін доставки: {policy.delivery_eta_text}.")

    if policy.pickup_available:
        lines.append("- Самовивіз: є; адреси називай тільки з get_pickup_points.")

    if policy.payment_methods:
        methods: list[str] = []
        for method in policy.payment_methods:
            label = PAYMENT_LABELS[method]
            if method == "cod" and policy.cod_fee_text:
                label += f" (комісія {policy.cod_fee_text})"
            elif method == "installments" and policy.installment_banks:
                label += f" ({', '.join(BANK_LABELS[b] for b in policy.installment_banks)})"
            methods.append(label)
        lines.append(f"- Оплата: {'; '.join(methods)}.")
    else:
        lines.append("- Оплата: способи не називай — це уточнить менеджер.")

    offered = [SERVICE_LABELS[s] for s in SERVICE_LABELS if s in policy.services]
    missing = [SERVICE_LABELS[s] for s in SERVICE_LABELS if s not in policy.services]
    if offered:
        lines.append(f"- Послуги мережі: {', '.join(offered)}.")
    if missing:
        lines.append(
            f"- Не надаємо: {', '.join(missing)}. Інші мережі не згадуй і туди не направляй."
        )

    if policy.extended_warranty_brands:
        brands = ", ".join(_brand_label(b) for b in sorted(policy.extended_warranty_brands))
        lines.append(f"- Розширена гарантія: тільки на шини {brands}.")
    else:
        lines.append("- Розширеної гарантії мережа не надає.")

    if policy.brand_priority:
        brands = ", ".join(_brand_label(b) for b in policy.brand_priority)
        lines.append(
            f"- Підбір: пропонуй не більше {policy.recommend_count} варіантів; "
            f"першими — бренди {brands}, якщо вони є в наявності."
        )
    else:
        lines.append(f"- Підбір: пропонуй не більше {policy.recommend_count} варіантів.")

    return "\n".join(lines)
