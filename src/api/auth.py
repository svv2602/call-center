"""JWT authentication for admin API with RBAC.

Supports multiple user roles: admin, analyst, operator.
Auth via DB (admin_users) with fallback to env credentials.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from base64 import urlsafe_b64decode, urlsafe_b64encode
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

_engine: AsyncEngine | None = None
_redis: Any = None

_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SECONDS = 900  # 15 minutes


async def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database.url, pool_pre_ping=True)
    return _engine


async def _get_redis() -> Any:
    """Lazily create and cache Redis connection for rate limiting."""
    global _redis
    if _redis is None:
        from redis.asyncio import Redis

        settings = get_settings()
        _redis = Redis.from_url(settings.redis.url, decode_responses=True)
    return _redis


async def blacklist_token(jti: str, ttl: int) -> None:
    """Add a JWT ID to the Redis blacklist with TTL.

    After TTL expires, the key auto-deletes (token would be expired anyway).
    """
    try:
        r = await _get_redis()
        await r.setex(f"jwt_blacklist:{jti}", ttl, "1")
    except Exception:
        logger.warning("Failed to blacklist token jti=%s", jti, exc_info=True)


async def is_token_blacklisted(jti: str) -> bool:
    """Check if a JWT ID is in the Redis blacklist."""
    try:
        r = await _get_redis()
        return await r.exists(f"jwt_blacklist:{jti}") > 0
    except Exception:
        logger.debug("Blacklist check failed, allowing request", exc_info=True)
        return False


async def _check_rate_limit(ip: str, username: str) -> bool:
    """Check login rate limit. Returns True if request should be BLOCKED."""
    try:
        r = await _get_redis()
        key = f"login_rl:{ip}:{username}"
        count = await r.incr(key)
        if count == 1:
            await r.expire(key, _LOGIN_WINDOW_SECONDS)
        return int(count) > _LOGIN_MAX_ATTEMPTS
    except Exception:
        logger.debug("Rate limit check failed, allowing request", exc_info=True)
        return False


async def _log_failed_login(username: str, ip: str) -> None:
    """Log a failed login attempt to the audit log."""
    try:
        engine = await _get_engine()
        async with engine.begin() as conn:
            await conn.execute(
                text("""
                    INSERT INTO admin_audit_log
                        (user_id, username, action, resource_type, details, ip_address)
                    VALUES
                        (NULL, :username, 'login_failed', 'auth', :details, :ip)
                """),
                {
                    "username": username,
                    "details": f"Failed login attempt for user '{username}'",
                    "ip": ip,
                },
            )
    except Exception:
        logger.debug("Failed to log login attempt", exc_info=True)


class LoginRequest(BaseModel):
    username: str
    password: str


def _b64_encode(data: bytes) -> str:
    return urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    return urlsafe_b64decode(s + "=" * padding)


def create_jwt(payload: dict[str, Any], secret: str, expires_in: int = 86400) -> str:
    """Create a simple JWT token (HS256)."""
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        **payload,
        "exp": int(time.time()) + expires_in,
        "iat": int(time.time()),
        "jti": str(uuid.uuid4()),
    }

    header_b64 = _b64_encode(json.dumps(header).encode())
    payload_b64 = _b64_encode(json.dumps(payload).encode())

    message = f"{header_b64}.{payload_b64}"
    signature = hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()
    sig_b64 = _b64_encode(signature)

    return f"{message}.{sig_b64}"


def verify_jwt(token: str, secret: str) -> dict[str, Any]:
    """Verify and decode a JWT token."""
    parts = token.split(".")
    if len(parts) != 3:
        msg = "Invalid token format"
        raise ValueError(msg)

    message = f"{parts[0]}.{parts[1]}"
    expected_sig = hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()
    actual_sig = _b64_decode(parts[2])

    if not hmac.compare_digest(expected_sig, actual_sig):
        msg = "Invalid signature"
        raise ValueError(msg)

    payload = json.loads(_b64_decode(parts[1]))
    if payload.get("exp", 0) < time.time():
        msg = "Token expired"
        raise ValueError(msg)

    result: dict[str, Any] = payload
    return result


async def require_admin(request: Request) -> dict[str, Any]:
    """FastAPI dependency: verify JWT from Authorization header."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization token")

    token = auth_header[7:]
    settings = get_settings()

    try:
        payload = verify_jwt(token, settings.admin.jwt_secret)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e

    # Check if token has been blacklisted (logout)
    jti = payload.get("jti")
    if jti and await is_token_blacklisted(jti):
        raise HTTPException(status_code=401, detail="Token has been revoked")

    return payload


