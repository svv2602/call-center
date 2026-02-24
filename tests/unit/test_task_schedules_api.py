"""Unit tests for the task schedules admin API (src/api/task_schedules.py)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth import create_jwt
from src.api.task_schedules import router
from src.tasks.schedule_utils import TASK_DEFAULTS

_TEST_SECRET = "test-secret"


def _admin_token() -> str:
    return create_jwt({"sub": "admin", "role": "admin"}, _TEST_SECRET)


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_admin_token()}"}


@pytest.fixture()
def mock_redis() -> AsyncMock:
    r = AsyncMock()
    r.get = AsyncMock(return_value=None)
    r.set = AsyncMock()
    r.delete = AsyncMock()
    return r


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestGetTaskSchedules:
    @patch("src.api.auth.get_settings")
    @patch("src.api.task_schedules._get_redis")
    def test_get_returns_defaults(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        """GET should return TASK_DEFAULTS when Redis is empty."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis

        resp = client.get("/admin/task-schedules", headers=_auth())

        assert resp.status_code == 200
        data = resp.json()
        assert "schedules" in data
        assert "catalog-full-sync" in data["schedules"]
        assert "refresh-stt-hints" in data["schedules"]
        assert data["schedules"]["catalog-full-sync"]["hour"] == 8
        assert data["schedules"]["refresh-stt-hints"]["frequency"] == "weekly"

    @patch("src.api.auth.get_settings")
    @patch("src.api.task_schedules._get_redis")
    def test_get_merges_redis_overrides(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        """GET should merge Redis overrides with defaults."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        overrides = {"catalog-full-sync": {"hour": 12}}
        mock_redis.get = AsyncMock(return_value=json.dumps(overrides))
        mock_get_redis.return_value = mock_redis

        resp = client.get("/admin/task-schedules", headers=_auth())

        assert resp.status_code == 200
        assert resp.json()["schedules"]["catalog-full-sync"]["hour"] == 12
        # Default fields preserved
        assert resp.json()["schedules"]["catalog-full-sync"]["frequency"] == "daily"


class TestPatchTaskSchedules:
    @patch("src.api.auth.get_settings")
    @patch("src.api.task_schedules._get_redis")
    def test_patch_updates_schedule(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        """PATCH should update schedule in Redis and return merged data."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis

        resp = client.patch(
            "/admin/task-schedules",
            headers=_auth(),
            json={"catalog-full-sync": {"hour": 10, "enabled": False}},
        )

        assert resp.status_code == 200
        mock_redis.set.assert_called_once()
        # Verify Redis was called with correct data
        saved = json.loads(mock_redis.set.call_args[0][1])
        assert saved["catalog-full-sync"]["hour"] == 10
        assert saved["catalog-full-sync"]["enabled"] is False

    @patch("src.api.auth.get_settings")
    @patch("src.api.task_schedules._get_redis")
    def test_patch_rejects_unknown_task(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        """PATCH should reject unknown task keys."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis

        resp = client.patch(
            "/admin/task-schedules",
            headers=_auth(),
            json={"nonexistent-task": {"hour": 10}},
        )

        assert resp.status_code == 400

    @patch("src.api.auth.get_settings")
    @patch("src.api.task_schedules._get_redis")
    def test_patch_validates_hour(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        """PATCH should reject invalid hour values."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis

        resp = client.patch(
            "/admin/task-schedules",
            headers=_auth(),
            json={"catalog-full-sync": {"hour": 25}},
        )

        assert resp.status_code == 422


class TestRunTask:
    @patch("src.api.auth.get_settings")
    @patch("src.api.task_schedules._get_redis")
    @patch("src.tasks.celery_app.app")
    def test_run_queues_task(
        self,
        mock_celery: MagicMock,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
    ) -> None:
        """POST /run should queue a Celery task."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = AsyncMock()
        mock_result = MagicMock()
        mock_result.id = "abc-123"
        mock_celery.send_task.return_value = mock_result

        resp = client.post("/admin/task-schedules/catalog-full-sync/run", headers=_auth())

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "queued"
        assert data["task_id"] == "abc-123"
        mock_celery.send_task.assert_called_once_with(
            "src.tasks.catalog_sync_tasks.catalog_full_sync",
            kwargs={"triggered_by": "manual"},
        )

    @patch("src.api.auth.get_settings")
    @patch("src.api.task_schedules._get_redis")
    def test_run_rejects_unknown_task(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
    ) -> None:
        """POST /run should 404 for unknown task key."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = AsyncMock()

        resp = client.post("/admin/task-schedules/nonexistent/run", headers=_auth())

        assert resp.status_code == 404


class TestResetSchedules:
    @patch("src.api.auth.get_settings")
    @patch("src.api.task_schedules._get_redis")
    def test_reset_clears_redis(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        """POST /reset should delete Redis key and return defaults."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis

        resp = client.post("/admin/task-schedules/reset", headers=_auth())

        assert resp.status_code == 200
        mock_redis.delete.assert_called_once()
        data = resp.json()
        assert data["schedules"]["catalog-full-sync"]["hour"] == TASK_DEFAULTS["catalog-full-sync"]["hour"]
        assert data["schedules"]["refresh-stt-hints"]["frequency"] == "weekly"
