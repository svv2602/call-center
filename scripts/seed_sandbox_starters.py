"""Seed sandbox scenario starters for quick-start testing.

Usage:
    python -m scripts.seed_sandbox_starters

Inserts starter templates for all 5 scenario types with varied customer personas.
Skips duplicates by title (ON CONFLICT DO NOTHING via check).
"""

from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

STARTERS = [
    # ── tire_search ──────────────────────────────────────────────────
    {
        "title": "Пошук шин за розміром",
        "first_message": "Добрий день! Мені потрібні шини 205/55 R16, що у вас є?",
        "scenario_type": "tire_search",
        "customer_persona": "neutral",
        "description": "Клієнт знає розмір, хоче побачити варіанти",
        "tags": ["розмір", "базовий"],
        "sort_order": 1,
    },
    {
        "title": "Шини для авто (марка/модель)",
        "first_message": "Привіт, у мене Toyota Camry 2020 року, які шини мені підійдуть?",
        "scenario_type": "tire_search",
        "customer_persona": "neutral",
        "description": "Клієнт не знає розмір, називає авто",
        "tags": ["авто", "підбір"],
        "sort_order": 2,
    },
    {
        "title": "Зимові шини — детальний запит",
        "first_message": "Доброго дня! Шукаю зимові шини на позашляховик, Toyota RAV4 2022. Їжджу переважно по місту, але буває і траса. Хотілось би щось тихе та з хорошим зчепленням на мокрій дорозі. Бюджет — до 4000 грн за штуку.",
        "scenario_type": "tire_search",
        "customer_persona": "detailed",
        "description": "Детальний запит з умовами експлуатації та бюджетом",
        "tags": ["зимові", "SUV", "бюджет", "детальний"],
        "sort_order": 3,
    },
    {
        "title": "Бюджетні шини — поспішає",
        "first_message": "Мені треба найдешевші шини 195/65 R15, швидко скажіть що є і скільки коштує",
        "scenario_type": "tire_search",
        "customer_persona": "rushed",
        "description": "Клієнт поспішає, хоче бюджетний варіант",
        "tags": ["бюджет", "швидко"],
        "sort_order": 4,
    },
    {
        "title": "Не знає розмір шин",
        "first_message": "Алло, я не знаю який розмір шин мені потрібен... В мене Hyundai Tucson, але я не пам'ятаю рік. Десь 2018 чи 2019...",
        "scenario_type": "tire_search",
        "customer_persona": "confused",
        "description": "Клієнт розгублений, не знає параметрів",
        "tags": ["невизначеність", "підбір"],
        "sort_order": 5,
    },
    {
        "title": "Порівняння брендів",
        "first_message": "Скажіть, а Michelin 205/55 R16 і Continental такого ж розміру — яка між ними різниця? Що краще для щоденної їзди?",
        "scenario_type": "tire_search",
        "customer_persona": "detailed",
        "description": "Клієнт порівнює бренди, потрібна консультація",
        "tags": ["порівняння", "бренди"],
        "sort_order": 6,
    },
    # ── order_creation ───────────────────────────────────────────────
    {
        "title": "Готовий замовити шини",
        "first_message": "Здрастуйте! Я вже обрав — хочу замовити Michelin Primacy 4 205/55 R16, 4 штуки. Як оформити?",
        "scenario_type": "order_creation",
        "customer_persona": "neutral",
        "description": "Клієнт точно знає що хоче, готовий до замовлення",
        "tags": ["замовлення", "конкретний"],
        "sort_order": 10,
    },
    {
        "title": "Замовлення з доставкою",
        "first_message": "Доброго дня. Мені потрібні шини 225/45 R17 з доставкою у Дніпро. Хотів би дізнатися варіанти та оформити замовлення.",
        "scenario_type": "order_creation",
        "customer_persona": "detailed",
        "description": "Замовлення з доставкою в інше місто",
        "tags": ["замовлення", "доставка"],
        "sort_order": 11,
    },
    {
        "title": "Терміновий самовивіз",
        "first_message": "Мені терміново потрібні шини 185/65 R15, хочу сьогодні забрати. Є що в наявності? Одразу оформлюйте!",
        "scenario_type": "order_creation",
        "customer_persona": "rushed",
        "description": "Термінове замовлення з самовивозом",
        "tags": ["замовлення", "терміново", "самовивіз"],
        "sort_order": 12,
    },
    # ── fitting_booking ──────────────────────────────────────────────
    {
        "title": "Запис на шиномонтаж",
        "first_message": "Добрий день, хочу записатися на шиномонтаж. Коли можна?",
        "scenario_type": "fitting_booking",
        "customer_persona": "neutral",
        "description": "Стандартний запис на шиномонтаж",
        "tags": ["шиномонтаж", "запис"],
        "sort_order": 20,
    },
    {
        "title": "Терміновий шиномонтаж",
        "first_message": "У мене колесо спустило! Мені терміново потрібен шиномонтаж десь поблизу центру Києва. Коли найшвидше можна потрапити?",
        "scenario_type": "fitting_booking",
        "customer_persona": "rushed",
        "description": "Екстрений запит на шиномонтаж",
        "tags": ["шиномонтаж", "терміново", "Київ"],
        "sort_order": 21,
    },
    {
        "title": "Купівля + монтаж комплексно",
        "first_message": "Доброго дня! Мені потрібно і шини купити, і одразу поставити. У мене Volkswagen Golf 2021. Хотілось би все зробити за один візит.",
        "scenario_type": "fitting_booking",
        "customer_persona": "detailed",
        "description": "Комплексний запит: підбір шин + запис на монтаж",
        "tags": ["шиномонтаж", "комплексний", "підбір"],
        "sort_order": 22,
    },
    # ── expert_consultation ──────────────────────────────────────────
    {
        "title": "Коли міняти на літні?",
        "first_message": "Підкажіть, коли вже можна переходити на літню гуму? Зараз ще холодно вранці, але вдень вже тепло.",
        "scenario_type": "expert_consultation",
        "customer_persona": "neutral",
        "description": "Питання про сезонну заміну шин",
        "tags": ["консультація", "сезон"],
        "sort_order": 30,
    },
    {
        "title": "Гарантія на шини",
        "first_message": "Я купив у вас шини два місяці тому і одна вже почала тріскатися збоку. Це гарантійний випадок? Що мені робити?",
        "scenario_type": "expert_consultation",
        "customer_persona": "confused",
        "description": "Питання про гарантію, потенційна рекламація",
        "tags": ["гарантія", "рекламація"],
        "sort_order": 31,
    },
    {
        "title": "Шум та вібрація після заміни",
        "first_message": "Після того як мені поставили нові шини, машина стала вібрувати на швидкості 80+ і якийсь гул з'явився. Що за ерунда? Я ж заплатив нормальні гроші!",
        "scenario_type": "expert_consultation",
        "customer_persona": "angry",
        "description": "Скарга на якість після монтажу",
        "tags": ["скарга", "вібрація", "якість"],
        "sort_order": 32,
    },
    {
        "title": "Різниця Run-Flat та звичайних",
        "first_message": "Розкажіть про шини Run-Flat — які переваги та недоліки? Чи варто їх ставити на звичайний седан?",
        "scenario_type": "expert_consultation",
        "customer_persona": "neutral",
        "description": "Технічна консультація щодо типу шин",
        "tags": ["консультація", "run-flat", "технічне"],
        "sort_order": 33,
    },
    {
        "title": "Тиск у шинах — що рекомендуєте?",
        "first_message": "Доброго дня! Я хочу дізнатися, який тиск потрібно тримати в шинах 215/60 R16? І як часто перевіряти?",
        "scenario_type": "expert_consultation",
        "customer_persona": "neutral",
        "description": "Консультація щодо тиску та обслуговування",
        "tags": ["консультація", "тиск", "обслуговування"],
        "sort_order": 34,
    },
    # ── operator_transfer ────────────────────────────────────────────
    {
        "title": "Хоче оператора одразу",
        "first_message": "З'єднайте мене з оператором, будь ласка. Мені потрібна жива людина.",
        "scenario_type": "operator_transfer",
        "customer_persona": "neutral",
        "description": "Клієнт одразу просить оператора",
        "tags": ["оператор", "переключення"],
        "sort_order": 40,
    },
    {
        "title": "Розлючений клієнт — скарга",
        "first_message": "Послухайте, я вже третій раз дзвоню! Мені привезли не ті шини, ніхто не передзвонює, і я вже втратив купу часу! Мені потрібен менеджер ЗАРАЗ!",
        "scenario_type": "operator_transfer",
        "customer_persona": "angry",
        "description": "Розлючений клієнт зі скаргою, потрібен оператор",
        "tags": ["скарга", "оператор", "ескалація"],
        "sort_order": 41,
    },
    {
        "title": "Корпоративне замовлення",
        "first_message": "Доброго дня, я представник компанії і хочу обговорити корпоративне замовлення шин на автопарк — 40 комплектів. Чи можна поговорити з відповідальним менеджером?",
        "scenario_type": "operator_transfer",
        "customer_persona": "neutral",
        "description": "Корпоративний клієнт — потребує менеджера",
        "tags": ["корпоративний", "оператор", "опт"],
        "sort_order": 42,
    },
]


