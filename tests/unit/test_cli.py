"""Unit tests for the CLI tool."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from src.cli.main import _mask_secret, app
from src.config import AdminSettings, AnthropicSettings, Settings

runner = CliRunner()


class TestVersion:
    """Test 'version' command."""

    def test_shows_version(self) -> None:
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert "Call Center AI v" in result.output


class TestConfigCheck:
    """Test 'config check' command."""

    def test_valid_config_passes(self) -> None:
        settings = Settings(
            anthropic=AnthropicSettings(api_key="sk-ant-test"),
            admin=AdminSettings(jwt_secret="not-the-default-secret"),
        )
        with patch("src.cli.main.get_settings", return_value=settings):
            result = runner.invoke(app, ["config", "check"])
        assert result.exit_code == 0
        assert "passed" in result.output.lower() or "\u2705" in result.output

    def test_invalid_config_fails(self) -> None:
        settings = Settings(
            anthropic=AnthropicSettings(api_key=""),
        )
        with patch("src.cli.main.get_settings", return_value=settings):
            result = runner.invoke(app, ["config", "check"])
        assert result.exit_code == 1
        assert "ANTHROPIC_API_KEY" in result.output


class TestConfigShow:
    """Test 'config show' command."""

    def test_shows_config(self) -> None:
        settings = Settings(
            anthropic=AnthropicSettings(api_key="sk-ant-very-secret-key"),
        )
        with patch("src.cli.main.get_settings", return_value=settings):
            result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 0
        # Secret should be masked
        assert "sk-ant-very-secret-key" not in result.output
        assert "sk-ant" in result.output
        assert "***" in result.output

    def test_shows_sections(self) -> None:
        settings = Settings()
        with patch("src.cli.main.get_settings", return_value=settings):
            result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 0
        assert "[anthropic]" in result.output
        assert "[database]" in result.output
        assert "[redis]" in result.output


class TestMaskSecret:
    """Test secret masking utility."""

    def test_mask_long_secret(self) -> None:
        assert _mask_secret("sk-ant-api03-longkey") == "sk-ant***"

    def test_mask_short_secret(self) -> None:
        assert _mask_secret("abc") == "***"

    def test_mask_exact_length(self) -> None:
        assert _mask_secret("123456") == "***"

    def test_mask_empty(self) -> None:
        assert _mask_secret("") == "***"
