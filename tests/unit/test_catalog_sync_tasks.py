"""Unit tests for catalog sync Celery tasks."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# Patch targets — imports are deferred inside async functions,
# so we patch at the source module level.
_PATCH_ENGINE = "sqlalchemy.ext.asyncio.create_async_engine"
_PATCH_REDIS = "redis.asyncio.Redis"
_PATCH_ONEC = "src.onec_client.client.OneCClient"
_PATCH_SYNC = "src.onec_client.sync.CatalogSyncService"
_PATCH_SETTINGS = "src.tasks.catalog_sync_tasks.get_settings"


def _mock_settings(
    username: str = "web_service",
    timeout: int = 120,
    full_sync_timeout: int = 600,
) -> MagicMock:
    """Create a mock settings object."""
    settings = MagicMock()
    settings.onec.username = username
    settings.onec.password = "pass"
    settings.onec.url = "http://192.168.11.9"
    settings.onec.timeout = timeout
    settings.onec.full_sync_timeout = full_sync_timeout
    settings.onec.stock_cache_ttl = 300
    settings.database.url = "postgresql+asyncpg://test:test@localhost/test"
    settings.redis.url = "redis://localhost:6379/0"
    return settings


def _mock_engine() -> MagicMock:
    """Create a mock async engine."""
    engine = MagicMock()
    engine.dispose = AsyncMock()
    return engine


def _mock_redis() -> AsyncMock:
    """Create a mock Redis client."""
    redis = AsyncMock()
    redis.ping = AsyncMock()
    redis.aclose = AsyncMock()
    return redis


def _mock_onec() -> AsyncMock:
    """Create a mock OneCClient."""
    onec = AsyncMock()
    onec.open = AsyncMock()
    onec.close = AsyncMock()
    return onec


class TestCatalogFullSync:
    """Tests for catalog_full_sync task."""

    @pytest.mark.asyncio
    async def test_skips_when_onec_not_configured(self) -> None:
        """Full sync should skip when ONEC_USERNAME is empty."""
        from src.tasks.catalog_sync_tasks import _catalog_full_sync_async

        mock_task = MagicMock()

        with patch(_PATCH_SETTINGS) as mock_get_settings:
            mock_get_settings.return_value = _mock_settings(username="")
            result = await _catalog_full_sync_async(mock_task)

        assert result["status"] == "skipped"
        assert result["reason"] == "onec_not_configured"

    @pytest.mark.asyncio
    async def test_uses_full_sync_timeout(self) -> None:
        """Full sync should create OneCClient with full_sync_timeout."""
        from src.tasks.catalog_sync_tasks import _catalog_full_sync_async

        mock_task = MagicMock()
        mock_onec = _mock_onec()
        mock_sync = AsyncMock()
        mock_sync.full_sync = AsyncMock()

        with (
            patch(_PATCH_SETTINGS) as mock_get_settings,
            patch(_PATCH_ENGINE, return_value=_mock_engine()),
            patch(_PATCH_REDIS) as mock_redis_cls,
            patch(_PATCH_ONEC, return_value=mock_onec) as mock_onec_cls,
            patch(_PATCH_SYNC, return_value=mock_sync),
        ):
            mock_get_settings.return_value = _mock_settings(
                timeout=120, full_sync_timeout=600
            )
            mock_redis_cls.from_url.return_value = _mock_redis()

            result = await _catalog_full_sync_async(mock_task)

        assert result["status"] == "ok"
        # Verify OneCClient was created with full_sync_timeout (600), not regular timeout (120)
        mock_onec_cls.assert_called_once_with(
            base_url="http://192.168.11.9",
            username="web_service",
            password="pass",
            timeout=600,
        )
        mock_sync.full_sync.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cleanup_on_success(self) -> None:
        """Resources should be cleaned up after successful sync."""
        from src.tasks.catalog_sync_tasks import _catalog_full_sync_async

        mock_task = MagicMock()
        onec = _mock_onec()
        redis = _mock_redis()
        engine = _mock_engine()
        mock_sync = AsyncMock()
        mock_sync.full_sync = AsyncMock()

        with (
            patch(_PATCH_SETTINGS) as mock_get_settings,
            patch(_PATCH_ENGINE, return_value=engine),
            patch(_PATCH_REDIS) as mock_redis_cls,
            patch(_PATCH_ONEC, return_value=onec),
            patch(_PATCH_SYNC, return_value=mock_sync),
        ):
            mock_get_settings.return_value = _mock_settings()
            mock_redis_cls.from_url.return_value = redis

            await _catalog_full_sync_async(mock_task)

        # Verify all resources were cleaned up
        onec.close.assert_awaited_once()
        redis.aclose.assert_awaited_once()
        engine.dispose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cleanup_on_failure(self) -> None:
        """Resources should be cleaned up even when sync fails."""
        from src.tasks.catalog_sync_tasks import _catalog_full_sync_async

        mock_task = MagicMock()
        mock_task.retry = MagicMock(side_effect=Exception("retry"))

        onec = _mock_onec()
        redis = _mock_redis()
        engine = _mock_engine()
        mock_sync = AsyncMock()
        mock_sync.full_sync = AsyncMock(side_effect=RuntimeError("1C down"))

        with (
            patch(_PATCH_SETTINGS) as mock_get_settings,
            patch(_PATCH_ENGINE, return_value=engine),
            patch(_PATCH_REDIS) as mock_redis_cls,
            patch(_PATCH_ONEC, return_value=onec),
            patch(_PATCH_SYNC, return_value=mock_sync),
        ):
            mock_get_settings.return_value = _mock_settings()
            mock_redis_cls.from_url.return_value = redis

            with pytest.raises(Exception, match="retry"):
                await _catalog_full_sync_async(mock_task)

        # Resources must still be cleaned up
        onec.close.assert_awaited_once()
        redis.aclose.assert_awaited_once()
        engine.dispose.assert_awaited_once()


class TestCatalogIncrementalSync:
    """Tests for catalog_incremental_sync task."""

    @pytest.mark.asyncio
    async def test_skips_when_onec_not_configured(self) -> None:
        """Incremental sync should skip when ONEC_USERNAME is empty."""
        from src.tasks.catalog_sync_tasks import _catalog_incremental_sync_async

        mock_task = MagicMock()

        with patch(_PATCH_SETTINGS) as mock_get_settings:
            mock_get_settings.return_value = _mock_settings(username="")
            result = await _catalog_incremental_sync_async(mock_task)

        assert result["status"] == "skipped"
        assert result["reason"] == "onec_not_configured"

    @pytest.mark.asyncio
    async def test_uses_regular_timeout(self) -> None:
        """Incremental sync should use regular timeout (not full_sync_timeout)."""
        from src.tasks.catalog_sync_tasks import _catalog_incremental_sync_async

        mock_task = MagicMock()
        mock_onec = _mock_onec()
        mock_sync = AsyncMock()
        mock_sync.incremental_sync = AsyncMock()

        with (
            patch(_PATCH_SETTINGS) as mock_get_settings,
            patch(_PATCH_ENGINE, return_value=_mock_engine()),
            patch(_PATCH_REDIS) as mock_redis_cls,
            patch(_PATCH_ONEC, return_value=mock_onec) as mock_onec_cls,
            patch(_PATCH_SYNC, return_value=mock_sync),
        ):
            mock_get_settings.return_value = _mock_settings(
                timeout=120, full_sync_timeout=600
            )
            mock_redis_cls.from_url.return_value = _mock_redis()

            result = await _catalog_incremental_sync_async(mock_task)

        assert result["status"] == "ok"
        # Verify OneCClient was created with regular timeout (120), not full_sync_timeout
        mock_onec_cls.assert_called_once_with(
            base_url="http://192.168.11.9",
            username="web_service",
            password="pass",
            timeout=120,
        )
        mock_sync.incremental_sync.assert_awaited_once()


class TestCeleryAppRegistration:
    """Verify catalog tasks are registered in Celery app config."""

    def test_catalog_tasks_in_include(self) -> None:
        """catalog_sync_tasks module is in Celery include list."""
        from src.tasks.celery_app import app

        assert "src.tasks.catalog_sync_tasks" in app.conf.include

    def test_catalog_queue_in_routes(self) -> None:
        """catalog_sync_tasks are routed to 'catalog' queue."""
        from src.tasks.celery_app import app

        routes = app.conf.task_routes
        assert "src.tasks.catalog_sync_tasks.*" in routes
        assert routes["src.tasks.catalog_sync_tasks.*"] == {"queue": "catalog"}

    def test_catalog_schedules_in_beat(self) -> None:
        """Both catalog sync schedules are in beat_schedule."""
        from src.tasks.celery_app import app

        assert "catalog-full-sync" in app.conf.beat_schedule
        assert "catalog-incremental-sync" in app.conf.beat_schedule

        full = app.conf.beat_schedule["catalog-full-sync"]
        assert full["task"] == "src.tasks.catalog_sync_tasks.catalog_full_sync"

        incr = app.conf.beat_schedule["catalog-incremental-sync"]
        assert incr["task"] == "src.tasks.catalog_sync_tasks.catalog_incremental_sync"


class TestOneCSettingsFullSyncTimeout:
    """Verify full_sync_timeout setting."""

    def test_default_value(self) -> None:
        """full_sync_timeout defaults to 1200."""
        from src.config import OneCSettings

        settings = OneCSettings()
        assert settings.full_sync_timeout == 1200

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """full_sync_timeout can be overridden via ONEC_FULL_SYNC_TIMEOUT."""
        monkeypatch.setenv("ONEC_FULL_SYNC_TIMEOUT", "900")
        from src.config import OneCSettings

        settings = OneCSettings()
        assert settings.full_sync_timeout == 900