def require_role(*roles: str) -> Any:
    """Create a FastAPI dependency that checks for specific roles.

    Usage:
        @router.get("/...", dependencies=[Depends(require_role("admin"))])
        or
        async def endpoint(_: dict = Depends(require_role("admin", "analyst"))):
    """

    async def _check_role(request: Request) -> dict[str, Any]:
        payload = await require_admin(request)
        user_role = payload.get("role", "")
        if user_role not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Insufficient permissions. Required: {', '.join(roles)}",
            )
        return payload

    return _check_role


async def _authenticate_via_db(username: str, password: str) -> dict[str, Any] | None:
    """Try to authenticate via admin_users table."""
    import bcrypt

    try:
        engine = await _get_engine()
        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    SELECT id, username, password_hash, role, is_active
                    FROM admin_users
                    WHERE username = :username
                """),
                {"username": username},
            )
            user = result.first()

            if not user:
                return None

            user_data = dict(user._mapping)

            if not user_data["is_active"]:
                return None

            if not bcrypt.checkpw(password.encode(), user_data["password_hash"].encode()):
                return None

            # Update last_login_at
            await conn.execute(
                text("UPDATE admin_users SET last_login_at = now() WHERE id = :id"),
                {"id": str(user_data["id"])},
            )

            return {
                "user_id": str(user_data["id"]),
                "username": user_data["username"],
                "role": user_data["role"],
            }
    except Exception:
        logger.debug("DB auth failed, will try env fallback", exc_info=True)
        return None


@router.post("/login")
async def login(login_data: LoginRequest, request: Request) -> dict[str, Any]:
    """Authenticate admin user and return JWT token.

    Tries DB auth first, falls back to env credentials.
    Rate-limited: max 5 attempts per 15 minutes per IP+username.
    """
    settings = get_settings()
    client_ip = request.client.host if request.client else "unknown"
    ttl_seconds = settings.admin.jwt_ttl_hours * 3600

    # Rate limit check
    if await _check_rate_limit(client_ip, login_data.username):
        await _log_failed_login(login_data.username, client_ip)
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")

    # Try DB authentication first
    db_user = await _authenticate_via_db(login_data.username, login_data.password)
    if db_user:
        token = create_jwt(
            {
                "sub": db_user["username"],
                "role": db_user["role"],
                "user_id": db_user["user_id"],
            },
            settings.admin.jwt_secret,
            expires_in=ttl_seconds,
        )
        return {"token": token, "token_type": "bearer", "expires_in": ttl_seconds}

    # Fallback to env credentials (constant-time comparison)
    if hmac.compare_digest(login_data.username, settings.admin.username) and hmac.compare_digest(
        login_data.password, settings.admin.password
    ):
        token = create_jwt(
            {"sub": login_data.username, "role": "admin"},
            settings.admin.jwt_secret,
            expires_in=ttl_seconds,
        )
        return {"token": token, "token_type": "bearer", "expires_in": ttl_seconds}

    # Failed login — log and reject
    await _log_failed_login(login_data.username, client_ip)
    raise HTTPException(status_code=401, detail="Invalid credentials")


@router.post("/logout")
async def logout(request: Request) -> dict[str, str]:
    """Logout — invalidate current JWT token via Redis blacklist."""
    payload = await require_admin(request)

    jti = payload.get("jti")
    if jti:
        settings = get_settings()
        ttl = settings.admin.effective_blacklist_ttl
        await blacklist_token(jti, ttl)

        try:
            from src.monitoring.metrics import jwt_logouts_total

            jwt_logouts_total.inc()
        except Exception:
            pass

        logger.info("Token blacklisted: jti=%s user=%s", jti, payload.get("sub"))

    return {"status": "logged_out"}
