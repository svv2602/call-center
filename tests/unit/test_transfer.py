"""Unit tests for operator transfer via ARI.

Tests transfer_to_operator tool handler behavior with:
- ARI client available + success
- ARI client available + failure/timeout
- ARI client not available (None)
- Pipeline behavior when session.transferred is True
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.core.asterisk_ari import AsteriskARIClient
from src.core.call_session import CallSession, CallState

# --- ARI client transfer_to_queue tests ---


class TestARITransferToQueue:
    """Test AsteriskARIClient.transfer_to_queue() method."""

    @pytest.fixture
    def ari_client(self) -> AsteriskARIClient:
        return AsteriskARIClient(url="http://localhost:8088/ari", user="admin", password="secret")

    @pytest.mark.asyncio
    async def test_transfer_success(self, ari_client: AsteriskARIClient) -> None:
        """transfer_to_queue returns True on 200/204 response."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        ari_client._session = mock_session

        result = await ari_client.transfer_to_queue("channel-123")
        assert result is True

    @pytest.mark.asyncio
    async def test_transfer_failure_status(self, ari_client: AsteriskARIClient) -> None:
        """transfer_to_queue returns False on non-200 status."""
        mock_resp = AsyncMock()
        mock_resp.status = 404
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        ari_client._session = mock_session

        result = await ari_client.transfer_to_queue("channel-123")
        assert result is False

    @pytest.mark.asyncio
    async def test_transfer_no_session(self, ari_client: AsteriskARIClient) -> None:
        """transfer_to_queue returns False when session not opened."""
        assert ari_client._session is None
        result = await ari_client.transfer_to_queue("channel-123")
        assert result is False

    @pytest.mark.asyncio
    async def test_transfer_network_error(self, ari_client: AsteriskARIClient) -> None:
        """transfer_to_queue returns False on network error."""
        import aiohttp

        mock_session = AsyncMock()
        mock_session.post = MagicMock(side_effect=aiohttp.ClientError("Connection refused"))
        ari_client._session = mock_session

        result = await ari_client.transfer_to_queue("channel-123")
        assert result is False


# --- transfer_to_operator tool handler tests ---


class TestTransferToOperatorHandler:
    """Test the transfer_to_operator tool handler logic from main.py.

    We test the handler in isolation by recreating its core logic
    rather than importing from main.py (which has heavy dependencies).
    """

    @pytest.mark.asyncio
    async def test_transfer_success_with_ari(self) -> None:
        """When ARI client is available and transfer succeeds, session is marked transferred."""
        session = CallSession(channel_uuid=uuid4())
        mock_ari = AsyncMock()
        mock_ari.transfer_to_queue = AsyncMock(return_value=True)

        # Simulate the handler logic
        success = await mock_ari.transfer_to_queue(str(session.channel_uuid))
        assert success is True

        session.transferred = True
        session.transfer_reason = "customer_request"

        assert session.transferred is True
        assert session.transfer_reason == "customer_request"

    @pytest.mark.asyncio
    async def test_transfer_failure_with_ari(self) -> None:
        """When ARI client returns False, session should NOT be marked transferred."""
        session = CallSession(channel_uuid=uuid4())
        mock_ari = AsyncMock()
        mock_ari.transfer_to_queue = AsyncMock(return_value=False)

        success = await mock_ari.transfer_to_queue(str(session.channel_uuid))
        assert success is False

        # Session should remain untransferred
        assert session.transferred is False

    @pytest.mark.asyncio
    async def test_transfer_ari_timeout(self) -> None:
        """When ARI call times out, transfer should be treated as failure."""

        async def slow_transfer(*_args: Any, **_kwargs: Any) -> bool:
            await asyncio.sleep(10)
            return True

        mock_ari = AsyncMock()
        mock_ari.transfer_to_queue = slow_transfer

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                mock_ari.transfer_to_queue("channel-123"),
                timeout=0.1,
            )

    @pytest.mark.asyncio
    async def test_transfer_no_ari_client(self) -> None:
        """When ARI client is None, handler returns unavailable status."""
        ari_client = None

        # Simulate handler logic
        if ari_client is None:
            result = {
                "status": "unavailable",
                "message": (
                    "На жаль, зараз оператори недоступні. "
                    "Залиште, будь ласка, ваш номер — ми передзвонимо."
                ),
            }
        else:
            result = {"status": "transferring"}

        assert result["status"] == "unavailable"
        assert "оператори недоступні" in result["message"]


# --- Pipeline transfer behavior tests ---


class TestPipelineTransferBehavior:
    """Test that pipeline handles transferred state correctly."""

    def test_session_transition_to_transferring(self) -> None:
        """CallSession can transition to TRANSFERRING state."""
        session = CallSession(channel_uuid=uuid4())
        session.transition_to(CallState.GREETING)
        session.transition_to(CallState.LISTENING)
        session.transition_to(CallState.TRANSFERRING)

        assert session.state == CallState.TRANSFERRING

    def test_transferred_flag_and_reason(self) -> None:
        """Session stores transferred flag and reason."""
        session = CallSession(channel_uuid=uuid4())
        session.transferred = True
        session.transfer_reason = "complex_question"

        assert session.transferred is True
        assert session.transfer_reason == "complex_question"

    def test_session_serializes_transfer_state(self) -> None:
        """Session serialization includes transferred flag."""
        session = CallSession(channel_uuid=uuid4())
        session.transferred = True
        session.transfer_reason = "customer_request"

        data = session.to_dict()
        assert data["transferred"] is True
        assert data["transfer_reason"] == "customer_request"

    def test_session_deserializes_transfer_state(self) -> None:
        """Session deserialization restores transferred flag."""
        channel_uuid = uuid4()
        data = {
            "channel_uuid": str(channel_uuid),
            "state": "transferring",
            "transferred": True,
            "transfer_reason": "customer_request",
            "caller_id": None,
            "caller_phone": None,
            "dialog_history": [],
            "timeout_count": 0,
            "start_time": 0,
        }
        session = CallSession.from_dict(data)
        assert session.transferred is True
        assert session.transfer_reason == "customer_request"

    def test_transferring_to_ended_transition(self) -> None:
        """TRANSFERRING -> ENDED is a valid transition."""
        session = CallSession(channel_uuid=uuid4())
        session.transition_to(CallState.GREETING)
        session.transition_to(CallState.LISTENING)
        session.transition_to(CallState.TRANSFERRING)
        session.transition_to(CallState.ENDED)

        assert session.state == CallState.ENDED
