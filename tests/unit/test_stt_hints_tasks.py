"""Unit tests for STT hints refresh Celery task."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_PATCH_SETTINGS = "src.tasks.stt_hints_tasks.get_settings"
_PATCH_ENGINE = "sqlalchemy.ext.asyncio.create_async_engine"
_PATCH_REDIS = "redis.asyncio.Redis"


def _mock_settings() -> MagicMock:
    settings = MagicMock()
    settings.database.url = "postgresql+asyncpg://test:test@localhost/test"
    settings.redis.url = "redis://localhost:6379/0"
    return settings


class TestRefreshSttHints:
    """Tests for refresh_stt_hints task."""

    @pytest.mark.asyncio
    async def test_manual_trigger_runs_immediately(self) -> None:
        """Manual trigger should skip schedule check and run refresh."""
        from src.tasks.stt_hints_tasks import _refresh_async

        mock_task = MagicMock()
        mock_engine = MagicMock()
        mock_engine.dispose = AsyncMock()
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        mock_redis.aclose = AsyncMock()

        stats = {
            "base_count": 100,
            "auto_count": 50,
            "custom_count": 10,
            "total": 160,
            "updated_at": "2026-02-24T10:00:00Z",
        }

        with (
            patch(_PATCH_SETTINGS) as mock_get_settings,
            patch(_PATCH_ENGINE) as mock_create_engine,
            patch(_PATCH_REDIS) as MockRedis,
            patch("src.stt.phrase_hints.refresh_phrase_hints", new_callable=AsyncMock) as mock_refresh,
        ):
            mock_get_settings.return_value = _mock_settings()
            mock_create_engine.return_value = mock_engine
            MockRedis.from_url.return_value = mock_redis
            mock_refresh.return_value = stats

            result = await _refresh_async(mock_task, "manual")

        assert result["status"] == "ok"
        assert result["triggered_by"] == "manual"
        assert result["base_count"] == 100
        mock_refresh.assert_called_once_with(mock_engine, mock_redis)

    @pytest.mark.asyncio
    async def test_beat_trigger_checks_schedule(self) -> None:
        """Beat trigger should check schedule and run if it's time."""
        from src.tasks.stt_hints_tasks import _refresh_async

        mock_task = MagicMock()
        mock_engine = MagicMock()
        mock_engine.dispose = AsyncMock()
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        mock_redis.aclose = AsyncMock()
        mock_redis_check = AsyncMock()
        mock_redis_check.aclose = AsyncMock()

        stats = {"base_count": 100, "auto_count": 50, "custom_count": 10, "total": 160, "updated_at": "2026-02-24"}

        with (
            patch(_PATCH_SETTINGS) as mock_get_settings,
            patch(_PATCH_ENGINE) as mock_create_engine,
            patch(_PATCH_REDIS) as MockRedis,
            patch("src.stt.phrase_hints.refresh_phrase_hints", new_callable=AsyncMock) as mock_refresh,
            patch("src.tasks.schedule_utils.load_schedules", new_callable=AsyncMock) as mock_load,
            patch("src.tasks.schedule_utils.should_run_now") as mock_should_run,
        ):
            mock_get_settings.return_value = _mock_settings()
            mock_create_engine.return_value = mock_engine
            # First from_url call → redis_check, second → main redis
            MockRedis.from_url.side_effect = [mock_redis_check, mock_redis]
            mock_load.return_value = {"refresh-stt-hints": {"enabled": True, "frequency": "weekly", "hour": 10, "day_of_week": 6}}
            mock_should_run.return_value = True
            mock_refresh.return_value = stats

            result = await _refresh_async(mock_task, "beat")

        assert result["status"] == "ok"
        assert result["triggered_by"] == "beat"
        mock_should_run.assert_called_once()
        mock_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_beat_trigger_skips_if_not_time(self) -> None:
        """Beat trigger should skip when schedule says not now."""
        from src.tasks.stt_hints_tasks import _refresh_async

        mock_task = MagicMock()
        mock_redis_check = AsyncMock()
        mock_redis_check.aclose = AsyncMock()

        with (
            patch(_PATCH_SETTINGS) as mock_get_settings,
            patch(_PATCH_REDIS) as MockRedis,
            patch("src.tasks.schedule_utils.load_schedules", new_callable=AsyncMock) as mock_load,
            patch("src.tasks.schedule_utils.should_run_now") as mock_should_run,
        ):
            mock_get_settings.return_value = _mock_settings()
            MockRedis.from_url.return_value = mock_redis_check
            mock_load.return_value = {"refresh-stt-hints": {"enabled": True, "frequency": "weekly", "hour": 10, "day_of_week": 6}}
            mock_should_run.return_value = False

            result = await _refresh_async(mock_task, "beat")

        assert result["status"] == "skipped"
        assert result["reason"] == "not_scheduled"

    @pytest.mark.asyncio
    async def test_skips_when_redis_unavailable(self) -> None:
        """Should skip when Redis ping fails."""
        from src.tasks.stt_hints_tasks import _refresh_async

        mock_task = MagicMock()
        mock_engine = MagicMock()
        mock_engine.dispose = AsyncMock()
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=ConnectionError("refused"))
        mock_redis.aclose = AsyncMock()

        with (
            patch(_PATCH_SETTINGS) as mock_get_settings,
            patch(_PATCH_ENGINE) as mock_create_engine,
            patch(_PATCH_REDIS) as MockRedis,
        ):
            mock_get_settings.return_value = _mock_settings()
            mock_create_engine.return_value = mock_engine
            MockRedis.from_url.return_value = mock_redis

            result = await _refresh_async(mock_task, "manual")

        assert result["status"] == "skipped"
        assert result["reason"] == "redis_unavailable"
