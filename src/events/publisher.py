"""Redis Pub/Sub event publisher for real-time updates.

Components publish events via `publish_event()`. The WebSocket endpoint
subscribes to the Redis channel and broadcasts events to connected admin clients.

Event format:
    {"type": "call:started", "data": {...}, "timestamp": "ISO8601"}
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

CHANNEL = "callcenter:admin_events"

_redis: Any = None


async def _get_redis() -> Any:
    """Lazily create and cache Redis connection for publishing."""
    global _redis
    if _redis is None:
        from redis.asyncio import Redis

        from src.config import get_settings

        settings = get_settings()
        _redis = Redis.from_url(settings.redis.url, decode_responses=True)
    return _redis


async def publish_event(event_type: str, data: dict[str, Any] | None = None) -> None:
    """Publish an event to Redis Pub/Sub channel.

    Args:
        event_type: Event type (e.g. "call:started", "operator:status_changed").
        data: Event payload.
    """
    message = json.dumps({
        "type": event_type,
        "data": data or {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    try:
        r = await _get_redis()
        await r.publish(CHANNEL, message)
    except Exception:
        logger.debug("Failed to publish event %s", event_type, exc_info=True)
