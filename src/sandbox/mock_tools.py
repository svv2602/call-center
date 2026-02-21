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
    "get_fitting_stations": {
        "stations": [
            {
                "id": "station-001",
                "name": "ШинСервіс Центральний",
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
        "station": "ШинСервіс Центральний",
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
    "search_knowledge_base": {
        "results": [
            {
                "title": "Порівняння Michelin vs Continental: що обрати?",
                "content": "Michelin Primacy 4+ — преміальна літня шина з відмінним гальмуванням на мокрій поверхні. "
                "Continental PremiumContact 6 — збалансований вибір з тихим ходом. "
                "Для міського водіння обидва бренди — чудовий вибір.",
                "category": "comparisons",
                "relevance": 0.92,
            }
        ],
    },
}


def build_mock_tool_router() -> ToolRouter:
    """Build a ToolRouter with mock handlers for all canonical tools."""
    router = ToolRouter()

    for tool_name, mock_data in MOCK_RESPONSES.items():

        async def _handler(
            _name: str = tool_name, _data: Any = mock_data, **_kwargs: object
        ) -> Any:
            return copy.deepcopy(_data)

        router.register(tool_name, _handler)

    return router
