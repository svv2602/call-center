"""LLM agent tool definitions for Claude API tool_use.

Canonical tool names from doc/development/00-overview.md.
MVP tools: search_tires, check_availability, transfer_to_operator.
Phase 2 tools: get_order_status, create_order_draft, update_order_delivery, confirm_order.
Phase 3 tools: get_fitting_stations, get_fitting_slots, book_fitting, cancel_fitting,
               get_fitting_price, search_knowledge_base.
"""

from __future__ import annotations

from src.knowledge.categories import CATEGORY_VALUES

# MVP tools — Claude API tool_use format
MVP_TOOLS: list[dict] = [  # type: ignore[type-arg]
    {
        "name": "get_vehicle_tire_sizes",
        "description": (
            "Отримати заводські розміри шин для автомобіля. "
            "Використовуй ПЕРЕД search_tires, коли клієнт називає авто. "
            "Повертає стокові та допустимі розміри шин."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "brand": {"type": "string", "description": "Марка (Kia, Toyota, BMW)"},
                "model": {"type": "string", "description": "Модель (Sportage, Camry, X5)"},
                "year": {"type": "integer", "description": "Рік випуску"},
            },
            "required": ["brand", "model"],
        },
    },
    {
        "name": "search_tires",
        "description": (
            "Пошук шин у каталозі магазину. "
            "УВАГА: НЕ викликай цей інструмент, поки не з'ясуєш у клієнта "
            "розмір шин (ширина/профіль/діаметр) та сезон. "
            "Спершу з'ясуй потреби, потім шукай."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "width": {
                    "type": "integer",
                    "description": "Ширина шини в мм (наприклад, 205, 225)",
                },
                "profile": {
                    "type": "integer",
                    "description": "Профіль шини в % (наприклад, 55, 60)",
                },
                "diameter": {
                    "type": "integer",
                    "description": "Діаметр диска в дюймах (наприклад, 16, 17)",
                },
                "season": {
                    "type": "string",
                    "enum": ["summer", "winter", "all_season"],
                    "description": "Сезон: літні, зимові або всесезонні",
                },
                "brand": {
                    "type": "string",
                    "description": "Бренд шин (наприклад, Michelin, Continental)",
                },
            },
            "required": ["width", "profile", "diameter", "season"],
        },
    },
    {
        "name": "check_availability",
        "description": (
            "Перевірка наявності конкретного товару на складі. "
            "Використовуй після пошуку, коли клієнт обрав конкретну шину."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "product_id": {
                    "type": "string",
                    "description": "ID товару з результатів пошуку",
                },
                "query": {
                    "type": "string",
                    "description": "Текстовий запит для пошуку по назві",
                },
            },
        },
    },
    {
        "name": "transfer_to_operator",
        "description": (
            "Переключити клієнта на живого оператора. "
            "Використовуй, коли не можеш допомогти або клієнт просить оператора."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "enum": [
                        "customer_request",
                        "cannot_help",
                        "complex_question",
                        "negative_emotion",
                    ],
                    "description": "Причина переключення",
                },
                "summary": {
                    "type": "string",
                    "description": "Короткий опис розмови для оператора",
                },
            },
            "required": ["reason", "summary"],
        },
    },
]

