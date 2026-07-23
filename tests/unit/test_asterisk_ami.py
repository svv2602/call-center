"""Unit tests for AsteriskAMIClient.redirect().

Uses in-process TCP servers to feed scripted responses to the client;
avoids running Asterisk. Verifies:
- happy path: Login → Redirect → Success
- rejected redirect (wrong channel)
- rejected login (bad secret)
- missing config (empty user/secret)
- connect timeout
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from src.core.asterisk_ami import AsteriskAMIClient

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@asynccontextmanager
async def fake_ami_server(
    handler: Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]],
):
    """Spin up a local TCP server that plays *handler* against one client."""
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield port
    finally:
        server.close()
        await server.wait_closed()


async def _read_message(reader: asyncio.StreamReader) -> dict[str, str]:
    fields: dict[str, str] = {}
    while True:
        line = await reader.readline()
        stripped = line.rstrip(b"\r\n")
        if not stripped:
            break
        key, _, value = stripped.partition(b":")
        fields[key.decode().strip()] = value.decode().strip()
    return fields


@pytest.mark.asyncio
async def test_redirect_success() -> None:
    """Happy path: banner → Login OK → Redirect OK → Logoff swallowed."""

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(b"Asterisk Call Manager/7.0.3\r\n")
        await writer.drain()

        login_msg = await _read_message(reader)
        assert login_msg["Action"] == "Login"
        assert login_msg["Username"] == "callcenter"
        assert login_msg["Secret"] == "s3cret"
        writer.write(
            f"Response: Success\r\nActionID: {login_msg['ActionID']}\r\n"
            f"Message: Authentication accepted\r\n\r\n".encode()
        )
        await writer.drain()

        redirect_msg = await _read_message(reader)
        assert redirect_msg["Action"] == "Redirect"
        assert redirect_msg["Channel"] == "SIP/trunk-00000042"
        assert redirect_msg["Context"] == "transfer-to-operator"
        assert redirect_msg["Exten"] == "s"
        assert redirect_msg["Priority"] == "1"
        writer.write(
            f"Response: Success\r\nActionID: {redirect_msg['ActionID']}\r\n"
            f"Message: Redirect successful\r\n\r\n".encode()
        )
        await writer.drain()

        # Drain Logoff (best-effort), then close.
        await reader.read(100)
        writer.close()

    async with fake_ami_server(handler) as port:
        client = AsteriskAMIClient("127.0.0.1", port, "callcenter", "s3cret")
        ok = await client.redirect(
            "SIP/trunk-00000042", "transfer-to-operator", "s", 1
        )
        assert ok is True


@pytest.mark.asyncio
async def test_redirect_bad_channel_returns_false() -> None:
    """When Asterisk answers Response: Error for Redirect, returns False."""

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(b"Asterisk Call Manager/7.0.3\r\n")
        await writer.drain()
        login = await _read_message(reader)
        writer.write(
            f"Response: Success\r\nActionID: {login['ActionID']}\r\n\r\n".encode()
        )
        await writer.drain()
        redirect = await _read_message(reader)
        writer.write(
            f"Response: Error\r\nActionID: {redirect['ActionID']}\r\n"
            f"Message: No such channel\r\n\r\n".encode()
        )
        await writer.drain()
        await reader.read(100)
        writer.close()

    async with fake_ami_server(handler) as port:
        client = AsteriskAMIClient("127.0.0.1", port, "u", "p")
        assert await client.redirect("SIP/ghost", "transfer-to-operator") is False


@pytest.mark.asyncio
async def test_login_failure_returns_false() -> None:
    """Wrong password → Response: Error on Login → False."""

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(b"Asterisk Call Manager/7.0.3\r\n")
        await writer.drain()
        login = await _read_message(reader)
        writer.write(
            f"Response: Error\r\nActionID: {login['ActionID']}\r\n"
            f"Message: Authentication failed\r\n\r\n".encode()
        )
        await writer.drain()
        await reader.read(100)
        writer.close()

    async with fake_ami_server(handler) as port:
        client = AsteriskAMIClient("127.0.0.1", port, "u", "wrong")
        assert await client.redirect("SIP/x", "transfer-to-operator") is False


@pytest.mark.asyncio
async def test_no_credentials_returns_false_without_connecting() -> None:
    """Empty user/secret → refuse before opening a socket."""
    client = AsteriskAMIClient("127.0.0.1", 5038, "", "")
    with patch("asyncio.open_connection") as mock_open:
        result = await client.redirect("SIP/x", "transfer-to-operator")
    assert result is False
    mock_open.assert_not_called()


@pytest.mark.asyncio
async def test_connect_timeout_returns_false() -> None:
    """Unreachable host + short timeout → False (no exception leaks)."""
    # 240.0.0.1 is a reserved TEST-NET-* address that reliably drops connects.
    client = AsteriskAMIClient(
        "240.0.0.1", 5038, "u", "p", connect_timeout=0.1, action_timeout=0.1
    )
    assert await client.redirect("SIP/x", "transfer-to-operator") is False
