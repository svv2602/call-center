"""Unit tests for system status and config reload API."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestCeleryHealth:
    """Test GET /health/celery."""

    @pytest.mark.asyncio
    async def test_celery_healthy(self) -> None:
        from src.api.system import celery_health

        mock_inspect = MagicMock()
        mock_inspect.ping.return_value = {"worker1": {"ok": "pong"}}
        mock_inspect.active.return_value = {"worker1": []}

        with (
            patch("src.api.system.celery_health.__module__", "src.api.system"),
            patch("src.tasks.celery_app.app") as mock_app,
        ):
                mock_app.control.inspect.return_value = mock_inspect
                result = await celery_health()

        assert result["status"] == "ok"
        assert result["workers_online"] == 1

    @pytest.mark.asyncio
    async def test_celery_no_workers(self) -> None:
        from src.api.system import celery_health

        mock_inspect = MagicMock()
        mock_inspect.ping.return_value = None
        mock_inspect.active.return_value = None

        with patch("src.tasks.celery_app.app") as mock_app:
            mock_app.control.inspect.return_value = mock_inspect
            result = await celery_health()

        assert result["status"] == "degraded"
        assert result["workers_online"] == 0

    @pytest.mark.asyncio
    async def test_celery_connection_error(self) -> None:
        from src.api.system import celery_health

        with patch("src.tasks.celery_app.app") as mock_app:
            mock_app.control.inspect.side_effect = Exception("connection refused")
            result = await celery_health()

        assert result["status"] == "unavailable"
        assert "error" in result


class TestConfigReload:
    """Test POST /admin/config/reload."""

    @pytest.mark.asyncio
    @patch("src.api.auth.require_admin", new_callable=AsyncMock, return_value={"sub": "test", "role": "admin"})
    async def test_reload_config(self, _mock_auth: AsyncMock) -> None:
        from src.api.system import reload_config

        with patch("src.api.system.get_settings") as mock_settings:
            mock_settings.return_value.quality.llm_model = "old-model"
            mock_settings.return_value.feature_flags.stt_provider = "google"
            mock_settings.return_value.logging.level = "INFO"
            result = await reload_config()

        assert result["status"] == "reloaded"
        assert "changes" in result


class TestSystemStatus:
    """Test GET /admin/system-status."""

    @pytest.mark.asyncio
    @patch("src.api.auth.require_admin", new_callable=AsyncMock, return_value={"sub": "test", "role": "admin"})
    @patch("src.api.system._get_engine", new_callable=AsyncMock)
    async def test_system_status_basic(self, mock_engine: AsyncMock, _mock_auth: AsyncMock) -> None:
        from src.api.system import system_status

        # Mock engine to raise so we test the except path
        mock_engine.side_effect = Exception("no db")

        result = await system_status()
        assert "version" in result
        assert "uptime_seconds" in result
        assert result["postgres"] == "unavailable"
