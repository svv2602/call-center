"""Unit tests for get_vehicle_tire_sizes tool and handler."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.tools import ALL_TOOLS, MVP_TOOLS
from src.store_client.client import StoreClient, _format_tire_size


class TestToolDefinition:
    """Test get_vehicle_tire_sizes tool definition."""

    def test_tool_in_mvp_tools(self) -> None:
        names = {t["name"] for t in MVP_TOOLS}
        assert "get_vehicle_tire_sizes" in names

    def test_tool_in_all_tools(self) -> None:
        names = {t["name"] for t in ALL_TOOLS}
        assert "get_vehicle_tire_sizes" in names

    def test_tool_schema(self) -> None:
        tool = next(t for t in MVP_TOOLS if t["name"] == "get_vehicle_tire_sizes")
        props = tool["input_schema"]["properties"]
        assert "brand" in props
        assert "model" in props
        assert "year" in props
        assert props["year"]["type"] == "integer"

    def test_required_fields(self) -> None:
        tool = next(t for t in MVP_TOOLS if t["name"] == "get_vehicle_tire_sizes")
        assert set(tool["input_schema"]["required"]) == {"brand", "model"}

    def test_year_is_optional(self) -> None:
        tool = next(t for t in MVP_TOOLS if t["name"] == "get_vehicle_tire_sizes")
        assert "year" not in tool["input_schema"]["required"]


class TestFormatTireSize:
    """Test _format_tire_size helper."""

    def test_basic_format(self) -> None:
        row = {"width": 235, "height": 65, "diameter": Decimal("17.0"), "axle": 0}
        assert _format_tire_size(row) == "235/65 R17"

    def test_half_diameter(self) -> None:
        row = {"width": 235, "height": 75, "diameter": Decimal("17.5"), "axle": 0}
        assert _format_tire_size(row) == "235/75 R17.5"

    def test_front_axle(self) -> None:
        row = {"width": 225, "height": 40, "diameter": Decimal("19.0"), "axle": 1}
        assert _format_tire_size(row) == "225/40 R19 (перед)"

    def test_rear_axle(self) -> None:
        row = {"width": 255, "height": 35, "diameter": Decimal("20.0"), "axle": 2}
        assert _format_tire_size(row) == "255/35 R20 (зад)"

    def test_no_axle_key(self) -> None:
        row = {"width": 205, "height": 55, "diameter": Decimal("16.0")}
        assert _format_tire_size(row) == "205/55 R16"


class TestHandlerNoDb:
    """Test handler when db_engine is None."""

    @pytest.mark.asyncio
    async def test_no_db_returns_unavailable(self) -> None:
        client = StoreClient(base_url="http://localhost", api_key="test", db_engine=None)
        result = await client.get_vehicle_tire_sizes(brand="Kia", model="Sportage")
        assert result["found"] is False
        assert "недоступна" in result["message"]


class TestHandlerWithMockDb:
    """Test handler with mocked database."""

    def _make_client_with_mock_engine(self) -> tuple[StoreClient, AsyncMock]:
        mock_engine = MagicMock()
        client = StoreClient(base_url="http://localhost", api_key="test", db_engine=mock_engine)
        mock_conn = AsyncMock()
        mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)
        return client, mock_conn

    @pytest.mark.asyncio
    async def test_brand_not_found(self) -> None:
        client, mock_conn = self._make_client_with_mock_engine()

        # Brand exact match returns nothing
        brand_result = MagicMock()
        brand_result.mappings.return_value.first.return_value = None

        # Brand fuzzy also returns nothing
        mock_conn.execute = AsyncMock(side_effect=[brand_result, brand_result])

        result = await client.get_vehicle_tire_sizes(brand="UnknownBrand", model="X")
        assert result["found"] is False
        assert "UnknownBrand" in result["message"]

    @pytest.mark.asyncio
    async def test_model_not_found(self) -> None:
        client, mock_conn = self._make_client_with_mock_engine()

        brand_row = {"id": 1, "name": "Kia"}
        brand_result = MagicMock()
        brand_result.mappings.return_value.first.return_value = brand_row

        model_result_exact = MagicMock()
        model_result_exact.mappings.return_value.first.return_value = None
        model_result_fuzzy = MagicMock()
        model_result_fuzzy.mappings.return_value.first.return_value = None

        mock_conn.execute = AsyncMock(
            side_effect=[brand_result, model_result_exact, model_result_fuzzy]
        )

        result = await client.get_vehicle_tire_sizes(brand="Kia", model="UnknownModel")
        assert result["found"] is False
        assert result["brand"] == "Kia"

    @pytest.mark.asyncio
    async def test_successful_lookup(self) -> None:
        client, mock_conn = self._make_client_with_mock_engine()

        brand_row = {"id": 1, "name": "Kia"}
        brand_result = MagicMock()
        brand_result.mappings.return_value.first.return_value = brand_row

        model_row = {"id": 10, "name": "Sportage"}
        model_result = MagicMock()
        model_result.mappings.return_value.first.return_value = model_row

        years_result = MagicMock()
        years_result.__iter__ = MagicMock(return_value=iter([(2022,), (2021,), (2020,)]))

        tire_rows = [
            {"width": 235, "height": 65, "diameter": Decimal("17.0"), "type": 1, "axle": 0},
            {"width": 235, "height": 60, "diameter": Decimal("18.0"), "type": 1, "axle": 0},
            {"width": 245, "height": 50, "diameter": Decimal("19.0"), "type": 2, "axle": 0},
        ]
        sizes_result = MagicMock()
        sizes_result.mappings.return_value.all.return_value = tire_rows

        mock_conn.execute = AsyncMock(
            side_effect=[brand_result, model_result, years_result, sizes_result]
        )

        result = await client.get_vehicle_tire_sizes(brand="Kia", model="Sportage", year=2022)
        assert result["found"] is True
        assert result["brand"] == "Kia"
        assert result["model"] == "Sportage"
        assert "235/65 R17" in result["stock_sizes"]
        assert "235/60 R18" in result["stock_sizes"]
        assert "245/50 R19" in result["acceptable_sizes"]
        assert result["selected_year"] == 2022
