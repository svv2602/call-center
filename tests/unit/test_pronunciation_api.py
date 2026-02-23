"""Unit tests for the pronunciation rules admin API (src/api/pronunciation.py)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth import create_jwt
from src.api.pronunciation import router

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


class TestGetPronunciationRules:
    @patch("src.api.auth.get_settings")
    @patch("src.api.pronunciation._get_redis")
    def test_get_rules_from_redis(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        """Redis has custom rules — returns them with source='redis'."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_redis.get = AsyncMock(return_value=json.dumps({"rules": "custom rules"}))
        mock_get_redis.return_value = mock_redis

        resp = client.get("/admin/agent/pronunciation-rules", headers=_auth())

        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "redis"
        assert data["rules"] == "custom rules"

    @patch("src.api.auth.get_settings")
    @patch("src.api.pronunciation._get_redis")
    def test_get_rules_default_when_redis_empty(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        """Redis returns None — falls back to hardcoded PRONUNCIATION_RULES constant."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_redis.get = AsyncMock(return_value=None)
        mock_get_redis.return_value = mock_redis

        from src.agent.prompts import PRONUNCIATION_RULES

        resp = client.get("/admin/agent/pronunciation-rules", headers=_auth())

        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "default"
        assert data["rules"] == PRONUNCIATION_RULES

    def test_get_no_auth_returns_401(self, client: TestClient) -> None:
        resp = client.get("/admin/agent/pronunciation-rules")
        assert resp.status_code == 401


class TestUpdatePronunciationRules:
    @patch("src.api.auth.get_settings")
    @patch("src.api.pronunciation._get_redis")
    def test_update_rules(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        """PATCH with new rules persists them to Redis."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis

        resp = client.patch(
            "/admin/agent/pronunciation-rules",
            json={"rules": "new rules"},
            headers=_auth(),
        )

        assert resp.status_code == 200
        assert resp.json()["message"] == "Pronunciation rules saved"
        mock_redis.set.assert_awaited_once()
        # Verify the stored JSON contains the new rules
        stored_arg = mock_redis.set.call_args[0][1]
        stored = json.loads(stored_arg)
        assert stored["rules"] == "new rules"

    def test_update_no_auth_returns_401(self, client: TestClient) -> None:
        resp = client.patch(
            "/admin/agent/pronunciation-rules",
            json={"rules": "anything"},
        )
        assert resp.status_code == 401


class TestResetPronunciationRules:
    @patch("src.api.auth.get_settings")
    @patch("src.api.pronunciation._get_redis")
    def test_reset_rules(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        """POST /reset deletes the Redis key and returns default rules."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis

        from src.agent.prompts import PRONUNCIATION_RULES

        resp = client.post(
            "/admin/agent/pronunciation-rules/reset",
            headers=_auth(),
        )

        assert resp.status_code == 200
        data = resp.json()
        assert "reset" in data["message"].lower()
        assert data["rules"] == PRONUNCIATION_RULES
        mock_redis.delete.assert_awaited_once()