async def main() -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif not database_url.startswith("postgresql+asyncpg://"):
        database_url = f"postgresql+asyncpg://{database_url}"

    engine = create_async_engine(database_url, echo=False)

    inserted = 0
    skipped = 0

    async with engine.begin() as conn:
        for s in STARTERS:
            # Check if starter with same title already exists
            existing = await conn.execute(
                text("SELECT id FROM sandbox_scenario_starters WHERE title = :title"),
                {"title": s["title"]},
            )
            if existing.first():
                print(f"  SKIP (exists): {s['title']}")
                skipped += 1
                continue

            await conn.execute(
                text("""
                    INSERT INTO sandbox_scenario_starters
                        (title, first_message, scenario_type, customer_persona,
                         description, tags, sort_order)
                    VALUES
                        (:title, :first_message, :scenario_type, :customer_persona,
                         :description, :tags, :sort_order)
                """),
                {
                    "title": s["title"],
                    "first_message": s["first_message"],
                    "scenario_type": s["scenario_type"],
                    "customer_persona": s["customer_persona"],
                    "description": s["description"],
                    "tags": s["tags"],
                    "sort_order": s["sort_order"],
                },
            )
            print(f"  ADD: {s['title']}")
            inserted += 1

    await engine.dispose()
    print(f"\nDone: {inserted} inserted, {skipped} skipped (already exist)")


if __name__ == "__main__":
    asyncio.run(main())
