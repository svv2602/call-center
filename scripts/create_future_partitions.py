#!/usr/bin/env python3
"""
Скрипт для автоматического создания будущих партиций PostgreSQL.

Запускается как cron job или как часть деплоя для создания партиций
на следующие N месяцев вперед.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import List

import asyncpg
from src.config import get_settings

logger = logging.getLogger(__name__)


async def create_partitions_for_month(conn: asyncpg.Connection, year: int, month: int) -> None:
    """Создать партиции для указанного месяца."""
    month_str = f"{year}_{month:02d}"
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    start_date = f"{year}-{month:02d}-01"
    end_date = f"{next_year}-{next_month:02d}-01"

    # Создание партиций для таблицы calls
    calls_table = f"calls_{month_str}"
    await conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {calls_table} PARTITION OF calls
        FOR VALUES FROM ('{start_date}') TO ('{end_date}')
    """)
    logger.info(f"Создана партиция {calls_table}")

    # Создание партиций для таблицы call_turns
    turns_table = f"call_turns_{month_str}"
    await conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {turns_table} PARTITION OF call_turns
        FOR VALUES FROM ('{start_date}') TO ('{end_date}')
    """)
    logger.info(f"Создана партиция {turns_table}")

    # Создание партиций для таблицы call_tool_calls
    tools_table = f"call_tool_calls_{month_str}"
    await conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {tools_table} PARTITION OF call_tool_calls
        FOR VALUES FROM ('{start_date}') TO ('{end_date}')
    """)
    logger.info(f"Создана партиция {tools_table}")


async def get_existing_partitions(conn: asyncpg.Connection, table_name: str) -> List[str]:
    """Получить список существующих партиций для таблицы."""
    query = """
        SELECT inhrelid::regclass::text as partition_name
        FROM pg_inherits
        WHERE inhparent = $1::regclass
        ORDER BY partition_name
    """
    rows = await conn.fetch(query, table_name)
    return [row["partition_name"] for row in rows]


async def create_future_partitions(months_ahead: int = 6) -> None:
    """Создать партиции на следующие N месяцев вперед."""
    settings = get_settings()

    try:
        conn = await asyncpg.connect(settings.database.url)

        # Проверяем существующие партиции
        existing_calls = await get_existing_partitions(conn, "calls")
        logger.info(f"Существующие партиции calls: {existing_calls}")

        # Создаем партиции на будущие месяцы
        today = datetime.now()
        for i in range(months_ahead):
            future_date = today + timedelta(days=30 * i)
            year = future_date.year
            month = future_date.month

            await create_partitions_for_month(conn, year, month)

        # Проверяем, что все создалось
        final_calls = await get_existing_partitions(conn, "calls")
        logger.info(f"Всего партиций calls после создания: {len(final_calls)}")

        await conn.close()

    except Exception as e:
        logger.error(f"Ошибка при создании партиций: {e}")
        raise


async def cleanup_old_partitions(keep_months: int = 24) -> None:
    """
    Удалить старые партиции, которые старше keep_months месяцев.
    ВНИМАНИЕ: Удаляет данные! Использовать с осторожностью.
    """
    settings = get_settings()

    try:
        conn = await asyncpg.connect(settings.database.url)

        cutoff_date = datetime.now() - timedelta(days=30 * keep_months)
        cutoff_year = cutoff_date.year
        cutoff_month = cutoff_date.month

        # Получаем все партиции
        all_partitions = await get_existing_partitions(conn, "calls")

        for partition in all_partitions:
            # Извлекаем год и месяц из имени партиции
            # Формат: calls_2026_01
            try:
                parts = partition.split("_")
                if len(parts) >= 3:
                    year = int(parts[1])
                    month = int(parts[2])

                    # Проверяем, нужно ли удалить
                    if year < cutoff_year or (year == cutoff_year and month < cutoff_month):
                        logger.warning(f"УДАЛЕНИЕ старой партиции: {partition}")
                        # В production нужно добавить backup перед удалением!
                        # await conn.execute(f"DROP TABLE {partition}")

            except (ValueError, IndexError):
                logger.warning(f"Не удалось разобрать имя партиции: {partition}")

        await conn.close()

    except Exception as e:
        logger.error(f"Ошибка при очистке старых партиций: {e}")
        raise


async def main() -> None:
    """Основная функция."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Создаем партиции на 12 месяцев вперед
    await create_future_partitions(months_ahead=12)

    # Опционально: очистка старых партиций (закомментировано для безопасности)
    # await cleanup_old_partitions(keep_months=24)


if __name__ == "__main__":
    asyncio.run(main())
