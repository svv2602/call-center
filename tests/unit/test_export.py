"""Unit tests for CSV/PDF export endpoints."""

from __future__ import annotations

import csv
import io
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.export import router

_ADMIN_PAYLOAD = {"sub": "test", "role": "admin"}


@pytest.fixture()
def client() -> TestClient:
    with patch("src.api.auth.require_admin", new_callable=AsyncMock, return_value=_ADMIN_PAYLOAD):
        app = FastAPI()
        app.include_router(router)
        yield TestClient(app)


def _make_mock_row(data: dict[str, Any]) -> MagicMock:
    """Create a mock SQLAlchemy row with _mapping."""
    row = MagicMock()
    row._mapping = data
    return row


def _make_mock_engine(rows: list[dict[str, Any]]) -> AsyncMock:
    """Create a mock async engine that returns the given rows."""
    mock_conn = AsyncMock()
    mock_result = MagicMock()
    mock_result.__iter__ = lambda self: iter([_make_mock_row(r) for r in rows])
    mock_conn.execute = AsyncMock(return_value=mock_result)

    mock_engine = MagicMock()

    @asynccontextmanager
    async def _begin() -> AsyncIterator[AsyncMock]:
        yield mock_conn

    mock_engine.begin = _begin
    return mock_engine, mock_conn  # type: ignore[return-value]


class TestExportCallsCSV:
    """Test GET /analytics/calls/export."""

    @patch("src.api.export._get_engine")
    def test_export_calls_basic(self, mock_engine_fn: MagicMock, client: TestClient) -> None:
        mock_engine, _ = _make_mock_engine([
            {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "started_at": "2026-02-14T10:00:00",
                "duration_seconds": 90,
                "caller_id": "+380501234567",
                "scenario": "tire_search",
                "transferred_to_operator": False,
                "transfer_reason": None,
                "quality_score": 0.85,
                "total_cost_usd": 0.05,
            },
        ])
        mock_engine_fn.return_value = mock_engine

        response = client.get("/analytics/calls/export?date_from=2026-02-14&date_to=2026-02-14")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert "calls_2026-02-14_2026-02-14.csv" in response.headers["content-disposition"]

        reader = csv.DictReader(io.StringIO(response.text))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["call_id"] == "550e8400-e29b-41d4-a716-446655440000"
        assert rows[0]["scenario"] == "tire_search"
        # PII should be masked
        assert "***" in rows[0]["caller_id"]

    @patch("src.api.export._get_engine")
    def test_export_calls_empty(self, mock_engine_fn: MagicMock, client: TestClient) -> None:
        mock_engine, _ = _make_mock_engine([])
        mock_engine_fn.return_value = mock_engine

        response = client.get("/analytics/calls/export")
        assert response.status_code == 200

        reader = csv.DictReader(io.StringIO(response.text))
        rows = list(reader)
        assert len(rows) == 0

    @patch("src.api.export._get_engine")
    def test_export_calls_with_filters(self, mock_engine_fn: MagicMock, client: TestClient) -> None:
        mock_engine, mock_conn = _make_mock_engine([])
        mock_engine_fn.return_value = mock_engine

        response = client.get(
            "/analytics/calls/export"
            "?date_from=2026-01-01&date_to=2026-01-31"
            "&scenario=tire_search&transferred=true&min_quality=0.5"
        )
        assert response.status_code == 200
        # Verify filters were applied
        call_args = mock_conn.execute.call_args
        sql_text = str(call_args[0][0])
        assert "scenario = :scenario" in sql_text
        assert "transferred_to_operator = :transferred" in sql_text
        assert "quality_score >= :min_quality" in sql_text

    @patch("src.api.export._get_engine")
    def test_export_calls_filename_no_dates(
        self, mock_engine_fn: MagicMock, client: TestClient
    ) -> None:
        mock_engine, _ = _make_mock_engine([])
        mock_engine_fn.return_value = mock_engine

        response = client.get("/analytics/calls/export")
        assert "calls_all_all.csv" in response.headers["content-disposition"]

    @patch("src.api.export._get_engine")
    def test_csv_columns_present(self, mock_engine_fn: MagicMock, client: TestClient) -> None:
        mock_engine, _ = _make_mock_engine([
            {
                "id": "test-id",
                "started_at": "2026-02-14T10:00:00",
                "duration_seconds": 60,
                "caller_id": "+380509876543",
                "scenario": "order",
                "transferred_to_operator": True,
                "transfer_reason": "complex_request",
                "quality_score": None,
                "total_cost_usd": None,
            },
        ])
        mock_engine_fn.return_value = mock_engine

        response = client.get("/analytics/calls/export")
        reader = csv.DictReader(io.StringIO(response.text))
        rows = list(reader)
        assert len(rows) == 1
        expected_cols = {
            "call_id", "started_at", "duration_sec", "caller_id",
            "scenario", "transferred", "transfer_reason",
            "quality_score", "total_cost",
        }
        assert set(rows[0].keys()) == expected_cols
        assert rows[0]["transferred"] == "True"
        assert rows[0]["transfer_reason"] == "complex_request"


