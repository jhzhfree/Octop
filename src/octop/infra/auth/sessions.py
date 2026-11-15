"""Revoke a JWT session family, including its sliding-renewal descendants.

No raw token is stored. Tombstones are not pruned because a sliding family has
no fixed expiry. In-flight requests and already-established streams are not
cancelled by revocation; new authentication checks always consult the database.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos.auth_session_revocations import AuthSessionRevocationRepo
from octop.infra.errors import ErrorCode, OctopError


def session_id(payload: dict[str, Any], token: str) -> str:
    sid = payload.get("sid")
    if "sid" not in payload:
        # Older tokens have no family. Renewals after upgrade preserve this ID.
        return "legacy:" + hashlib.sha256(token.encode()).hexdigest()
    if not isinstance(sid, str) or not sid or len(sid) > 128:
        raise OctopError(ErrorCode.AUTH_FAILED, "invalid session")
    return sid


def _digest(payload: dict[str, Any], token: str) -> str:
    # Bind to subject too, rather than treating a sid as a global identity.
    value = f"{payload['sub']}:{session_id(payload, token)}"
    return hashlib.sha256(value.encode()).hexdigest()


def require_active(db: DatabasePool, payload: dict[str, Any], token: str) -> None:
    try:
        revoked = AuthSessionRevocationRepo(db).is_revoked(_digest(payload, token))
    except OctopError:
        raise
    except Exception as exc:
        raise OctopError(
            ErrorCode.INTERNAL_ERROR, "session validation unavailable", status=503
        ) from exc
    if revoked:
        raise OctopError(ErrorCode.AUTH_FAILED, "session revoked")


def revoke(db: DatabasePool, payload: dict[str, Any], token: str) -> None:
    try:
        AuthSessionRevocationRepo(db).revoke(_digest(payload, token), int(time.time()))
    except OctopError:
        raise
    except Exception as exc:
        raise OctopError(
            ErrorCode.INTERNAL_ERROR, "session revocation unavailable", status=503
        ) from exc
