"""Unit tests for tool result compressor."""

from __future__ import annotations

from src.agent.tool_result_compressor import compress_tool_result


class TestCompressToolResult:
    """Tests for compress_tool_result()."""

    def test_non_dict_returns_str(self) -> None:
        assert compress_tool_result("search_tires", "plain string") == "plain string"
        assert compress_tool_result("search_tires", 42) == "42"

    def test_unknown_tool_returns_str(self) -> None:
        data = {"foo": "bar", "extra": 123}
        assert compress_tool_result("transfer_to_operator", data) == str(data)

    def test_vehicle_sizes_strips_years_when_long(self) -> None:
        data = {
            "found": True,
            "brand": "Toyota",
            "model": "Camry",
            "years": list(range(2000, 2025)),
            "stock_sizes": ["205/55 R16"],
            "acceptable_sizes": ["215/55 R16"],
        }
        result = compress_tool_result("get_vehicle_tire_sizes", data)
        assert "Toyota" in result
        assert "stock_sizes" in result
        assert "2000" not in result  # years stripped

    def test_vehicle_sizes_keeps_short_years(self) -> None:
        data = {
            "found": True,
            "brand": "Kia",
            "model": "Sportage",
            "years": [2022, 2023, 2024],
            "stock_sizes": ["235/60 R18"],
            "acceptable_sizes": [],
        }
        result = compress_tool_result("get_vehicle_tire_sizes", data)
        assert "2022" in result

    def test_order_status_strips_id_and_items_summary(self) -> None:
        data = {
            "orders": [
                {
                    "id": "uuid-123",
                    "order_number": "ORD-001",
                    "status": "shipped",
                    "status_label": "Відправлено",
                    "total": 12800,
                    "estimated_delivery": "2026-02-25",
                    "items_summary": "4x Michelin 205/55 R16",
                },
            ]
        }
        result = compress_tool_result("get_order_status", data)
        assert "ORD-001" in result
        assert "uuid-123" not in result
        assert "items_summary" not in result

    def test_order_draft_keeps_essential_fields(self) -> None:
        data = {
            "order_id": "draft-1",
            "order_number": "ORD-002",
            "status": "draft",
            "total": 6400,
            "items": [
                {
                    "product_id": "SKU-123",
                    "name": "Michelin 205/55 R16",
                    "quantity": 4,
                    "price": 1600,
                    "total": 6400,
                    "sku": "MIC-205-55-16",
                },
            ],
            "created_at": "2026-02-22T10:00:00",
        }
        result = compress_tool_result("create_order_draft", data)
        assert "draft-1" in result
        assert "Michelin" in result
        assert "SKU-123" not in result  # product_id stripped from items
        assert "created_at" not in result

    def test_fitting_stations_strips_phone_and_services(self) -> None:
        data = {
            "stations": [
                {
                    "id": "st-1",
                    "name": "СТО Центр",
                    "address": "вул. Хрещатик 1",
                    "working_hours": "08:00-20:00",
                    "phone": "+380441234567",
                    "district": "Шевченківський",
                    "services": ["tire_change", "balancing"],
                },
            ]
        }
        result = compress_tool_result("get_fitting_stations", data)
        assert "СТО Центр" in result
        assert "+380441234567" not in result
        assert "district" not in result

    def test_pickup_points_strips_type(self) -> None:
        data = {
            "points": [
                {
                    "id": "pp-1",
                    "address": "вул. Велика Васильківська 100",
                    "city": "Київ",
                    "type": "pickup",
                },
            ]
        }
        result = compress_tool_result("get_pickup_points", data)
        assert "Київ" in result
        assert "'type'" not in result

    def test_knowledge_truncates_long_content(self) -> None:
        long_content = "A" * 1500
        data = {
            "articles": [
                {
                    "article_id": "art-1",
                    "title": "Доставка",
                    "content": long_content,
                    "category": "delivery",
                    "relevance": 0.95,
                },
            ]
        }
        result = compress_tool_result("search_knowledge_base", data)
        assert "Доставка" in result
        assert "article_id" not in result
        assert "category" not in result
        assert "relevance" not in result
        # Content truncated to 500 + "..."
        assert "..." in result
        assert len(result) < len(str(data))

    def test_knowledge_truncates_at_500(self) -> None:
        """Knowledge content now truncated at 500 chars (down from 800)."""
        content_600 = "B" * 600
        data = {
            "articles": [{"title": "Test", "content": content_600}]
        }
        result = compress_tool_result("search_knowledge_base", data)
        assert "..." in result
        # Should contain first 500 chars + "..."
        assert "B" * 500 in result

    def test_knowledge_keeps_short_content(self) -> None:
        data = {
            "articles": [
                {
                    "title": "FAQ",
                    "content": "Short answer",
                },
            ]
        }
        result = compress_tool_result("search_knowledge_base", data)
        assert "Short answer" in result
        assert "..." not in result

    def test_search_tires_limits_to_3_items(self) -> None:
        """search_tires compressor keeps top 3 results with essential fields."""
        data = {
            "total": 5,
            "items": [
                {"id": "1", "brand": "Michelin", "model": "Primacy 4", "size": "205/55 R16", "price": 3200, "in_stock": True, "season": "summer"},
                {"id": "2", "brand": "Continental", "model": "PremiumContact 6", "size": "205/55 R16", "price": 3500, "in_stock": True, "season": "summer"},
                {"id": "3", "brand": "Nokian", "model": "Hakka Green 3", "size": "205/55 R16", "price": 2800, "in_stock": True, "season": "summer"},
                {"id": "4", "brand": "Bridgestone", "model": "Turanza T005", "size": "205/55 R16", "price": 3100, "in_stock": False, "season": "summer"},
                {"id": "5", "brand": "Goodyear", "model": "EfficientGrip", "size": "205/55 R16", "price": 2900, "in_stock": True, "season": "summer"},
            ],
        }
        result = compress_tool_result("search_tires", data)
        assert "Michelin" in result
        assert "Continental" in result
        assert "Nokian" in result
        assert "Bridgestone" not in result  # 4th item dropped
        assert "Goodyear" not in result  # 5th item dropped
        assert "'id'" not in result  # id stripped
        assert "season" not in result  # season stripped
        assert "total" in result  # total preserved

    def test_search_tires_essential_fields_only(self) -> None:
        data = {
            "total": 1,
            "items": [
                {"id": "1", "brand": "Michelin", "model": "Primacy", "size": "205/55", "price": 3200, "in_stock": True, "season": "winter", "sku": "MIC-123"},
            ],
        }
        result = compress_tool_result("search_tires", data)
        assert "brand" in result
        assert "model" in result
        assert "price" in result
        assert "sku" not in result

    def test_check_availability_compressed(self) -> None:
        data = {
            "available": True,
            "price": 3200,
            "stock_quantity": 12,
            "warehouses": [
                {"id": "w1", "name": "Центральний"},
                {"id": "w2", "name": "Лівобережний"},
                {"id": "w3", "name": "Правобережний"},
                {"id": "w4", "name": "Оболонь"},
            ],
            "product_id": "p-123",
            "updated_at": "2026-02-22T10:00:00",
        }
        result = compress_tool_result("check_availability", data)
        assert "True" in result
        assert "3200" in result
        assert "stock_quantity" in result
        # Warehouses trimmed to first 3
        assert "Оболонь" not in result
        # Internal fields dropped
        assert "product_id" not in result
        assert "updated_at" not in result

    def test_fitting_slots_compressed(self) -> None:
        data = {
            "station_id": "st-1",
            "slots": [
                {"slot_id": "s1", "date": "2026-02-25", "time": "10:00", "available": True},
                {"slot_id": "s2", "date": "2026-02-25", "time": "11:00", "available": False},
            ],
        }
        result = compress_tool_result("get_fitting_slots", data)
        assert "2026-02-25" in result
        assert "10:00" in result
        assert "slot_id" not in result  # internal ID dropped
        assert "station_id" in result  # top-level field preserved

    def test_book_fitting_unchanged(self) -> None:
        data = {"booking_id": "b-1", "status": "confirmed"}
        assert compress_tool_result("book_fitting", data) == str(data)
