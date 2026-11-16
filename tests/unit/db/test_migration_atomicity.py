"""Interrupted SQLite script upgrades must leave their version and DDL together."""

import sqlite3

import pytest

from octop.infra.db import migrate
from octop.infra.db.pool import SqlitePool


@pytest.fixture
def before_script(tmp_path, monkeypatch, version):
    db = SqlitePool(tmp_path / "upgrade.db")
    discover = migrate._discover
    with monkeypatch.context() as patch:
        patch.setattr(
            migrate,
            "_discover",
            lambda dialect: [
                (number, path) for number, path in discover(dialect) if number < version
            ],
        )
        migrate.run_migrations(db)
    yield db
    db.close()


@pytest.mark.parametrize("version", range(32, 50))
def test_failed_script_rolls_back_and_restart_can_retry(before_script, version):
    db = before_script
    path = next(path for number, path in migrate._discover("sqlite") if number == version)
    with db.connect() as conn:
        conn.execute(
            f"CREATE TRIGGER fail_upgrade BEFORE UPDATE ON _schema_version WHEN NEW.version={version} BEGIN SELECT RAISE(ABORT,'INJECTED_FAILURE'); END"
        )
        before = [
            tuple(r)
            for r in conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name")
        ]
    with pytest.raises(sqlite3.IntegrityError, match="INJECTED_FAILURE"):
        migrate._apply_sqlite_migration(db, version, path)
    with db.connect() as conn:
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == version - 1
        assert [
            tuple(r)
            for r in conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name")
        ] == before
        assert not conn.in_transaction
        conn.execute("DROP TRIGGER fail_upgrade")
    db.close()
    reopened = SqlitePool(db.path)
    try:
        migrate.run_migrations(reopened)
        migrate.run_migrations(reopened)
        with reopened.connect() as conn:
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 49
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        reopened.close()


@pytest.mark.parametrize("version", [35, 42, 44])
def test_reader_never_sees_new_ddl_with_old_watermark(before_script, version):
    db = before_script
    reader = SqlitePool(db.path)
    observations = []

    def observe():
        with reader.connect() as conn:
            observations.append(
                (
                    conn.execute("SELECT version FROM _schema_version").fetchone()[0],
                    [
                        tuple(r)
                        for r in conn.execute(
                            "SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name"
                        )
                    ],
                )
            )
        return 1

    try:
        observe()
        with db.connect() as conn:
            conn.create_function("observe_upgrade", 0, observe)
            conn.execute(
                f"CREATE TRIGGER observe_upgrade_step BEFORE UPDATE ON _schema_version WHEN NEW.version={version} BEGIN SELECT observe_upgrade(); END"
            )
        path = next(path for number, path in migrate._discover("sqlite") if number == version)
        migrate._apply_sqlite_migration(db, version, path)
        assert observations[0] == observations[1]
        with reader.connect() as conn:
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == version
        with db.connect() as conn:
            conn.execute("DROP TRIGGER observe_upgrade_step")
            conn.create_function("observe_upgrade", 0, None)
    finally:
        reader.close()
