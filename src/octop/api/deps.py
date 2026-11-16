"""FastAPI Depends, JWT helpers, and auth routing."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, cast

from fastapi import Depends, Header, Query, Request

from octop.infra.auth import sessions
from octop.infra.auth.tokens import InvalidToken as InvalidToken
from octop.infra.auth.tokens import TokenExpired as TokenExpired
from octop.infra.auth.tokens import decode_token as decode_token
from octop.infra.auth.tokens import require_session_age, require_token_activity
from octop.infra.auth.tokens import sign_token as sign_token
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.permissions import PERMISSIONS, user_has_permission

if TYPE_CHECKING:
    from octop.infra.server import OctopServer
    from octop.infra.users.identity import User
else:
    from octop.infra.users.identity import User


# Sliding renew: at or below max(1, configured TTL // 3) seconds remaining,
# the explicit renewal endpoint issues a token via ACCESS_TOKEN_RESPONSE_HEADER.
ACCESS_TOKEN_RESPONSE_HEADER = "X-Octop-Access-Token"
RENEW_CHECK_AFTER_HEADER = "X-Octop-Renew-Check-After"


# Paths that bypass JWT middleware (setup wizard, health, login).
_JWT_EXEMPT_PREFIXES = (
    "/api/setup/",
    "/api/health/",
    "/api/i18n/",
    "/api/connectors/oauth/callback",
    "/api/internal/mcp/",
)
_JWT_EXEMPT_EXACT = (
    "/api/health",
    "/api/auth/login",
    "/api/auth/captcha",
    "/api/auth/oidc/status",
    "/api/auth/oidc/start",
    "/api/auth/oidc/callback",
    "/api/auth/oidc/exchange",
    "/api/auth/oauth/status",
    "/api/auth/oauth/start",
    "/api/auth/oauth/callback",
    "/api/auth/oauth/exchange",
    "/api/auth/ldap/status",
    "/api/auth/invite/validate",
    "/api/auth/invite/redeem",
    "/api/docs",
    "/api/openapi.json",
)


def is_jwt_exempt_path(path: str) -> bool:
    return path in _JWT_EXEMPT_EXACT or any(path.startswith(p) for p in _JWT_EXEMPT_PREFIXES)


def is_jwt_exempt_request(request: Request) -> bool:
    return is_jwt_exempt_path(request.url.path)


def get_server(request: Request) -> OctopServer:
    server: OctopServer = request.app.state.octop_server
    if not getattr(server, "_started", False):
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server not started")
    return server


def require_database(server: OctopServer) -> None:
    """Raise when the control-plane DB has not been bound yet (deferred setup)."""
    if not server.database_bound:
        raise OctopError(
            ErrorCode.SETUP_REQUIRED,
            "control-plane database not configured yet",
            status=503,
        )


def extract_raw_token(
    *,
    authorization: str | None = None,
    access_token: str | None = None,
) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    if access_token:
        return access_token
    return None


def _decode(server: OctopServer, token: str) -> dict[str, Any]:
    assert server.services is not None
    secret = server.services.secret_repo.get("jwt")
    if secret is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "jwt secret missing")
    try:
        payload = decode_token(secret, token)
        require_session_age(payload, server.services.config.session_absolute_timeout_seconds)
        require_token_activity(payload, server.services.config.session_idle_timeout_seconds)
        sessions.require_active(server.services.db, payload, token)
        return payload
    except TokenExpired as exc:
        raise OctopError(ErrorCode.TOKEN_EXPIRED, "token expired") from exc
    except InvalidToken as exc:
        raise OctopError(ErrorCode.AUTH_FAILED, "invalid token") from exc


def resolve_user_from_token(server: OctopServer, token: str) -> User:
    payload = _decode(server, token)
    assert server.user_manager is not None
    user = server.user_manager.get_by_id(int(payload["sub"]))
    if user is None:
        raise OctopError(ErrorCode.USER_DISABLED, "user not active")
    return user


def maybe_sliding_renew_token(server: OctopServer, token: str, user: User) -> str | None:
    """Renew at the TTL threshold, or on explicit activity when idle policy is enabled.

    Called only by the explicit renewal endpoint; ordinary traffic is not activity.
    Threshold is max(1, TTL // 3), inclusive, so short TTLs have a valid
    renewal window. This does not guarantee a browser sends activity in time.
    """
    assert server.services is not None
    payload = _decode(server, token)
    # Cached middleware identity is not sufficient for issuing new credentials.
    row = server.services.user_repo.get(int(payload["sub"]))
    if row is None or row.disabled or row.id != user.id:
        raise OctopError(ErrorCode.USER_DISABLED, "user not active")
    ttl = int(server.services.config.access_token_ttl_seconds)
    remaining = int(payload["exp"]) - int(time.time())
    if remaining <= 0:
        raise OctopError(ErrorCode.TOKEN_EXPIRED, "token expired")
    threshold = max(1, ttl // 3)
    # With an idle policy, every admitted explicit activity must issue fresh
    # signed activity evidence even while the access token has plenty of TTL.
    if remaining > threshold and not server.services.config.session_idle_timeout_seconds:
        return None
    secret = server.services.secret_repo.get("jwt")
    if secret is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "jwt secret missing")
    return sign_token(
        secret,
        sub=user.id,
        uname=row.username,
        role=row.role,
        ttl_seconds=ttl,
        session_id=sessions.session_id(payload, token),
        session_started_at=payload.get("session_started_at"),
    )


def authenticate_request(request: Request, server: OctopServer) -> User:
    raw = extract_raw_token(
        authorization=request.headers.get("authorization"),
        access_token=request.query_params.get("access_token"),
    )
    if not raw:
        raise OctopError(ErrorCode.AUTH_FAILED, "missing credentials")
    return resolve_user_from_token(server, raw)


async def current_user(
    request: Request,
    server: OctopServer = Depends(get_server),
    authorization: str | None = Header(None),
    access_token: str | None = Query(None),
) -> User:
    cached = getattr(request.state, "octop_user", None)
    if cached is not None:
        return cast("User", cached)
    raw = extract_raw_token(authorization=authorization, access_token=access_token)
    if not raw:
        raise OctopError(ErrorCode.AUTH_FAILED, "missing credentials")
    return resolve_user_from_token(server, raw)


def require_permission(key: str) -> Callable[..., Awaitable[User]]:
    """Dependency factory: require the module permission ``key``.

    ``admin`` is bypassed inside ``user_has_permission``. Unknown keys fail
    fast at construction so typos cannot silently grant access.
    """
    if key not in PERMISSIONS:
        raise RuntimeError(f"unknown permission key: {key}")

    async def _dep(user: User = Depends(current_user)) -> User:
        if not user_has_permission(user, key):
            raise OctopError(
                ErrorCode.FORBIDDEN,
                "permission required",
                details={"permission": key},
            )
        return user

    return _dep


def require_admin() -> Callable[..., Awaitable[User]]:
    """Dependency factory: require the admin role (no module key)."""

    async def _dep(user: User = Depends(current_user)) -> User:
        if not user.is_admin:
            raise OctopError(ErrorCode.FORBIDDEN, "admin required")
        return user

    return _dep
