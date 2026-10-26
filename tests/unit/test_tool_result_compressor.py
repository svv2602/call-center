"""Unit tests for tool result compressor."""

from __future__ import annotations

import json
from typing import Any

from src.agent.tool_result_compressor import compress_tool_result
from src.store_client.client import StoreClient


class TestCompressToolResult:
    """Tests for compress_tool_result()."""

    def test_non_dict_returns_str(self) -> None:
        assert compress_tool_result("search_tires", "plain string") == "plain string"
        assert compress_tool_result("search_tires", 42) == "42"

    def test_unknown_tool_returns_compact_json(self) -> None:
        data = {"foo": "bar", "extra": 123}
        result = compress_tool_result("transfer_to_operator", data)
        assert '"foo":"bar"' in result
        assert '"extra":123' in result

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

    def test_fitting_stations_keeps_phone_strips_services(self) -> None:
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
        assert "+380441234567" in result  # phone preserved for LLM to tell customer
        assert "Шевченківський" in result  # district is preserved for LLM
        assert "services" not in result
        assert "working_hours" not in result  # dropped to save tokens

    def test_pickup_points_strips_type_keeps_hints(self) -> None:
        data = {
            "points": [
                {
                    "id": "pp-1",
                    "address": "вул. Велика Васильківська 100",
                    "city": "Київ",
                    "type": "pickup",
                    "district": "Шевченківський",
                    "landmarks": "біля метро",
                },
            ]
        }
        result = compress_tool_result("get_pickup_points", data)
        assert "Київ" in result
        assert "Шевченківський" in result  # district preserved
        assert "біля метро" in result  # landmarks preserved
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

    def test_knowledge_truncates_at_300(self) -> None:
        """Knowledge content truncated at 300 chars to save tokens."""
        content_400 = "B" * 400
        data = {"articles": [{"title": "Test", "content": content_400}]}
        result = compress_tool_result("search_knowledge_base", data)
        assert "..." in result
        # Should contain first 300 chars + "..."
        assert "B" * 300 in result

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
                {
                    "id": "1",
                    "brand": "Michelin",
                    "model": "Primacy 4",
                    "size": "205/55 R16",
                    "price": 3200,
                    "in_stock": True,
                    "season": "summer",
                },
                {
                    "id": "2",
                    "brand": "Continental",
                    "model": "PremiumContact 6",
                    "size": "205/55 R16",
                    "price": 3500,
                    "in_stock": True,
                    "season": "summer",
                },
                {
                    "id": "3",
                    "brand": "Nokian",
                    "model": "Hakka Green 3",
                    "size": "205/55 R16",
                    "price": 2800,
                    "in_stock": True,
                    "season": "summer",
                },
                {
                    "id": "4",
                    "brand": "Bridgestone",
                    "model": "Turanza T005",
                    "size": "205/55 R16",
                    "price": 3100,
                    "in_stock": False,
                    "season": "summer",
                },
                {
                    "id": "5",
                    "brand": "Goodyear",
                    "model": "EfficientGrip",
                    "size": "205/55 R16",
                    "price": 2900,
                    "in_stock": True,
                    "season": "summer",
                },
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
                {
                    "id": "1",
                    "brand": "Michelin",
                    "model": "Primacy",
                    "size": "205/55",
                    "price": 3200,
                    "in_stock": True,
                    "season": "winter",
                    "sku": "MIC-123",
                },
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
        assert "true" in result
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
        result = compress_tool_result("book_fitting", data)
        assert '"booking_id":"b-1"' in result
        assert '"status":"confirmed"' in result


# ── Season, studs, XL and indices of each offered tyre (sales only) ──────


def _catalog_row(**over: Any) -> dict[str, Any]:
    """A row as ``StoreClient._query_tire_rows`` selects it."""
    row: dict[str, Any] = {
        "id": "0101",
        "brand": "Nokian",
        "model": "Hakkapeliitta R5",
        "size": "205/55 R16",
        "season": "winter",
        "price": 5120,
        "stock_quantity": 8,
        "runflat": False,
        "eu_fuel": None,
        "eu_wet": None,
        "eu_noise_db": None,
        "attr_studded": None,
        "attr_xl": None,
        "load_index": None,
        "speed_index": None,
        "desc_studded": False,
        "desc_studdable": False,
        "desc_xl": False,
    }
    row.update(over)
    return row


