"""JWT authentication for admin API.

Simple username/password auth with JWT tokens.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from src.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


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
    payload = {**payload, "exp": int(time.time()) + expires_in, "iat": int(time.time())}

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

    return payload


@router.post("/login")
async def login(request: LoginRequest) -> dict[str, Any]:
    """Authenticate admin user and return JWT token."""
    settings = get_settings()

    if request.username != settings.admin.username or request.password != settings.admin.password:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_jwt(
        {"sub": request.username, "role": "admin"},
        settings.admin.jwt_secret,
    )

    return {"token": token, "token_type": "bearer", "expires_in": 86400}
