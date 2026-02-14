"""WebSocket endpoint for real-time admin UI updates.

Provides live event streaming to connected admin clients via Redis Pub/Sub.
Authentication via JWT token in query parameter: /ws?token=JWT.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from redis.asyncio import Redis

from src.api.auth import verify_jwt
from src.config import get_settings
from src.events.publisher import CHANNEL
from src.monitoring.metrics import (
    admin_websocket_connections_active,
    admin_websocket_messages_sent_total,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Connected clients
_clients: set[WebSocket] = set()
_PING_INTERVAL = 30  # seconds


def _authenticate(token: str) -> dict[str, Any] | None:
    """Validate JWT token. Returns payload or None."""
    if not token:
        return None
    try:
        settings = get_settings()
        return verify_jwt(token, settings.admin.jwt_secret)
    except (ValueError, Exception):
        return None


async def _subscribe_and_broadcast(redis_url: str) -> None:
    """Background task: subscribe to Redis Pub/Sub and broadcast to all clients."""
    r = Redis.from_url(redis_url, decode_responses=True)
    pubsub = r.pubsub()
    await pubsub.subscribe(CHANNEL)

    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue

            dead: list[WebSocket] = []
            for ws in _clients.copy():
                try:
                    await ws.send_text(message["data"])
                    admin_websocket_messages_sent_total.inc()
                except Exception:
                    dead.append(ws)

            for ws in dead:
                _clients.discard(ws)
                admin_websocket_connections_active.dec()
    except asyncio.CancelledError:
        pass
    finally:
        await pubsub.unsubscribe(CHANNEL)
        await pubsub.aclose()
        await r.aclose()


# Background subscriber task reference
_subscriber_task: asyncio.Task | None = None


def ensure_subscriber_started() -> None:
    """Start the Redis subscriber task if not already running."""
    global _subscriber_task
    if _subscriber_task is None or _subscriber_task.done():
        settings = get_settings()
        _subscriber_task = asyncio.create_task(
            _subscribe_and_broadcast(settings.redis.url)
        )


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for admin real-time updates.

    Auth: pass JWT token as query parameter ?token=...
    """
    token = websocket.query_params.get("token", "")
    payload = _authenticate(token)
    if payload is None:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await websocket.accept()
    _clients.add(websocket)
    admin_websocket_connections_active.inc()

    logger.info(
        "WebSocket connected: user=%s, clients=%d",
        payload.get("sub", "unknown"),
        len(_clients),
    )

    # Ensure Redis subscriber is running
    ensure_subscriber_started()

    try:
        while True:
            # Wait for client messages (ping/pong or close)
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(), timeout=_PING_INTERVAL
                )
                # Handle client ping
                if data == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
            except asyncio.TimeoutError:
                # Send server ping
                try:
                    await websocket.send_text(json.dumps({"type": "ping"}))
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("WebSocket error", exc_info=True)
    finally:
        _clients.discard(websocket)
        admin_websocket_connections_active.dec()
        logger.info("WebSocket disconnected, clients=%d", len(_clients))
