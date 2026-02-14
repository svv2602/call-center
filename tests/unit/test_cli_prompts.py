"""Unit tests for CLI prompts commands."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

from typer.testing import CliRunner

from src.cli.main import app

runner = CliRunner()


class TestPromptsList:
    """Test 'prompts list' command."""

    @patch("src.cli.prompts.asyncio.run")
    def test_shows_versions(self, mock_run: MagicMock) -> None:
        uid = str(uuid4())
        mock_run.return_value = [
            {
                "id": uid,
                "name": "v1.0-tire-search",
                "is_active": True,
                "created_at": "2026-02-14T00:00:00",
            },
        ]
        result = runner.invoke(app, ["prompts", "list"])
        assert result.exit_code == 0
        assert "v1.0-tire-search" in result.output
        assert "\u2705" in result.output

    @patch("src.cli.prompts.asyncio.run", return_value=[])
    def test_no_versions(self, mock_run: MagicMock) -> None:
        result = runner.invoke(app, ["prompts", "list"])
        assert result.exit_code == 0
        assert "No prompt versions found" in result.output


class TestPromptsActivate:
    """Test 'prompts activate' command."""

    @patch("src.cli.prompts.asyncio.run")
    def test_activate_success(self, mock_run: MagicMock) -> None:
        uid = str(uuid4())
        mock_run.return_value = {"id": uid, "name": "v2.0-improved"}
        result = runner.invoke(app, ["prompts", "activate", uid])
        assert result.exit_code == 0
        assert "Activated" in result.output

    def test_invalid_uuid(self) -> None:
        result = runner.invoke(app, ["prompts", "activate", "not-a-uuid"])
        assert result.exit_code == 1
        assert "Invalid UUID" in result.output

    @patch("src.cli.prompts.asyncio.run", side_effect=ValueError("not found"))
    def test_version_not_found(self, mock_run: MagicMock) -> None:
        uid = str(uuid4())
        result = runner.invoke(app, ["prompts", "activate", uid])
        assert result.exit_code == 1
        assert "not found" in result.output


class TestPromptsRollback:
    """Test 'prompts rollback' command."""

    @patch("src.cli.prompts.asyncio.run")
    def test_rollback_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = {"id": str(uuid4()), "name": "v1.0-previous"}
        result = runner.invoke(app, ["prompts", "rollback"])
        assert result.exit_code == 0
        assert "Rolled back" in result.output

    @patch("src.cli.prompts.asyncio.run", side_effect=ValueError("No previous version"))
    def test_no_previous_version(self, mock_run: MagicMock) -> None:
        result = runner.invoke(app, ["prompts", "rollback"])
        assert result.exit_code == 1
        assert "No previous version" in result.output
