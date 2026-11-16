"""Serialize cooperating wizard mutations on one stable local home directory.

Not a barrier for old binaries, direct config editors or ordinary DB writers.
Never unlink the companion file while any process may be using this home.
"""

from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from octop.config import load_config
from octop.infra.db.sqlite_path import prepare_sqlite_path, validate_open_path
from octop.infra.errors import ErrorCode, OctopError


@contextmanager
def setup_mutation_guard(server: Any) -> Iterator[None]:
    root = server.paths.root.resolve()
    config_path = root / "config.json"
    config_stat = config_path.lstat()
    if (
        not stat.S_ISREG(config_stat.st_mode)
        or config_stat.st_nlink != 1
        or config_path.resolve() != config_path
    ):
        raise OctopError(
            ErrorCode.SETUP_REQUIRED, "setup requires a private, unaliased config file", status=409
        )
    path = root / ".setup-mutation-lock"
    if path.resolve() != path:
        raise OctopError(ErrorCode.SETUP_REQUIRED, "unsafe setup lock path", status=409)
    path, expected = prepare_sqlite_path(path)
    mutex = sqlite3.connect(str(path), timeout=0, isolation_level=None)
    try:
        identity = validate_open_path(path, expected)
        if os.name == "posix":
            os.chmod(path, 0o600, follow_symlinks=False)
        try:
            mutex.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError as exc:
            if exc.sqlite_errorcode in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise OctopError(
                    ErrorCode.SETUP_REQUIRED, "setup operation in progress; retry later", status=410
                ) from exc
            raise
        validate_open_path(path, identity)
        # A lock alone does not retarget a different process's already loaded
        # repositories. Refuse stale workers before they touch either database.
        loaded = server.services.config if server.services is not None else server.config
        current = load_config(server.paths.config)
        if loaded is None or (
            loaded.database != current.database
            or loaded.database_in_file != current.database_in_file
        ):
            raise OctopError(
                ErrorCode.SETUP_REQUIRED,
                "database configuration changed; restart this setup worker",
                status=409,
            )
        yield
    finally:
        try:
            if mutex.in_transaction:
                mutex.rollback()
        finally:
            mutex.close()
