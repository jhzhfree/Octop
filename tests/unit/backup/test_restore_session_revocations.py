"""Synthetic SQLite backup/restore/reopen, with real signed-token verification."""

import hashlib

import pytest

from octop.config import DatabaseConfig
from octop.infra.auth import sessions
from octop.infra.auth.tokens import decode_token, sign_token
from octop.infra.backup.system_archive import create_system_backup, restore_system_backup
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.paths import PathLayout


@pytest.mark.parametrize("preserve_users", [False, True])
def test_restore_and_reopen_never_resurrect_revoked_session_families(tmp_path, preserve_users):
    paths = PathLayout(tmp_path / "synthetic")
    pool = SqlitePool(paths.db)
    run_migrations(pool)
    secret = b"synthetic-test-only-not-a-real-secret"
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(username,password_hash,role,created_at) VALUES('fixture','not-a-password','admin',1)"
        )
        conn.execute("INSERT INTO secrets(k,v,created_at) VALUES('jwt',?,1)", (secret,))

    def token(sid):
        return sign_token(secret, sub=1, uname="fixture", role="admin", session_id=sid)

    archived, later, active = [token(sid) for sid in ("archive-revoked", "later-revoked", "active")]
    sessions.revoke(pool, decode_token(secret, archived), archived)
    archive = tmp_path / "synthetic.tar.gz"
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
    before = hashlib.sha256(archive.read_bytes()).hexdigest()
    sessions.revoke(pool, decode_token(secret, later), later)
    renewal = sign_token(
        secret, sub=1, uname="fixture", role="admin", session_id="later-revoked", ttl_seconds=7200
    )
    assert renewal != later
    restore_system_backup(
        archive,
        paths=paths,
        pool=pool,
        db_config=DatabaseConfig(),
        restore_config=False,
        preserve_users=preserve_users,
    )
    pool.close()
    reopened = SqlitePool(paths.db)
    try:
        run_migrations(reopened)
        with reopened.connect() as conn:
            restored_secret = bytes(
                conn.execute("SELECT v FROM secrets WHERE k='jwt'").fetchone()[0]
            )
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        for revoked in (archived, later, renewal):
            with pytest.raises(OctopError, match="session revoked"):
                sessions.require_active(reopened, decode_token(restored_secret, revoked), revoked)
        sessions.require_active(reopened, decode_token(restored_secret, active), active)
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == before
    finally:
        reopened.close()


def _prepared_fixture(tmp_path):
    from octop.infra.backup.snapshot import snapshot_sqlite_file

    pool = SqlitePool(tmp_path / "target.sqlite")
    run_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(username,password_hash,role,created_at) VALUES('archive','fixture','user',1)"
        )
    backup = tmp_path / "source.sqlite"
    snapshot_sqlite_file(pool.path, backup)
    with pool.transaction() as conn:
        conn.execute("UPDATE users SET username='live'")
        conn.execute("INSERT INTO auth_session_revocations VALUES(?,1)", ("a" * 64,))
    return pool, backup


@pytest.mark.parametrize("phase", ["prepare", "merge"])
def test_preparation_failure_does_not_replace_target_or_modify_source(tmp_path, monkeypatch, phase):
    from octop.infra.backup import snapshot

    pool, backup = _prepared_fixture(tmp_path)
    digest = hashlib.sha256(backup.read_bytes()).hexdigest()

    def failed(*args):
        raise RuntimeError("synthetic preparation failure")

    monkeypatch.setattr(
        snapshot, "run_migrations" if phase == "prepare" else "_merge_session_revocations", failed
    )
    try:
        with pytest.raises(OctopError if phase == "prepare" else RuntimeError) as error:
            snapshot.restore_sqlite_into_pool(backup, pool)
        if phase == "prepare":
            assert error.value.code == ErrorCode.BACKUP_SCHEMA_INCOMPATIBLE
        with pool.connect() as conn:
            assert conn.execute("SELECT username FROM users").fetchone()[0] == "live"
            assert conn.execute("SELECT COUNT(*) FROM auth_session_revocations").fetchone()[0] == 1
        assert hashlib.sha256(backup.read_bytes()).hexdigest() == digest
    finally:
        pool.close()


def test_same_pool_auth_read_waits_until_revocations_are_in_restored_image(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from octop.infra.backup import snapshot
    from octop.infra.db.repos.auth_session_revocations import AuthSessionRevocationRepo

    pool, backup = _prepared_fixture(tmp_path)
    entered, release, attempted, finished = Event(), Event(), Event(), Event()
    original = snapshot._merge_session_revocations

    def paused(live, prepared):
        original(live, prepared)
        entered.set()
        assert release.wait(5)

    def read():
        attempted.set()
        result = AuthSessionRevocationRepo(pool).is_revoked("a" * 64)
        finished.set()
        return result

    monkeypatch.setattr(snapshot, "_merge_session_revocations", paused)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            restore = executor.submit(snapshot.restore_sqlite_into_pool, backup, pool)
            assert entered.wait(5)
            reader = executor.submit(read)
            assert attempted.wait(5)
            try:
                assert not finished.wait(0.05)
            finally:
                release.set()
            restore.result(timeout=5)
            assert reader.result(timeout=5) is True
    finally:
        release.set()
        pool.close()


def test_missing_current_revocation_history_fails_closed(tmp_path):
    import sqlite3

    from octop.infra.backup.snapshot import restore_sqlite_into_pool

    pool, backup = _prepared_fixture(tmp_path)
    try:
        with pool.transaction() as conn:
            conn.execute("DROP TABLE auth_session_revocations")
        with pytest.raises(sqlite3.DatabaseError, match="history is missing"):
            restore_sqlite_into_pool(backup, pool)
        with pool.connect() as conn:
            assert conn.execute("SELECT username FROM users").fetchone()[0] == "live"
    finally:
        pool.close()


def test_restore_into_empty_target(tmp_path):
    from octop.infra.backup.snapshot import restore_sqlite_into_pool

    pool, backup = _prepared_fixture(tmp_path)
    empty = SqlitePool(tmp_path / "empty.sqlite")
    try:
        restore_sqlite_into_pool(backup, empty)
        with empty.connect() as conn:
            assert conn.execute("SELECT username FROM users").fetchone()[0] == "archive"
            assert conn.execute("SELECT COUNT(*) FROM auth_session_revocations").fetchone()[0] == 0
    finally:
        empty.close()
        pool.close()


def test_pre_revocation_archive_is_upgraded_before_merge(tmp_path, monkeypatch):
    from octop.infra.backup.snapshot import restore_sqlite_into_pool
    from octop.infra.db import migrate

    pool, _ = _prepared_fixture(tmp_path)
    old = SqlitePool(tmp_path / "old.sqlite")
    discover = migrate._discover
    try:
        with monkeypatch.context() as scoped:
            scoped.setattr(
                migrate,
                "_discover",
                lambda dialect="sqlite": [(v, p) for v, p in discover(dialect) if v <= 37],
            )
            migrate.run_migrations(old)
        with old.connect() as conn:
            assert (
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='auth_session_revocations'"
                ).fetchone()
                is None
            )
        old.close()
        restore_sqlite_into_pool(tmp_path / "old.sqlite", pool)
        with pool.connect() as conn:
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 42
            assert (
                conn.execute("SELECT session_digest FROM auth_session_revocations").fetchone()[0]
                == "a" * 64
            )
    finally:
        old.close()
        pool.close()
