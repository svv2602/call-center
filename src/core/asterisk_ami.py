"""Asterisk Manager Interface (AMI) client — used for blind transfers.

ARI cannot redirect a channel that is currently stuck inside a dialplan
Application (like ``AudioSocket()``): both ``/channels/{id}/redirect`` and
``/channels/{id}/continue`` return 409 "Channel not in Stasis application".
AMI operates at a lower level and its ``Redirect`` action can move any
active channel to a different dialplan extension.

The client is one-shot per transfer: connect → login → redirect → logoff.
Transfers are rare (once per call at most), so the ~200ms setup cost is
irrelevant and we avoid the complexity of a persistent connection with
reconnect/heartbeat logic.

Protocol reference:
  https://docs.asterisk.org/Latest_API/API_Documentation/AMI_Actions/Redirect/
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid

logger = logging.getLogger(__name__)


class AsteriskAMIClient:
    """Minimal one-shot Asterisk AMI client for blind operator transfers."""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        secret: str,
        connect_timeout: float = 3.0,
        action_timeout: float = 5.0,
    ) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._secret = secret
        self._connect_timeout = connect_timeout
        self._action_timeout = action_timeout

    async def redirect(
        self,
        channel_name: str,
        context: str,
        extension: str = "s",
        priority: int = 1,
    ) -> bool:
        """Blind-transfer *channel_name* to ``context,extension,priority``.

        ``channel_name`` must be the Asterisk channel name (e.g.
        ``SIP/trunk1058-00000042``, ``PJSIP/1234-00000001``). AMI Redirect
        does NOT accept the uniqueid — use the mapping populated by the
        dialplan curl call (``call:channel_name:{uuid}`` in Redis).

        Returns True if the redirect was accepted by Asterisk.
        """
        if not self._user or not self._secret:
            logger.error("AMI not configured (AMI_USER/AMI_SECRET empty)")
            return False

        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port),
                timeout=self._connect_timeout,
            )

            # Asterisk sends a banner on connect: ``Asterisk Call Manager/x.y.z\r\n``
            await asyncio.wait_for(reader.readline(), timeout=self._action_timeout)

            action_id = uuid.uuid4().hex[:12]
            login = (
                "Action: Login\r\n"
                f"Username: {self._user}\r\n"
                f"Secret: {self._secret}\r\n"
                "Events: off\r\n"
                f"ActionID: {action_id}\r\n"
                "\r\n"
            )
            writer.write(login.encode("utf-8"))
            await writer.drain()

            login_msg = await asyncio.wait_for(
                self._read_message(reader), timeout=self._action_timeout
            )
            if login_msg.get("Response", "").lower() != "success":
                logger.error(
                    "AMI login failed: response=%s message=%s",
                    login_msg.get("Response"),
                    login_msg.get("Message"),
                )
                return False

            action_id = uuid.uuid4().hex[:12]
            redirect = (
                "Action: Redirect\r\n"
                f"Channel: {channel_name}\r\n"
                f"Context: {context}\r\n"
                f"Exten: {extension}\r\n"
                f"Priority: {priority}\r\n"
                f"ActionID: {action_id}\r\n"
                "\r\n"
            )
            writer.write(redirect.encode("utf-8"))
            await writer.drain()

            # Skip any events (Events: off should suppress them, but a stray
            # Response to our ActionID is what we want).
            for _ in range(5):
                msg = await asyncio.wait_for(
                    self._read_message(reader), timeout=self._action_timeout
                )
                if msg.get("ActionID") != action_id:
                    continue
                if msg.get("Response", "").lower() == "success":
                    logger.info(
                        "AMI Redirect ok: channel=%s → %s,%s,%d",
                        channel_name,
                        context,
                        extension,
                        priority,
                    )
                    return True
                logger.warning(
                    "AMI Redirect rejected: channel=%s response=%s message=%s",
                    channel_name,
                    msg.get("Response"),
                    msg.get("Message"),
                )
                return False

            logger.warning("AMI Redirect: no matching Response after 5 messages")
            return False

        except TimeoutError:
            logger.error(
                "AMI Redirect timeout (channel=%s, host=%s:%d)",
                channel_name,
                self._host,
                self._port,
            )
            return False
        except OSError as exc:
            logger.error(
                "AMI connection error: %s (host=%s:%d)", exc, self._host, self._port
            )
            return False
        finally:
            if writer is not None:
                with contextlib.suppress(OSError, ConnectionError):
                    writer.write(b"Action: Logoff\r\n\r\n")
                    await writer.drain()
                writer.close()
                with contextlib.suppress(OSError, ConnectionError):
                    await writer.wait_closed()

    async def _read_message(self, reader: asyncio.StreamReader) -> dict[str, str]:
        """Read one AMI message (fields until blank line)."""
        fields: dict[str, str] = {}
        while True:
            line = await reader.readline()
            if not line:
                break
            stripped = line.rstrip(b"\r\n")
            if not stripped:
                break
            if b":" not in stripped:
                continue
            key, _, value = stripped.partition(b":")
            fields[key.decode("utf-8", "replace").strip()] = value.decode(
                "utf-8", "replace"
            ).strip()
        return fields
