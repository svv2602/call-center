"""Configure tenant-specific services and tool availability.

ProKoleso: no fitting services (шиномонтаж) — 5 fitting tools removed,
           prompt_suffix instructs agent to decline fitting requests.
Твоя Шина (tvoya-shina): all tools enabled, display name updated.

Both: ``config`` gets ``sales_enabled`` + ``network_policy`` (delivery,
payment, services, warranty, brand priority — owner decisions 2026-09-28),
MERGED into the existing JSONB so ``store_api_url``, ``excluded_station_ids``,
``agent_provider_override`` etc. survive. ``sales_enabled`` stays ``false``
until the order/consultation flow is accepted — the «Умови мережі» prompt
block is not rendered while it is off (see ``src/agent/network_policy.py``).

Usage: python -m scripts.configure_tenants
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ProKoleso: all tools EXCEPT the 5 fitting-related ones
PROKOLESO_ENABLED_TOOLS = [
    "get_vehicle_tire_sizes",
    "search_tires",
    "check_availability",
    "transfer_to_operator",
    "get_order_status",
    "create_order_draft",
    "update_order_delivery",
    "confirm_order",
    "get_pickup_points",
    "search_knowledge_base",
]

PROKOLESO_PROMPT_SUFFIX = (
    "Мережа «Про Колесо» не надає послуги шиномонтажу, встановлення шин та балансування.\n"
    "Якщо клієнт запитує про монтаж або будь-які послуги шиномонтажу — одразу повідом:\n"
    "«На жаль, ми не надаємо послуги шиномонтажу.»\n"
    "Мережа «Про Колесо» також не надає послуги зберігання шин.\n"
    "Якщо клієнт запитує про зберігання шин — повідом:\n"
    "«На жаль, ми не надаємо послуги зберігання шин.»\n"
    "Не згадуй інші мережі та не пропонуй альтернативи."
)

# Conditions shared by both networks (owner decisions 2026-09-28).
_COMMON_NETWORK_POLICY: dict[str, Any] = {
    "delivery_carriers": ["Нова Пошта"],
    "delivery_eta_text": "зазвичай 1–3 дні, точніше — за ТТН",
    "pickup_available": True,
    "payment_methods": ["cod", "card", "prepay", "installments"],
    "cod_fee_text": "2 % + 20 грн, тариф перевізника, може змінюватися",
    "installment_banks": ["monobank", "privatbank"],
    "brand_priority": ["bridgestone", "firestone", "laufenn"],
    "recommend_count": 3,
    "order_finish": "request_manager_callback",
}

# ``config || patch`` replaces top-level keys only: ``network_policy`` is
# written whole, every other existing config key is kept.
TVOYA_SHINA_CONFIG_PATCH: dict[str, Any] = {
    "sales_enabled": False,
    "network_policy": {
        **_COMMON_NETWORK_POLICY,
        "delivery_mode": "free",
        "services": ["fitting", "storage"],
        "extended_warranty_brands": ["bridgestone"],
    },
}

PROKOLESO_CONFIG_PATCH: dict[str, Any] = {
    "sales_enabled": False,
    "network_policy": {
        **_COMMON_NETWORK_POLICY,
        "delivery_mode": "carrier_tariff",
        "services": [],
        "extended_warranty_brands": [],
    },
}


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database.url)

    try:
        async with engine.begin() as conn:
            # ── Step 1: Update ProKoleso ──
            result = await conn.execute(
                text("""
                    UPDATE tenants
                    SET enabled_tools = CAST(:enabled_tools AS text[]),
                        prompt_suffix = :prompt_suffix,
                        config = COALESCE(config, CAST('{}' AS jsonb)) || CAST(:config_patch AS jsonb),
                        updated_at = now()
                    WHERE slug = 'prokoleso'
                    RETURNING id, name, slug
                """),
                {
                    "enabled_tools": PROKOLESO_ENABLED_TOOLS,
                    "prompt_suffix": PROKOLESO_PROMPT_SUFFIX,
                    "config_patch": json.dumps(PROKOLESO_CONFIG_PATCH, ensure_ascii=False),
                },
            )
            row = result.first()
            if row:
                logger.info(
                    "Updated ProKoleso (id=%s): enabled_tools=%d tools, prompt_suffix set",
                    row.id,
                    len(PROKOLESO_ENABLED_TOOLS),
                )
            else:
                logger.warning("Tenant 'prokoleso' not found — skipping")

            # ── Step 2: Update Tshina ──
            result = await conn.execute(
                text("""
                    UPDATE tenants
                    SET name = :name,
                        enabled_tools = CAST(:enabled_tools AS text[]),
                        prompt_suffix = :prompt_suffix,
                        config = COALESCE(config, CAST('{}' AS jsonb)) || CAST(:config_patch AS jsonb),
                        updated_at = now()
                    WHERE slug = 'tvoya-shina'
                    RETURNING id, name, slug
                """),
                {
                    "name": "Твоя Шина",
                    "enabled_tools": [],
                    "prompt_suffix": None,
                    "config_patch": json.dumps(TVOYA_SHINA_CONFIG_PATCH, ensure_ascii=False),
                },
            )
            row = result.first()
            if row:
                logger.info(
                    "Updated Твоя Шина → '%s' (id=%s): enabled_tools=[] (all), prompt_suffix cleared",
                    row.name,
                    row.id,
                )
            else:
                logger.warning("Tenant 'tvoya-shina' not found — skipping")

        # ── Verify ──
        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    SELECT slug, name, enabled_tools, prompt_suffix,
                           config->'sales_enabled' AS sales_enabled,
                           config->'network_policy'->>'delivery_mode' AS delivery_mode
                    FROM tenants
                    WHERE slug IN ('prokoleso', 'tvoya-shina')
                    ORDER BY slug
                """)
            )
            logger.info("── Verification ──")
            for row in result:
                tools = row.enabled_tools or []
                suffix = (row.prompt_suffix or "")[:60]
                logger.info(
                    "  %s (%s): %d tools, suffix=%s, sales_enabled=%s, delivery_mode=%s",
                    row.slug,
                    row.name,
                    len(tools),
                    repr(suffix + "...") if suffix else "None",
                    row.sales_enabled,
                    row.delivery_mode,
                )

        logger.info("Done!")

    finally:
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
