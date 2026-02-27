"""Tests for test phones admin API endpoints."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth import create_jwt
from src.api.test_phones import router

_TEST_SECRET = "test-secret"


def _admin_token() -> str:
    return create_jwt({"sub": "admin", "role": "admin"}, _TEST_SECRET)


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_admin_token()}"}


@pytest.fixture()
def mock_redis():
    store: dict[str, str] = {}
    mock = AsyncMock()

    async def _get(key: str) -> str | None:
        return store.get(key)

    async def _set(key: str, value: str) -> None:
        store[key] = value

    async def _delete(key: str) -> None:
        store.pop(key, None)

    mock.get = AsyncMock(side_effect=_get)
    mock.set = AsyncMock(side_effect=_set)
    mock.delete = AsyncMock(side_effect=_delete)
    mock._store = store
    return mock


@pytest.fixture()
def mock_engine():
    """Mock SQLAlchemy engine with transaction context manager."""
    engine = AsyncMock()
    conn = AsyncMock()

    result_mock = MagicMock()
    result_mock.rowcount = 0
    conn.execute = AsyncMock(return_value=result_mock)

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    engine.begin = MagicMock(return_value=ctx)

    return engine, conn


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestGetConfig:
    @patch("src.api.auth.get_settings")
    @patch("src.api.test_phones._get_redis")
    def test_get_empty(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis

        resp = client.get("/admin/test-phones/config", headers=_auth())
        assert resp.status_code == 200
        assert resp.json() == {"phones": {}}

    @patch("src.api.auth.get_settings")
    @patch("src.api.test_phones._get_redis")
    def test_get_returns_existing(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis
        mock_redis._store["test:phones"] = json.dumps({"0504874375": "no_history"})

        resp = client.get("/admin/test-phones/config", headers=_auth())
        assert resp.status_code == 200
        assert resp.json()["phones"] == {"0504874375": "no_history"}


class TestPutConfig:
    @patch("src.api.auth.get_settings")
    @patch("src.api.test_phones._get_redis")
    def test_add_phone(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis

        resp = client.put(
            "/admin/test-phones/config",
            json={"phone": "+380504874375", "mode": "no_history"},
            headers=_auth(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["phone"] == "0504874375"
        assert data["mode"] == "no_history"
        assert "0504874375" in data["phones"]

    @patch("src.api.auth.get_settings")
    @patch("src.api.test_phones._get_redis")
    def test_normalizes_phone(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis

        resp = client.put(
            "/admin/test-phones/config",
            json={"phone": "380671234567", "mode": "with_history"},
            headers=_auth(),
        )
        data = resp.json()
        assert data["phone"] == "0671234567"
        assert data["phones"]["0671234567"] == "with_history"

    @patch("src.api.auth.get_settings")
    @patch("src.api.test_phones._get_redis")
    def test_rejects_short_phone(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis

        resp = client.put(
            "/admin/test-phones/config",
            json={"phone": "123", "mode": "no_history"},
            headers=_auth(),
        )
        assert resp.status_code == 422


class TestDeleteConfig:
    @patch("src.api.auth.get_settings")
    @patch("src.api.test_phones._get_redis")
    def test_delete_phone(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis
        mock_redis._store["test:phones"] = json.dumps({"0504874375": "no_history"})

        resp = client.delete("/admin/test-phones/config/0504874375", headers=_auth())
        assert resp.status_code == 200
        data = resp.json()
        assert data["removed"] == "0504874375"
        assert "0504874375" not in data["phones"]

    @patch("src.api.auth.get_settings")
    @patch("src.api.test_phones._get_redis")
    def test_delete_not_found(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis

        resp = client.delete("/admin/test-phones/config/0999999999", headers=_auth())
        assert resp.status_code == 404


class TestClearHistory:
    @patch("src.api.auth.get_settings")
    @patch("src.api.test_phones._get_engine")
    @patch("src.api.test_phones._get_redis")
    def test_clear_history(
        self,
        mock_get_redis: MagicMock,
        mock_get_engine: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
        mock_engine: tuple,
    ) -> None:
        engine, conn = mock_engine
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis
        mock_get_engine.return_value = engine

        results = [
            MagicMock(rowcount=5),   # tool_calls
            MagicMock(rowcount=12),  # turns
            MagicMock(rowcount=3),   # calls
            MagicMock(rowcount=1),   # update customers
        ]
        conn.execute = AsyncMock(side_effect=results)

        resp = client.post(
            "/admin/test-phones/clear-history/0504874375", headers=_auth()
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["phone"] == "0504874375"
        assert data["tool_calls_deleted"] == 5
        assert data["turns_deleted"] == 12
        assert data["calls_deleted"] == 3
        assert conn.execute.call_count == 4


class TestRoundTrip:
    @patch("src.api.auth.get_settings")
    @patch("src.api.test_phones._get_redis")
    def test_put_then_get(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        """Round-trip: PUT then GET returns the same data."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis

        client.put(
            "/admin/test-phones/config",
            json={"phone": "0504874375", "mode": "no_history"},
            headers=_auth(),
        )
        resp = client.get("/admin/test-phones/config", headers=_auth())
        assert resp.json()["phones"] == {"0504874375": "no_history"}
