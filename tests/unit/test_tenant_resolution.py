"""Unit tests for _resolve_tenant() from src/main.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_tenant_row(
    slug: str = "prokoleso",
    name: str = "ProKoleso",
    network_id: str = "prokoleso-net",
    extensions: list[str] | None = None,
) -> dict[str, Any]:
    """Build a dict that looks like a DB row from the tenants table."""
    import uuid

    return {
        "id": uuid.uuid4(),
        "slug": slug,
        "name": name,
        "network_id": network_id,
        "agent_name": "Олена",
        "greeting": None,
        "enabled_tools": [],
        "extensions": extensions or [],
        "prompt_suffix": None,
        "config": {},
        "is_active": True,
    }


def _make_mock_engine(rows: list[dict[str, Any]] | None = None) -> MagicMock:
    """Create a mock SQLAlchemy async engine that returns the given rows."""
    engine = MagicMock()
    conn = AsyncMock()

    if rows is not None and len(rows) > 0:
        row = MagicMock()
        row._mapping = rows[0]
        result = MagicMock()
        result.first.return_value = row
    else:
        result = MagicMock()
        result.first.return_value = None

    conn.execute = AsyncMock(return_value=result)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    engine.begin.return_value = ctx
    return engine


class TestResolveTenant:
    """Test _resolve_tenant() from src.main."""

    @pytest.mark.asyncio
    async def test_ari_returns_slug_loads_from_db(self) -> None:
        """When ARI returns a slug, tenant is loaded from DB by slug."""
        tenant_data = _make_tenant_row(slug="prokoleso")
        engine = _make_mock_engine([tenant_data])

        mock_ari = AsyncMock()
        mock_ari.get_channel_variable = AsyncMock(return_value="prokoleso")
        mock_ari.open = AsyncMock()
        mock_ari.close = AsyncMock()

        mock_settings = MagicMock()
        mock_settings.ari.url = "http://localhost:8088/ari"
        mock_settings.ari.user = "user"
        mock_settings.ari.password = "pass"

        with (
            patch("src.main.get_settings", return_value=mock_settings),
            patch("src.core.asterisk_ari.AsteriskARIClient", return_value=mock_ari),
        ):
            from src.main import _resolve_tenant

            result = await _resolve_tenant("test-uuid", engine)

        assert result is not None
        assert result["slug"] == "prokoleso"

    @pytest.mark.asyncio
    async def test_ari_unavailable_falls_back_to_first_active(self) -> None:
        """When ARI is unavailable, falls back to first active tenant."""
        tenant_data = _make_tenant_row(slug="default-tenant")
        engine = _make_mock_engine([tenant_data])

        mock_settings = MagicMock()
        mock_settings.ari.url = ""  # ARI not configured

        with patch("src.main.get_settings", return_value=mock_settings):
            from src.main import _resolve_tenant

            result = await _resolve_tenant("test-uuid", engine)

        assert result is not None
        assert result["slug"] == "default-tenant"

    @pytest.mark.asyncio
    async def test_no_active_tenants_returns_none(self) -> None:
        """When DB has no active tenants, returns None."""
        engine = _make_mock_engine([])  # no rows

        mock_settings = MagicMock()
        mock_settings.ari.url = ""

        with patch("src.main.get_settings", return_value=mock_settings):
            from src.main import _resolve_tenant

            result = await _resolve_tenant("test-uuid", engine)

        assert result is None

    @pytest.mark.asyncio
    async def test_db_unavailable_returns_none(self) -> None:
        """When db_engine is None, returns None."""
        mock_settings = MagicMock()
        mock_settings.ari.url = ""

        with patch("src.main.get_settings", return_value=mock_settings):
            from src.main import _resolve_tenant

            result = await _resolve_tenant("test-uuid", None)

        assert result is None

    @pytest.mark.asyncio
    async def test_db_error_returns_none(self) -> None:
        """When DB query raises an exception, returns None gracefully."""
        engine = MagicMock()
        ctx = AsyncMock()
        conn = AsyncMock()
        conn.execute = AsyncMock(side_effect=RuntimeError("connection refused"))
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        engine.begin.return_value = ctx

        mock_settings = MagicMock()
        mock_settings.ari.url = ""

        with patch("src.main.get_settings", return_value=mock_settings):
            from src.main import _resolve_tenant

            result = await _resolve_tenant("test-uuid", engine)

        assert result is None

    @pytest.mark.asyncio
    async def test_called_exten_resolves_tenant(self) -> None:
        """When ARI returns CALLED_EXTEN=7770, tenant with extensions=['7770'] is resolved."""
        tenant_data = _make_tenant_row(slug="prokoleso", extensions=["7770"])
        engine = _make_mock_engine([tenant_data])

        mock_ari = AsyncMock()
        # TENANT_SLUG is empty, CALLED_EXTEN returns 7770
        mock_ari.get_channel_variable = AsyncMock(
            side_effect=lambda _uuid, var: None if var == "TENANT_SLUG" else "7770"
        )
        mock_ari.open = AsyncMock()
        mock_ari.close = AsyncMock()

        mock_settings = MagicMock()
        mock_settings.ari.url = "http://localhost:8088/ari"
        mock_settings.ari.user = "user"
        mock_settings.ari.password = "pass"

        with (
            patch("src.main.get_settings", return_value=mock_settings),
            patch("src.core.asterisk_ari.AsteriskARIClient", return_value=mock_ari),
        ):
            from src.main import _resolve_tenant

            result = await _resolve_tenant("test-uuid", engine)

        assert result is not None
        assert result["slug"] == "prokoleso"
        assert result["extensions"] == ["7770"]

    @pytest.mark.asyncio
    async def test_no_slug_no_exten_falls_back(self) -> None:
        """When neither TENANT_SLUG nor CALLED_EXTEN is set, fallback to first active."""
        tenant_data = _make_tenant_row(slug="fallback-tenant")
        engine = _make_mock_engine([tenant_data])

        mock_ari = AsyncMock()
        mock_ari.get_channel_variable = AsyncMock(return_value=None)
        mock_ari.open = AsyncMock()
        mock_ari.close = AsyncMock()

        mock_settings = MagicMock()
        mock_settings.ari.url = "http://localhost:8088/ari"
        mock_settings.ari.user = "user"
        mock_settings.ari.password = "pass"

        with (
            patch("src.main.get_settings", return_value=mock_settings),
            patch("src.core.asterisk_ari.AsteriskARIClient", return_value=mock_ari),
        ):
            from src.main import _resolve_tenant

            result = await _resolve_tenant("test-uuid", engine)

        assert result is not None
        assert result["slug"] == "fallback-tenant"
