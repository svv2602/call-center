"""Redis-backed rate limiting middleware.

Provides per-IP global rate limiting and per-endpoint overrides.
Uses sliding window counter in Redis for distributed rate limiting.
"""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING, Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

if TYPE_CHECKING:
    from starlette.requests import Request

logger = logging.getLogger(__name__)

# Paths excluded from rate limiting
_SKIP_PATHS = {"/health", "/health/ready", "/metrics"}

# Default limits (overridable via env)
_GLOBAL_LIMIT = int(os.environ.get("RATE_LIMIT_GLOBAL", "100"))  # req/min per IP
_PER_USER_LIMIT = int(os.environ.get("RATE_LIMIT_PER_USER", "60"))  # req/min per user
_WINDOW_SECONDS = 60

# Per-endpoint overrides: path prefix -> (limit, window_seconds)
_ENDPOINT_LIMITS: dict[str, tuple[int, int]] = {
    "/analytics/calls/export": (10, 60),
    "/analytics/summary/export": (10, 60),
    "/knowledge": (30, 60),  # POST/PATCH/DELETE only
}

_redis: Any = None


async def _get_redis() -> Any:
    """Lazily create and cache Redis connection."""
    global _redis
    if _redis is None:
        from redis.asyncio import Redis

        from src.config import get_settings

        settings = get_settings()
        _redis = Redis.from_url(settings.redis.url, decode_responses=True)
    return _redis


async def _check_limit(key: str, limit: int, window: int) -> tuple[bool, int, int]:
    """Check rate limit using Redis sliding window counter.

    Returns (is_blocked, remaining, reset_timestamp).
    """
    try:
        r = await _get_redis()
        now = int(time.time())
        window_start = now - window

        pipe = r.pipeline()
        # Remove old entries
        pipe.zremrangebyscore(key, 0, window_start)
        # Add current request
        pipe.zadd(key, {str(now * 1000 + id(key) % 1000): now})
        # Count requests in window
        pipe.zcard(key)
        # Set expiry on the key
        pipe.expire(key, window + 1)
        results = await pipe.execute()

        count = results[2]
        remaining = max(0, limit - count)
        reset_ts = now + window

        return count > limit, remaining, reset_ts
    except Exception:
        logger.debug("Rate limit check failed, allowing request", exc_info=True)
        return False, limit, int(time.time()) + window


def _get_client_ip(request: Request) -> str:
    """Extract client IP, respecting X-Forwarded-For behind proxy."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _get_user_id(request: Request) -> str | None:
    """Extract user identifier from JWT token (best effort)."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    try:
        from src.api.auth import verify_jwt
        from src.config import get_settings

        settings = get_settings()
        payload = verify_jwt(auth_header[7:], settings.admin.jwt_secret)
        return payload.get("sub")
    except Exception:
        return None


def _get_endpoint_limit(path: str, method: str) -> tuple[int, int] | None:
    """Check if a specific endpoint has stricter limits."""
    for prefix, (limit, window) in _ENDPOINT_LIMITS.items():
        if path.startswith(prefix):
            # For knowledge, only limit mutations
            if prefix == "/knowledge" and method == "GET":
                return None
            return limit, window
    return None


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Redis-backed rate limiting middleware."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path

        # Skip health/metrics endpoints
        if path in _SKIP_PATHS:
            return await call_next(request)

        client_ip = _get_client_ip(request)

        # 1. Check endpoint-specific limit first
        endpoint_override = _get_endpoint_limit(path, request.method)
        if endpoint_override:
            ep_limit, ep_window = endpoint_override
            ep_key = f"rl:ep:{client_ip}:{path}"
            blocked, remaining, reset_ts = await _check_limit(ep_key, ep_limit, ep_window)
            if blocked:
                _log_rate_limit(client_ip, path)
                return _rate_limit_response(ep_limit, 0, reset_ts)

        # 2. Check per-user limit (if authenticated)
        user_id = _get_user_id(request)
        if user_id:
            user_key = f"rl:user:{user_id}"
            blocked, remaining, reset_ts = await _check_limit(
                user_key, _PER_USER_LIMIT, _WINDOW_SECONDS
            )
            if blocked:
                _log_rate_limit(client_ip, path, user_id)
                return _rate_limit_response(_PER_USER_LIMIT, 0, reset_ts)

        # 3. Check global per-IP limit
        global_key = f"rl:ip:{client_ip}"
        blocked, remaining, reset_ts = await _check_limit(
            global_key, _GLOBAL_LIMIT, _WINDOW_SECONDS
        )
        if blocked:
            _log_rate_limit(client_ip, path)
            return _rate_limit_response(_GLOBAL_LIMIT, 0, reset_ts)

        # Request allowed — add rate limit headers
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(_GLOBAL_LIMIT)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_ts)

        return response


def _rate_limit_response(limit: int, remaining: int, reset_ts: int) -> JSONResponse:
    """Build 429 response with Retry-After header."""
    retry_after = max(1, reset_ts - int(time.time()))
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Please try again later."},
        headers={
            "Retry-After": str(retry_after),
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": str(remaining),
            "X-RateLimit-Reset": str(reset_ts),
        },
    )


def _log_rate_limit(ip: str, path: str, user_id: str | None = None) -> None:
    """Log rate limit hit and increment Prometheus counter."""
    try:
        from src.monitoring.metrics import rate_limit_exceeded_total

        rate_limit_exceeded_total.labels(endpoint=path, ip=ip).inc()
    except Exception:
        pass
    logger.warning("Rate limit exceeded: ip=%s path=%s user=%s", ip, path, user_id or "anonymous")
