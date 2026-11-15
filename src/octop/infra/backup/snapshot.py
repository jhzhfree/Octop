"""SQLite snapshot helpers."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from urllib.parse import quote

from octop.infra.db.pool import SqlitePool
from octop.infra.errors import ErrorCode, OctopError


def _readonly_sqlite_uri(path: Path) -> str:
    """Build a read-only SQLite URI with a percent-encoded filesystem path."""
    encoded = quote(path.resolve().as_posix(), safe="/:")
    return f"file:{encoded}?mode=ro"


def snapshot_sqlite_file(source: Path, dest: Path) -> None:
    """Snapshot into a new file, never use export as an in-place restore.

    Existing files/symlinks are rejected with exclusive creation. On a copy
    failure the newly created partial belongs to the caller (system exports
    use a private temporary directory that is cleaned automatically).
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(_readonly_sqlite_uri(source), uri=True)
    try:
        # All production callers use a fresh private staging path.
        with dest.open("xb"):
            pass
        dest_conn = sqlite3.connect(dest)
        try:
            src.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        src.close()


def restore_sqlite_file(backup_file: Path, target: Path) -> None:
    """Refuse legacy in-place file restore, including empty/missing targets."""
    raise OctopError.localized(ErrorCode.BACKUP_RESTORE_UNAVAILABLE)


def restore_sqlite_into_pool(backup_file: Path, pool: SqlitePool) -> None:
    """Refuse pooled restore; a process-local RLock is not maintenance proof."""
    raise OctopError.localized(ErrorCode.BACKUP_RESTORE_UNAVAILABLE)


def _rewrite_session_key(session_key: str, *, old_uid: int, new_uid: int) -> str:
    """Rewrite dashboard/cli subject segment ``agent:channel:subject:chat``."""
    parts = session_key.split(":", 3)
    if len(parts) != 4:
        return session_key
    agent_id, channel_type, subject, chat_type = parts
    if channel_type not in ("dashboard", "cli"):
        return session_key
    if subject != str(old_uid):
        return session_key
    return f"{agent_id}:{channel_type}:{new_uid}:{chat_type}"


def _patch_channel_metadata(raw: object, *, old_uid: int, new_uid: int) -> str | None:
    if raw is None:
        return None
    try:
        meta = json.loads(str(raw)) if not isinstance(raw, dict) else dict(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(meta, dict):
        return None
    changed = False
    if meta.get("user_id") == old_uid or meta.get("user_id") == str(old_uid):
        meta["user_id"] = new_uid
        changed = True
    if meta.get("to_handle") == str(old_uid):
        meta["to_handle"] = str(new_uid)
        changed = True
    if not changed:
        return None
    return json.dumps(meta, ensure_ascii=False)
