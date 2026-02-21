"""Mock tool handlers for sandbox agent testing.

Returns realistic static data for all canonical tools so the sandbox
can operate without a live Store API connection.
"""

from __future__ import annotations

import copy
from typing import Any

from src.agent.agent import ToolRouter

MOCK_RESPONSES: dict[str, Any] = {
    "get_vehicle_tire_sizes": {
        "vehicle": {"brand": "Toyota", "model": "Camry", "year": 2022},
        "stock_sizes": [
            {"width": 205, "profile": 55, "diameter": 16, "label": "205/55 R16"},
            {"width": 215, "profile": 55, "diameter": 17, "label": "215/55 R17"},
        ],
        "alternative_sizes": [
            {"width": 225, "profile": 45, "diameter": 18, "label": "225/45 R18"},
        ],
    },
    "search_tires": {
        "items": [
            {
                "id": "tire-001",
                "brand": "Michelin",
                "model": "Primacy 4+",
                "size": "205/55 R16",
                "season": "summer",
                "price": 3200,
                "currency": "UAH",
                "in_stock": True,
            },
            {
                "id": "tire-002",
                "brand": "Continental",
                "model": "PremiumContact 6",
                "size": "205/55 R16",
                "season": "summer",
                "price": 2800,
                "currency": "UAH",
                "in_stock": True,
            },
            {
                "id": "tire-003",
                "brand": "Nokian",
                "model": "Hakkapeliitta R5",
                "size": "205/55 R16",
                "season": "winter",
                "price": 3500,
                "currency": "UAH",
                "in_stock": True,
            },
        ],
        "total": 3,
    },
    "check_availability": {
        "product_id": "tire-001",
        "available": True,
        "quantity": 12,
        "warehouse": "Київ, склад №1",
        "delivery_days": 1,
    },
    "transfer_to_operator": {
        "status": "transferring",
        "message": "З'єдную з оператором",
    },
    "get_order_status": {
        "orders": [
            {
                "order_id": "ORD-2026-0042",
                "status": "shipped",
                "items": [{"name": "Michelin Primacy 4+ 205/55 R16", "quantity": 4, "price": 3200}],
                "total": 12800,
                "created_at": "2026-02-15T10:30:00Z",
                "estimated_delivery": "2026-02-20",
                "tracking_number": "UA1234567890",
            }
        ],
    },
    "create_order_draft": {
        "order_id": "ORD-2026-0099",
        "status": "draft",
        "items": [
            {
                "product_id": "tire-001",
                "name": "Michelin Primacy 4+ 205/55 R16",
                "quantity": 4,
                "price": 3200,
            }
        ],
        "subtotal": 12800,
        "currency": "UAH",
    },
    "update_order_delivery": {
        "order_id": "ORD-2026-0099",
        "delivery_type": "delivery",
        "city": "Київ",
        "address": "вул. Хрещатик, 1",
        "delivery_cost": 150,
        "total": 12950,
        "estimated_delivery": "2026-02-22",
    },
    "confirm_order": {
        "order_id": "ORD-2026-0099",
        "status": "confirmed",
        "total": 12950,
        "payment_method": "cod",
        "message": "Замовлення підтверджено. Номер: ORD-2026-0099",
    },
    "get_pickup_points": {
        "total": 3,
        "points": [
            {
                "id": "000000054",
                "address": "вул. Академіка Заболотного 3",
                "type": "Стороння точка",
                "city": "Київ",
            },
            {
                "id": "000000052",
                "address": "вул. Богатирська 2-е",
                "type": "Стороння точка",
                "city": "Київ",
            },
            {
                "id": "000000020",
                "address": "вул. Кротова, 21К",
                "type": "ОСПП",
                "city": "Дніпро",
            },
        ],
    },
    "get_fitting_stations": {
        "stations": [
            {
                "id": "station-001",
                "name": "Твоя Шина Центральний",
                "address": "Київ, вул. Велика Васильківська, 100",
                "phone": "+380441234567",
                "rating": 4.8,
            },
            {
                "id": "station-002",
                "name": "АвтоШина Лівобережна",
                "address": "Київ, пр. Бажана, 12",
                "phone": "+380441234568",
                "rating": 4.5,
            },
        ],
    },
    "get_fitting_slots": {
        "station_id": "station-001",
        "slots": [
            {"date": "2026-02-20", "time": "09:00", "available": True},
            {"date": "2026-02-20", "time": "11:00", "available": True},
            {"date": "2026-02-20", "time": "14:00", "available": True},
            {"date": "2026-02-21", "time": "10:00", "available": True},
        ],
    },
    "book_fitting": {
        "booking_id": "FIT-2026-0015",
        "station": "Твоя Шина Центральний",
        "address": "Київ, вул. Велика Васильківська, 100",
        "date": "2026-02-20",
        "time": "09:00",
        "status": "confirmed",
    },
    "cancel_fitting": {
        "booking_id": "FIT-2026-0015",
        "status": "cancelled",
        "message": "Запис скасовано",
    },
    "get_fitting_price": {
        "tire_diameter": 16,
        "prices": {
            "tire_change": {"price": 600, "description": "Заміна 4 шин R16"},
            "balancing": {"price": 400, "description": "Балансування 4 коліс R16"},
            "full_service": {
                "price": 900,
                "description": "Повний сервіс R16 (заміна + балансування)",
            },
        },
        "currency": "UAH",
    },
    "search_knowledge_base": "dynamic",  # handled by _search_knowledge_mock
}


