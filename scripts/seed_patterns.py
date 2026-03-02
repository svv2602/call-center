"""Seed conversation patterns from live call analysis.

Patterns provide contextual behavioral instructions to the LLM agent.
They are matched by cosine similarity to customer text at runtime.

Usage:
    python -m scripts.seed_patterns
"""

from __future__ import annotations

import asyncio
import logging
import os

import asyncpg

from src.knowledge.embeddings import EmbeddingGenerator

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Patterns discovered from live call analysis.
# Each pattern has:
#   intent_label: short name (shown in Admin UI)
#   pattern_type: "positive" (do this) or "negative" (don't do this)
#   customer_messages: representative customer text (used for embedding match)
#   guidance_note: instruction injected into system prompt when matched
#   tags: categories for filtering
PATTERNS = [
    {
        "intent_label": "Номер авто — читай парами",
        "pattern_type": "positive",
        "customer_messages": "номер машини 1873, мій номер АЕ1541ММ, держномер вісімнадцять сімдесят три",
        "guidance_note": (
            "Державний номер авто ЗАВЖДИ читай цифри ПАРАМИ по дві: "
            '"1873" → "вісімнадцять сімдесят три" (НЕ "тисяча вісімсот сімдесят три"). '
            '"1541" → "п\'ятнадцять сорок один". '
            "Це номерний знак, НЕ число!"
        ),
        "tags": ["fitting", "pronunciation"],
    },
    {
        "intent_label": "Номер авто — не переспитуй 3+ символи",
        "pattern_type": "negative",
        "customer_messages": "1873, номер 1541, АЕ15, мій номер чотири цифри",
        "guidance_note": (
            "НЕ переспитуй номер авто якщо він 3+ символів (наприклад '1873', 'АЕ15'). "
            "Переспитуй ТІЛЬКИ якщо 1-2 символи ('11', 'а') — це може бути помилка STT."
        ),
        "tags": ["fitting"],
    },
    {
        "intent_label": "ММ в номері — літери, не міліметри",
        "pattern_type": "positive",
        "customer_messages": "АЕ1873ММ, номер з літерами ММ, ем ем",
        "guidance_note": (
            '"ММ" в державному номері (наприклад "АЕ1873ММ") — це ЛІТЕРИ. '
            'Читай як "ем ем", НЕ "міліметрів".'
        ),
        "tags": ["fitting", "pronunciation"],
    },
    {
        "intent_label": "Не плутай station ID з телефоном",
        "pattern_type": "negative",
        "customer_messages": "телефон шиномонтажу, дайте номер телефону точки, як зателефонувати",
        "guidance_note": (
            "Поле `id` станції (наприклад '000000003') — це технічний код, НЕ телефон! "
            "Телефон — це поле `phone` (наприклад '(067) 130-36-03'). "
            "НІКОЛИ не називай id як телефон клієнту."
        ),
        "tags": ["fitting"],
    },
    {
        "intent_label": "Завтра ≠ сьогодні — не блокуй запис",
        "pattern_type": "negative",
        "customer_messages": "на завтра, запишіть на завтра, завтра вранці, на послезавтра",
        "guidance_note": (
            'Обмеження "на сьогодні запис недоступний" застосовуй ТІЛЬКИ якщо клієнт просить '
            "саме на СЬОГОДНІ. Якщо клієнт каже 'завтра' або будь-яку іншу дату — "
            "НЕ згадуй це обмеження, одразу переходь до get_fitting_slots."
        ),
        "tags": ["fitting"],
    },
    {
        "intent_label": "Про Колесо — через пробіл для TTS",
        "pattern_type": "positive",
        "customer_messages": "Проколесо, мережа Проколесо, магазин Проколесо",
        "guidance_note": (
            '"Проколесо" ЗАВЖДИ пиши як два слова: "Про Колесо" — '
            "TTS інакше читає з неправильним наголосом."
        ),
        "tags": ["pronunciation", "brand"],
    },
    {
        "intent_label": "Адреса — номер будинку не відмінюється",
        "pattern_type": "positive",
        "customer_messages": "адреса шиномонтажу, Запорізьке шосе 55К, Перемоги 24А, Донецьке шосе 1Д",
        "guidance_note": (
            "Номери будинків НІКОЛИ не відмінюються! Завжди називний відмінок: "
            '"за адресою Запорізьке шосе, п\'ятдесят п\'ять ка" (НЕ "п\'ятдесяти п\'яти ка"). '
            '"1Д" → "один де" (НЕ "один день"), "55К" → "п\'ятдесят п\'ять ка".'
        ),
        "tags": ["pronunciation", "fitting"],
    },
]


async def main() -> None:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        logger.error("DATABASE_URL not set")
        return

    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if not openai_key:
        logger.error("OPENAI_API_KEY not set")
        return

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2, command_timeout=30)
    generator = EmbeddingGenerator(openai_key)
    await generator.open()

    try:
        created = 0
        skipped = 0
        for p in PATTERNS:
            # Check for existing pattern with same intent_label
            async with pool.acquire() as conn:
                existing = await conn.fetchval(
                    "SELECT count(*) FROM conversation_patterns WHERE intent_label = $1",
                    p["intent_label"],
                )
                if existing > 0:
                    logger.info("SKIP (exists): %s", p["intent_label"])
                    skipped += 1
                    continue

            # Generate embedding
            embed_text = f"{p['intent_label']}: {p['customer_messages']}"
            embedding = await generator.generate_single(embed_text)
            embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"

            # Insert
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO conversation_patterns
                        (intent_label, pattern_type, customer_messages, guidance_note,
                         tags, embedding, is_active)
                    VALUES ($1, $2, $3, $4, $5, CAST($6 AS vector), true)
                    """,
                    p["intent_label"],
                    p["pattern_type"],
                    p["customer_messages"],
                    p["guidance_note"],
                    p["tags"],
                    embedding_str,
                )
            logger.info("CREATED: %s (%s)", p["intent_label"], p["pattern_type"])
            created += 1

        logger.info("Done: %d created, %d skipped", created, skipped)
    finally:
        await generator.close()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
