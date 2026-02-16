"""Unit tests for configuration validation."""

from __future__ import annotations

from unittest.mock import patch

from src.config import (
    AdminSettings,
    AnthropicSettings,
    DatabaseSettings,
    RedisSettings,
    Settings,
    StoreAPISettings,
)


def _make_settings(**overrides: object) -> Settings:
    """Create Settings with sensible defaults for testing."""
    defaults = {
        "anthropic": AnthropicSettings(api_key="sk-ant-test-key"),
        "database": DatabaseSettings(url="postgresql+asyncpg://u:p@localhost/db"),
        "redis": RedisSettings(url="redis://localhost:6379/0"),
        "store_api": StoreAPISettings(url="http://localhost:3000/api/v1"),
        "admin": AdminSettings(jwt_secret="test-secret-not-default"),
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


class TestValidateRequired:
    """Test Settings.validate_required() semantic checks."""

    def test_all_valid_passes(self) -> None:
        settings = _make_settings()
        result = settings.validate_required()
        assert result.ok
        assert len(result.errors) == 0

    def test_empty_anthropic_key(self) -> None:
        settings = _make_settings(anthropic=AnthropicSettings(api_key=""))
        result = settings.validate_required()
        assert not result.ok
        errors = {e.field for e in result.errors}
        assert "ANTHROPIC_API_KEY" in errors

    def test_invalid_database_url_scheme(self) -> None:
        settings = _make_settings(database=DatabaseSettings(url="mysql://localhost/db"))
        result = settings.validate_required()
        assert not result.ok
        errors = {e.field for e in result.errors}
        assert "DATABASE_URL" in errors

    def test_valid_database_url_with_asyncpg(self) -> None:
        settings = _make_settings(database=DatabaseSettings(url="postgresql+asyncpg://u:p@host/db"))
        result = settings.validate_required()
        db_errors = [e for e in result.errors if e.field == "DATABASE_URL"]
        assert len(db_errors) == 0

    def test_invalid_redis_url_scheme(self) -> None:
        settings = _make_settings(redis=RedisSettings(url="http://localhost:6379"))
        result = settings.validate_required()
        assert not result.ok
        errors = {e.field for e in result.errors}
        assert "REDIS_URL" in errors

    def test_rediss_scheme_accepted(self) -> None:
        settings = _make_settings(redis=RedisSettings(url="rediss://localhost:6379/0"))
        result = settings.validate_required()
        redis_errors = [e for e in result.errors if e.field == "REDIS_URL"]
        assert len(redis_errors) == 0

    def test_invalid_store_api_url(self) -> None:
        settings = _make_settings(store_api=StoreAPISettings(url="not-a-url"))
        result = settings.validate_required()
        assert not result.ok
        errors = {e.field for e in result.errors}
        assert "STORE_API_URL" in errors

    def test_google_credentials_missing_file(self) -> None:
        settings = _make_settings()
        with patch.dict(
            "os.environ",
            {"GOOGLE_APPLICATION_CREDENTIALS": "/nonexistent/path.json"},
        ):
            result = settings.validate_required()
        assert not result.ok
        errors = {e.field for e in result.errors}
        assert "GOOGLE_APPLICATION_CREDENTIALS" in errors

    def test_google_credentials_not_set_is_ok(self) -> None:
        settings = _make_settings()
        with patch.dict("os.environ", {}, clear=False):
            # Remove the env var if it exists
            import os

            env = os.environ.copy()
            env.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
            with patch.dict("os.environ", env, clear=True):
                result = settings.validate_required()
        cred_errors = [e for e in result.errors if e.field == "GOOGLE_APPLICATION_CREDENTIALS"]
        assert len(cred_errors) == 0

    def test_default_jwt_secret_warns(self) -> None:
        settings = _make_settings(admin=AdminSettings(jwt_secret="change-me-in-production"))
        result = settings.validate_required()
        assert not result.ok
        errors = {e.field for e in result.errors}
        assert "ADMIN_JWT_SECRET" in errors

    def test_multiple_errors_collected(self) -> None:
        settings = _make_settings(
            anthropic=AnthropicSettings(api_key=""),
            database=DatabaseSettings(url="mysql://x"),
            admin=AdminSettings(jwt_secret="change-me-in-production"),
        )
        result = settings.validate_required()
        assert not result.ok
        assert len(result.errors) >= 3

    def test_validation_error_has_hint(self) -> None:
        settings = _make_settings(anthropic=AnthropicSettings(api_key=""))
        result = settings.validate_required()
        err = next(e for e in result.errors if e.field == "ANTHROPIC_API_KEY")
        assert err.hint
        assert "export" in err.hint