# Phase 2: Order management tools
ORDER_TOOLS: list[dict] = [  # type: ignore[type-arg]
    {
        "name": "get_order_status",
        "description": (
            "Отримати статус замовлення за номером телефону або номером замовлення. "
            "Використовуй, коли клієнт запитує про статус замовлення."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {
                    "type": "string",
                    "description": "Номер телефону клієнта (+380XXXXXXXXX)",
                },
                "order_id": {
                    "type": "string",
                    "description": "Номер або ID замовлення",
                },
            },
        },
    },
    {
        "name": "create_order_draft",
        "description": (
            "Створити чорновик замовлення. "
            "Використовуй після того, як клієнт обрав шини і хоче замовити."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": "Список товарів для замовлення",
                    "items": {
                        "type": "object",
                        "properties": {
                            "product_id": {
                                "type": "string",
                                "description": "ID товару",
                            },
                            "quantity": {
                                "type": "integer",
                                "description": "Кількість (від 1 до 99)",
                            },
                        },
                        "required": ["product_id", "quantity"],
                    },
                },
                "customer_phone": {
                    "type": "string",
                    "description": "Номер телефону клієнта (+380XXXXXXXXX)",
                },
            },
            "required": ["items", "customer_phone"],
        },
    },
    {
        "name": "update_order_delivery",
        "description": (
            "Вказати спосіб та адресу доставки для замовлення. "
            "Використовуй після створення чорновика замовлення."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "ID замовлення",
                },
                "delivery_type": {
                    "type": "string",
                    "enum": ["delivery", "pickup"],
                    "description": "Тип: доставка або самовивіз",
                },
                "city": {
                    "type": "string",
                    "description": "Місто доставки",
                },
                "address": {
                    "type": "string",
                    "description": "Адреса доставки",
                },
                "pickup_point_id": {
                    "type": "string",
                    "description": "ID пункту самовивозу",
                },
            },
            "required": ["order_id", "delivery_type"],
        },
    },
    {
        "name": "confirm_order",
        "description": (
            "Підтвердити та фіналізувати замовлення. "
            "ОБОВ'ЯЗКОВО: перед викликом оголоси клієнту склад, суму та отримай підтвердження 'так'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "ID замовлення",
                },
                "payment_method": {
                    "type": "string",
                    "enum": ["cod", "online", "card_on_delivery"],
                    "description": "Спосіб оплати: накладений платіж, онлайн, картка при отриманні",
                },
                "customer_name": {
                    "type": "string",
                    "description": "Ім'я клієнта для замовлення",
                },
            },
            "required": ["order_id", "payment_method"],
        },
    },
    {
        "name": "get_pickup_points",
        "description": (
            "Отримати список пунктів видачі (самовивозу) для поточної мережі. "
            "Використовуй, коли клієнт обирає самовивіз як спосіб доставки. "
            "Після вибору пункту клієнтом — передай його id в update_order_delivery(pickup_point_id=...)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "Місто для фільтрації пунктів (наприклад, 'Київ')",
                },
            },
            "required": [],
        },
    },
]

