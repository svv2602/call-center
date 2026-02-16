"""Unit tests for CLI export commands."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from src.cli.main import app

runner = CliRunner()


class TestExportCalls:
    """Test 'export calls' command."""

    @patch("src.cli.export.asyncio.run")
    def test_export_calls_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = 42
        result = runner.invoke(
            app,
            [
                "export",
                "calls",
                "--date-from",
                "2026-02-01",
                "--date-to",
                "2026-02-14",
                "--output",
                "/tmp/test_calls.csv",
            ],
        )
        assert result.exit_code == 0
        assert "42" in result.output
        assert "Exported" in result.output

    @patch("src.cli.export.asyncio.run", side_effect=Exception("DB error"))
    def test_export_calls_error(self, mock_run: MagicMock) -> None:
        result = runner.invoke(app, ["export", "calls", "--output", "/tmp/test.csv"])
        assert result.exit_code == 1
        assert "failed" in result.output.lower()

    @patch("src.cli.export.asyncio.run")
    def test_export_calls_default_output(self, mock_run: MagicMock) -> None:
        mock_run.return_value = 5
        result = runner.invoke(app, ["export", "calls"])
        assert result.exit_code == 0
        assert "calls.csv" in result.output


class TestExportReport:
    """Test 'export report' command."""

    @patch("src.cli.export.asyncio.run")
    def test_export_report_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = None
        result = runner.invoke(
            app,
            [
                "export",
                "report",
                "--date-from",
                "2026-02-01",
                "--date-to",
                "2026-02-14",
                "--output",
                "/tmp/test_report.pdf",
            ],
        )
        assert result.exit_code == 0
        assert "Report saved" in result.output

    @patch("src.cli.export.asyncio.run", side_effect=Exception("WeasyPrint error"))
    def test_export_report_error(self, mock_run: MagicMock) -> None:
        result = runner.invoke(
            app,
            [
                "export",
                "report",
                "--date-from",
                "2026-02-01",
                "--date-to",
                "2026-02-14",
            ],
        )
        assert result.exit_code == 1
        assert "failed" in result.output.lower()

    def test_export_report_requires_dates(self) -> None:
        result = runner.invoke(app, ["export", "report"])
        assert result.exit_code != 0
