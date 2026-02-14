"""LLM agent tool definitions for Claude API tool_use.

Canonical tool names from doc/development/00-overview.md.
MVP tools: search_tires, check_availability, transfer_to_operator.
"""

from __future__ import annotations

# MVP tools — Claude API tool_use format
MVP_TOOLS = [
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
