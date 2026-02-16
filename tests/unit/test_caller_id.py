"""Unit tests for CallerID via Asterisk ARI."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.asterisk_ari import AsteriskARIClient
from src.core.call_session import CallSession


class TestAsteriskARIClient:
    """Test ARI client CallerID retrieval."""

    @pytest.fixture
    def ari_client(self) -> AsteriskARIClient:
        return AsteriskARIClient(url="http://localhost:8088/ari", user="admin", password="secret")

    @pytest.mark.asyncio
    async def test_get_caller_id_returns_number(self, ari_client: AsteriskARIClient) -> None:
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"caller": {"number": "+380501234567", "name": ""}})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        ari_client._session = mock_session

        result = await ari_client.get_caller_id("channel-123")
        assert result == "+380501234567"

    @pytest.mark.asyncio
    async def test_get_caller_id_anonymous(self, ari_client: AsteriskARIClient) -> None:
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"caller": {"number": "anonymous", "name": ""}})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        ari_client._session = mock_session

        result = await ari_client.get_caller_id("channel-123")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_caller_id_restricted(self, ari_client: AsteriskARIClient) -> None:
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"caller": {"number": "restricted", "name": ""}})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        ari_client._session = mock_session

        result = await ari_client.get_caller_id("channel-123")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_caller_id_channel_not_found(self, ari_client: AsteriskARIClient) -> None:
        mock_resp = AsyncMock()
        mock_resp.status = 404
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        ari_client._session = mock_session

        result = await ari_client.get_caller_id("nonexistent-channel")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_caller_id_session_not_opened(self, ari_client: AsteriskARIClient) -> None:
        result = await ari_client.get_caller_id("channel-123")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_caller_id_empty_string(self, ari_client: AsteriskARIClient) -> None:
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"caller": {"number": "", "name": ""}})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        ari_client._session = mock_session

        result = await ari_client.get_caller_id("channel-123")
        assert result is None


class TestCallerIDInSession:
    """Test CallerID integration with CallSession."""

    def test_session_has_caller_phone_field(self) -> None:
        session = CallSession(uuid.uuid4())
        assert session.caller_phone is None

    def test_session_caller_phone_setter(self) -> None:
        session = CallSession(uuid.uuid4())
        session.caller_phone = "+380501234567"
        assert session.caller_phone == "+380501234567"

    def test_session_needs_phone_verification_default(self) -> None:
        session = CallSession(uuid.uuid4())
        assert session.needs_phone_verification is False

    def test_session_order_id_field(self) -> None:
        session = CallSession(uuid.uuid4())
        assert session.order_id is None
        session.order_id = "order-123"
        assert session.order_id == "order-123"

    def test_session_serialization_with_caller(self) -> None:
        session = CallSession(uuid.uuid4())
        session.caller_phone = "+380501234567"
        session.needs_phone_verification = True
        session.order_id = "order-abc"

        serialized = session.serialize()
        restored = CallSession.deserialize(serialized)

        assert restored.caller_phone == "+380501234567"
        assert restored.needs_phone_verification is True
        assert restored.order_id == "order-abc"
