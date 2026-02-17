"""Unit tests for vehicles browser API."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth import create_jwt
from src.api.vehicles import router

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


def _make_mock_row(data: dict[str, Any]) -> MagicMock:
    row = MagicMock()
    row._mapping = data
    for k, v in data.items():
        setattr(row, k, v)
    return row


def _make_mock_engine(
    rows: list[dict[str, Any]],
    *,
    multi_results: list[list[dict[str, Any]]] | None = None,
) -> tuple[Any, AsyncMock]:
    mock_conn = AsyncMock()

    if multi_results is not None:
        results = []
        for result_rows in multi_results:
            mock_result = MagicMock()
            mock_result.__iter__ = lambda self, r=result_rows: iter(
                [_make_mock_row(row) for row in r]
            )
            mock_result.first.return_value = _make_mock_row(result_rows[0]) if result_rows else None
            mock_result.scalar.return_value = len(result_rows)
            results.append(mock_result)
        mock_conn.execute = AsyncMock(side_effect=results)
    else:
        mock_result = MagicMock()
        mock_result.__iter__ = lambda self: iter([_make_mock_row(r) for r in rows])
        mock_result.first.return_value = _make_mock_row(rows[0]) if rows else None
        mock_result.scalar.return_value = len(rows)
        mock_conn.execute = AsyncMock(return_value=mock_result)

    mock_engine = MagicMock()

    @asynccontextmanager
    async def _begin() -> AsyncIterator[AsyncMock]:
        yield mock_conn

    mock_engine.begin = _begin
    return mock_engine, mock_conn


def _admin_token() -> str:
    return create_jwt({"sub": "admin", "role": "admin"}, "test-secret")


def _analyst_token() -> str:
    return create_jwt({"sub": "analyst", "role": "analyst"}, "test-secret")


def _operator_token() -> str:
    return create_jwt({"sub": "op1", "role": "operator"}, "test-secret")


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


_SAMPLE_META = {
    "brand_count": 227,
    "model_count": 5902,
    "kit_count": 304000,
    "tire_size_count": 1200000,
    "imported_at": "2026-02-15T12:00:00",
    "source_path": "/data/vehicles.csv",
}

_SAMPLE_BRAND = {"id": 1, "name": "Toyota", "model_count": 42}
_SAMPLE_MODEL = {"id": 10, "name": "Camry", "kit_count": 15}
_SAMPLE_KIT = {
    "id": 100,
    "year": 2024,
    "name": "XLE V6",
    "pcd": 114.3,
    "bolt_count": 5,
    "dia": 60.1,
    "bolt_size": "M12x1.5",
    "tire_size_count": 4,
}
_SAMPLE_TIRE_SIZE = {
    "id": 1000,
    "width": 215,
    "height": 55,
    "diameter": 17,
    "type": 1,
    "axle": 0,
    "axle_group": None,
}


class TestGetStats:
    @patch("src.api.auth.get_settings")
    @patch("src.api.vehicles._get_engine")
    def test_stats_returns_metadata(
        self, mock_engine_fn: MagicMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        mock_engine, _ = _make_mock_engine([_SAMPLE_META])
        mock_engine_fn.return_value = mock_engine

        response = client.get(
            "/admin/vehicles/stats",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["brand_count"] == 227
        assert data["model_count"] == 5902

    @patch("src.api.auth.get_settings")
    @patch("src.api.vehicles._get_engine")
    def test_stats_as_analyst(
        self, mock_engine_fn: MagicMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        mock_engine, _ = _make_mock_engine([_SAMPLE_META])
        mock_engine_fn.return_value = mock_engine

        response = client.get(
            "/admin/vehicles/stats",
            headers={"Authorization": f"Bearer {_analyst_token()}"},
        )
        assert response.status_code == 200

    @patch("src.api.auth.get_settings")
    def test_stats_operator_forbidden(self, mock_settings: MagicMock, client: TestClient) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        response = client.get(
            "/admin/vehicles/stats",
            headers={"Authorization": f"Bearer {_operator_token()}"},
        )
        assert response.status_code == 403

    def test_stats_no_auth(self, client: TestClient) -> None:
        response = client.get("/admin/vehicles/stats")
        assert response.status_code == 401

    @patch("src.api.auth.get_settings")
    @patch("src.api.vehicles._get_engine")
    def test_stats_fallback_when_no_metadata(
        self, mock_engine_fn: MagicMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        """When vehicle_db_metadata has no rows, fallback to counting tables."""
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        fallback_counts = {
            "brand_count": 10,
            "model_count": 50,
            "kit_count": 200,
            "tire_size_count": 800,
        }
        # First query (metadata) returns no rows, second (counts) returns data
        mock_engine, _ = _make_mock_engine([], multi_results=[[], [fallback_counts]])
        mock_engine_fn.return_value = mock_engine

        response = client.get(
            "/admin/vehicles/stats",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["brand_count"] == 10
        assert data["imported_at"] is None


class TestListBrands:
    @patch("src.api.auth.get_settings")
    @patch("src.api.vehicles._get_engine")
    def test_list_brands(
        self, mock_engine_fn: MagicMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        mock_engine, _ = _make_mock_engine([], multi_results=[[_SAMPLE_BRAND], [_SAMPLE_BRAND]])
        mock_engine_fn.return_value = mock_engine

        response = client.get(
            "/admin/vehicles/brands?limit=10",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["name"] == "Toyota"

    @patch("src.api.auth.get_settings")
    @patch("src.api.vehicles._get_engine")
    def test_list_brands_with_search(
        self, mock_engine_fn: MagicMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        mock_engine, mock_conn = _make_mock_engine(
            [], multi_results=[[_SAMPLE_BRAND], [_SAMPLE_BRAND]]
        )
        mock_engine_fn.return_value = mock_engine

        response = client.get(
            "/admin/vehicles/brands?search=toy",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert response.status_code == 200
        # Verify LIKE param was passed
        assert any("toy" in str(a) for a in mock_conn.execute.call_args_list)

    @patch("src.api.auth.get_settings")
    @patch("src.api.vehicles._get_engine")
    def test_list_brands_empty(
        self, mock_engine_fn: MagicMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        mock_engine, _ = _make_mock_engine([], multi_results=[[], []])
        mock_engine_fn.return_value = mock_engine

        response = client.get(
            "/admin/vehicles/brands",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert response.status_code == 200
        assert response.json()["total"] == 0
        assert response.json()["items"] == []


class TestListModels:
    @patch("src.api.auth.get_settings")
    @patch("src.api.vehicles._get_engine")
    def test_list_models_for_brand(
        self, mock_engine_fn: MagicMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        brand_row = {"id": 1, "name": "Toyota"}
        mock_engine, _ = _make_mock_engine(
            [],
            multi_results=[[brand_row], [_SAMPLE_MODEL], [_SAMPLE_MODEL]],
        )
        mock_engine_fn.return_value = mock_engine

        response = client.get(
            "/admin/vehicles/brands/1/models",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["brand"]["name"] == "Toyota"
        assert len(data["items"]) == 1
        assert data["items"][0]["name"] == "Camry"

    @patch("src.api.auth.get_settings")
    @patch("src.api.vehicles._get_engine")
    def test_models_brand_not_found(
        self, mock_engine_fn: MagicMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        mock_engine, _ = _make_mock_engine([])
        mock_engine_fn.return_value = mock_engine

        response = client.get(
            "/admin/vehicles/brands/99999/models",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert response.status_code == 404


class TestListKits:
    @patch("src.api.auth.get_settings")
    @patch("src.api.vehicles._get_engine")
    def test_list_kits_for_model(
        self, mock_engine_fn: MagicMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        model_row = {"id": 10, "name": "Camry", "brand_id": 1, "brand_name": "Toyota"}
        mock_engine, _ = _make_mock_engine(
            [],
            multi_results=[[model_row], [_SAMPLE_KIT], [_SAMPLE_KIT]],
        )
        mock_engine_fn.return_value = mock_engine

        response = client.get(
            "/admin/vehicles/models/10/kits",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["model"]["name"] == "Camry"
        assert data["model"]["brand_name"] == "Toyota"
        assert len(data["items"]) == 1
        assert data["items"][0]["year"] == 2024

    @patch("src.api.auth.get_settings")
    @patch("src.api.vehicles._get_engine")
    def test_kits_model_not_found(
        self, mock_engine_fn: MagicMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        mock_engine, _ = _make_mock_engine([])
        mock_engine_fn.return_value = mock_engine

        response = client.get(
            "/admin/vehicles/models/99999/kits",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert response.status_code == 404

    @patch("src.api.auth.get_settings")
    @patch("src.api.vehicles._get_engine")
    def test_kits_with_year_filter(
        self, mock_engine_fn: MagicMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        model_row = {"id": 10, "name": "Camry", "brand_id": 1, "brand_name": "Toyota"}
        mock_engine, _ = _make_mock_engine(
            [],
            multi_results=[[model_row], [_SAMPLE_KIT], [_SAMPLE_KIT]],
        )
        mock_engine_fn.return_value = mock_engine

        response = client.get(
            "/admin/vehicles/models/10/kits?year=2024",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert response.status_code == 200


class TestListTireSizes:
    @patch("src.api.auth.get_settings")
    @patch("src.api.vehicles._get_engine")
    def test_tire_sizes_for_kit(
        self, mock_engine_fn: MagicMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        kit_row = {
            "id": 100,
            "year": 2024,
            "name": "XLE V6",
            "pcd": 114.3,
            "bolt_count": 5,
            "dia": 60.1,
            "bolt_size": "M12x1.5",
            "model_name": "Camry",
            "brand_id": 1,
            "brand_name": "Toyota",
        }
        mock_engine, _ = _make_mock_engine(
            [],
            multi_results=[[kit_row], [_SAMPLE_TIRE_SIZE]],
        )
        mock_engine_fn.return_value = mock_engine

        response = client.get(
            "/admin/vehicles/kits/100/tire-sizes",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["kit"]["brand_name"] == "Toyota"
        assert len(data["items"]) == 1
        assert data["items"][0]["width"] == 215

    @patch("src.api.auth.get_settings")
    @patch("src.api.vehicles._get_engine")
    def test_tire_sizes_kit_not_found(
        self, mock_engine_fn: MagicMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        mock_engine, _ = _make_mock_engine([])
        mock_engine_fn.return_value = mock_engine

        response = client.get(
            "/admin/vehicles/kits/99999/tire-sizes",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert response.status_code == 404


class TestImportVehicleDb:
    @patch("src.api.auth.get_settings")
    @patch("src.api.vehicles._get_engine")
    @patch("scripts.import_vehicle_db.import_data", new_callable=AsyncMock)
    def test_import_success(
        self,
        mock_import: AsyncMock,
        mock_engine_fn: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"

        # Create expected CSV files in tmp_path
        for name in [
            "test_table_car2_brand.csv",
            "test_table_car2_model.csv",
            "test_table_car2_kit.csv",
            "test_table_car2_kit_tyre_size.csv",
        ]:
            (tmp_path / name).write_text("id\n1\n")

        meta_row = {
            "brand_count": 10,
            "model_count": 50,
            "kit_count": 200,
            "tire_size_count": 800,
            "imported_at": "2026-02-17T12:00:00",
            "source_path": str(tmp_path),
        }
        mock_engine, _ = _make_mock_engine([meta_row])
        mock_engine_fn.return_value = mock_engine

        response = client.post(
            "/admin/vehicles/import",
            json={"csv_dir": str(tmp_path)},
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["brand_count"] == 10
        assert data["tire_size_count"] == 800
        mock_import.assert_awaited_once()

    @patch("src.api.auth.get_settings")
    def test_import_missing_dir(
        self,
        mock_settings: MagicMock,
        client: TestClient,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"

        response = client.post(
            "/admin/vehicles/import",
            json={"csv_dir": "/nonexistent/path/to/csvs"},
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert response.status_code == 400
        assert "not found" in response.json()["detail"].lower()

    @patch("src.api.auth.get_settings")
    def test_import_operator_forbidden(
        self,
        mock_settings: MagicMock,
        client: TestClient,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"

        response = client.post(
            "/admin/vehicles/import",
            json={"csv_dir": "/some/path"},
            headers={"Authorization": f"Bearer {_operator_token()}"},
        )
        assert response.status_code == 403
