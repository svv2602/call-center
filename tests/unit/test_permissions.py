"""Unit tests for granular permissions system."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.auth import (
    create_jwt,
    has_permission,
    invalidate_user_permissions_cache,
    require_permission,
    resolve_permissions,
)
from src.api.permissions import ALL_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS


class TestPermissionResolution:
    """Test resolve_permissions logic."""

    def test_admin_gets_wildcard(self) -> None:
        result = resolve_permissions("admin")
        assert result == ["*"]

    def test_analyst_gets_defaults(self) -> None:
        result = resolve_permissions("analyst")
        assert "analytics:read" in result
        assert "analytics:export" in result
        assert "knowledge:read" in result
        assert "sandbox:write" not in result

    def test_operator_gets_minimal(self) -> None:
        result = resolve_permissions("operator")
        assert result == ["operators:read"]

    def test_content_manager_gets_content_perms(self) -> None:
        result = resolve_permissions("content_manager")
        assert "sandbox:read" in result
        assert "sandbox:write" in result
        assert "knowledge:write" in result
        assert "scraper:execute" in result
        assert "prompts:delete" in result
        # System perms NOT included
        assert "users:write" not in result
        assert "analytics:read" not in result

    def test_custom_perms_override_role(self) -> None:
        custom = ["sandbox:read", "knowledge:read"]
        result = resolve_permissions("admin", custom)
        assert result == custom  # custom wins over admin wildcard

    def test_null_perms_use_role_defaults(self) -> None:
        result = resolve_permissions("analyst", None)
        assert result == ROLE_DEFAULT_PERMISSIONS["analyst"]

    def test_empty_list_means_no_permissions(self) -> None:
        result = resolve_permissions("admin", [])
        assert result == []

    def test_unknown_role_gets_empty(self) -> None:
        result = resolve_permissions("nonexistent_role")
        assert result == []


class TestHasPermission:
    """Test has_permission check."""

    def test_wildcard_matches_anything(self) -> None:
        assert has_permission(["*"], "sandbox:read") is True
        assert has_permission(["*"], "users:write") is True

    def test_exact_match(self) -> None:
        assert has_permission(["sandbox:read", "sandbox:write"], "sandbox:read") is True

    def test_no_match(self) -> None:
        assert has_permission(["sandbox:read"], "users:write") is False

    def test_empty_perms_deny_all(self) -> None:
        assert has_permission([], "sandbox:read") is False


class TestRequirePermission:
    """Test require_permission FastAPI dependency factory."""

    @pytest.mark.asyncio
    @patch("src.api.auth._load_user_permissions", new_callable=AsyncMock, return_value=None)
    @patch("src.api.auth.is_token_blacklisted", new_callable=AsyncMock, return_value=False)
    @patch("src.api.auth.get_settings")
    async def test_admin_passes_any_permission(
        self, mock_settings: MagicMock, _mock_bl: AsyncMock, _mock_load: AsyncMock
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        token = create_jwt({"sub": "admin", "role": "admin", "user_id": "u1"}, "test-secret")

        request = MagicMock()
        request.headers = {"Authorization": f"Bearer {token}"}

        check_fn = require_permission("sandbox:read")
        payload = await check_fn(request)
        assert payload["role"] == "admin"

    @pytest.mark.asyncio
    @patch("src.api.auth._load_user_permissions", new_callable=AsyncMock, return_value=None)
    @patch("src.api.auth.is_token_blacklisted", new_callable=AsyncMock, return_value=False)
    @patch("src.api.auth.get_settings")
    async def test_content_manager_passes_sandbox(
        self, mock_settings: MagicMock, _mock_bl: AsyncMock, _mock_load: AsyncMock
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        token = create_jwt(
            {"sub": "cm_user", "role": "content_manager", "user_id": "u2"},
            "test-secret",
        )

        request = MagicMock()
        request.headers = {"Authorization": f"Bearer {token}"}

        check_fn = require_permission("sandbox:read")
        payload = await check_fn(request)
        assert payload["role"] == "content_manager"

    @pytest.mark.asyncio
    @patch("src.api.auth._load_user_permissions", new_callable=AsyncMock, return_value=None)
    @patch("src.api.auth.is_token_blacklisted", new_callable=AsyncMock, return_value=False)
    @patch("src.api.auth.get_settings")
    async def test_content_manager_blocked_from_users(
        self, mock_settings: MagicMock, _mock_bl: AsyncMock, _mock_load: AsyncMock
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        token = create_jwt(
            {"sub": "cm_user", "role": "content_manager", "user_id": "u2"},
            "test-secret",
        )

        request = MagicMock()
        request.headers = {"Authorization": f"Bearer {token}"}

        check_fn = require_permission("users:write")
        with pytest.raises(HTTPException) as exc_info:
            await check_fn(request)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    @patch("src.api.auth._load_user_permissions", new_callable=AsyncMock, return_value=None)
    @patch("src.api.auth.is_token_blacklisted", new_callable=AsyncMock, return_value=False)
    @patch("src.api.auth.get_settings")
    async def test_analyst_blocked_from_sandbox_write(
        self, mock_settings: MagicMock, _mock_bl: AsyncMock, _mock_load: AsyncMock
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        token = create_jwt(
            {"sub": "analyst_user", "role": "analyst", "user_id": "u3"},
            "test-secret",
        )

        request = MagicMock()
        request.headers = {"Authorization": f"Bearer {token}"}

        check_fn = require_permission("sandbox:write")
        with pytest.raises(HTTPException) as exc_info:
            await check_fn(request)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    @patch(
        "src.api.auth._load_user_permissions",
        new_callable=AsyncMock,
        return_value=["sandbox:read", "users:write"],
    )
    @patch("src.api.auth.is_token_blacklisted", new_callable=AsyncMock, return_value=False)
    @patch("src.api.auth.get_settings")
    async def test_custom_perms_override_role(
        self, mock_settings: MagicMock, _mock_bl: AsyncMock, _mock_load: AsyncMock
    ) -> None:
        """Operator with custom permissions gets sandbox:read."""
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        token = create_jwt(
            {"sub": "op_user", "role": "operator", "user_id": "u4"},
            "test-secret",
        )

        request = MagicMock()
        request.headers = {"Authorization": f"Bearer {token}"}

        # Operator normally doesn't have sandbox:read, but custom perms grant it
        check_fn = require_permission("sandbox:read")
        payload = await check_fn(request)
        assert payload["role"] == "operator"

    @pytest.mark.asyncio
    @patch("src.api.auth._load_user_permissions", new_callable=AsyncMock, return_value=None)
    @patch("src.api.auth.is_token_blacklisted", new_callable=AsyncMock, return_value=False)
    @patch("src.api.auth.get_settings")
    async def test_multi_permission_any_match(
        self, mock_settings: MagicMock, _mock_bl: AsyncMock, _mock_load: AsyncMock
    ) -> None:
        """require_permission with multiple perms accepts if any matches."""
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        token = create_jwt(
            {"sub": "analyst_user", "role": "analyst", "user_id": "u5"},
            "test-secret",
        )

        request = MagicMock()
        request.headers = {"Authorization": f"Bearer {token}"}

        # Analyst has analytics:read but not sandbox:write
        check_fn = require_permission("sandbox:write", "analytics:read")
        payload = await check_fn(request)
        assert payload["role"] == "analyst"

    @pytest.mark.asyncio
    @patch("src.api.auth.is_token_blacklisted", new_callable=AsyncMock, return_value=False)
    @patch("src.api.auth.get_settings")
    async def test_env_user_no_user_id_gets_role_defaults(
        self, mock_settings: MagicMock, _mock_bl: AsyncMock
    ) -> None:
        """Env fallback user (no user_id) gets role defaults."""
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        token = create_jwt({"sub": "admin", "role": "admin"}, "test-secret")

        request = MagicMock()
        request.headers = {"Authorization": f"Bearer {token}"}

        check_fn = require_permission("users:write")
        payload = await check_fn(request)
        assert payload["role"] == "admin"


class TestPermissionCache:
    """Test _load_user_permissions caching."""

    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached(self) -> None:
        from src.api.auth import _load_user_permissions

        mock_redis = AsyncMock()
        mock_redis.get.return_value = '["sandbox:read","sandbox:write"]'

        with patch("src.api.auth._get_redis", new_callable=AsyncMock, return_value=mock_redis):
            result = await _load_user_permissions("user-123")

        assert result == ["sandbox:read", "sandbox:write"]
        mock_redis.get.assert_called_once_with("user_perms:user-123")

    @pytest.mark.asyncio
    async def test_cache_null_sentinel(self) -> None:
        from src.api.auth import _load_user_permissions

        mock_redis = AsyncMock()
        mock_redis.get.return_value = "__null__"

        with patch("src.api.auth._get_redis", new_callable=AsyncMock, return_value=mock_redis):
            result = await _load_user_permissions("user-123")

        assert result is None

    @pytest.mark.asyncio
    async def test_cache_miss_falls_back_to_db(self) -> None:
        from src.api.auth import _load_user_permissions

        mock_redis = AsyncMock()
        mock_redis.get.return_value = None

        mock_row = MagicMock()
        mock_row.permissions = ["sandbox:read"]

        mock_result = MagicMock()
        mock_result.first.return_value = mock_row

        mock_conn = AsyncMock()
        mock_conn.execute.return_value = mock_result

        # engine.begin() returns an async context manager
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.begin.return_value = mock_ctx

        with (
            patch("src.api.auth._get_redis", new_callable=AsyncMock, return_value=mock_redis),
            patch("src.api.auth._get_engine", new_callable=AsyncMock, return_value=mock_engine),
        ):
            result = await _load_user_permissions("user-456")

        assert result == ["sandbox:read"]
        # Should cache the result
        mock_redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalidate_deletes_cache_key(self) -> None:
        mock_redis = AsyncMock()
        with patch("src.api.auth._get_redis", new_callable=AsyncMock, return_value=mock_redis):
            await invalidate_user_permissions_cache("user-789")
        mock_redis.delete.assert_called_once_with("user_perms:user-789")


class TestGetMe:
    """Test GET /auth/me endpoint."""

    @pytest.mark.asyncio
    @patch("src.api.auth._load_user_permissions", new_callable=AsyncMock, return_value=None)
    @patch("src.api.auth.is_token_blacklisted", new_callable=AsyncMock, return_value=False)
    @patch("src.api.auth.get_settings")
    async def test_returns_effective_permissions(
        self, mock_settings: MagicMock, _mock_bl: AsyncMock, _mock_load: AsyncMock
    ) -> None:
        from src.api.auth import get_me

        mock_settings.return_value.admin.jwt_secret = "test-secret"
        token = create_jwt(
            {"sub": "cm_user", "role": "content_manager", "user_id": "u1"},
            "test-secret",
        )

        request = MagicMock()
        request.headers = {"Authorization": f"Bearer {token}"}

        result = await get_me(request)
        assert result["user_id"] == "u1"
        assert result["username"] == "cm_user"
        assert result["role"] == "content_manager"
        assert "sandbox:read" in result["permissions"]
        assert "users:write" not in result["permissions"]

    @pytest.mark.asyncio
    @patch("src.api.auth._load_user_permissions", new_callable=AsyncMock, return_value=None)
    @patch("src.api.auth.is_token_blacklisted", new_callable=AsyncMock, return_value=False)
    @patch("src.api.auth.get_settings")
    async def test_admin_returns_wildcard(
        self, mock_settings: MagicMock, _mock_bl: AsyncMock, _mock_load: AsyncMock
    ) -> None:
        from src.api.auth import get_me

        mock_settings.return_value.admin.jwt_secret = "test-secret"
        token = create_jwt(
            {"sub": "admin", "role": "admin", "user_id": "a1"},
            "test-secret",
        )

        request = MagicMock()
        request.headers = {"Authorization": f"Bearer {token}"}

        result = await get_me(request)
        assert result["permissions"] == ["*"]


class TestAllPermissionsConsistency:
    """Verify permission constants are internally consistent."""

    def test_role_defaults_only_contain_valid_permissions(self) -> None:
        for role, perms in ROLE_DEFAULT_PERMISSIONS.items():
            for p in perms:
                if p == "*":
                    continue
                assert p in ALL_PERMISSIONS, f"Role '{role}' has unknown permission '{p}'"

    def test_all_permissions_sorted(self) -> None:
        assert sorted(ALL_PERMISSIONS) == ALL_PERMISSIONS

    def test_new_granular_permissions_present(self) -> None:
        assert "configuration:read" in ALL_PERMISSIONS
        assert "configuration:write" in ALL_PERMISSIONS
        assert "monitoring:read" in ALL_PERMISSIONS
        assert "onec_data:read" in ALL_PERMISSIONS

    def test_old_system_permissions_removed(self) -> None:
        assert "system:read" not in ALL_PERMISSIONS
        assert "system:write" not in ALL_PERMISSIONS

    def test_permission_groups_have_new_groups(self) -> None:
        from src.api.permissions import PERMISSION_GROUPS

        assert "configuration" in PERMISSION_GROUPS
        assert "monitoring" in PERMISSION_GROUPS
        assert "onec_data" in PERMISSION_GROUPS
        assert "system" not in PERMISSION_GROUPS
        assert PERMISSION_GROUPS["configuration"] == [
            "configuration:read",
            "configuration:write",
        ]
        assert PERMISSION_GROUPS["monitoring"] == ["monitoring:read"]
        assert PERMISSION_GROUPS["onec_data"] == ["onec_data:read"]
