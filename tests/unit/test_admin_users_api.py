"""Unit tests for admin users API."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.admin_users import router
from src.api.auth import create_jwt

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _make_mock_row(data: dict[str, Any]) -> MagicMock:
    row = MagicMock()
    row._mapping = data
    return row


def _make_mock_engine(rows: list[dict[str, Any]]) -> Any:
    mock_conn = AsyncMock()
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


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestListUsers:
    @patch("src.api.auth.get_settings")
    @patch("src.api.admin_users._get_engine")
    def test_list_users_as_admin(
        self, mock_engine_fn: MagicMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        mock_engine, _ = _make_mock_engine(
            [
                {
                    "id": "1",
                    "username": "admin",
                    "role": "admin",
                    "is_active": True,
                    "created_at": "2026-02-14",
                    "last_login_at": None,
                },
            ]
        )
        mock_engine_fn.return_value = mock_engine

        response = client.get(
            "/admin/users",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert response.status_code == 200
        assert len(response.json()["users"]) == 1

    @patch("src.api.auth.get_settings")
    def test_list_users_as_analyst_forbidden(
        self, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        response = client.get(
            "/admin/users",
            headers={"Authorization": f"Bearer {_analyst_token()}"},
        )
        assert response.status_code == 403


class TestCreateUser:
    @patch("src.api.auth.get_settings")
    @patch("src.api.admin_users._get_engine")
    def test_create_user(
        self, mock_engine_fn: MagicMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        mock_engine, _ = _make_mock_engine(
            [
                {
                    "id": "new-id",
                    "username": "new_user",
                    "role": "analyst",
                    "is_active": True,
                    "created_at": "2026-02-14",
                },
            ]
        )
        mock_engine_fn.return_value = mock_engine

        response = client.post(
            "/admin/users",
            headers={"Authorization": f"Bearer {_admin_token()}"},
            json={"username": "new_user", "password": "pass123", "role": "analyst"},
        )
        assert response.status_code == 200
        assert response.json()["user"]["username"] == "new_user"

    @patch("src.api.auth.get_settings")
    @patch("src.api.admin_users._get_engine")
    def test_create_user_invalid_role(
        self, mock_engine_fn: MagicMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        response = client.post(
            "/admin/users",
            headers={"Authorization": f"Bearer {_admin_token()}"},
            json={"username": "x", "password": "y", "role": "superadmin"},
        )
        assert response.status_code == 400


class TestGetAuditLog:
    @patch("src.api.auth.get_settings")
    @patch("src.api.admin_users._get_engine")
    def test_get_audit_log(
        self, mock_engine_fn: MagicMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        mock_engine, mock_conn = _make_mock_engine(
            [
                {
                    "id": "log-1",
                    "user_id": "1",
                    "username": "admin",
                    "action": "POST",
                    "resource_type": "admin/users",
                    "resource_id": None,
                    "details": None,
                    "ip_address": "127.0.0.1",
                    "created_at": "2026-02-14",
                },
            ]
        )
        # First call returns count, second returns rows
        count_result = MagicMock()
        count_result.scalar.return_value = 1
        rows_result = MagicMock()
        rows_result.__iter__ = lambda self: iter(
            [
                _make_mock_row(
                    {
                        "id": "log-1",
                        "user_id": "1",
                        "username": "admin",
                        "action": "POST",
                        "resource_type": "admin/users",
                        "resource_id": None,
                        "details": None,
                        "ip_address": "127.0.0.1",
                        "created_at": "2026-02-14",
                    },
                )
            ]
        )
        mock_conn.execute = AsyncMock(side_effect=[count_result, rows_result])
        mock_engine_fn.return_value = mock_engine

        response = client.get(
            "/admin/audit-log",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert response.status_code == 200
        assert response.json()["total"] == 1
