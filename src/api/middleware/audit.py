"""Audit logging middleware.

Logs all mutating requests (POST, PATCH, DELETE) to admin_audit_log.
Skips GET requests, health checks, and metrics.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

if TYPE_CHECKING:
    from fastapi import Request, Response

from src.api.auth import verify_jwt
from src.config import get_settings

logger = logging.getLogger(__name__)

_SKIP_PATHS = {"/health", "/health/ready", "/metrics", "/auth/login"}
_AUDIT_METHODS = {"POST", "PATCH", "PUT", "DELETE"}

_engine: AsyncEngine | None = None


async def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database.url)
    return _engine


def _extract_resource(path: str) -> tuple[str, str | None]:
    """Extract resource_type and resource_id from path."""
    parts = [p for p in path.strip("/").split("/") if p]
    resource_type = parts[0] if parts else ""
    resource_id = None
    if len(parts) >= 2:
        # e.g. /admin/users/123 -> type=admin/users, id=123
        resource_type = "/".join(parts[:-1]) if len(parts) > 2 else parts[0]
        resource_id = parts[-1]
    return resource_type, resource_id


class AuditMiddleware(BaseHTTPMiddleware):
    """Middleware that logs mutating API requests to audit_log."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Skip non-mutating methods and excluded paths
        if request.method not in _AUDIT_METHODS:
            return await call_next(request)

        if request.url.path in _SKIP_PATHS:
            return await call_next(request)

        response = await call_next(request)

        # Only log successful mutations (2xx status)
        if response.status_code < 200 or response.status_code >= 300:
            return response

        # Extract user info from JWT (best effort)
        user_id: str | None = None
        username: str | None = None
        try:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                settings = get_settings()
                payload: dict[str, Any] = verify_jwt(auth_header[7:], settings.admin.jwt_secret)
                user_id = payload.get("user_id")
                username = payload.get("sub")
        except Exception:
            pass

        # Extract resource info from path
        resource_type, resource_id = _extract_resource(request.url.path)
        ip_address = request.client.host if request.client else None

        # Write audit log asynchronously (fire and forget)
        try:
            engine = await _get_engine()
            async with engine.begin() as conn:
                await conn.execute(
                    text("""
                        INSERT INTO admin_audit_log
                            (user_id, username, action, resource_type, resource_id, ip_address)
                        VALUES
                            (:user_id, :username, :action, :resource_type, :resource_id, :ip_address)
                    """),
                    {
                        "user_id": user_id,
                        "username": username,
                        "action": request.method,
                        "resource_type": resource_type,
                        "resource_id": resource_id,
                        "ip_address": ip_address,
                    },
                )
        except Exception:
            logger.debug("Failed to write audit log", exc_info=True)

        return response
