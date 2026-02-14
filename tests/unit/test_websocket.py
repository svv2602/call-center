"""Unit tests for WebSocket endpoint."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.api.websocket import _authenticate


class TestWebSocketAuth:
    """Test WebSocket JWT authentication."""

    def test_empty_token_rejected(self) -> None:
        assert _authenticate("") is None

    def test_invalid_token_rejected(self) -> None:
        assert _authenticate("not.a.valid.token") is None

    def test_valid_token_accepted(self) -> None:
        from src.api.auth import create_jwt

        token = create_jwt(
            {"sub": "admin", "role": "admin"},
            "test-secret",
            expires_in=3600,
        )
        with patch("src.api.websocket.get_settings") as mock_settings:
            mock_settings.return_value.admin.jwt_secret = "test-secret"
            payload = _authenticate(token)

        assert payload is not None
        assert payload["sub"] == "admin"
        assert payload["role"] == "admin"

    def test_expired_token_rejected(self) -> None:
        from src.api.auth import create_jwt

        token = create_jwt(
            {"sub": "admin", "role": "admin"},
            "test-secret",
            expires_in=-1,  # Already expired
        )
        with patch("src.api.websocket.get_settings") as mock_settings:
            mock_settings.return_value.admin.jwt_secret = "test-secret"
            payload = _authenticate(token)

        assert payload is None
