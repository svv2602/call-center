"""Unit tests for operators API."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth import create_jwt
from src.api.operators import router

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


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
            mock_result.first.return_value = (
                _make_mock_row(result_rows[0]) if result_rows else None
            )
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


def _operator_token() -> str:
    return create_jwt({"sub": "op1", "role": "operator"}, "test-secret")


def _analyst_token() -> str:
    return create_jwt({"sub": "analyst", "role": "analyst"}, "test-secret")


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


_SAMPLE_OPERATOR = {
    "id": "op-uuid-1",
    "name": "John Doe",
    "extension": "101",
    "is_active": True,
    "skills": ["tires", "orders"],
    "shift_start": "09:00",
    "shift_end": "18:00",
    "created_at": "2026-02-14T10:00:00",
    "updated_at": "2026-02-14T10:00:00",
    "current_status": "online",
}


class TestListOperators:
    @patch("src.api.auth.get_settings")
    @patch("src.api.operators._get_engine")
    def test_list_operators_as_admin(
        self, mock_engine_fn: MagicMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        mock_engine, _ = _make_mock_engine([_SAMPLE_OPERATOR])
        mock_engine_fn.return_value = mock_engine

        response = client.get(
            "/operators",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["operators"]) == 1
        assert data["operators"][0]["name"] == "John Doe"

    @patch("src.api.auth.get_settings")
    def test_list_operators_as_analyst_forbidden(
        self, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        response = client.get(
            "/operators",
            headers={"Authorization": f"Bearer {_analyst_token()}"},
        )
        assert response.status_code == 403


class TestCreateOperator:
    @patch("src.api.auth.get_settings")
    @patch("src.api.operators._get_engine")
    def test_create_operator(
        self, mock_engine_fn: MagicMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        mock_engine, mock_conn = _make_mock_engine([_SAMPLE_OPERATOR])
        mock_engine_fn.return_value = mock_engine

        response = client.post(
            "/operators",
            headers={"Authorization": f"Bearer {_admin_token()}"},
            json={
                "name": "John Doe",
                "extension": "101",
                "skills": ["tires"],
                "shift_start": "09:00",
                "shift_end": "18:00",
            },
        )
        assert response.status_code == 200
        assert response.json()["operator"]["name"] == "John Doe"
        # Two execute calls: INSERT operator + INSERT status log
        assert mock_conn.execute.call_count == 2


class TestUpdateOperator:
    @patch("src.api.auth.get_settings")
    @patch("src.api.operators._get_engine")
    def test_update_operator(
        self, mock_engine_fn: MagicMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        updated = {**_SAMPLE_OPERATOR, "name": "Jane Doe"}
        mock_engine, _ = _make_mock_engine([updated])
        mock_engine_fn.return_value = mock_engine

        response = client.patch(
            "/operators/op-uuid-1",
            headers={"Authorization": f"Bearer {_admin_token()}"},
            json={"name": "Jane Doe"},
        )
        assert response.status_code == 200
        assert response.json()["operator"]["name"] == "Jane Doe"

    @patch("src.api.auth.get_settings")
    @patch("src.api.operators._get_engine")
    def test_update_operator_not_found(
        self, mock_engine_fn: MagicMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        mock_engine, _ = _make_mock_engine([])
        mock_engine_fn.return_value = mock_engine

        response = client.patch(
            "/operators/nonexistent",
            headers={"Authorization": f"Bearer {_admin_token()}"},
            json={"name": "X"},
        )
        assert response.status_code == 404

    @patch("src.api.auth.get_settings")
    def test_update_no_fields(
        self, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        response = client.patch(
            "/operators/op-uuid-1",
            headers={"Authorization": f"Bearer {_admin_token()}"},
            json={},
        )
        assert response.status_code == 400


class TestDeactivateOperator:
    @patch("src.api.auth.get_settings")
    @patch("src.api.operators._get_engine")
    def test_deactivate_operator(
        self, mock_engine_fn: MagicMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        mock_engine, _ = _make_mock_engine([{"id": "op-uuid-1", "name": "John Doe"}])
        mock_engine_fn.return_value = mock_engine

        response = client.delete(
            "/operators/op-uuid-1",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert response.status_code == 200
        assert "deactivated" in response.json()["message"]


class TestChangeOperatorStatus:
    @patch("src.api.auth.get_settings")
    @patch("src.api.operators._get_engine")
    def test_change_status_as_operator(
        self, mock_engine_fn: MagicMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        mock_engine, _ = _make_mock_engine([{"id": "op-uuid-1"}])
        mock_engine_fn.return_value = mock_engine

        response = client.patch(
            "/operators/op-uuid-1/status",
            headers={"Authorization": f"Bearer {_operator_token()}"},
            json={"status": "online"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "online"

    @patch("src.api.auth.get_settings")
    @patch("src.api.operators._get_engine")
    def test_change_status_invalid(
        self, mock_engine_fn: MagicMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        response = client.patch(
            "/operators/op-uuid-1/status",
            headers={"Authorization": f"Bearer {_operator_token()}"},
            json={"status": "sleeping"},
        )
        assert response.status_code == 400

    @patch("src.api.auth.get_settings")
    @patch("src.api.operators._get_engine")
    def test_change_status_operator_not_found(
        self, mock_engine_fn: MagicMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        mock_engine, _ = _make_mock_engine([])
        mock_engine_fn.return_value = mock_engine

        response = client.patch(
            "/operators/nonexistent/status",
            headers={"Authorization": f"Bearer {_operator_token()}"},
            json={"status": "online"},
        )
        assert response.status_code == 404


class TestQueueStatus:
    @patch("src.api.auth.get_settings")
    @patch("src.api.operators._get_engine")
    def test_queue_status(
        self, mock_engine_fn: MagicMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"

        mock_conn = AsyncMock()
        online_result = MagicMock()
        online_result.scalar.return_value = 3
        transfers_result = MagicMock()
        transfers_result.scalar.return_value = 7
        mock_conn.execute = AsyncMock(side_effect=[online_result, transfers_result])

        mock_engine = MagicMock()

        @asynccontextmanager
        async def _begin() -> AsyncIterator[AsyncMock]:
            yield mock_conn

        mock_engine.begin = _begin
        mock_engine_fn.return_value = mock_engine

        response = client.get(
            "/operators/queue",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["operators_online"] == 3
        assert data["transfers_last_hour"] == 7


class TestTransfers:
    @patch("src.api.auth.get_settings")
    @patch("src.api.operators._get_engine")
    def test_get_transfers(
        self, mock_engine_fn: MagicMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        transfer_row = {
            "id": "call-1",
            "caller_id": "+380991234567",
            "started_at": "2026-02-14T10:00:00",
            "duration_seconds": 120,
            "transfer_reason": "complex_order",
            "quality_score": 0.75,
        }
        mock_engine, _ = _make_mock_engine(
            [],
            multi_results=[[transfer_row], [transfer_row]],
        )
        mock_engine_fn.return_value = mock_engine

        response = client.get(
            "/operators/transfers?limit=10",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["transfers"]) == 1


class TestOperatorStats:
    @patch("src.api.auth.get_settings")
    @patch("src.api.operators._get_engine")
    def test_get_operator_stats(
        self, mock_engine_fn: MagicMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        op_row = {"id": "op-uuid-1", "name": "John Doe"}
        status_row = {"status": "online", "changed_at": "2026-02-14T10:00:00"}
        mock_engine, _ = _make_mock_engine(
            [],
            multi_results=[[op_row], [status_row]],
        )
        mock_engine_fn.return_value = mock_engine

        response = client.get(
            "/operators/op-uuid-1/stats",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "John Doe"
        assert len(data["status_history"]) == 1

    @patch("src.api.auth.get_settings")
    @patch("src.api.operators._get_engine")
    def test_get_operator_stats_not_found(
        self, mock_engine_fn: MagicMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        mock_engine, _ = _make_mock_engine([])
        mock_engine_fn.return_value = mock_engine

        response = client.get(
            "/operators/nonexistent/stats",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert response.status_code == 404
