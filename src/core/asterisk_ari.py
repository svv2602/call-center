"""Asterisk ARI (Asterisk REST Interface) client.

Provides CallerID lookup and channel management for the Call Center AI.
"""

from __future__ import annotations

import logging

import aiohttp

logger = logging.getLogger(__name__)

# Anonymous/restricted CallerID values to treat as hidden
_ANONYMOUS_IDS = {"anonymous", "restricted", "unavailable", "unknown", ""}


class AsteriskARIClient:
    """HTTP client for Asterisk ARI.

    Used for:
      - Getting CallerID from channel UUID
      - Transferring calls to operator queue
    """

    def __init__(self, url: str, user: str, password: str) -> None:
        self._url = url.rstrip("/")
        self._auth = aiohttp.BasicAuth(user, password)
        self._session: aiohttp.ClientSession | None = None

    async def open(self) -> None:
        """Open the HTTP session."""
        self._session = aiohttp.ClientSession(
            auth=self._auth,
            timeout=aiohttp.ClientTimeout(total=5),
        )

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def get_caller_id(self, channel_uuid: str) -> str | None:
        """Get CallerID (phone number) for a channel.

        Returns None if CallerID is hidden/anonymous or ARI is unavailable.
        """
        if self._session is None:
            logger.warning("ARI client not opened")
            return None

        try:
            async with self._session.get(f"{self._url}/channels/{channel_uuid}") as resp:
                if resp.status != 200:
                    logger.warning(
                        "ARI channel lookup failed: status=%d, channel=%s",
                        resp.status,
                        channel_uuid,
                    )
                    return None

                data = await resp.json()
                caller = data.get("caller", {})
                number = caller.get("number", "")

                if number.lower().strip() in _ANONYMOUS_IDS:
                    logger.info("CallerID hidden for channel %s", channel_uuid)
                    return None

                logger.info("CallerID=%s for channel %s", number, channel_uuid)
                return str(number)

        except (aiohttp.ClientError, OSError) as exc:
            logger.warning("ARI unavailable: %s", exc)
            return None

    async def get_channel_variable(self, channel_uuid: str, variable_name: str) -> str | None:
        """Get a channel variable value via ARI.

        Used for tenant resolution: the dialplan sets CHANNEL(tenant_slug)
        before calling AudioSocket().

        Returns None if variable is not set or ARI is unavailable.
        """
        if self._session is None:
            return None

        try:
            async with self._session.get(
                f"{self._url}/channels/{channel_uuid}/variable",
                params={"variable": variable_name},
            ) as resp:
                if resp.status != 200:
                    logger.debug(
                        "ARI variable not found: %s on channel %s (status=%d)",
                        variable_name,
                        channel_uuid,
                        resp.status,
                    )
                    return None

                data = await resp.json()
                value = data.get("value", "")
                if value:
                    logger.info(
                        "ARI variable %s=%s for channel %s",
                        variable_name,
                        value,
                        channel_uuid,
                    )
                return value or None

        except (aiohttp.ClientError, OSError) as exc:
            logger.debug("ARI variable lookup failed: %s", exc)
            return None

    async def transfer_to_queue(
        self, channel_uuid: str, context: str = "transfer-to-operator"
    ) -> bool:
        """Transfer a channel to the operator queue via ARI.

        Returns True if transfer was initiated successfully.
        """
        if self._session is None:
            return False

        try:
            async with self._session.post(
                f"{self._url}/channels/{channel_uuid}/redirect",
                json={"endpoint": f"Local/s@{context}"},
            ) as resp:
                if resp.status in (200, 204):
                    logger.info("Transfer initiated: %s → %s", channel_uuid, context)
                    return True
                logger.warning(
                    "Transfer failed: status=%d, channel=%s",
                    resp.status,
                    channel_uuid,
                )
                return False
        except (aiohttp.ClientError, OSError) as exc:
            logger.warning("ARI transfer error: %s", exc)
            return False
