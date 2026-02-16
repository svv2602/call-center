"""Unit tests for Store API client."""

from __future__ import annotations

from src.store_client.client import StoreAPIError, StoreClient


class TestStoreClientFormatting:
    """Test response formatting for LLM consumption."""

    def test_format_tire_results_limits_to_5(self) -> None:
        data = {
            "total": 10,
            "items": [{"id": str(i), "name": f"Tire {i}", "price": 1000 + i} for i in range(10)],
        }
        result = StoreClient._format_tire_results(data)
        assert len(result["items"]) == 5
        assert result["total"] == 10

    def test_format_tire_results_strips_fields(self) -> None:
        data = {
            "items": [
                {
                    "id": "1",
                    "name": "Michelin Primacy 4",
                    "brand": "Michelin",
                    "size": "205/55 R16",
                    "season": "summer",
                    "price": 3200,
                    "in_stock": True,
                    "image_url": "https://example.com/img.jpg",
                    "description": "Long description...",
                }
            ]
        }
        result = StoreClient._format_tire_results(data)
        item = result["items"][0]
        assert "image_url" not in item
        assert "description" not in item
        assert item["brand"] == "Michelin"
        assert item["price"] == 3200

    def test_format_empty_results(self) -> None:
        result = StoreClient._format_tire_results({"items": []})
        assert result["items"] == []
        assert result["total"] == 0


class TestStoreAPIError:
    """Test StoreAPIError."""

    def test_error_message(self) -> None:
        err = StoreAPIError(404, "Not found")
        assert err.status == 404
        assert "404" in str(err)
        assert "Not found" in str(err)

    def test_error_is_exception(self) -> None:
        err = StoreAPIError(500, "Internal error")
        assert isinstance(err, Exception)
