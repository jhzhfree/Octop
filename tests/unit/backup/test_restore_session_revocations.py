"""Refusal preserves security state; these are not successful recovery tests."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from octop.cli.commands import backup as backup_cli
from octop.config import DatabaseConfig
from octop.infra.auth import sessions
from octop.infra.auth.tokens import InvalidToken, decode_token, sign_token
from octop.infra.backup import restore_system_backup
from octop.infra.backup.snapshot import restore_sqlite_file, restore_sqlite_into_pool
from octop.infra.backup.system_archive import create_system_backup
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.paths import PathLayout


def rejected(call):
    with pytest.raises(OctopError) as error:
        call()
    assert error.value.code == ErrorCode.BACKUP_RESTORE_UNAVAILABLE
    assert error.value.status == 409


def digest(pool):
    with pool.connect() as conn:
        return hashlib.sha256(conn.serialize()).hexdigest()


@pytest.fixture
def security_fixture(tmp_path):
    paths = PathLayout(tmp_path / "synthetic")
    pool = SqlitePool(paths.db)
    run_migrations(pool)
    old_key = b"synthetic-archive-key-only-for-tests-0001"
    current_key = b"synthetic-current-key-only-for-tests-0002"
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id,username,password_hash,role,created_at) VALUES(1,'fixture','old-password','admin',1)"
        )
        conn.execute("INSERT INTO secrets(k,v,created_at) VALUES('jwt',?,1)", (old_key,))
        conn.execute(
            "INSERT INTO organizations(organization_id,owner_user_id,name,created_at,updated_at) VALUES('o',1,'fixture',1,1)"
        )
        conn.execute(
            "INSERT INTO organization_members(organization_id,user_id,role,created_at) VALUES('o',1,'owner',1)"
        )
    archive = tmp_path / "old.tar.gz"
    create_system_backup(
        paths=paths,
        agent_rows=[],
        pool=pool,
        db_config=DatabaseConfig(),
        dest=archive,
        include_config=False,
        include_workspaces=False,
        include_skill_packages=False,
        include_plugins=False,
        include_knowledge=False,
    )
    with pool.transaction() as conn:
        conn.execute("UPDATE secrets SET v=? WHERE k='jwt'", (current_key,))
        conn.execute(
            "UPDATE users SET password_hash='new-password',role='user',permissions='[]',disabled=1 WHERE id=1"
        )
        conn.execute("DELETE FROM organization_members")
    revoked = sign_token(current_key, sub=1, uname="fixture", role="user", session_id="revoked")
    sessions.revoke(pool, decode_token(current_key, revoked), revoked)
    sentinel = paths.root / "current-workspace.txt"
    sentinel.write_text("current data")
    yield paths, pool, archive, sentinel, revoked, current_key
    pool.close()


@pytest.mark.parametrize("preserve_users", [None, False, True])
@pytest.mark.parametrize("source_kind", ["valid", "invalid", "missing", "bytes"])
def test_no_restore_option_rolls_back_identity_or_files(
    security_fixture, tmp_path, preserve_users, source_kind
):
    paths, pool, archive, sentinel, revoked, key = security_fixture
    source = archive
    if source_kind == "invalid":
        source = tmp_path / "invalid.tar.gz"
        source.write_bytes(b"invalid archive")
    elif source_kind == "missing":
        source = tmp_path / "missing.tar.gz"
    elif source_kind == "bytes":
        source = archive.read_bytes()
    before = digest(pool)
    archive_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    rejected(
        lambda: restore_system_backup(
            source,
            paths=paths,
            pool=pool,
            db_config=DatabaseConfig(),
            preserve_users=preserve_users,
            owner_user_id=1,
        )
    )
    assert digest(pool) == before
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == archive_hash
    assert sentinel.read_text() == "current data"
    archived_token = sign_token(
        b"synthetic-archive-key-only-for-tests-0001", sub=1, uname="fixture", role="admin"
    )
    with pool.connect() as conn:
        assert bytes(conn.execute("SELECT v FROM secrets WHERE k='jwt'").fetchone()[0]) == key
        assert tuple(
            conn.execute("SELECT role,permissions,disabled FROM users WHERE id=1").fetchone()
        ) == ("user", "[]", 1)
        assert conn.execute("SELECT COUNT(*) FROM organization_members").fetchone()[0] == 0
    with pytest.raises(InvalidToken):
        decode_token(key, archived_token)
    with pytest.raises(OctopError, match="session revoked"):
        sessions.require_active(pool, decode_token(key, revoked), revoked)


@pytest.mark.parametrize("entry", ["system", "pool", "file"])
def test_refusal_precedes_disk_access_or_replacement(monkeypatch, tmp_path, entry):
    # I/O failures are injected as tripwires: refusal must not reach any of them.
    import os
    import shutil
    import sqlite3

    from octop.infra.backup import system_archive

    tripwire = Mock(side_effect=OSError("synthetic disk failure"))
    monkeypatch.setattr(sqlite3, "connect", tripwire)
    monkeypatch.setattr(shutil, "copy2", tripwire)
    monkeypatch.setattr(os, "replace", tripwire)
    monkeypatch.setattr(system_archive.tarfile, "open", tripwire)
    source, target = tmp_path / "missing", tmp_path / "new" / "target.sqlite"
    if entry == "system":
        rejected(
            lambda: restore_system_backup(
                source,
                paths=PathLayout(target.parent),
                pool=Mock(dialect="postgresql"),
                db_config=DatabaseConfig(),
            )
        )
    elif entry == "pool":
        rejected(lambda: restore_sqlite_into_pool(source, Mock()))
    else:
        rejected(lambda: restore_sqlite_file(source, target))
    tripwire.assert_not_called()
    assert not target.parent.exists()


@pytest.mark.parametrize("entry", ["pool", "file", "system"])
def test_competing_connection_does_not_make_refusal_wait_or_overwrite(security_fixture, entry):
    paths, pool, archive, _, _, _ = security_fixture
    second = SqlitePool(paths.db)
    try:
        with second.transaction() as conn:
            conn.execute("UPDATE users SET display_name='concurrent change' WHERE id=1")
            with ThreadPoolExecutor(max_workers=1) as executor:

                def call():
                    if entry == "pool":
                        restore_sqlite_into_pool(archive, pool)
                    elif entry == "file":
                        restore_sqlite_file(archive, paths.db)
                    else:
                        restore_system_backup(
                            archive, paths=paths, pool=pool, db_config=DatabaseConfig()
                        )

                executor.submit(rejected, call).result(timeout=2)
        with pool.connect() as conn:
            assert (
                conn.execute("SELECT display_name FROM users WHERE id=1").fetchone()[0]
                == "concurrent change"
            )
    finally:
        second.close()


@pytest.mark.parametrize("flags", [[], ["--yes"], ["--yes", "--no-config", "--owner-user-id", "1"]])
def test_cli_refuses_before_home_config_or_database_access(tmp_path, monkeypatch, flags):
    archive = tmp_path / "archive.tar.gz"
    archive.write_bytes(b"synthetic old archive")
    home = tmp_path / "uncreated-home"
    tripwire = Mock(side_effect=AssertionError("must not read config or open database"))
    monkeypatch.setattr(backup_cli, "load_config", tripwire)
    monkeypatch.setattr(backup_cli, "open_database", tripwire)
    result = CliRunner().invoke(
        backup_cli.backup, ["restore", str(archive), "--home", str(home), *flags]
    )
    assert result.exit_code != 0
    assert "System restore is unavailable" in result.output
    assert "Continue?" not in result.output
    tripwire.assert_not_called()
    assert not home.exists()


def test_cli_create_remains_available(tmp_path):
    import tarfile

    dest = tmp_path / "created.tar.gz"
    result = CliRunner().invoke(
        backup_cli.backup,
        [
            "create",
            "--home",
            str(tmp_path / "fresh-home"),
            "--output",
            str(dest),
            "--no-config",
            "--no-workspaces",
            "--no-skill-packages",
            "--no-plugins",
            "--no-knowledge",
        ],
    )
    assert result.exit_code == 0, result.output
    with tarfile.open(dest) as archive:
        assert "manifest.json" in archive.getnames()
        assert "db/octop.db" in archive.getnames()
