"""Configure tenant-specific services and tool availability.

ProKoleso: no fitting services (шиномонтаж) — 5 fitting tools removed,
           prompt_suffix instructs agent to decline fitting requests.
Твоя шина (shinservice): all tools enabled, display name updated.

Usage: python -m scripts.configure_tenants
"""

from __future__ import annotations

import asyncio
import logging
import sys

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
    "Не згадуй інші мережі та не пропонуй альтернативи."
)


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
                        updated_at = now()
                    WHERE slug = 'prokoleso'
                    RETURNING id, name, slug
                """),
                {
                    "enabled_tools": PROKOLESO_ENABLED_TOOLS,
                    "prompt_suffix": PROKOLESO_PROMPT_SUFFIX,
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
                        updated_at = now()
                    WHERE slug = 'shinservice'
                    RETURNING id, name, slug
                """),
                {
                    "name": "Твоя шина",
                    "enabled_tools": [],
                    "prompt_suffix": None,
                },
            )
            row = result.first()
            if row:
                logger.info(
                    "Updated Tshina → '%s' (id=%s): enabled_tools=[] (all), prompt_suffix cleared",
                    row.name,
                    row.id,
                )
            else:
                logger.warning("Tenant 'shinservice' not found — skipping")

        # ── Verify ──
        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    SELECT slug, name, enabled_tools, prompt_suffix
                    FROM tenants
                    WHERE slug IN ('prokoleso', 'shinservice')
                    ORDER BY slug
                """)
            )
            logger.info("── Verification ──")
            for row in result:
                tools = row.enabled_tools or []
                suffix = (row.prompt_suffix or "")[:60]
                logger.info(
                    "  %s (%s): %d tools, suffix=%s",
                    row.slug,
                    row.name,
                    len(tools),
                    repr(suffix + "...") if suffix else "None",
                )

        logger.info("Done!")

    finally:
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
