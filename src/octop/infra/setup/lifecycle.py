"""Setup lifecycle access and authenticated owner checks."""

from __future__ import annotations

from typing import Any

from octop.infra.db.repos.setup_lifecycle import SetupLifecycleRepo
from octop.infra.errors import ErrorCode, OctopError


def lifecycle(server: Any) -> SetupLifecycleRepo:
    return SetupLifecycleRepo(server.services.db)


def require_phase(server: Any, allowed: tuple[str, ...]) -> None:
    if server.services is None:
        return
    phase, _ = lifecycle(server).state()
    # CLI-created users did not enter the web wizard. Do not turn such installs
    # into an authenticated or anonymous web setup session.
    with server.services.db.connect() as conn:
        users_exist = conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None
    if phase not in allowed or (phase == "pending" and users_exist):
        raise OctopError(
            ErrorCode.SETUP_REQUIRED, "setup closed or operation in progress", status=410
        )


def require_owner(server: Any, user_id: int) -> None:
    phase, owner = lifecycle(server).state()
    with server.services.db.connect() as conn:
        user = conn.execute("SELECT role, disabled FROM users WHERE id=?", (user_id,)).fetchone()
    if (
        phase != "active"
        or owner != user_id
        or not user
        or user["disabled"]
        or user["role"] != "admin"
    ):
        raise OctopError(ErrorCode.SETUP_TOKEN_INVALID, "active setup administrator required")
