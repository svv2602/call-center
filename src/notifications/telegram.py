"""Reusable Telegram sender for backend notifications.

Reads bot_token / chat_id from Redis (`notifications:telegram`) first,
falls back to env vars TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID. Fire-and-
forget: never raises to the caller — logs and swallows errors so a Telegram
outage cannot break a call flow.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

_REDIS_KEY = "notifications:telegram"
_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
_TIMEOUT_S = 5


async def _get_credentials() -> tuple[str, str]:
    from src.core.redis_client import get_redis

    try:
        redis = await get_redis()
        raw = await redis.get(_REDIS_KEY)
        if raw:
            cfg: dict[str, Any] = json.loads(raw)
            token = cfg.get("bot_token") or ""
            chat_id = cfg.get("chat_id") or ""
            if token and chat_id:
                return token, chat_id
    except Exception:
        logger.debug("Telegram Redis config lookup failed", exc_info=True)

    return (
        os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        os.environ.get("TELEGRAM_CHAT_ID", ""),
    )


async def send_message(text: str, *, parse_mode: str = "HTML") -> bool:
    """Send `text` to the configured Telegram chat. Returns True on success."""
    token, chat_id = await _get_credentials()
    if not token or not chat_id:
        logger.debug("Telegram not configured — skipping notification")
        return False

    url = _API_URL.format(token=token)
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}

    try:
        async with (
            aiohttp.ClientSession() as session,
            session.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=_TIMEOUT_S)
            ) as resp,
        ):
            if resp.status == 200:
                body = await resp.json()
                if body.get("ok"):
                    return True
            logger.warning(
                "Telegram send failed: status=%s body=%s",
                resp.status,
                (await resp.text())[:200],
            )
    except Exception:
        logger.warning("Telegram send raised", exc_info=True)
    return False
