"""Unit tests for the tenants CRUD API (src/api/tenants.py)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth import create_jwt
from src.api.tenants import router

# ── helpers ──────────────────────────────────────────────

_TEST_SECRET = "test-secret"


def _admin_token() -> str:
    return create_jwt({"sub": "admin", "role": "admin"}, _TEST_SECRET)


def _auth_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {_admin_token()}"}


def _patch_engine_with_conn(conn: AsyncMock) -> Any:
    """Create engine mock with async context manager that yields conn."""
    from contextlib import asynccontextmanager

    engine = MagicMock()

    @asynccontextmanager
    async def _begin():
        yield conn

    engine.begin = _begin
    return engine


# ── fixtures ─────────────────────────────────────────────

@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ── Tests ────────────────────────────────────────────────

class TestListTenants:
    @patch("src.api.auth.get_settings")
    @patch("src.api.tenants._get_engine", new_callable=AsyncMock)
    def test_empty_list(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        conn = AsyncMock()
        count_result = MagicMock()
        count_result.scalar.return_value = 0
        rows_result = MagicMock()
        rows_result.__iter__ = MagicMock(return_value=iter([]))
        conn.execute = AsyncMock(side_effect=[count_result, rows_result])
        mock_engine_fn.return_value = _patch_engine_with_conn(conn)

        resp = client.get("/admin/tenants", headers=_auth_header())

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["tenants"] == []

    @patch("src.api.auth.get_settings")
    @patch("src.api.tenants._get_engine", new_callable=AsyncMock)
    def test_with_data(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        tenant = {
            "id": str(uuid4()),
            "slug": "prokoleso",
            "name": "ProKoleso",
            "network_id": "prokoleso-net",
            "agent_name": "Олена",
            "greeting": None,
            "enabled_tools": [],
            "prompt_suffix": None,
            "config": {},
            "is_active": True,
            "created_at": "2026-02-01T00:00:00",
            "updated_at": None,
        }
        conn = AsyncMock()
        count_result = MagicMock()
        count_result.scalar.return_value = 1
        row = MagicMock()
        row._mapping = tenant
        rows_result = MagicMock()
        rows_result.__iter__ = MagicMock(return_value=iter([row]))
        conn.execute = AsyncMock(side_effect=[count_result, rows_result])
        mock_engine_fn.return_value = _patch_engine_with_conn(conn)

        resp = client.get("/admin/tenants", headers=_auth_header())

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["tenants"]) == 1
        assert data["tenants"][0]["slug"] == "prokoleso"

    @patch("src.api.auth.get_settings")
    @patch("src.api.tenants._get_engine", new_callable=AsyncMock)
    def test_active_filter(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        conn = AsyncMock()
        count_result = MagicMock()
        count_result.scalar.return_value = 0
        rows_result = MagicMock()
        rows_result.__iter__ = MagicMock(return_value=iter([]))
        conn.execute = AsyncMock(side_effect=[count_result, rows_result])
        mock_engine_fn.return_value = _patch_engine_with_conn(conn)

        resp = client.get("/admin/tenants?is_active=true", headers=_auth_header())

        assert resp.status_code == 200


class TestCreateTenant:
    @patch("src.api.auth.get_settings")
    @patch("src.api.tenants._get_engine", new_callable=AsyncMock)
    def test_success(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        new_id = uuid4()
        conn = AsyncMock()
        row = MagicMock()
        row.id = new_id
        result = MagicMock()
        result.first.return_value = row
        conn.execute = AsyncMock(return_value=result)
        mock_engine_fn.return_value = _patch_engine_with_conn(conn)

        resp = client.post(
            "/admin/tenants",
            json={
                "slug": "test-net",
                "name": "Test Network",
                "network_id": "test-net-id",
            },
            headers=_auth_header(),
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == str(new_id)

    @patch("src.api.auth.get_settings")
    def test_invalid_slug(self, mock_settings: MagicMock, client: TestClient) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET

        resp = client.post(
            "/admin/tenants",
            json={
                "slug": "INVALID SLUG!!",
                "name": "Test",
                "network_id": "test",
            },
            headers=_auth_header(),
        )

        assert resp.status_code == 422

    @patch("src.api.auth.get_settings")
    @patch("src.api.tenants._get_engine", new_callable=AsyncMock)
    def test_invalid_tools(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        conn = AsyncMock()
        mock_engine_fn.return_value = _patch_engine_with_conn(conn)

        resp = client.post(
            "/admin/tenants",
            json={
                "slug": "test",
                "name": "Test",
                "network_id": "test",
                "enabled_tools": ["nonexistent_tool"],
            },
            headers=_auth_header(),
        )

        assert resp.status_code == 400
        assert "Invalid tool names" in resp.json()["detail"]


class TestGetTenant:
    @patch("src.api.auth.get_settings")
    @patch("src.api.tenants._get_engine", new_callable=AsyncMock)
    def test_success(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        tid = uuid4()
        tenant = {
            "id": str(tid),
            "slug": "prokoleso",
            "name": "ProKoleso",
            "network_id": "prokoleso-net",
            "agent_name": "Олена",
            "greeting": None,
            "enabled_tools": [],
            "prompt_suffix": None,
            "config": {},
            "is_active": True,
            "created_at": "2026-02-01T00:00:00",
            "updated_at": None,
        }
        conn = AsyncMock()
        row = MagicMock()
        row._mapping = tenant
        result = MagicMock()
        result.first.return_value = row
        conn.execute = AsyncMock(return_value=result)
        mock_engine_fn.return_value = _patch_engine_with_conn(conn)

        resp = client.get(f"/admin/tenants/{tid}", headers=_auth_header())

        assert resp.status_code == 200
        assert resp.json()["tenant"]["slug"] == "prokoleso"

    @patch("src.api.auth.get_settings")
    @patch("src.api.tenants._get_engine", new_callable=AsyncMock)
    def test_not_found(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        tid = uuid4()
        conn = AsyncMock()
        result = MagicMock()
        result.first.return_value = None
        conn.execute = AsyncMock(return_value=result)
        mock_engine_fn.return_value = _patch_engine_with_conn(conn)

        resp = client.get(f"/admin/tenants/{tid}", headers=_auth_header())

        assert resp.status_code == 404


class TestUpdateTenant:
    @patch("src.api.auth.get_settings")
    @patch("src.api.tenants._get_engine", new_callable=AsyncMock)
    def test_success(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        tid = uuid4()
        conn = AsyncMock()
        row = MagicMock()
        row.id = tid
        result = MagicMock()
        result.first.return_value = row
        conn.execute = AsyncMock(return_value=result)
        mock_engine_fn.return_value = _patch_engine_with_conn(conn)

        resp = client.patch(
            f"/admin/tenants/{tid}",
            json={"name": "Updated Name"},
            headers=_auth_header(),
        )

        assert resp.status_code == 200
        assert resp.json()["message"] == "Tenant updated"

    @patch("src.api.auth.get_settings")
    @patch("src.api.tenants._get_engine", new_callable=AsyncMock)
    def test_not_found(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        tid = uuid4()
        conn = AsyncMock()
        result = MagicMock()
        result.first.return_value = None
        conn.execute = AsyncMock(return_value=result)
        mock_engine_fn.return_value = _patch_engine_with_conn(conn)

        resp = client.patch(
            f"/admin/tenants/{tid}",
            json={"name": "Updated"},
            headers=_auth_header(),
        )

        assert resp.status_code == 404


class TestDeleteTenant:
    @patch("src.api.auth.get_settings")
    @patch("src.api.tenants._get_engine", new_callable=AsyncMock)
    def test_soft_delete(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        tid = uuid4()
        conn = AsyncMock()
        row = MagicMock()
        row.id = tid
        result = MagicMock()
        result.first.return_value = row
        conn.execute = AsyncMock(return_value=result)
        mock_engine_fn.return_value = _patch_engine_with_conn(conn)

        resp = client.delete(f"/admin/tenants/{tid}", headers=_auth_header())

        assert resp.status_code == 200
        assert resp.json()["message"] == "Tenant deactivated"

    @patch("src.api.auth.get_settings")
    @patch("src.api.tenants._get_engine", new_callable=AsyncMock)
    def test_not_found(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        tid = uuid4()
        conn = AsyncMock()
        result = MagicMock()
        result.first.return_value = None
        conn.execute = AsyncMock(return_value=result)
        mock_engine_fn.return_value = _patch_engine_with_conn(conn)

        resp = client.delete(f"/admin/tenants/{tid}", headers=_auth_header())

        assert resp.status_code == 404
