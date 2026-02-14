"""Unit tests for CLI operations commands."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from src.cli.main import app

runner = CliRunner()


class TestCeleryStatus:
    @patch("src.tasks.celery_app.app")
    def test_celery_status_with_workers(self, mock_celery: MagicMock) -> None:
        mock_inspect = MagicMock()
        mock_inspect.ping.return_value = {"worker1@host": {"ok": "pong"}}
        mock_inspect.active.return_value = {"worker1@host": []}
        mock_inspect.scheduled.return_value = {"worker1@host": []}
        mock_celery.control.inspect.return_value = mock_inspect

        result = runner.invoke(app, ["ops", "celery-status"])
        assert result.exit_code == 0
        assert "worker1@host" in result.output

    @patch("src.tasks.celery_app.app")
    def test_celery_status_no_workers(self, mock_celery: MagicMock) -> None:
        mock_inspect = MagicMock()
        mock_inspect.ping.return_value = None
        mock_celery.control.inspect.return_value = mock_inspect

        result = runner.invoke(app, ["ops", "celery-status"])
        assert result.exit_code == 1
        assert "No workers" in result.output


class TestConfigReload:
    @patch("src.config.get_settings")
    def test_config_reload(self, mock_settings: MagicMock) -> None:
        mock_settings.return_value.quality.llm_model = "model"
        mock_settings.return_value.feature_flags.stt_provider = "google"
        mock_settings.return_value.logging.level = "INFO"

        result = runner.invoke(app, ["ops", "config-reload"])
        assert result.exit_code == 0
        assert "reloaded" in result.output.lower()


class TestSystemStatus:
    @patch("src.tasks.celery_app.app")
    @patch("src.config.get_settings")
    def test_system_status(self, mock_settings: MagicMock, mock_celery: MagicMock) -> None:
        mock_settings.return_value.database.url = "postgresql+asyncpg://user:pass@localhost:5432/db"
        mock_settings.return_value.redis.url = "redis://localhost:6379/0"
        mock_settings.return_value.backup.backup_dir = "/tmp/nonexistent_backups_test"

        mock_inspect = MagicMock()
        mock_inspect.ping.return_value = None
        mock_celery.control.inspect.return_value = mock_inspect

        result = runner.invoke(app, ["ops", "system-status"])
        assert result.exit_code == 0
        assert "Version" in result.output
