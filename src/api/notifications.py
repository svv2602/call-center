"""Admin API for notification channel configuration (Telegram, etc.)."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import aiohttp
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from redis.asyncio import Redis

from src.api.auth import require_role
from src.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/notifications", tags=["notifications"])

REDIS_KEY = "notifications:telegram"

_redis: Redis | None = None

# Module-level dependency to satisfy B008 lint rule
_admin_dep = Depends(require_role("admin"))


async def _get_redis() -> Redis:
    global _redis
    if _redis is None:
        settings = get_settings()
        _redis = Redis.from_url(settings.redis.url, decode_responses=True)
    return _redis


class TelegramPatch(BaseModel):
    bot_token: str | None = None
    chat_id: str | None = None


@router.get("/telegram")
async def get_telegram_config(_: dict[str, Any] = _admin_dep) -> dict[str, Any]:
    """Get Telegram notification config (token masked)."""
    redis = await _get_redis()
    raw = await redis.get(REDIS_KEY)
    config = json.loads(raw) if raw else {}

    # Fallback to env vars
    bot_token = config.get("bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = config.get("chat_id") or os.environ.get("TELEGRAM_CHAT_ID", "")

    return {
        "token_set": bool(bot_token),
        "token_hint": bot_token[-4:] if len(bot_token) >= 4 else "",
        "chat_id": chat_id,
        "source": "redis" if config.get("bot_token") else ("env" if bot_token else "none"),
    }


@router.patch("/telegram")
async def update_telegram_config(
    request: TelegramPatch, _: dict[str, Any] = _admin_dep
) -> dict[str, Any]:
    """Save Telegram bot_token and/or chat_id to Redis."""
    redis = await _get_redis()

    raw = await redis.get(REDIS_KEY)
    config = json.loads(raw) if raw else {}

    if request.bot_token:
        config["bot_token"] = request.bot_token
    if request.chat_id is not None:
        config["chat_id"] = request.chat_id

    await redis.set(REDIS_KEY, json.dumps(config))
    logger.info("Telegram config updated (chat_id=%s)", config.get("chat_id", ""))

    # Best-effort: regenerate alertmanager config and reload
    _try_update_alertmanager(config)

    return {"message": "Telegram config saved"}


@router.post("/telegram/test")
async def test_telegram(_: dict[str, Any] = _admin_dep) -> dict[str, Any]:
    """Send a test message via Telegram Bot API."""
    redis = await _get_redis()
    raw = await redis.get(REDIS_KEY)
    config = json.loads(raw) if raw else {}

    bot_token = config.get("bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = config.get("chat_id") or os.environ.get("TELEGRAM_CHAT_ID", "")

    if not bot_token or not chat_id:
        return {"success": False, "latency_ms": 0, "error": "bot_token or chat_id not configured"}

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": "\u2705 Call Center AI \u2014 test notification.\nTelegram integration is working!",
        "parse_mode": "HTML",
    }

    start = time.monotonic()
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp,
        ):
                latency_ms = int((time.monotonic() - start) * 1000)
                body = await resp.json()
                if resp.status == 200 and body.get("ok"):
                    return {"success": True, "latency_ms": latency_ms}
                return {
                    "success": False,
                    "latency_ms": latency_ms,
                    "error": body.get("description", f"HTTP {resp.status}"),
                }
    except Exception as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        return {"success": False, "latency_ms": latency_ms, "error": str(exc)[:200]}


def _try_update_alertmanager(config: dict[str, str]) -> None:
    """Best-effort: update alertmanager config file and reload container."""
    import pathlib
    import subprocess

    bot_token = config.get("bot_token", "")
    chat_id = config.get("chat_id", "")
    if not bot_token or not chat_id:
        return

    config_path = pathlib.Path("alertmanager/config.yml")
    if not config_path.exists():
        logger.debug("alertmanager/config.yml not found, skipping update")
        return

    try:
        content = config_path.read_text()
        # Replace bot_token and chat_id values in YAML
        # Alertmanager config has: bot_token: 'VALUE' and chat_id: VALUE
        import re

        content = re.sub(
            r"(bot_token:\s*')[^']*(')",
            rf"\g<1>{bot_token}\g<2>",
            content,
        )
        content = re.sub(
            r"(chat_id:\s*)-?\d+",
            rf"\g<1>{chat_id}",
            content,
        )
        config_path.write_text(content)
        logger.info("alertmanager/config.yml updated with new Telegram credentials")

        # Try to reload alertmanager container
        subprocess.run(
            ["docker", "kill", "-s", "SIGHUP", "alertmanager"],
            capture_output=True,
            timeout=5,
        )
        logger.info("Alertmanager reload signal sent")
    except Exception:
        logger.debug("Alertmanager config update failed (non-critical)", exc_info=True)
