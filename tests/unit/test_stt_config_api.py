"""Tests for STT phrase hints admin API endpoints."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.stt_config import router


async def _fake_require_perm(*_args: object, **_kwargs: object) -> dict[str, Any]:
    return {"sub": "test-user", "role": "admin"}


@pytest.fixture()
def mock_redis():
    store: dict[str, bytes] = {}
    mock = AsyncMock()

    async def _get(key: str) -> bytes | None:
        return store.get(key)

    async def _set(key: str, value: str) -> None:
        store[key] = value.encode() if isinstance(value, str) else value

    async def _delete(key: str) -> None:
        store.pop(key, None)

    mock.get = AsyncMock(side_effect=_get)
    mock.set = AsyncMock(side_effect=_set)
    mock.delete = AsyncMock(side_effect=_delete)
    mock._store = store
    return mock


@pytest.fixture()
def app():
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)
    return test_app


def _settings_patch():
    """Patch Settings to return known defaults."""
    mock_settings = MagicMock()
    mock_settings.return_value.redis.url = "redis://localhost"
    mock_settings.return_value.database.url = "postgresql+asyncpg://test/test"
    return mock_settings


class TestGetPhraseHints:
    """Test GET /admin/stt/phrase-hints."""

    @pytest.mark.asyncio()
    async def test_returns_base_only_on_empty_redis(
        self, app: Any, mock_redis: AsyncMock
    ) -> None:
        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.stt_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.api.stt_config.get_settings", _settings_patch()),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.get("/admin/stt/phrase-hints")

        assert response.status_code == 200
        data = response.json()
        assert data["stats"]["base_count"] > 0
        assert data["stats"]["auto_count"] == 0
        assert data["stats"]["custom_count"] == 0
        assert data["stats"]["google_limit"] == 5000
        assert data["custom_phrases"] == []

    @pytest.mark.asyncio()
    async def test_returns_stats_with_redis_data(
        self, app: Any, mock_redis: AsyncMock
    ) -> None:
        redis_data = json.dumps({
            "base": ["a", "b", "c"],
            "auto": ["d", "e"],
            "custom": ["f"],
            "updated_at": "2024-01-01T00:00:00Z",
        })
        mock_redis._store["stt:phrase_hints"] = redis_data.encode()

        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.stt_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.api.stt_config.get_settings", _settings_patch()),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.get("/admin/stt/phrase-hints")

        data = response.json()
        assert data["stats"]["base_count"] == 3
        assert data["stats"]["auto_count"] == 2
        assert data["stats"]["custom_count"] == 1
        assert data["stats"]["total"] == 6
        assert data["stats"]["updated_at"] == "2024-01-01T00:00:00Z"
        assert data["custom_phrases"] == ["f"]


class TestPatchCustomPhrases:
    """Test PATCH /admin/stt/phrase-hints/custom."""

    @pytest.mark.asyncio()
    async def test_updates_custom_list(self, app: Any, mock_redis: AsyncMock) -> None:
        existing = json.dumps({"base": ["a"], "auto": ["b"], "custom": []})
        mock_redis._store["stt:phrase_hints"] = existing.encode()

        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.stt_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.api.stt_config.get_settings", _settings_patch()),
            patch("src.stt.phrase_hints.invalidate_cache"),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.patch(
                    "/admin/stt/phrase-hints/custom",
                    json={"phrases": ["term1", "term2"]},
                )

        assert response.status_code == 200
        data = response.json()
        assert data["custom_count"] == 2

    @pytest.mark.asyncio()
    async def test_validation_too_many_phrases(self, app: Any, mock_redis: AsyncMock) -> None:
        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.stt_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.api.stt_config.get_settings", _settings_patch()),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.patch(
                    "/admin/stt/phrase-hints/custom",
                    json={"phrases": ["x"] * 1001},
                )

        assert response.status_code == 422

    @pytest.mark.asyncio()
    async def test_validation_phrase_too_long(self, app: Any, mock_redis: AsyncMock) -> None:
        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.stt_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.api.stt_config.get_settings", _settings_patch()),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.patch(
                    "/admin/stt/phrase-hints/custom",
                    json={"phrases": ["x" * 201]},
                )

        assert response.status_code == 422

    @pytest.mark.asyncio()
    async def test_filters_empty_strings(self, app: Any, mock_redis: AsyncMock) -> None:
        existing = json.dumps({"base": ["a"], "auto": [], "custom": []})
        mock_redis._store["stt:phrase_hints"] = existing.encode()

        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.stt_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.api.stt_config.get_settings", _settings_patch()),
            patch("src.stt.phrase_hints.invalidate_cache"),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.patch(
                    "/admin/stt/phrase-hints/custom",
                    json={"phrases": ["valid", "", "  ", "also valid"]},
                )

        assert response.status_code == 200
        data = response.json()
        assert data["custom_count"] == 2


class TestPostRefresh:
    """Test POST /admin/stt/phrase-hints/refresh."""

    @pytest.mark.asyncio()
    async def test_refresh_returns_stats(self, app: Any, mock_redis: AsyncMock) -> None:
        mock_engine = AsyncMock()
        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([]))
        mock_conn.execute = AsyncMock(return_value=mock_result)
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_engine.begin = MagicMock(return_value=mock_ctx)

        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.stt_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.api.stt_config._get_engine", AsyncMock(return_value=mock_engine)),
            patch("src.api.stt_config.get_settings", _settings_patch()),
            patch("src.stt.phrase_hints.invalidate_cache"),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post("/admin/stt/phrase-hints/refresh")

        assert response.status_code == 200
        data = response.json()
        assert "base_count" in data
        assert data["base_count"] > 0
        assert "auto_count" in data
        assert "updated_at" in data

    @pytest.mark.asyncio()
    async def test_refresh_db_error_returns_500(self, app: Any, mock_redis: AsyncMock) -> None:
        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.stt_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch(
                "src.api.stt_config._get_engine",
                AsyncMock(side_effect=RuntimeError("DB down")),
            ),
            patch("src.api.stt_config.get_settings", _settings_patch()),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post("/admin/stt/phrase-hints/refresh")

        assert response.status_code == 500


class TestPostReset:
    """Test POST /admin/stt/phrase-hints/reset."""

    @pytest.mark.asyncio()
    async def test_deletes_redis_key(self, app: Any, mock_redis: AsyncMock) -> None:
        mock_redis._store["stt:phrase_hints"] = json.dumps(
            {"base": ["a"], "auto": ["b"], "custom": ["c"]}
        ).encode()

        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.stt_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.api.stt_config.get_settings", _settings_patch()),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post("/admin/stt/phrase-hints/reset")

        assert response.status_code == 200
        data = response.json()
        assert data["auto_count"] == 0
        assert data["custom_count"] == 0
        assert data["base_count"] > 0
        mock_redis.delete.assert_called_once_with("stt:phrase_hints")


class TestGetPhraseHintsExtended:
    """Test extended GET /admin/stt/phrase-hints response."""

    @pytest.mark.asyncio()
    async def test_returns_all_phrase_arrays(
        self, app: Any, mock_redis: AsyncMock
    ) -> None:
        redis_data = json.dumps({
            "base": ["brand1", "brand2"],
            "auto": ["auto1"],
            "custom": ["custom1"],
            "base_customized": True,
            "updated_at": "2024-01-01T00:00:00Z",
        })
        mock_redis._store["stt:phrase_hints"] = redis_data.encode()

        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.stt_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.api.stt_config.get_settings", _settings_patch()),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.get("/admin/stt/phrase-hints")

        data = response.json()
        assert data["base_phrases"] == ["brand1", "brand2"]
        assert data["auto_phrases"] == ["auto1"]
        assert data["custom_phrases"] == ["custom1"]
        assert data["stats"]["base_customized"] is True


class TestPatchBasePhrases:
    """Test PATCH /admin/stt/phrase-hints/base."""

    @pytest.mark.asyncio()
    async def test_updates_base_phrases(self, app: Any, mock_redis: AsyncMock) -> None:
        existing = json.dumps({"base": ["a"], "auto": ["b"], "custom": ["c"]})
        mock_redis._store["stt:phrase_hints"] = existing.encode()

        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.stt_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.api.stt_config.get_settings", _settings_patch()),
            patch("src.stt.phrase_hints.invalidate_cache"),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.patch(
                    "/admin/stt/phrase-hints/base",
                    json={"phrases": ["new1", "new2"]},
                )

        assert response.status_code == 200
        data = response.json()
        assert data["base_count"] == 2
        assert data["base_customized"] is True

    @pytest.mark.asyncio()
    async def test_validation_too_many_phrases(self, app: Any, mock_redis: AsyncMock) -> None:
        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.stt_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.api.stt_config.get_settings", _settings_patch()),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.patch(
                    "/admin/stt/phrase-hints/base",
                    json={"phrases": ["x"] * 2001},
                )

        assert response.status_code == 422


class TestPostBaseReset:
    """Test POST /admin/stt/phrase-hints/base/reset."""

    @pytest.mark.asyncio()
    async def test_resets_base_to_defaults(self, app: Any, mock_redis: AsyncMock) -> None:
        existing = json.dumps({
            "base": ["custom1"],
            "auto": ["auto1"],
            "custom": ["cust1"],
            "base_customized": True,
        })
        mock_redis._store["stt:phrase_hints"] = existing.encode()

        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.stt_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.api.stt_config.get_settings", _settings_patch()),
            patch("src.stt.phrase_hints.invalidate_cache"),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post("/admin/stt/phrase-hints/base/reset")

        assert response.status_code == 200
        data = response.json()
        assert data["base_customized"] is False
        assert data["base_count"] > 0


class TestGetWordOverrides:
    """Test GET /admin/stt/word-overrides."""

    @pytest.mark.asyncio()
    async def test_returns_defaults_on_empty_redis(
        self, app: Any, mock_redis: AsyncMock
    ) -> None:
        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.stt_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.api.stt_config.get_settings", _settings_patch()),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.get("/admin/stt/word-overrides")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 5  # 5 default overrides
        assert data["is_customized"] is False
        assert "weather" in data["overrides"]

    @pytest.mark.asyncio()
    async def test_returns_custom_overrides(
        self, app: Any, mock_redis: AsyncMock
    ) -> None:
        custom = json.dumps({"hello": "хелло"})
        mock_redis._store["stt:word_overrides"] = custom.encode()

        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.stt_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.api.stt_config.get_settings", _settings_patch()),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.get("/admin/stt/word-overrides")

        data = response.json()
        assert data["count"] == 1
        assert data["is_customized"] is True
        assert data["overrides"]["hello"] == "хелло"


class TestPatchWordOverrides:
    """Test PATCH /admin/stt/word-overrides."""

    @pytest.mark.asyncio()
    async def test_saves_overrides(self, app: Any, mock_redis: AsyncMock) -> None:
        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.stt_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.api.stt_config.get_settings", _settings_patch()),
            patch("src.stt.phrase_hints.invalidate_cache"),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.patch(
                    "/admin/stt/word-overrides",
                    json={"overrides": {"test": "тест", "hello": "хелло"}},
                )

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
        assert data["is_customized"] is True

    @pytest.mark.asyncio()
    async def test_rejects_non_latin_keys(self, app: Any, mock_redis: AsyncMock) -> None:
        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.stt_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.api.stt_config.get_settings", _settings_patch()),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.patch(
                    "/admin/stt/word-overrides",
                    json={"overrides": {"тест": "test"}},
                )

        assert response.status_code == 422

    @pytest.mark.asyncio()
    async def test_rejects_too_many_overrides(self, app: Any, mock_redis: AsyncMock) -> None:
        overrides = {f"word{i}": f"слово{i}" for i in range(501)}
        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.stt_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.api.stt_config.get_settings", _settings_patch()),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.patch(
                    "/admin/stt/word-overrides",
                    json={"overrides": overrides},
                )

        assert response.status_code == 422

    @pytest.mark.asyncio()
    async def test_normalizes_keys_to_lowercase(self, app: Any, mock_redis: AsyncMock) -> None:
        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.stt_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.api.stt_config.get_settings", _settings_patch()),
            patch("src.stt.phrase_hints.invalidate_cache"),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.patch(
                    "/admin/stt/word-overrides",
                    json={"overrides": {"Hello": "хелло"}},
                )

        assert response.status_code == 200
        data = response.json()
        assert "hello" in data["overrides"]


class TestResetWordOverrides:
    """Test POST /admin/stt/word-overrides/reset."""

    @pytest.mark.asyncio()
    async def test_resets_to_defaults(self, app: Any, mock_redis: AsyncMock) -> None:
        custom = json.dumps({"hello": "хелло"})
        mock_redis._store["stt:word_overrides"] = custom.encode()

        with (
            patch("src.api.auth.require_admin", _fake_require_perm),
            patch("src.api.stt_config._get_redis", AsyncMock(return_value=mock_redis)),
            patch("src.api.stt_config.get_settings", _settings_patch()),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post("/admin/stt/word-overrides/reset")

        assert response.status_code == 200
        data = response.json()
        assert data["is_customized"] is False
        assert data["count"] == 5  # 5 default overrides
        assert "weather" in data["overrides"]
        mock_redis.delete.assert_called_once_with("stt:word_overrides")
