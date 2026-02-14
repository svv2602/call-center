"""Unit tests for security headers middleware."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.middleware.security_headers import SecurityHeadersMiddleware


@pytest.fixture
def client() -> TestClient:
    """Create a test app with security headers middleware."""
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/test")
    async def test_endpoint() -> dict:
        return {"status": "ok"}

    return TestClient(app)


class TestSecurityHeaders:
    """Test that all security headers are present."""

    def test_x_content_type_options(self, client: TestClient) -> None:
        resp = client.get("/test")
        assert resp.headers["X-Content-Type-Options"] == "nosniff"

    def test_x_frame_options(self, client: TestClient) -> None:
        resp = client.get("/test")
        assert resp.headers["X-Frame-Options"] == "DENY"

    def test_x_xss_protection(self, client: TestClient) -> None:
        resp = client.get("/test")
        assert resp.headers["X-XSS-Protection"] == "0"

    def test_hsts(self, client: TestClient) -> None:
        resp = client.get("/test")
        assert "max-age=31536000" in resp.headers["Strict-Transport-Security"]
        assert "includeSubDomains" in resp.headers["Strict-Transport-Security"]

    def test_csp(self, client: TestClient) -> None:
        resp = client.get("/test")
        csp = resp.headers["Content-Security-Policy"]
        assert "default-src 'self'" in csp
        assert "script-src 'self' 'unsafe-inline'" in csp
        assert "frame-ancestors 'none'" in csp

    def test_referrer_policy(self, client: TestClient) -> None:
        resp = client.get("/test")
        assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"

    def test_permissions_policy(self, client: TestClient) -> None:
        resp = client.get("/test")
        assert "camera=()" in resp.headers["Permissions-Policy"]
        assert "microphone=()" in resp.headers["Permissions-Policy"]

    def test_all_headers_present(self, client: TestClient) -> None:
        resp = client.get("/test")
        required = [
            "X-Content-Type-Options",
            "X-Frame-Options",
            "X-XSS-Protection",
            "Strict-Transport-Security",
            "Content-Security-Policy",
            "Referrer-Policy",
            "Permissions-Policy",
        ]
        for header in required:
            assert header in resp.headers, f"Missing header: {header}"
