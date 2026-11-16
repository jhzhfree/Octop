"""Explicit local maintenance; does not load configuration, credentials or networking."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Literal

from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.mattermost_ingress import IntakeError as IntakeError
from octop.infra.db.repos.mattermost_maintenance import MattermostMaintenanceRepo


def maintain_broker(
    database: Path,
    broker: str,
    action: Literal["plan", "status", "retire-and-resume", "suspend"],
    *,
    digest: str = "",
    revision: int = 0,
    reason: str = "",
) -> dict[str, Any]:
    if not database.is_file():
        raise ValueError("Existing SQLite database required")
    db = SqlitePool(database)
    try:
        repo = MattermostMaintenanceRepo(db)
        if action == "status":
            return repo.status(broker)
        if action == "plan":
            return repo.plan(broker)
        if action == "retire-and-resume":
            return repo.retire_and_resume(broker, digest, reason, int(time.time()))
        return repo.suspend(broker, revision, reason, int(time.time()))
    finally:
        db.close()
