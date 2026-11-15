"""Check SQLite names and inode metadata before SQLite can replay journals.

Never open/close a raw descriptor on an existing SQLite database: POSIX close
can release locks held by another SQLite connection in the same process.
Path checks are not a sandbox against same-UID writers or atomic VFS binding.
"""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

from octop.infra.errors import ErrorCode, OctopError

RECOVERY_QUARANTINE_FILE = ".octop-recovery-quarantine"
RECOVERY_QUARANTINE_XATTR = "user.octop.recovery_quarantine"


def _unsafe() -> OctopError:
    return OctopError.localized(ErrorCode.SQLITE_UNSAFE_PATH)


def _check_directory(path: Path) -> None:
    try:
        (path.parent / RECOVERY_QUARANTINE_FILE).lstat()
    except FileNotFoundError:
        return
    raise OctopError.localized(ErrorCode.BACKUP_RECOVERY_QUARANTINED)


def validate_open_path(path: Path, expected: tuple[int, int] | None) -> tuple[int, int]:
    """Inspect names/stat/xattr only; never open the database or execute SQLite."""
    current = path.lstat()
    identity = (current.st_dev, current.st_ino)
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_nlink != 1
        or (expected is not None and identity != expected)
        or path.resolve() != path
    ):
        raise _unsafe()
    _check_directory(path)
    if hasattr(os, "getxattr"):
        try:
            os.getxattr(path, RECOVERY_QUARANTINE_XATTR, follow_symlinks=False)
        except OSError as exc:
            # Ordinary databases may live on filesystems without xattrs. Recovery
            # publication requires xattr support and fails closed without it.
            if exc.errno not in (errno.ENODATA, errno.ENOTSUP):
                raise
        else:
            # Presence, not the caller-controlled attribute value, is the gate.
            raise OctopError.localized(ErrorCode.BACKUP_RECOVERY_QUARANTINED)
    return identity


def prepare_sqlite_path(path: Path) -> tuple[Path, tuple[int, int] | None]:
    """Canonicalize supported symlinks; refuse hardlinks and marked recovery inodes."""
    _check_directory(path)
    canonical = path.resolve()
    _check_directory(canonical)
    canonical.parent.mkdir(parents=True, exist_ok=True)
    try:
        return canonical, validate_open_path(canonical, None)
    except FileNotFoundError:
        # SQLite itself creates a missing database, keeping descriptor/lock
        # ownership within SQLite. Recheck its path before running the first SQL.
        return canonical, None
