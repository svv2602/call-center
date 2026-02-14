"""Unit tests for weekly PDF report generator."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

import pytest

from src.reports.generator import _fetch_report_data, _render_html


def _make_mock_row(data: dict[str, Any]) -> MagicMock:
    row = MagicMock()
    row._mapping = data
    return row


def _make_mock_engine(rows: list[dict[str, Any]]) -> Any:
    mock_conn = AsyncMock()
    mock_result = MagicMock()
    mock_result.__iter__ = lambda self: iter([_make_mock_row(r) for r in rows])
    mock_conn.execute = AsyncMock(return_value=mock_result)

    mock_engine = MagicMock()

    @asynccontextmanager
    async def _begin() -> AsyncIterator[AsyncMock]:
        yield mock_conn

    mock_engine.begin = _begin
    return mock_engine


class TestFetchReportData:
    """Test _fetch_report_data."""

    @pytest.mark.asyncio
    async def test_aggregates_totals(self) -> None:
        engine = _make_mock_engine([
            {
                "stat_date": "2026-02-10",
                "total_calls": 20,
                "resolved_by_bot": 15,
                "transferred": 5,
                "avg_duration_seconds": 100,
                "avg_quality_score": 0.8,
                "total_cost_usd": 1.0,
                "transfer_reasons": '{"complex_request": 3, "no_stock": 2}',
            },
            {
                "stat_date": "2026-02-11",
                "total_calls": 30,
                "resolved_by_bot": 25,
                "transferred": 5,
                "avg_duration_seconds": 80,
                "avg_quality_score": 0.9,
                "total_cost_usd": 1.5,
                "transfer_reasons": '{"complex_request": 2, "language": 3}',
            },
        ])

        data = await _fetch_report_data(engine, "2026-02-10", "2026-02-11")

        assert data["totals"]["total_calls"] == 50
        assert data["totals"]["transferred"] == 10
        assert data["totals"]["total_cost"] == 2.5
        assert len(data["days"]) == 2
        assert data["days"][0]["date"] == "2026-02-10"
        assert data["days"][1]["date"] == "2026-02-11"

        # Top transfer reasons
        assert len(data["top_transfer_reasons"]) == 3
        assert data["top_transfer_reasons"][0]["name"] == "complex_request"
        assert data["top_transfer_reasons"][0]["count"] == 5

    @pytest.mark.asyncio
    async def test_empty_period(self) -> None:
        engine = _make_mock_engine([])
        data = await _fetch_report_data(engine, "2026-02-10", "2026-02-11")

        assert data["totals"]["total_calls"] == 0
        assert data["totals"]["resolved_pct"] == 0
        assert len(data["days"]) == 0
        assert len(data["top_transfer_reasons"]) == 0

    @pytest.mark.asyncio
    async def test_dict_transfer_reasons(self) -> None:
        """Test when transfer_reasons is already a dict."""
        engine = _make_mock_engine([
            {
                "stat_date": "2026-02-10",
                "total_calls": 10,
                "resolved_by_bot": 7,
                "transferred": 3,
                "avg_duration_seconds": 60,
                "avg_quality_score": 0.75,
                "total_cost_usd": 0.5,
                "transfer_reasons": {"angry_customer": 2, "language": 1},
            },
        ])

        data = await _fetch_report_data(engine, "2026-02-10", "2026-02-10")
        assert data["top_transfer_reasons"][0]["name"] == "angry_customer"


class TestRenderHtml:
    """Test _render_html template rendering."""

    def test_renders_basic_report(self) -> None:
        data = {
            "date_from": "2026-02-10",
            "date_to": "2026-02-16",
            "totals": {
                "total_calls": 100,
                "resolved_pct": 80.0,
                "transferred": 20,
                "avg_quality": 0.85,
                "total_cost": 5.0,
                "avg_duration": 90,
            },
            "days": [
                {
                    "date": "2026-02-10",
                    "total_calls": 50,
                    "resolved_by_bot": 40,
                    "transferred": 10,
                    "avg_duration": 90,
                    "avg_quality": 0.85,
                    "total_cost": 2.5,
                },
            ],
            "top_transfer_reasons": [
                {"name": "complex_request", "count": 8},
            ],
        }

        html = _render_html(data)
        assert "2026-02-10" in html
        assert "2026-02-16" in html
        assert "100" in html
        assert "80.0%" in html
        assert "complex_request" in html
        assert "Call Center AI" in html

    def test_renders_empty_report(self) -> None:
        data = {
            "date_from": "2026-02-10",
            "date_to": "2026-02-16",
            "totals": {
                "total_calls": 0,
                "resolved_pct": 0,
                "transferred": 0,
                "avg_quality": 0,
                "total_cost": 0,
                "avg_duration": 0,
            },
            "days": [],
            "top_transfer_reasons": [],
        }

        html = _render_html(data)
        assert "Call Center AI" in html


class TestGenerateWeeklyReport:
    """Test generate_weekly_report end-to-end (with mocked weasyprint)."""

    @pytest.mark.asyncio
    @patch("src.reports.generator._html_to_pdf", return_value=b"%PDF-1.4 mock")
    async def test_generate_returns_pdf_bytes(self, mock_pdf: MagicMock) -> None:
        from src.reports.generator import generate_weekly_report

        engine = _make_mock_engine([
            {
                "stat_date": "2026-02-10",
                "total_calls": 10,
                "resolved_by_bot": 8,
                "transferred": 2,
                "avg_duration_seconds": 60,
                "avg_quality_score": 0.8,
                "total_cost_usd": 0.5,
                "transfer_reasons": None,
            },
        ])

        result = await generate_weekly_report("2026-02-10", "2026-02-16", engine=engine)
        assert result == b"%PDF-1.4 mock"
        mock_pdf.assert_called_once()
