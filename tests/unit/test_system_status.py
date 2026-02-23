"""Unit tests for system status and config reload API."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth import create_jwt
from src.api.system import router

_TEST_SECRET = "test-secret"


def _admin_token() -> str:
    return create_jwt({"sub": "admin", "role": "admin"}, _TEST_SECRET)


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_admin_token()}"}


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestCeleryHealth:
    """Test GET /health/celery — no auth required."""

    @patch("src.tasks.celery_app.app")
    def test_celery_healthy(self, mock_celery_app: MagicMock, client: TestClient) -> None:
        mock_inspect = MagicMock()
        mock_inspect.ping.return_value = {"worker1": {"ok": "pong"}}
        mock_inspect.active.return_value = {"worker1": []}
        mock_celery_app.control.inspect.return_value = mock_inspect

        resp = client.get("/health/celery")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["workers_online"] == 1

    @patch("src.tasks.celery_app.app")
    def test_celery_no_workers(self, mock_celery_app: MagicMock, client: TestClient) -> None:
        mock_inspect = MagicMock()
        mock_inspect.ping.return_value = None
        mock_inspect.active.return_value = None
        mock_celery_app.control.inspect.return_value = mock_inspect

        resp = client.get("/health/celery")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["workers_online"] == 0

    @patch("src.tasks.celery_app.app")
    def test_celery_connection_error(self, mock_celery_app: MagicMock, client: TestClient) -> None:
        mock_celery_app.control.inspect.side_effect = Exception("connection refused")

        resp = client.get("/health/celery")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "unavailable"
        assert "error" in data


class TestConfigReload:
    """Test POST /admin/config/reload."""

    @patch("src.api.auth.get_settings")
    def test_reload_config(self, mock_settings: MagicMock, client: TestClient) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET

        resp = client.post("/admin/config/reload", headers=_auth())

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "reloaded"
        assert "changes" in data

    def test_reload_no_auth_returns_401(self, client: TestClient) -> None:
        resp = client.post("/admin/config/reload")
        assert resp.status_code == 401


class TestSystemStatus:
    """Test GET /admin/system-status."""

    @patch("src.api.auth.get_settings")
    @patch("src.api.system._get_engine", new_callable=AsyncMock)
    def test_system_status_db_unavailable(
        self,
        mock_engine_fn: AsyncMock,
        mock_settings: MagicMock,
        client: TestClient,
    ) -> None:
        """When DB is unreachable, postgres field shows 'unavailable'."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_settings.return_value.redis.url = "redis://localhost:6379/0"
        mock_settings.return_value.backup.backup_dir = "/tmp/nonexistent-backups"
        mock_engine_fn.side_effect = Exception("no db")

        resp = client.get("/admin/system-status", headers=_auth())

        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data
        assert "uptime_seconds" in data
        assert data["postgres"] == "unavailable"

    def test_system_status_no_auth_returns_401(self, client: TestClient) -> None:
        resp = client.get("/admin/system-status")
        assert resp.status_code == 401
