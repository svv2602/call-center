"""Tests for TTS config admin API endpoints."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.tts_config import router


async def _fake_require_perm(*_args: object, **_kwargs: object) -> dict[str, Any]:
    return {"sub": "test-user", "role": "admin"}


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
    mock._store = store  # expose for tests that pre-seed
    return mock


@pytest.fixture()
def app():
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)
    return test_app


def _env_patch():
    """Patch Settings to return known TTS defaults."""
    mock_settings = MagicMock()
    mock_settings.return_value.google_tts.voice = "uk-UA-Wavenet-A"
    mock_settings.return_value.google_tts.speaking_rate = 0.93
    mock_settings.return_value.google_tts.pitch = -1.0
    mock_settings.return_value.redis.url = "redis://localhost"
    return mock_settings


class TestGetConfig:
    """Test GET /admin/tts/config."""

    @pytest.mark.asyncio()
    async def test_returns_env_defaults_when_redis_empty(
        self, app: Any, mock_redis: AsyncMock
    ) -> None:
        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.tts_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.api.tts_config.get_settings", _env_patch()),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.get("/admin/tts/config")

        assert response.status_code == 200
        data = response.json()
        assert data["source"] == "env"
        assert data["config"]["voice_name"] == "uk-UA-Wavenet-A"
        assert data["config"]["speaking_rate"] == 0.93
        assert data["config"]["pitch"] == -1.0

    @pytest.mark.asyncio()
    async def test_returns_redis_config_when_set(self, app: Any, mock_redis: AsyncMock) -> None:
        redis_config = {"voice_name": "uk-UA-Neural2-A", "speaking_rate": 1.0}
        mock_redis._store["tts:config"] = json.dumps(redis_config)

        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.tts_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.api.tts_config.get_settings", _env_patch()),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.get("/admin/tts/config")

        data = response.json()
        assert data["source"] == "redis"
        assert data["config"]["voice_name"] == "uk-UA-Neural2-A"
        assert data["config"]["speaking_rate"] == 1.0
        # pitch falls back to env default since not in Redis
        assert data["config"]["pitch"] == -1.0

    @pytest.mark.asyncio()
    async def test_known_voices_present(self, app: Any, mock_redis: AsyncMock) -> None:
        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.tts_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.api.tts_config.get_settings", _env_patch()),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.get("/admin/tts/config")

        data = response.json()
        assert "known_voices" in data
        assert "uk-UA-Wavenet-A" in data["known_voices"]
        assert len(data["known_voices"]) >= 5


class TestPatchConfig:
    """Test PATCH /admin/tts/config."""

    @pytest.mark.asyncio()
    async def test_update_voice_name(self, app: Any, mock_redis: AsyncMock) -> None:
        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.tts_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.api.tts_config.get_settings", _env_patch()),
            patch(
                "src.api.tts_config._reinitialize_with_config", new_callable=AsyncMock
            ) as mock_reinit,
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.patch(
                    "/admin/tts/config",
                    json={"voice_name": "uk-UA-Neural2-A"},
                )

        assert response.status_code == 200
        data = response.json()
        assert data["config"]["voice_name"] == "uk-UA-Neural2-A"
        mock_redis.set.assert_called_once()
        mock_reinit.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_partial_update_preserves_other_fields(
        self, app: Any, mock_redis: AsyncMock
    ) -> None:
        existing = {"voice_name": "uk-UA-Neural2-A", "speaking_rate": 1.2}
        mock_redis._store["tts:config"] = json.dumps(existing)

        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.tts_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.api.tts_config.get_settings", _env_patch()),
            patch("src.api.tts_config._reinitialize_with_config", new_callable=AsyncMock),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.patch(
                    "/admin/tts/config",
                    json={"pitch": 2.0},
                )

        assert response.status_code == 200
        # Check what was saved to Redis
        saved = json.loads(mock_redis._store["tts:config"])
        assert saved["voice_name"] == "uk-UA-Neural2-A"
        assert saved["speaking_rate"] == 1.2
        assert saved["pitch"] == 2.0

    @pytest.mark.asyncio()
    async def test_validation_rate_out_of_range(self, app: Any, mock_redis: AsyncMock) -> None:
        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.tts_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.api.tts_config.get_settings", _env_patch()),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.patch(
                    "/admin/tts/config",
                    json={"speaking_rate": 5.0},
                )

        assert response.status_code == 422

    @pytest.mark.asyncio()
    async def test_validation_pitch_out_of_range(self, app: Any, mock_redis: AsyncMock) -> None:
        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.tts_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.api.tts_config.get_settings", _env_patch()),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.patch(
                    "/admin/tts/config",
                    json={"pitch": -25.0},
                )

        assert response.status_code == 422

    @pytest.mark.asyncio()
    async def test_validation_invalid_voice_name(self, app: Any, mock_redis: AsyncMock) -> None:
        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.tts_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.api.tts_config.get_settings", _env_patch()),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.patch(
                    "/admin/tts/config",
                    json={"voice_name": "invalid voice!@#"},
                )

        assert response.status_code == 422


class TestPostTest:
    """Test POST /admin/tts/test."""

    @pytest.mark.asyncio()
    async def test_returns_audio_on_success(self, app: Any, mock_redis: AsyncMock) -> None:
        mock_engine = AsyncMock()
        mock_engine.synthesize = AsyncMock(return_value=b"\x00\x01" * 100)

        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.tts_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.api.tts_config.get_settings", _env_patch()),
            patch("src.tts.get_engine", return_value=mock_engine),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post("/admin/tts/test")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "audio_base64" in data
        assert data["duration_ms"] >= 0

    @pytest.mark.asyncio()
    async def test_returns_error_on_failure(self, app: Any, mock_redis: AsyncMock) -> None:
        mock_engine = AsyncMock()
        mock_engine.synthesize = AsyncMock(side_effect=RuntimeError("TTS failed"))

        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.tts_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.api.tts_config.get_settings", _env_patch()),
            patch("src.tts.get_engine", return_value=mock_engine),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post("/admin/tts/test")

        data = response.json()
        assert data["success"] is False
        assert "TTS failed" in data["error"]

    @pytest.mark.asyncio()
    async def test_503_when_engine_not_initialized(self, app: Any, mock_redis: AsyncMock) -> None:
        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.tts_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.api.tts_config.get_settings", _env_patch()),
            patch("src.tts.get_engine", return_value=None),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post("/admin/tts/test")

        assert response.status_code == 503


class TestPostReset:
    """Test POST /admin/tts/config/reset."""

    @pytest.mark.asyncio()
    async def test_deletes_redis_key_and_reinitializes(
        self, app: Any, mock_redis: AsyncMock
    ) -> None:
        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.tts_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.api.tts_config.get_settings", _env_patch()),
            patch(
                "src.api.tts_config._reinitialize_with_config", new_callable=AsyncMock
            ) as mock_reinit,
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post("/admin/tts/config/reset")

        assert response.status_code == 200
        data = response.json()
        assert data["source"] == "env"
        assert data["config"]["voice_name"] == "uk-UA-Wavenet-A"
        mock_redis.delete.assert_called_once_with("tts:config")
        mock_reinit.assert_awaited_once()