_KNOWLEDGE_BRAND_RESPONSES: dict[str, dict[str, Any]] = {
    "michelin": {
        "title": "Michelin — профіль бренду",
        "content": (
            "Michelin — французький преміум-бренд. "
            "Літні: Primacy 4+ (комфорт, мокра дорога), Pilot Sport 5 (спорт). "
            "Зимові: Alpin 6 (фрикційні, відмінне гальмування на снігу), "
            "X-Ice Snow (для суворих зим). Сильні сторони: найвищий ресурс, "
            "тиха їзда, гарантія Total Performance."
        ),
        "category": "brands",
        "relevance": 0.95,
    },
    "bridgestone": {
        "title": "Bridgestone — профіль бренду",
        "content": (
            "Bridgestone — японський преміум-бренд, найбільший виробник шин у світі. "
            "Літні: Turanza T005 (комфорт), Potenza Sport (спорт). "
            "Зимові: Blizzak LM005 (фрикційні, лідер на мокрій зимовій дорозі), "
            "Blizzak Ice (зчеплення на льоду). Сильні сторони: якість виготовлення, "
            "тихий хід, відмінні характеристики на мокрій дорозі."
        ),
        "category": "brands",
        "relevance": 0.95,
    },
    "continental": {
        "title": "Continental — профіль бренду",
        "content": (
            "Continental — німецький преміум-бренд. "
            "Літні: PremiumContact 6 (збалансований), SportContact 7 (спорт). "
            "Зимові: WinterContact TS 870 (фрикційні), IceContact 3 (шиповані). "
            "Сильні сторони: найкоротший гальмівний шлях, технологія ContiSeal."
        ),
        "category": "brands",
        "relevance": 0.95,
    },
    "nokian": {
        "title": "Nokian — профіль бренду",
        "content": (
            "Nokian — фінський бренд, спеціаліст із зимових шин. "
            "Зимові: Hakkapeliitta R5 (фрикційні, top-1), Hakkapeliitta 10 (шиповані). "
            "Літні: Hakka Green 3 (екологічні), Wetproof 1. "
            "Сильні сторони: найкращі зимові шини у світі, арамідні боковини."
        ),
        "category": "brands",
        "relevance": 0.95,
    },
}

_KNOWLEDGE_FALLBACK: dict[str, Any] = {
    "title": "Порівняння шин: загальні рекомендації",
    "content": (
        "При виборі шин враховуйте: сезон, розмір, стиль водіння та бюджет. "
        "Преміум-бренди (Michelin, Continental, Bridgestone) — найкращі характеристики, "
        "але вища ціна. Середній сегмент (Hankook, Kumho, Nexen) — оптимальне "
        "співвідношення ціна/якість. Бюджетні (Triangle, Sailun) — прийнятна якість "
        "за мінімальну ціну."
    ),
    "category": "comparisons",
    "relevance": 0.80,
}


async def _search_knowledge_mock(**kwargs: object) -> dict[str, Any]:
    """Context-aware mock for search_knowledge_base.

    Returns brand-specific data when query mentions a known brand,
    or a generic comparison result otherwise.
    """
    query = str(kwargs.get("query", "")).lower()
    category = str(kwargs.get("category", ""))

    # Check if query mentions a specific brand
    results: list[dict[str, Any]] = []
    for brand_key, brand_data in _KNOWLEDGE_BRAND_RESPONSES.items():
        if brand_key in query:
            results.append(copy.deepcopy(brand_data))

    if results:
        return {"results": results}

    # Category-based fallback
    if category == "faq":
        return {
            "results": [
                {
                    "title": "Що таке індекс навантаження та швидкості?",
                    "content": (
                        "Індекс навантаження — максимальна вага на одну шину (91 = 615 кг). "
                        "Індекс швидкості — максимальна швидкість (T = 190 км/год, H = 210, V = 240). "
                        "XL (Extra Load) — посилена конструкція для більшого навантаження."
                    ),
                    "category": "faq",
                    "relevance": 0.88,
                }
            ]
        }

    if category == "guides":
        return {
            "results": [
                {
                    "title": "Як обрати зимові шини: повний гайд",
                    "content": (
                        "Фрикційні (липучки) — для міста та м'якої зими. "
                        "Шиповані — для льоду та суворої зими. "
                        "Маркування 3PMSF (сніжинка) — обов'язкове для справжніх зимових шин."
                    ),
                    "category": "guides",
                    "relevance": 0.90,
                }
            ]
        }

    # Default fallback
    return {"results": [copy.deepcopy(_KNOWLEDGE_FALLBACK)]}


def build_mock_tool_router() -> ToolRouter:
    """Build a ToolRouter with mock handlers for all canonical tools."""
    router = ToolRouter()

    for tool_name, mock_data in MOCK_RESPONSES.items():
        if mock_data == "dynamic":
            continue  # handled separately below

        async def _handler(
            _name: str = tool_name, _data: Any = mock_data, **_kwargs: object
        ) -> Any:
            return copy.deepcopy(_data)

        router.register(tool_name, _handler)

    # Register dynamic handlers
    router.register("search_knowledge_base", _search_knowledge_mock)

    return router
