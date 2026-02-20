"""Admin API for pronunciation rules management.

Pronunciation rules are injected into the agent's system prompt
to ensure correct TTS output (tire sizes, brand names, etc.).
Stored in Redis; falls back to hardcoded defaults from prompts.py.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from redis.asyncio import Redis

from src.agent.prompts import PRONUNCIATION_RULES
from src.api.auth import require_role
from src.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/agent", tags=["agent"])

REDIS_KEY = "agent:pronunciation_rules"

_redis: Redis | None = None

# Module-level dependency to satisfy B008 lint rule
_admin_dep = Depends(require_role("admin"))


async def _get_redis() -> Redis:
    global _redis
    if _redis is None:
        settings = get_settings()
        _redis = Redis.from_url(settings.redis.url, decode_responses=True)
    return _redis


class PronunciationRulesPatch(BaseModel):
    rules: str


@router.get("/pronunciation-rules")
async def get_pronunciation_rules(_: dict[str, Any] = _admin_dep) -> dict[str, Any]:
    """Get current pronunciation rules (from Redis or hardcoded default)."""
    redis = await _get_redis()
    raw = await redis.get(REDIS_KEY)

    if raw:
        data = json.loads(raw)
        return {
            "rules": data["rules"],
            "source": "redis",
        }

    return {
        "rules": PRONUNCIATION_RULES,
        "source": "default",
    }


@router.patch("/pronunciation-rules")
async def update_pronunciation_rules(
    request: PronunciationRulesPatch, _: dict[str, Any] = _admin_dep
) -> dict[str, Any]:
    """Save pronunciation rules to Redis."""
    redis = await _get_redis()
    await redis.set(REDIS_KEY, json.dumps({"rules": request.rules}))
    logger.info("Pronunciation rules updated (%d chars)", len(request.rules))
    return {"message": "Pronunciation rules saved"}


@router.post("/pronunciation-rules/reset")
async def reset_pronunciation_rules(_: dict[str, Any] = _admin_dep) -> dict[str, Any]:
    """Reset pronunciation rules to hardcoded defaults."""
    redis = await _get_redis()
    await redis.delete(REDIS_KEY)
    logger.info("Pronunciation rules reset to defaults")
    return {"message": "Pronunciation rules reset to defaults", "rules": PRONUNCIATION_RULES}