# Phase 3: Fitting and knowledge base tools
FITTING_TOOLS: list[dict] = [  # type: ignore[type-arg]
    {
        "name": "get_fitting_stations",
        "description": (
            "Отримати список точок шиномонтажу. "
            "Без параметра city — повертає всі точки (для відповіді 'в яких містах є шиномонтаж'). "
            "З параметром city — фільтрує по місту."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "Місто для пошуку точок шиномонтажу (необов'язково)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_fitting_slots",
        "description": (
            "Отримати доступні слоти для запису на шиномонтаж. "
            "Використовуй після вибору точки шиномонтажу."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "station_id": {
                    "type": "string",
                    "description": "ID точки шиномонтажу",
                },
                "date_from": {
                    "type": "string",
                    "description": "Початкова дата у форматі YYYY-MM-DD",
                },
                "date_to": {
                    "type": "string",
                    "description": "Кінцева дата у форматі YYYY-MM-DD (за замовчуванням = date_from)",
                },
                "service_type": {
                    "type": "string",
                    "enum": ["tire_change", "balancing", "full_service"],
                    "description": "Тип послуги: заміна шин, балансування, повний сервіс",
                },
            },
            "required": ["station_id"],
        },
    },
    {
        "name": "book_fitting",
        "description": (
            "Записати клієнта на шиномонтаж. Використовуй після вибору точки, дати та часу."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "station_id": {
                    "type": "string",
                    "description": "ID точки шиномонтажу",
                },
                "date": {
                    "type": "string",
                    "description": "Дата запису (YYYY-MM-DD)",
                },
                "time": {
                    "type": "string",
                    "description": "Час запису (HH:MM)",
                },
                "customer_name": {
                    "type": "string",
                    "description": "Ім'я клієнта (як назвався)",
                },
                "customer_phone": {
                    "type": "string",
                    "description": "Телефон клієнта (0XXXXXXXXX — 10 цифр без +38)",
                },
                "auto_number": {
                    "type": "string",
                    "description": "Державний номер автомобіля (якщо клієнт назвав)",
                },
                "vehicle_info": {
                    "type": "string",
                    "description": "Марка/модель автомобіля (якщо клієнт назвав)",
                },
                "service_type": {
                    "type": "string",
                    "enum": ["tire_change", "balancing", "full_service"],
                    "description": "Тип послуги",
                },
                "tire_diameter": {
                    "type": "integer",
                    "description": "Діаметр шин у дюймах (наприклад, 16, 17)",
                },
                "storage_contract": {
                    "type": "string",
                    "description": "Номер договору зберігання шин (якщо клієнт має шини на зберіганні)",
                },
                "linked_order_id": {
                    "type": "string",
                    "description": "ID пов'язаного замовлення (якщо клієнт замовив шини)",
                },
            },
            "required": ["station_id", "date", "time", "customer_name", "customer_phone"],
        },
    },
    {
        "name": "cancel_fitting",
        "description": (
            "Скасувати або перенести запис на шиномонтаж. "
            "Використовуй, коли клієнт хоче скасувати або змінити час запису."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "booking_id": {
                    "type": "string",
                    "description": "ID запису на шиномонтаж",
                },
                "action": {
                    "type": "string",
                    "enum": ["cancel", "reschedule"],
                    "description": "Дія: скасувати або перенести",
                },
                "new_date": {
                    "type": "string",
                    "description": "Нова дата (YYYY-MM-DD, тільки для перенесення)",
                },
                "new_time": {
                    "type": "string",
                    "description": "Новий час (HH:MM, тільки для перенесення)",
                },
            },
            "required": ["booking_id", "action"],
        },
    },
    {
        "name": "get_fitting_price",
        "description": (
            "Дізнатися вартість шиномонтажу. Використовуй, коли клієнт запитує про ціну монтажу."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tire_diameter": {
                    "type": "integer",
                    "description": "Діаметр шин у дюймах (наприклад, 16, 17)",
                },
                "station_id": {
                    "type": "string",
                    "description": "ID точки шиномонтажу (для конкретних цін)",
                },
                "service_type": {
                    "type": "string",
                    "enum": ["tire_change", "balancing", "full_service"],
                    "description": "Тип послуги",
                },
            },
            "required": ["tire_diameter"],
        },
    },
    {
        "name": "get_customer_bookings",
        "description": (
            "Перевірити існуючі записи клієнта на шиномонтаж за номером телефону. "
            "Використовуй, коли клієнт запитує про свої записи або хоче перевірити бронювання."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {
                    "type": "string",
                    "description": "Номер телефону (0XXXXXXXXX)",
                },
                "station_id": {
                    "type": "string",
                    "description": "ID станції (опціонально)",
                },
            },
            "required": ["phone"],
        },
    },
    {
        "name": "search_knowledge_base",
        "description": (
            "Пошук по базі знань магазину: акції, доставка, оплата, повернення, "
            "гарантія, бренди, порівняння, FAQ. МОЖНА викликати КІЛЬКА РАЗІВ."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Пошуковий запит (питання клієнта)",
                },
                "category": {
                    "type": "string",
                    "enum": CATEGORY_VALUES,
                    "description": "Категорія пошуку для точнішого результату",
                },
            },
            "required": ["query"],
        },
    },
]

# All tools for the agent (MVP + Orders + Fitting/Knowledge)
ALL_TOOLS = MVP_TOOLS + ORDER_TOOLS + FITTING_TOOLS
