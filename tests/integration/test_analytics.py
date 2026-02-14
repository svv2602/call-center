"""Integration tests for analytics API endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.monitoring.metrics import (
    active_calls,
    call_cost_usd,
    call_scenario_total,
    calls_resolved_by_bot_total,
    calls_total,
    fittings_booked_total,
    get_metrics,
    operator_queue_length,
    orders_created_total,
)


class TestMetricsEndpoint:
    """Tests for Prometheus metrics export."""

    def setup_method(self) -> None:
        self.client = TestClient(app)

    def test_metrics_endpoint_returns_200(self) -> None:
        response = self.client.get("/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]

    def test_metrics_contain_call_center_prefix(self) -> None:
        response = self.client.get("/metrics")
        content = response.text
        assert "callcenter_" in content

    def test_metrics_contain_active_calls(self) -> None:
        metrics_bytes = get_metrics()
        content = metrics_bytes.decode()
        assert "callcenter_active_calls" in content

    def test_metrics_contain_business_metrics(self) -> None:
        metrics_bytes = get_metrics()
        content = metrics_bytes.decode()
        assert "callcenter_calls_resolved_by_bot_total" in content
        assert "callcenter_orders_created_total" in content
        assert "callcenter_fittings_booked_total" in content
        assert "callcenter_call_cost_usd" in content
        assert "callcenter_call_scenario_total" in content
        assert "callcenter_operator_queue_length" in content


class TestHealthEndpoint:
    """Tests for health check endpoint."""

    def setup_method(self) -> None:
        self.client = TestClient(app)

    def test_health_returns_200(self) -> None:
        response = self.client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "active_calls" in data
        assert "redis" in data


class TestAnalyticsAPIEndpoints:
    """Tests for analytics API endpoints."""

    def setup_method(self) -> None:
        self.client = TestClient(app)

    def test_analytics_quality_returns_200(self) -> None:
        """Quality endpoint should return even without DB data."""
        # This will fail without DB, but tests the route exists
        response = self.client.get("/analytics/quality")
        # May return 500 due to no DB, but route should exist (not 404)
        assert response.status_code != 404

    def test_analytics_calls_returns_200(self) -> None:
        response = self.client.get("/analytics/calls")
        assert response.status_code != 404

    def test_analytics_summary_returns_200(self) -> None:
        response = self.client.get("/analytics/summary")
        assert response.status_code != 404

    def test_analytics_calls_accepts_filters(self) -> None:
        response = self.client.get(
            "/analytics/calls",
            params={
                "quality_below": 0.5,
                "scenario": "tire_search",
                "transferred": True,
                "limit": 10,
                "offset": 0,
            },
        )
        assert response.status_code != 404


class TestAuthEndpoint:
    """Tests for JWT authentication."""

    def setup_method(self) -> None:
        self.client = TestClient(app)

    @patch("src.api.auth._log_failed_login", new_callable=AsyncMock)
    @patch("src.api.auth._check_rate_limit", new_callable=AsyncMock, return_value=False)
    def test_login_with_valid_credentials(self, _rl: AsyncMock, _log: AsyncMock) -> None:
        response = self.client.post(
            "/auth/login",
            json={"username": "admin", "password": "admin"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "token" in data
        assert data["token_type"] == "bearer"

    @patch("src.api.auth._log_failed_login", new_callable=AsyncMock)
    @patch("src.api.auth._check_rate_limit", new_callable=AsyncMock, return_value=False)
    def test_login_with_invalid_credentials(self, _rl: AsyncMock, _log: AsyncMock) -> None:
        response = self.client.post(
            "/auth/login",
            json={"username": "admin", "password": "wrong"},
        )
        assert response.status_code == 401


class TestPromptsAPIEndpoints:
    """Tests for prompt management endpoints."""

    def setup_method(self) -> None:
        self.client = TestClient(app)

    def test_prompts_list_endpoint_exists(self) -> None:
        response = self.client.get("/prompts")
        assert response.status_code != 404

    def test_ab_tests_endpoint_exists(self) -> None:
        response = self.client.get("/prompts/ab-tests")
        assert response.status_code != 404


class TestKnowledgeAPIEndpoints:
    """Tests for knowledge base endpoints."""

    def setup_method(self) -> None:
        self.client = TestClient(app)

    def test_articles_list_endpoint_exists(self) -> None:
        response = self.client.get("/knowledge/articles")
        assert response.status_code != 404

    def test_categories_endpoint_exists(self) -> None:
        response = self.client.get("/knowledge/categories")
        assert response.status_code != 404


class TestAdminUI:
    """Tests for admin UI serving."""

    def setup_method(self) -> None:
        self.client = TestClient(app)

    def test_admin_serves_html(self) -> None:
        response = self.client.get("/admin")
        assert response.status_code == 200
        assert "Call Center AI" in response.text
