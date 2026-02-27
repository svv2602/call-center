"""Unit tests for the customers read-only API (src/api/customers.py)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth import create_jwt
from src.api.customers import router

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


def _customer_row(
    phone: str = "+380501234567",
    name: str = "Іван Петренко",
    city: str = "Київ",
    total_calls: int = 3,
) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "phone": phone,
        "name": name,
        "city": city,
        "vehicles": [{"plate": "AA1234BB", "brand": "Toyota", "tire_size": "205/55R16"}],
        "delivery_address": "вул. Хрещатик 1",
        "total_calls": total_calls,
        "first_call_at": "2026-02-01T10:00:00",
        "last_call_at": "2026-02-25T14:30:00",
    }


# ── fixtures ─────────────────────────────────────────────


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ── TestListCustomers ────────────────────────────────────


class TestListCustomers:
    @patch("src.api.auth.get_settings")
    @patch("src.api.customers._get_engine", new_callable=AsyncMock)
    def test_empty_list(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        conn = AsyncMock()
        result = MagicMock()
        result.__iter__ = MagicMock(return_value=iter([]))
        conn.execute = AsyncMock(return_value=result)
        mock_engine_fn.return_value = _patch_engine_with_conn(conn)

        resp = client.get("/admin/customers", headers=_auth_header())

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["customers"] == []

    @patch("src.api.auth.get_settings")
    @patch("src.api.customers._get_engine", new_callable=AsyncMock)
    def test_with_data(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        cust = _customer_row()
        cust["_total"] = 1
        conn = AsyncMock()
        row = MagicMock()
        row._mapping = cust
        result = MagicMock()
        result.__iter__ = MagicMock(return_value=iter([row]))
        conn.execute = AsyncMock(return_value=result)
        mock_engine_fn.return_value = _patch_engine_with_conn(conn)

        resp = client.get("/admin/customers", headers=_auth_header())

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["customers"]) == 1
        assert data["customers"][0]["phone"] == "+380501234567"
        assert "_total" not in data["customers"][0]

    @patch("src.api.auth.get_settings")
    @patch("src.api.customers._get_engine", new_callable=AsyncMock)
    def test_search(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        conn = AsyncMock()
        result = MagicMock()
        result.__iter__ = MagicMock(return_value=iter([]))
        conn.execute = AsyncMock(return_value=result)
        mock_engine_fn.return_value = _patch_engine_with_conn(conn)

        resp = client.get("/admin/customers?search=Іван", headers=_auth_header())

        assert resp.status_code == 200
        # Verify search param was passed to SQL
        call_args = conn.execute.call_args
        assert "%Іван%" in call_args[0][1]["search"]

    @patch("src.api.auth.get_settings")
    def test_invalid_sort_by(self, mock_settings: MagicMock, client: TestClient) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET

        resp = client.get(
            "/admin/customers?sort_by=invalid_col", headers=_auth_header()
        )

        assert resp.status_code == 400
        assert "Invalid sort_by" in resp.json()["detail"]

    @patch("src.api.auth.get_settings")
    @patch("src.api.customers._get_engine", new_callable=AsyncMock)
    def test_pagination(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        conn = AsyncMock()
        result = MagicMock()
        result.__iter__ = MagicMock(return_value=iter([]))
        conn.execute = AsyncMock(return_value=result)
        mock_engine_fn.return_value = _patch_engine_with_conn(conn)

        resp = client.get(
            "/admin/customers?limit=10&offset=20", headers=_auth_header()
        )

        assert resp.status_code == 200
        call_args = conn.execute.call_args
        assert call_args[0][1]["limit"] == 10
        assert call_args[0][1]["offset"] == 20

    def test_no_auth(self, client: TestClient) -> None:
        resp = client.get("/admin/customers")
        assert resp.status_code in (401, 403)


# ── TestGetCustomer ──────────────────────────────────────


class TestGetCustomer:
    @patch("src.api.auth.get_settings")
    @patch("src.api.customers._get_engine", new_callable=AsyncMock)
    def test_found_with_calls(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        cid = uuid4()
        cust = _customer_row()
        cust["id"] = str(cid)

        call_data = {
            "id": str(uuid4()),
            "started_at": "2026-02-25T14:30:00",
            "ended_at": "2026-02-25T14:35:00",
            "duration_seconds": 300,
            "scenario": "tire_search",
            "transferred_to_operator": False,
        }

        conn = AsyncMock()
        cust_row = MagicMock()
        cust_row._mapping = cust
        cust_result = MagicMock()
        cust_result.first.return_value = cust_row

        call_row = MagicMock()
        call_row._mapping = call_data
        calls_result = MagicMock()
        calls_result.__iter__ = MagicMock(return_value=iter([call_row]))

        conn.execute = AsyncMock(side_effect=[cust_result, calls_result])
        mock_engine_fn.return_value = _patch_engine_with_conn(conn)

        resp = client.get(f"/admin/customers/{cid}", headers=_auth_header())

        assert resp.status_code == 200
        data = resp.json()
        assert data["customer"]["phone"] == "+380501234567"
        assert len(data["recent_calls"]) == 1
        assert data["recent_calls"][0]["scenario"] == "tire_search"

    @patch("src.api.auth.get_settings")
    @patch("src.api.customers._get_engine", new_callable=AsyncMock)
    def test_not_found(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        cid = uuid4()
        conn = AsyncMock()
        result = MagicMock()
        result.first.return_value = None
        conn.execute = AsyncMock(return_value=result)
        mock_engine_fn.return_value = _patch_engine_with_conn(conn)

        resp = client.get(f"/admin/customers/{cid}", headers=_auth_header())

        assert resp.status_code == 404

    def test_no_auth(self, client: TestClient) -> None:
        cid = uuid4()
        resp = client.get(f"/admin/customers/{cid}")
        assert resp.status_code in (401, 403)
