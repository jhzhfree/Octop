"""Access-token JWT helpers shared by HTTP auth and infra (Bridge)."""

from __future__ import annotations

import time
import uuid
from typing import Any

import jwt


class InvalidToken(Exception): ...


class TokenExpired(InvalidToken): ...


def sign_token(
    secret: bytes,
    *,
    sub: int,
    uname: str,
    role: str,
    ttl_seconds: int = 86400,
    session_id: str | None = None,
    session_started_at: int | None = None,
) -> str:
    now = int(time.time())
    payload = {
        "sid": session_id if session_id is not None else uuid.uuid4().hex,
        "sub": str(sub),
        "uname": uname,
        "role": role,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    if session_id is None:
        payload["session_started_at"] = now
    elif session_started_at is not None:
        payload["session_started_at"] = session_started_at
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_token(secret: bytes, token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        if "sub" in payload:
            payload["sub"] = int(payload["sub"])
        return payload
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpired() from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidToken(str(exc)) from exc


def require_session_age(payload: dict[str, Any], maximum_seconds: int) -> None:
    """Enforce an operator-selected lifetime; legacy families fail closed when enabled."""
    if maximum_seconds <= 0:
        return
    started = payload.get("session_started_at")
    issued = payload.get("iat")
    now = int(time.time())
    if (
        type(started) is not int
        or type(issued) is not int
        or started < 0
        or started > issued
        or started > now
    ):
        raise InvalidToken("session start missing or invalid")
    if now - started >= maximum_seconds:
        raise TokenExpired()
