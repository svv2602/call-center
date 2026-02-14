"""LLM agent tool definitions for Claude API tool_use.

Canonical tool names from doc/development/00-overview.md.
MVP tools: search_tires, check_availability, transfer_to_operator.
Phase 2 tools: get_order_status, create_order_draft, update_order_delivery, confirm_order.
"""

from __future__ import annotations

# MVP tools — Claude API tool_use format
MVP_TOOLS: list[dict] = [  # type: ignore[type-arg]
    {
        "name": "search_tires",
        "description": (
            "Пошук шин у каталозі магазину за параметрами автомобіля або розміром. "
            "Використовуй, коли клієнт хоче підібрати шини."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "vehicle_make": {
                    "type": "string",
                    "description": "Марка автомобіля (наприклад, Toyota, BMW)",
                },
                "vehicle_model": {
                    "type": "string",
                    "description": "Модель автомобіля (наприклад, Camry, X5)",
                },
                "vehicle_year": {
                    "type": "integer",
                    "description": "Рік випуску автомобіля",
                },
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
]

# All tools for the agent (MVP + Orders)
ALL_TOOLS = MVP_TOOLS + ORDER_TOOLS
