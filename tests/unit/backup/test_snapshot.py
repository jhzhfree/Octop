"""Unit tests for SQLite snapshot helpers."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from octop.infra.backup.snapshot import (
    snapshot_sqlite_file,
)
from octop.infra.db.pool import SqlitePool


@pytest.mark.skipif(os.name == "nt", reason="Windows disallows '?' in path names")
def test_snapshot_sqlite_file_with_special_chars_in_path(tmp_path: Path) -> None:
    """Paths containing URI metacharacters must not alter connect options."""
    tricky_dir = tmp_path / "dir?mode=memory"
    tricky_dir.mkdir()
    source = tricky_dir / "data.db"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.execute("INSERT INTO t VALUES ('ok')")

    dest = tmp_path / "backup.db"
    snapshot_sqlite_file(source, dest)

    with sqlite3.connect(dest) as conn:
        row = conn.execute("SELECT v FROM t").fetchone()
    assert row is not None
    assert row[0] == "ok"


def test_snapshot_includes_committed_wal_without_copying_sidecars(tmp_path):
    """Synthetic WAL-only commit is readable from the standalone backup file."""
    import shutil

    source = tmp_path / "source.sqlite"
    pool = SqlitePool(source)
    try:
        with pool.connect() as conn:
            conn.execute("CREATE TABLE probe(value TEXT)")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("PRAGMA wal_autocheckpoint=0")  # This synthetic connection only.
            conn.execute("INSERT INTO probe VALUES('committed in WAL')")
        wal = Path(str(source) + "-wal")
        assert wal.stat().st_size > 0
        backup = tmp_path / "snapshot.sqlite"
        snapshot_sqlite_file(source, backup)
        isolated = tmp_path / "isolated"
        isolated.mkdir()
        standalone = isolated / "copy.sqlite"
        shutil.copy2(backup, standalone)
        assert len(list(isolated.iterdir())) == 1
        with sqlite3.connect(standalone) as check:
            assert check.execute("SELECT value FROM probe").fetchone()[0] == "committed in WAL"
            assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        with pool.connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM probe").fetchone()[0] == 1
    finally:
        pool.close()


def test_snapshot_refuses_existing_destination_without_overwriting(tmp_path):
    source = tmp_path / "source.sqlite"
    dest = tmp_path / "existing.sqlite"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE archived(value TEXT)")
    pool = SqlitePool(dest)
    try:
        with pool.connect() as conn:
            conn.execute("CREATE TABLE current(value TEXT)")
            conn.execute("INSERT INTO current VALUES('keep')")
        with pytest.raises(FileExistsError):
            snapshot_sqlite_file(source, dest)
        with pool.connect() as conn:
            assert conn.execute("SELECT value FROM current").fetchone()[0] == "keep"
            assert (
                conn.execute("SELECT 1 FROM sqlite_master WHERE name='archived'").fetchone() is None
            )
    finally:
        pool.close()
