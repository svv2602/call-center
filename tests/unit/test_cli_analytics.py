"""Unit tests for CLI analytics commands."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from src.cli.main import app

runner = CliRunner()


class TestStatsToday:
    """Test 'stats today' command."""

    @patch("src.cli.analytics._fetch_today_stats")
    def test_shows_stats(self, mock_fetch: MagicMock) -> None:
        mock_fetch.return_value = {
            "total_calls": 42,
            "resolved_by_bot": 35,
            "transferred": 7,
            "avg_duration_seconds": 120,
            "avg_quality_score": 0.85,
            "total_cost_usd": 1.2345,
        }
        with patch(
            "src.cli.analytics.asyncio.run", side_effect=lambda coro: mock_fetch.return_value
        ):
            result = runner.invoke(app, ["stats", "today"])
        assert result.exit_code == 0
        assert "42" in result.output
        assert "35" in result.output

    @patch("src.cli.analytics.asyncio.run", side_effect=Exception("DB error"))
    def test_handles_db_error(self, mock_run: MagicMock) -> None:
        result = runner.invoke(app, ["stats", "today"])
        assert result.exit_code == 1
        assert "Failed" in result.output


class TestStatsRecalculate:
    """Test 'stats recalculate' command."""

    @patch("src.tasks.daily_stats.calculate_daily_stats")
    def test_submits_task(self, mock_task: MagicMock) -> None:
        mock_task.delay.return_value = MagicMock(id="task-123")
        result = runner.invoke(app, ["stats", "recalculate", "--date", "2026-01-15"])
        assert result.exit_code == 0
        assert "task-123" in result.output
        mock_task.delay.assert_called_once_with("2026-01-15")


class TestCallsList:
    """Test 'calls list' command."""

    @patch("src.cli.analytics.asyncio.run")
    def test_shows_calls(self, mock_run: MagicMock) -> None:
        mock_run.return_value = [
            {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "caller_id": "+380501234567",
                "started_at": "2026-02-14T10:00:00",
                "duration_seconds": 90,
                "scenario": "tire_search",
                "transferred_to_operator": False,
                "quality_score": 0.9,
            }
        ]
        result = runner.invoke(app, ["calls", "list", "--limit", "5"])
        assert result.exit_code == 0
        assert "550e8400" in result.output
        assert "resolved" in result.output

    @patch("src.cli.analytics.asyncio.run", return_value=[])
    def test_no_calls(self, mock_run: MagicMock) -> None:
        result = runner.invoke(app, ["calls", "list"])
        assert result.exit_code == 0
        assert "No calls found" in result.output


class TestCallsShow:
    """Test 'calls show' command."""

    @patch("src.cli.analytics.asyncio.run")
    def test_shows_call_details(self, mock_run: MagicMock) -> None:
        mock_run.return_value = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "caller_id": "+380501234567",
            "started_at": "2026-02-14T10:00:00",
            "ended_at": "2026-02-14T10:01:30",
            "duration_seconds": 90,
            "scenario": "tire_search",
            "transferred_to_operator": False,
            "quality_score": 0.9,
            "total_cost_usd": 0.05,
            "turns": [
                {"turn_index": 0, "speaker": "bot", "text": "Вітаю!"},
                {"turn_index": 1, "speaker": "user", "text": "Потрібні шини"},
            ],
            "tool_calls": [
                {"tool_name": "search_tires", "success": True, "duration_ms": 150},
            ],
        }
        result = runner.invoke(app, ["calls", "show", "550e8400-e29b-41d4-a716-446655440000"])
        assert result.exit_code == 0
        assert "Call Details" in result.output
        assert "Transcription" in result.output
        assert "BOT:" in result.output
        assert "search_tires" in result.output

    @patch("src.cli.analytics.asyncio.run", return_value=None)
    def test_call_not_found(self, mock_run: MagicMock) -> None:
        result = runner.invoke(app, ["calls", "show", "nonexistent-id"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()
