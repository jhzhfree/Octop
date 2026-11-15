"""Read-only persisted account and organization authorization change detection."""

import hashlib
import json
from typing import Any

from octop.infra.db.services import RepoBundle
from octop.infra.users.identity import User
from octop.infra.users.permissions import effective_permissions


class AccessStateUnavailable(Exception):
    def __init__(self, unauthenticated: bool) -> None:
        super().__init__("access state unavailable")
        self.unauthenticated = unauthenticated


def read_access_state(repos: RepoBundle, user_id: int) -> dict[str, Any]:
    row = repos.user_repo.get(user_id)
    if row is None or row.disabled:
        raise AccessStateUnavailable(unauthenticated=True)
    identity = User(row.id, row.username, row.role, row.display_name, row.locale, row.permissions)
    permissions = sorted(effective_permissions(identity))
    memberships = repos.organization_repo.access_memberships(user_id)
    current = repos.user_repo.get(user_id)
    if current != row:
        raise AccessStateUnavailable(unauthenticated=current is None or bool(current.disabled))
    revision = hashlib.sha256(
        json.dumps(
            [row.id, row.role, permissions, memberships], sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    return {
        "revision": revision,
        "user": {
            "id": row.id,
            "username": row.username,
            "role": row.role,
            "permissions": permissions,
            "display_name": row.display_name,
            "locale": row.locale,
        },
    }
