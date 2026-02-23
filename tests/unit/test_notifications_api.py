"""Unit tests for the notifications admin API (src/api/notifications.py)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth import create_jwt
from src.api.notifications import router

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
    return r


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestGetTelegramConfig:
    @patch("src.api.auth.get_settings")
    @patch("src.api.notifications._get_redis")
    def test_get_telegram_from_redis(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        """Config stored in Redis — returns masked token hint and chat_id."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_redis.get = AsyncMock(
            return_value=json.dumps({"bot_token": "123:ABCDEF", "chat_id": "-100"})
        )
        mock_get_redis.return_value = mock_redis

        resp = client.get("/admin/notifications/telegram", headers=_auth())

        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "redis"
        assert data["chat_id"] == "-100"
        assert data["token_hint"] == "CDEF"  # last 4 chars of "123:ABCDEF"
        assert data["token_set"] is True

    @patch("src.api.auth.get_settings")
    @patch("src.api.notifications._get_redis")
    def test_get_telegram_from_env(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        """Redis empty — falls back to env vars."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_redis.get = AsyncMock(return_value=None)
        mock_get_redis.return_value = mock_redis

        with patch.dict(
            "os.environ",
            {"TELEGRAM_BOT_TOKEN": "999:XYZABCD", "TELEGRAM_CHAT_ID": "-200"},
        ):
            resp = client.get("/admin/notifications/telegram", headers=_auth())

        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "env"
        assert data["chat_id"] == "-200"
        assert data["token_set"] is True

    def test_get_no_auth_returns_401(self, client: TestClient) -> None:
        resp = client.get("/admin/notifications/telegram")
        assert resp.status_code == 401


class TestUpdateTelegramConfig:
    @patch("src.api.auth.get_settings")
    @patch("src.api.notifications._get_redis")
    @patch("src.api.notifications._try_update_alertmanager")
    def test_update_telegram(
        self,
        mock_alertmanager: MagicMock,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        """PATCH saves bot_token and chat_id to Redis."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis

        resp = client.patch(
            "/admin/notifications/telegram",
            json={"bot_token": "123:TOKEN", "chat_id": "-100500"},
            headers=_auth(),
        )

        assert resp.status_code == 200
        assert resp.json()["message"] == "Telegram config saved"
        mock_redis.set.assert_awaited_once()
        stored = json.loads(mock_redis.set.call_args[0][1])
        assert stored["bot_token"] == "123:TOKEN"
        assert stored["chat_id"] == "-100500"

    def test_update_no_auth_returns_401(self, client: TestClient) -> None:
        resp = client.patch(
            "/admin/notifications/telegram",
            json={"bot_token": "x", "chat_id": "-1"},
        )
        assert resp.status_code == 401


class TestTestTelegram:
    @patch("src.api.auth.get_settings")
    @patch("src.api.notifications._get_redis")
    def test_test_telegram_not_configured(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        """POST /test when no token configured returns success=False."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_redis.get = AsyncMock(return_value=None)
        mock_get_redis.return_value = mock_redis

        with patch.dict("os.environ", {}, clear=True):
            resp = client.post("/admin/notifications/telegram/test", headers=_auth())

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "not configured" in data["error"]

    @patch("src.api.auth.get_settings")
    @patch("src.api.notifications._get_redis")
    @patch("src.api.notifications.aiohttp")
    def test_test_telegram_success(
        self,
        mock_aiohttp: MagicMock,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        """POST /test with valid config returns success=True when Telegram responds 200."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_redis.get = AsyncMock(
            return_value=json.dumps({"bot_token": "123:TOKEN", "chat_id": "-100"})
        )
        mock_get_redis.return_value = mock_redis

        # Build a mock aiohttp session/response chain.
        # The endpoint does:
        #   async with aiohttp.ClientSession() as session, session.post(...) as resp:
        # ClientSession() returns a CM; session.post() also returns a CM (not a coroutine).
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"ok": True})

        # post_cm: context manager returned by session.post(...)
        post_cm = MagicMock()
        post_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        post_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post.return_value = post_cm
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_aiohttp.ClientSession.return_value = mock_session
        mock_aiohttp.ClientTimeout = MagicMock(return_value=MagicMock())

        resp = client.post("/admin/notifications/telegram/test", headers=_auth())

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "latency_ms" in data
