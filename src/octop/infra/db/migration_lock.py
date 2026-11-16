"""Cross-process mutex for cooperating SQLite migrators on a local filesystem.

Keep this companion file in place. Unlinking/replacing it while processes run
breaks mutual exclusion. This is not a barrier against ordinary DB writers or
old binaries that do not participate in this protocol.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from octop.infra.db.sqlite_path import prepare_sqlite_path, validate_open_path


@contextmanager
def sqlite_migration_lock(connection: sqlite3.Connection) -> Iterator[None]:
    # Use SQLite's OS locking on a separate file: legacy migration helpers may
    # commit the main connection. No lock row/lease can be stranded by a crash.
    filename = next(
        (row[2] for row in connection.execute("PRAGMA database_list") if row[1] == "main"), ""
    )
    if not filename:
        raise RuntimeError("DATABASE_MIGRATION_FILE_REQUIRED")
    database = Path(filename).resolve()
    mutex_path = database.with_name(database.name + ".migration-lock")
    if mutex_path.resolve() != mutex_path:
        raise RuntimeError("DATABASE_MIGRATION_LOCK_UNSAFE")
    mutex_path, expected = prepare_sqlite_path(mutex_path)
    mutex = sqlite3.connect(str(mutex_path), timeout=5.0, isolation_level=None)
    try:
        identity = validate_open_path(mutex_path, expected)
        if os.name == "posix":
            os.chmod(mutex_path, 0o600, follow_symlinks=False)
        try:
            mutex.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError as exc:
            if exc.sqlite_errorcode in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise RuntimeError("DATABASE_MIGRATION_BUSY") from exc
            raise
        validate_open_path(mutex_path, identity)
        yield
    finally:
        try:
            if mutex.in_transaction:
                mutex.rollback()
        finally:
            mutex.close()