def _tyre_fixture() -> dict[str, Any]:
    """A ``search_tires`` result built by ``_tire_item`` from catalogue rows."""
    rows = [
        _catalog_row(attr_studded="ship", attr_xl=True, load_index="94", speed_index="T"),
        _catalog_row(
            id="0102",
            brand="Michelin",
            model="X-Ice North 4",
            price=6400,
            attr_studded="pod_ship",
            load_index="91",
            speed_index="T",
            desc_studded=True,
        ),
        _catalog_row(id="0103", brand="Kumho", model="WinterCraft WI51", price=3300),
        _catalog_row(id="0104", brand="Hankook", model="W330", price=3000, desc_studded=True),
    ]
    items = [StoreClient._tire_item(row) for row in rows]
    return {"total": len(items), "items": items}


#: ``compress_tool_result("search_tires", _tyre_fixture())`` on HEAD ``4ad037e``
#: (before the fields reached the model) — sales off must not move a byte.
_SALES_OFF_HEAD = (
    '{"total":4,"items":['
    '{"brand":"Nokian","model":"Hakkapeliitta R5","size":"205/55 R16","price":5120,'
    '"in_stock":true},'
    '{"brand":"Michelin","model":"X-Ice North 4","size":"205/55 R16","price":6400,'
    '"in_stock":true},'
    '{"brand":"Kumho","model":"WinterCraft WI51","size":"205/55 R16","price":3300,'
    '"in_stock":true}]}'
)


def test_sales_off_output_is_byte_identical() -> None:
    assert compress_tool_result("search_tires", _tyre_fixture()) == _SALES_OFF_HEAD
    assert (
        compress_tool_result("search_tires", _tyre_fixture(), sales_enabled=False)
        == _SALES_OFF_HEAD
    )


def test_sales_on_keeps_the_facts_of_each_shown_tyre() -> None:
    out = json.loads(compress_tool_result("search_tires", _tyre_fixture(), sales_enabled=True))
    items = out["items"]
    assert len(items) == 3
    assert [i.get("studded") for i in items] == ["studded", "studdable", "friction"]
    assert [i.get("season") for i in items] == ["winter"] * 3
    assert items[0]["xl"] is True
    assert "xl" not in items[1]
    assert "xl" not in items[2]
    assert [(i.get("load_index"), i.get("speed_index")) for i in items] == [
        ("94", "T"),
        ("91", "T"),
        (None, None),
    ]
    assert "load_index" not in items[2]
    assert "id" not in items[0]


def test_sales_on_summer_tyre_carries_no_studs_key() -> None:
    row = _catalog_row(season="summer", attr_studded="no_ship", desc_studded=True)
    result = {"total": 1, "items": [StoreClient._tire_item(row)]}
    item = json.loads(compress_tool_result("search_tires", result, sales_enabled=True))["items"][0]
    assert item["season"] == "summer"
    assert "studded" not in item


# ── load index below the caller's minimum (wave 7-Q) ─────────────────


def _below_fixture() -> dict[str, Any]:
    rows = [
        _catalog_row(id="l1", season="summer", load_index="95", speed_index="H"),
        _catalog_row(id="l2", season="summer", load_index="99", speed_index="H", attr_xl=True),
    ]
    items = [StoreClient._tire_item(row) for row in rows]
    return {
        "total": 2,
        "items": items,
        "load_index_below": {"required": 102, "shown": [95, 99]},
    }


def test_sales_on_load_index_below_reaches_the_model() -> None:
    out = json.loads(compress_tool_result("search_tires", _below_fixture(), sales_enabled=True))
    flag = out["load_index_below"]
    assert flag["required"] == 102
    assert flag["shown"] == [95, 99]
    assert "від 102" in flag["say"] and "95–99" in flag["say"]


def test_sales_on_single_shown_index_is_not_a_range() -> None:
    result = _below_fixture()
    result["load_index_below"] = {"required": 102, "shown": [95]}
    out = json.loads(compress_tool_result("search_tires", result, sales_enabled=True))
    say = out["load_index_below"]["say"]
    assert "у показаних індекс 95," in say
    assert "–" not in say


def test_sales_on_without_flag_has_no_load_index_key() -> None:
    result = _below_fixture()
    del result["load_index_below"]
    out = compress_tool_result("search_tires", result, sales_enabled=True)
    assert "load_index_below" not in out


def test_sales_off_load_index_flag_does_not_move_a_byte() -> None:
    flagged = _below_fixture()
    plain = _below_fixture()
    del plain["load_index_below"]
    assert compress_tool_result("search_tires", flagged) == compress_tool_result(
        "search_tires", plain
    )
    assert "load_index_below" not in compress_tool_result("search_tires", flagged)
