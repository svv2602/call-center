"""Unit tests for /internal/caller-id and /metrics endpoint auth."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from fastapi.testclient import TestClient


def _make_app() -> FastAPI:
    """Create a minimal app with the internal endpoints for testing."""
    import hmac
    import uuid as uuid_mod

    app = FastAPI()
    _redis_mock = AsyncMock()

    @app.post("/internal/caller-id")
    async def store_caller_id(request: Request, data: dict[str, str]) -> dict[str, str]:
        from src.config import get_settings

        settings = get_settings()
        expected_secret = settings.internal_api.secret
        if expected_secret:
            provided_secret = request.headers.get("X-Internal-Secret", "")
            if not hmac.compare_digest(provided_secret, expected_secret):
                raise HTTPException(status_code=403, detail="Forbidden")

        call_uuid = data.get("uuid", "").strip()
        number = data.get("number", "").strip()
        exten = data.get("exten", "").strip()
        if not call_uuid:
            return {"status": "ignored"}
        try:
            uuid_mod.UUID(call_uuid)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid UUID format")  # noqa: B904
        if _redis_mock is not None:
            if number:
                await _redis_mock.set(f"call:caller:{call_uuid}", number, ex=120)
            if exten:
                await _redis_mock.set(f"call:exten:{call_uuid}", exten, ex=120)
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics_endpoint(request: Request) -> Response:
        from src.config import get_settings

        settings = get_settings()
        expected_token = settings.metrics.bearer_token
        if expected_token:
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer ") or not hmac.compare_digest(
                auth_header[7:], expected_token
            ):
                raise HTTPException(status_code=403, detail="Forbidden")
        return Response(content="# metrics\n", media_type="text/plain; charset=utf-8")

    return app


class TestInternalCallerIdAuth:
    """Test /internal/caller-id authentication."""

    @patch("src.config.get_settings")
    def test_no_secret_configured_allows_request(self, mock_settings: MagicMock) -> None:
        """When INTERNAL_API_SECRET is empty, requests pass without header."""
        mock_settings.return_value.internal_api.secret = ""
        app = _make_app()
        client = TestClient(app)

        resp = client.post(
            "/internal/caller-id",
            json={"uuid": "550e8400-e29b-41d4-a716-446655440000", "number": "+380501234567"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    @patch("src.config.get_settings")
    def test_missing_secret_returns_403(self, mock_settings: MagicMock) -> None:
        """When secret is configured but not provided, returns 403."""
        mock_settings.return_value.internal_api.secret = "my-secret"
        app = _make_app()
        client = TestClient(app)

        resp = client.post(
            "/internal/caller-id",
            json={"uuid": "550e8400-e29b-41d4-a716-446655440000", "number": "+380501234567"},
        )
        assert resp.status_code == 403

    @patch("src.config.get_settings")
    def test_wrong_secret_returns_403(self, mock_settings: MagicMock) -> None:
        """When wrong secret is provided, returns 403."""
        mock_settings.return_value.internal_api.secret = "my-secret"
        app = _make_app()
        client = TestClient(app)

        resp = client.post(
            "/internal/caller-id",
            json={"uuid": "550e8400-e29b-41d4-a716-446655440000", "number": "+380501234567"},
            headers={"X-Internal-Secret": "wrong-secret"},
        )
        assert resp.status_code == 403

    @patch("src.config.get_settings")
    def test_correct_secret_returns_200(self, mock_settings: MagicMock) -> None:
        """When correct secret is provided, returns 200."""
        mock_settings.return_value.internal_api.secret = "my-secret"
        app = _make_app()
        client = TestClient(app)

        resp = client.post(
            "/internal/caller-id",
            json={"uuid": "550e8400-e29b-41d4-a716-446655440000", "number": "+380501234567"},
            headers={"X-Internal-Secret": "my-secret"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    @patch("src.config.get_settings")
    def test_invalid_uuid_returns_400(self, mock_settings: MagicMock) -> None:
        """When UUID format is invalid, returns 400."""
        mock_settings.return_value.internal_api.secret = ""
        app = _make_app()
        client = TestClient(app)

        resp = client.post(
            "/internal/caller-id",
            json={"uuid": "not-a-uuid", "number": "+380501234567"},
        )
        assert resp.status_code == 400


class TestMetricsAuth:
    """Test /metrics endpoint authentication."""

    @patch("src.config.get_settings")
    def test_no_token_configured_allows_request(self, mock_settings: MagicMock) -> None:
        """When METRICS_BEARER_TOKEN is empty, /metrics is open."""
        mock_settings.return_value.metrics.bearer_token = ""
        app = _make_app()
        client = TestClient(app)

        resp = client.get("/metrics")
        assert resp.status_code == 200

    @patch("src.config.get_settings")
    def test_missing_token_returns_403(self, mock_settings: MagicMock) -> None:
        """When token is configured but not provided, returns 403."""
        mock_settings.return_value.metrics.bearer_token = "prom-token"
        app = _make_app()
        client = TestClient(app)

        resp = client.get("/metrics")
        assert resp.status_code == 403

    @patch("src.config.get_settings")
    def test_correct_token_returns_200(self, mock_settings: MagicMock) -> None:
        """When correct bearer token is provided, returns 200."""
        mock_settings.return_value.metrics.bearer_token = "prom-token"
        app = _make_app()
        client = TestClient(app)

        resp = client.get("/metrics", headers={"Authorization": "Bearer prom-token"})
        assert resp.status_code == 200