class TestExportSummaryCSV:
    """Test GET /analytics/summary/export."""

    @patch("src.api.export._get_engine")
    def test_export_summary_basic(self, mock_engine_fn: MagicMock, client: TestClient) -> None:
        mock_engine, _ = _make_mock_engine([
            {
                "stat_date": "2026-02-14",
                "total_calls": 42,
                "resolved_by_bot": 35,
                "transferred": 7,
                "avg_duration_seconds": 120.5,
                "avg_quality_score": 0.85,
                "total_cost_usd": 2.1,
                "scenario_breakdown": '{"tire_search": 30, "order": 12}',
            },
        ])
        mock_engine_fn.return_value = mock_engine

        response = client.get("/analytics/summary/export?date_from=2026-02-14&date_to=2026-02-14")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert "daily_stats_2026-02-14_2026-02-14.csv" in response.headers["content-disposition"]

        reader = csv.DictReader(io.StringIO(response.text))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["total_calls"] == "42"
        assert rows[0]["top_scenario"] == "tire_search"

    @patch("src.api.export._get_engine")
    def test_export_summary_empty(self, mock_engine_fn: MagicMock, client: TestClient) -> None:
        mock_engine, _ = _make_mock_engine([])
        mock_engine_fn.return_value = mock_engine

        response = client.get("/analytics/summary/export")
        assert response.status_code == 200

        reader = csv.DictReader(io.StringIO(response.text))
        rows = list(reader)
        assert len(rows) == 0

    @patch("src.api.export._get_engine")
    def test_export_summary_dict_breakdown(
        self, mock_engine_fn: MagicMock, client: TestClient
    ) -> None:
        """Test when scenario_breakdown is already a dict (not JSON string)."""
        mock_engine, _ = _make_mock_engine([
            {
                "stat_date": "2026-02-14",
                "total_calls": 10,
                "resolved_by_bot": 8,
                "transferred": 2,
                "avg_duration_seconds": 60,
                "avg_quality_score": 0.7,
                "total_cost_usd": 0.5,
                "scenario_breakdown": {"order": 5, "fitting": 5},
            },
        ])
        mock_engine_fn.return_value = mock_engine

        response = client.get("/analytics/summary/export")
        assert response.status_code == 200

        reader = csv.DictReader(io.StringIO(response.text))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["top_scenario"] in ("order", "fitting")

    @patch("src.api.export._get_engine")
    def test_export_summary_columns(self, mock_engine_fn: MagicMock, client: TestClient) -> None:
        mock_engine, _ = _make_mock_engine([
            {
                "stat_date": "2026-02-13",
                "total_calls": 5,
                "resolved_by_bot": 4,
                "transferred": 1,
                "avg_duration_seconds": 45,
                "avg_quality_score": 0.6,
                "total_cost_usd": 0.3,
                "scenario_breakdown": None,
            },
        ])
        mock_engine_fn.return_value = mock_engine

        response = client.get("/analytics/summary/export")
        reader = csv.DictReader(io.StringIO(response.text))
        rows = list(reader)
        expected_cols = {
            "date", "total_calls", "resolved_by_bot", "transferred",
            "avg_duration_sec", "avg_quality", "total_cost", "top_scenario",
        }
        assert set(rows[0].keys()) == expected_cols
        assert rows[0]["top_scenario"] == ""
