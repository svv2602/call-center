"""Unit tests for JWT authentication with RBAC."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.auth import (
    blacklist_token,
    create_jwt,
    is_token_blacklisted,
    require_role,
    verify_jwt,
)


class TestJWT:
    """Test JWT creation and verification."""

    def test_create_and_verify(self) -> None:
        token = create_jwt({"sub": "admin", "role": "admin"}, "test-secret")
        payload = verify_jwt(token, "test-secret")
        assert payload["sub"] == "admin"
        assert payload["role"] == "admin"
        assert "jti" in payload
        assert "exp" in payload

    def test_invalid_signature(self) -> None:
        token = create_jwt({"sub": "admin"}, "secret1")
        with pytest.raises(ValueError, match="Invalid signature"):
            verify_jwt(token, "secret2")

    def test_expired_token(self) -> None:
        token = create_jwt({"sub": "admin"}, "secret", expires_in=-1)
        with pytest.raises(ValueError, match="Token expired"):
            verify_jwt(token, "secret")

    def test_malformed_token(self) -> None:
        with pytest.raises(ValueError, match="Invalid token format"):
            verify_jwt("not.a.valid.token.format", "secret")

    def test_jti_is_unique(self) -> None:
        t1 = create_jwt({"sub": "admin"}, "secret")
        t2 = create_jwt({"sub": "admin"}, "secret")
        p1 = verify_jwt(t1, "secret")
        p2 = verify_jwt(t2, "secret")
        assert p1["jti"] != p2["jti"]


class TestRequireRole:
    """Test require_role dependency factory."""

    @pytest.mark.asyncio
    @patch("src.api.auth.is_token_blacklisted", new_callable=AsyncMock, return_value=False)
    @patch("src.api.auth.get_settings")
    async def test_admin_role_accepted(self, mock_settings: MagicMock, _mock_bl: AsyncMock) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        token = create_jwt({"sub": "admin", "role": "admin"}, "test-secret")

        request = MagicMock()
        request.headers = {"Authorization": f"Bearer {token}"}

        check_fn = require_role("admin")
        payload = await check_fn(request)
        assert payload["role"] == "admin"

    @pytest.mark.asyncio
    @patch("src.api.auth.is_token_blacklisted", new_callable=AsyncMock, return_value=False)
    @patch("src.api.auth.get_settings")
    async def test_analyst_rejected_for_admin_only(
        self, mock_settings: MagicMock, _mock_bl: AsyncMock
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        token = create_jwt({"sub": "analyst_user", "role": "analyst"}, "test-secret")

        request = MagicMock()
        request.headers = {"Authorization": f"Bearer {token}"}

        check_fn = require_role("admin")
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await check_fn(request)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    @patch("src.api.auth.is_token_blacklisted", new_callable=AsyncMock, return_value=False)
    @patch("src.api.auth.get_settings")
    async def test_multi_role_accepted(self, mock_settings: MagicMock, _mock_bl: AsyncMock) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        token = create_jwt({"sub": "analyst_user", "role": "analyst"}, "test-secret")

        request = MagicMock()
        request.headers = {"Authorization": f"Bearer {token}"}

        check_fn = require_role("admin", "analyst")
        payload = await check_fn(request)
        assert payload["role"] == "analyst"

    @pytest.mark.asyncio
    @patch("src.api.auth.get_settings")
    async def test_missing_auth_header(self, mock_settings: MagicMock) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"

        request = MagicMock()
        request.headers = {}

        check_fn = require_role("admin")
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await check_fn(request)
        assert exc_info.value.status_code == 401


def _mock_request() -> MagicMock:
    """Create a mock FastAPI Request with client IP."""
    req = MagicMock()
    req.client.host = "127.0.0.1"
    return req


class TestLoginEndpoint:
    """Test login with DB fallback."""

    @pytest.mark.asyncio
    @patch("src.api.auth._log_failed_login", new_callable=AsyncMock)
    @patch("src.api.auth._check_rate_limit", new_callable=AsyncMock, return_value=False)
    @patch("src.api.auth._authenticate_via_db", new_callable=AsyncMock)
    @patch("src.api.auth.get_settings")
    async def test_login_via_db(
        self,
        mock_settings: MagicMock,
        mock_db_auth: AsyncMock,
        _mock_rl: AsyncMock,
        _mock_log: AsyncMock,
    ) -> None:
        from src.api.auth import LoginRequest, login

        mock_settings.return_value.admin.jwt_secret = "secret"
        mock_settings.return_value.admin.jwt_ttl_hours = 24
        mock_settings.return_value.admin.username = "env_admin"
        mock_settings.return_value.admin.password = "env_pass"

        mock_db_auth.return_value = {
            "user_id": "user-123",
            "username": "db_admin",
            "role": "admin",
        }

        result = await login(LoginRequest(username="db_admin", password="pass"), _mock_request())
        assert "token" in result
        assert result["expires_in"] == 86400

    @pytest.mark.asyncio
    @patch("src.api.auth._log_failed_login", new_callable=AsyncMock)
    @patch("src.api.auth._check_rate_limit", new_callable=AsyncMock, return_value=False)
    @patch("src.api.auth._authenticate_via_db", new_callable=AsyncMock)
    @patch("src.api.auth.get_settings")
    async def test_login_fallback_to_env(
        self,
        mock_settings: MagicMock,
        mock_db_auth: AsyncMock,
        _mock_rl: AsyncMock,
        _mock_log: AsyncMock,
    ) -> None:
        from src.api.auth import LoginRequest, login

        mock_settings.return_value.admin.jwt_secret = "secret"
        mock_settings.return_value.admin.jwt_ttl_hours = 24
        mock_settings.return_value.admin.username = "admin"
        mock_settings.return_value.admin.password = "admin"

        mock_db_auth.return_value = None

        result = await login(LoginRequest(username="admin", password="admin"), _mock_request())
        assert "token" in result

    @pytest.mark.asyncio
    @patch("src.api.auth._log_failed_login", new_callable=AsyncMock)
    @patch("src.api.auth._check_rate_limit", new_callable=AsyncMock, return_value=False)
    @patch("src.api.auth._authenticate_via_db", new_callable=AsyncMock)
    @patch("src.api.auth.get_settings")
    async def test_login_invalid(
        self,
        mock_settings: MagicMock,
        mock_db_auth: AsyncMock,
        _mock_rl: AsyncMock,
        mock_log: AsyncMock,
    ) -> None:
        from fastapi import HTTPException

        from src.api.auth import LoginRequest, login

        mock_settings.return_value.admin.jwt_secret = "secret"
        mock_settings.return_value.admin.jwt_ttl_hours = 24
        mock_settings.return_value.admin.username = "admin"
        mock_settings.return_value.admin.password = "admin"

        mock_db_auth.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await login(LoginRequest(username="wrong", password="wrong"), _mock_request())
        assert exc_info.value.status_code == 401
        mock_log.assert_called_once()

    @pytest.mark.asyncio
    @patch("src.api.auth._log_failed_login", new_callable=AsyncMock)
    @patch("src.api.auth._check_rate_limit", new_callable=AsyncMock, return_value=True)
    @patch("src.api.auth.get_settings")
    async def test_login_rate_limited(
        self,
        mock_settings: MagicMock,
        _mock_rl: AsyncMock,
        _mock_log: AsyncMock,
    ) -> None:
        from fastapi import HTTPException

        from src.api.auth import LoginRequest, login

        mock_settings.return_value.admin.jwt_ttl_hours = 24

        with pytest.raises(HTTPException) as exc_info:
            await login(LoginRequest(username="admin", password="admin"), _mock_request())
        assert exc_info.value.status_code == 429


class TestJWTBlacklist:
    """Test JWT blacklist (logout invalidation)."""

    @pytest.mark.asyncio
    async def test_blacklist_token_sets_redis_key(self) -> None:
        mock_redis = AsyncMock()
        with patch("src.api.auth._get_redis", new_callable=AsyncMock, return_value=mock_redis):
            await blacklist_token("test-jti-123", 86400)
        mock_redis.setex.assert_called_once_with("jwt_blacklist:test-jti-123", 86400, "1")

    @pytest.mark.asyncio
    async def test_is_token_blacklisted_true(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.exists.return_value = 1
        with patch("src.api.auth._get_redis", new_callable=AsyncMock, return_value=mock_redis):
            result = await is_token_blacklisted("test-jti-123")
        assert result is True
        mock_redis.exists.assert_called_once_with("jwt_blacklist:test-jti-123")

    @pytest.mark.asyncio
    async def test_is_token_blacklisted_false(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.exists.return_value = 0
        with patch("src.api.auth._get_redis", new_callable=AsyncMock, return_value=mock_redis):
            result = await is_token_blacklisted("test-jti-123")
        assert result is False

    @pytest.mark.asyncio
    async def test_blacklist_graceful_on_redis_failure(self) -> None:
        with patch("src.api.auth._get_redis", new_callable=AsyncMock, side_effect=ConnectionError):
            # Should not raise
            await blacklist_token("jti", 100)

    @pytest.mark.asyncio
    async def test_is_blacklisted_returns_false_on_redis_failure(self) -> None:
        with patch("src.api.auth._get_redis", new_callable=AsyncMock, side_effect=ConnectionError):
            result = await is_token_blacklisted("jti")
        assert result is False

    @pytest.mark.asyncio
    @patch("src.api.auth.is_token_blacklisted", new_callable=AsyncMock, return_value=True)
    @patch("src.api.auth.get_settings")
    async def test_blacklisted_token_rejected_by_require_admin(
        self, mock_settings: MagicMock, _mock_bl: AsyncMock
    ) -> None:
        from fastapi import HTTPException

        from src.api.auth import require_admin

        mock_settings.return_value.admin.jwt_secret = "test-secret"
        token = create_jwt({"sub": "admin", "role": "admin"}, "test-secret")

        request = MagicMock()
        request.headers = {"Authorization": f"Bearer {token}"}

        with pytest.raises(HTTPException) as exc_info:
            await require_admin(request)
        assert exc_info.value.status_code == 401
        assert "revoked" in exc_info.value.detail

    @pytest.mark.asyncio
    @patch("src.api.auth.blacklist_token", new_callable=AsyncMock)
    @patch("src.api.auth.is_token_blacklisted", new_callable=AsyncMock, return_value=False)
    @patch("src.api.auth.get_settings")
    async def test_logout_calls_blacklist(
        self, mock_settings: MagicMock, _mock_bl: AsyncMock, mock_blacklist: AsyncMock
    ) -> None:
        from src.api.auth import logout

        mock_settings.return_value.admin.jwt_secret = "test-secret"
        mock_settings.return_value.admin.effective_blacklist_ttl = 86400
        token = create_jwt({"sub": "admin", "role": "admin"}, "test-secret")

        request = MagicMock()
        request.headers = {"Authorization": f"Bearer {token}"}

        result = await logout(request)
        assert result == {"status": "logged_out"}
        mock_blacklist.assert_called_once()
        # Verify the jti was passed
        call_args = mock_blacklist.call_args
        assert call_args[0][1] == 86400  # TTL
