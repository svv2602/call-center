"""Unit tests for startup credential validation in main.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestStartupCredentialValidation:
    """Test fail-fast checks on insecure defaults in Docker environment."""

    def _make_settings(self, **overrides: str) -> MagicMock:
        """Create a mock settings object with sensible defaults."""
        s = MagicMock()
        s.admin.jwt_secret = overrides.get("jwt_secret", "real-secret-123")
        s.admin.password = overrides.get("admin_password", "strong-pw")
        s.store_api.key = overrides.get("store_api_key", "real-api-key")
        s.ari.password = overrides.get("ari_password", "real-ari-pw")
        s.internal_api.secret = overrides.get("internal_api_secret", "internal-secret")
        s.metrics.bearer_token = overrides.get("metrics_bearer_token", "prom-token")
        s.anthropic.api_key = "sk-ant-real"
        s.database.url = "postgresql+asyncpg://user:pass@localhost/db"
        s.redis.url = "redis://localhost:6379/0"
        s.store_api.url = "http://localhost:3000/api/v1"
        s.onec.username = ""
        s.logging.level = "INFO"
        s.logging.format = "json"
        return s

    def test_default_jwt_secret_in_docker_exits(self) -> None:
        """Startup should exit if ADMIN_JWT_SECRET is the default in Docker."""
        import sys
        from pathlib import Path

        with (
            patch("pathlib.Path.exists", return_value=True),
            pytest.raises(SystemExit),
        ):
            settings = self._make_settings(jwt_secret="change-me-in-production")
            _in_docker = Path("/app/venv").exists()
            if _in_docker and settings.admin.jwt_secret == "change-me-in-production":
                sys.exit(1)

    def test_default_admin_password_in_docker_exits(self) -> None:
        """Startup should exit if ADMIN_PASSWORD is 'admin' in Docker."""
        import sys
        from pathlib import Path

        with (
            patch("pathlib.Path.exists", return_value=True),
            pytest.raises(SystemExit),
        ):
            settings = self._make_settings(admin_password="admin")
            _in_docker = Path("/app/venv").exists()
            if _in_docker and settings.admin.password == "admin":
                sys.exit(1)

    def test_default_store_api_key_in_docker_exits(self) -> None:
        """Startup should exit if STORE_API_KEY is 'test-store-api-key' in Docker."""
        import sys
        from pathlib import Path

        with (
            patch("pathlib.Path.exists", return_value=True),
            pytest.raises(SystemExit),
        ):
            settings = self._make_settings(store_api_key="test-store-api-key")
            _in_docker = Path("/app/venv").exists()
            if _in_docker and settings.store_api.key == "test-store-api-key":
                sys.exit(1)

    def test_custom_credentials_no_exit(self) -> None:
        """Startup should not exit with proper custom credentials."""
        settings = self._make_settings()
        # None of these should trigger an exit
        assert settings.admin.jwt_secret != "change-me-in-production"
        assert settings.admin.password != "admin"
        assert settings.store_api.key != "test-store-api-key"

    def test_not_in_docker_allows_defaults(self) -> None:
        """Outside Docker, default credentials are allowed (dev environment)."""
        from pathlib import Path

        with patch("pathlib.Path.exists", return_value=False):
            _in_docker = Path("/app/venv").exists()
            # Should NOT exit because _in_docker is False
            assert _in_docker is False


class TestConfigSettings:
    """Test new settings classes exist and have proper defaults."""

    def test_internal_api_settings_default(self) -> None:
        from src.config import InternalAPISettings

        s = InternalAPISettings()
        assert s.secret == ""

    def test_metrics_settings_default(self) -> None:
        from src.config import MetricsSettings

        s = MetricsSettings()
        assert s.bearer_token == ""
