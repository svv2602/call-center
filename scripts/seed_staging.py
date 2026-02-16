#!/usr/bin/env python3
"""Seed staging database with test data.

Creates test operators, knowledge base articles, and sample call records
for the staging environment.

Usage:
    python scripts/seed_staging.py
    # or with custom DB URL:
    DATABASE_URL=postgresql+asyncpg://... python scripts/seed_staging.py
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.config import get_settings

logger = logging.getLogger(__name__)


async def seed_operators(engine) -> int:  # type: ignore[no-untyped-def]
    """Create test operators."""
    operators = [
        ("Олена Коваленко", "olena@example.com", "available"),
        ("Микола Шевченко", "mykola@example.com", "available"),
        ("Ірина Бондаренко", "iryna@example.com", "busy"),
        ("Андрій Мельник", "andriy@example.com", "offline"),
    ]
    count = 0
    async with engine.begin() as conn:
        for name, email, status in operators:
            await conn.execute(
                text(
                    "INSERT INTO operators (id, name, email, status, created_at) "
                    "VALUES (:id, :name, :email, :status, :created_at) "
                    "ON CONFLICT (email) DO NOTHING"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "name": name,
                    "email": email,
                    "status": status,
                    "created_at": datetime.now(UTC),
                },
            )
            count += 1
    return count


async def seed_knowledge_base(engine) -> int:  # type: ignore[no-untyped-def]
    """Create test knowledge base articles."""
    articles = [
        {
            "title": "Як обрати зимові шини",
            "category": "guides",
            "content": (
                "Зимові шини рекомендується встановлювати при температурі нижче +7°C. "
                "Основні критерії вибору: розмір (ширина/профіль/діаметр), "
                "індекс швидкості, індекс навантаження, тип (шиповані/фрикційні)."
            ),
        },
        {
            "title": "Графік роботи шиномонтажу",
            "category": "service",
            "content": (
                "Наші станції шиномонтажу працюють з 08:00 до 20:00 без вихідних. "
                "Час обслуговування одного автомобіля: 30-60 хвилин залежно від послуги."
            ),
        },
        {
            "title": "Умови доставки",
            "category": "delivery",
            "content": (
                "Доставка по Києву: 1-2 дні, безкоштовно при замовленні від 4 шин. "
                "Доставка по Україні: 2-5 днів через Нову Пошту. "
                "Самовивіз зі складу — безкоштовно."
            ),
        },
        {
            "title": "Гарантійні умови",
            "category": "warranty",
            "content": (
                "Гарантія на шини: 5 років або 60,000 км (що настане раніше). "
                "Гарантія на шиномонтаж: 30 днів. "
                "Повернення протягом 14 днів якщо шини не встановлені."
            ),
        },
    ]
    count = 0
    async with engine.begin() as conn:
        for article in articles:
            await conn.execute(
                text(
                    "INSERT INTO knowledge_articles (id, title, category, content, created_at) "
                    "VALUES (:id, :title, :category, :content, :created_at) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "title": article["title"],
                    "category": article["category"],
                    "content": article["content"],
                    "created_at": datetime.now(UTC),
                },
            )
            count += 1
    return count


async def seed_sample_calls(engine) -> int:  # type: ignore[no-untyped-def]
    """Create sample call records for dashboard testing."""
    now = datetime.now(UTC)
    statuses = ["completed", "completed", "completed", "transferred", "error"]
    scenarios = ["tire_search", "tire_search", "order", "fitting", "consultation"]
    count = 0

    async with engine.begin() as conn:
        for i in range(20):
            call_id = str(uuid.uuid4())
            started = now - timedelta(hours=i * 2, minutes=i * 3)
            duration = 30 + (i * 7) % 180
            status = statuses[i % len(statuses)]
            scenario = scenarios[i % len(scenarios)]

            await conn.execute(
                text(
                    "INSERT INTO calls (id, caller_id, started_at, ended_at, "
                    "duration_seconds, status, scenario, cost_usd) "
                    "VALUES (:id, :caller_id, :started_at, :ended_at, "
                    ":duration, :status, :scenario, :cost) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "id": call_id,
                    "caller_id": f"+38067{1000000 + i:07d}",
                    "started_at": started,
                    "ended_at": started + timedelta(seconds=duration),
                    "duration": duration,
                    "status": status,
                    "scenario": scenario,
                    "cost": round(0.02 + (duration / 60) * 0.03, 4),
                },
            )
            count += 1
    return count


async def create_partitions(engine) -> None:  # type: ignore[no-untyped-def]
    """Ensure current month partitions exist."""
    now = datetime.now(UTC)
    year = now.year
    month = now.month
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    start_date = f"{year}-{month:02d}-01"
    end_date = f"{next_year}-{next_month:02d}-01"

    async with engine.begin() as conn:
        for table in ["calls", "call_turns", "call_tool_calls"]:
            partition = f"{table}_{year}_{month:02d}"
            await conn.execute(
                text(
                    f"CREATE TABLE IF NOT EXISTS {partition} "
                    f"PARTITION OF {table} "
                    f"FOR VALUES FROM ('{start_date}') TO ('{end_date}')"
                )
            )


async def main() -> None:
    """Run all seed operations."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    settings = get_settings()
    engine = create_async_engine(settings.database.url)

    try:
        # Ensure partitions exist before inserting data
        logger.info("Creating partitions...")
        await create_partitions(engine)

        logger.info("Seeding operators...")
        op_count = await seed_operators(engine)
        logger.info("  Created %d operators", op_count)

        logger.info("Seeding knowledge base articles...")
        kb_count = await seed_knowledge_base(engine)
        logger.info("  Created %d articles", kb_count)

        logger.info("Seeding sample calls...")
        call_count = await seed_sample_calls(engine)
        logger.info("  Created %d sample calls", call_count)

        logger.info("Staging seed completed successfully!")

    except Exception:
        logger.exception("Seed failed")
        raise
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
