"""Health and credential-age summaries for worker knowledge sources."""

from __future__ import annotations

from typing import Any

ROTATION_REMINDER_DAYS = 90
EXPIRY_WARNING_DAYS = 7


def credential_rotation_state(
    *,
    has_credentials: bool,
    expires_at: int | None,
    rotated_at: int | None,
    now: int,
) -> dict[str, Any]:
    if not has_credentials:
        return {"state": "missing", "expires_at": expires_at, "rotated_at": rotated_at}
    if expires_at is not None and expires_at <= now:
        state = "expired"
    elif expires_at is not None and expires_at <= now + EXPIRY_WARNING_DAYS * 86400:
        state = "expires_soon"
    elif rotated_at is not None and rotated_at <= now - ROTATION_REMINDER_DAYS * 86400:
        state = "rotation_due"
    else:
        state = "current"
    return {"state": state, "expires_at": expires_at, "rotated_at": rotated_at}
