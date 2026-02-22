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
        # Content truncated to 800 + "..."
        assert "..." in result
        assert len(result) < len(str(data))

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

    def test_search_tires_unchanged(self) -> None:
        """search_tires has no compressor — returns str(result)."""
        data = {"total": 5, "items": [{"id": "1", "brand": "Michelin"}]}
        assert compress_tool_result("search_tires", data) == str(data)

    def test_check_availability_unchanged(self) -> None:
        data = {"available": True, "quantity": 12, "price": 3200}
        assert compress_tool_result("check_availability", data) == str(data)

    def test_book_fitting_unchanged(self) -> None:
        data = {"booking_id": "b-1", "status": "confirmed"}
        assert compress_tool_result("book_fitting", data) == str(data)
