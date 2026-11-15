"""Real synthetic SQLite recovery images; never a live application restore."""

import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from octop.cli.commands.backup import backup
from octop.config import DatabaseConfig
from octop.infra.auth.tokens import InvalidToken, decode_token, sign_token
from octop.infra.backup import recovery_image as recovery
from octop.infra.backup.system_archive import create_system_backup
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.paths import PathLayout

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="Linux local-filesystem recovery protocol"
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def recover(source, output):
    return recovery.create_recovery_image(
        source, output, identity_policy="quarantine-all", expected_sha256=sha(source)
    )


@pytest.fixture
def source(tmp_path):
    paths = PathLayout(tmp_path / "original")
    pool = SqlitePool(paths.db)
    run_migrations(pool)
    old = b"synthetic-old-archive-key-0000000000001"
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id,username,password_hash,role,permissions,created_at) VALUES(1,'fixture','old-password','admin','[\"backup\"]',1)"
        )
        conn.execute("INSERT INTO secrets(k,v,created_at) VALUES('jwt',?,1)", (old,))
        conn.execute(
            "INSERT INTO organizations(organization_id,owner_user_id,name,created_at,updated_at) VALUES('o',1,'archived business data',1,1)"
        )
        conn.execute(
            "INSERT INTO organization_members(organization_id,user_id,role,created_at) VALUES('o',1,'owner',1)"
        )
        conn.execute("INSERT INTO auth_session_revocations VALUES(?,1)", ("a" * 64,))
    archive = tmp_path / "backup.tar.gz"
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
    # Original instance tightens access after the backup: recovery must never undo it.
    with pool.transaction() as conn:
        conn.execute("UPDATE users SET disabled=1,role='user',permissions='[]'")
        conn.execute("DELETE FROM organization_members")
        conn.execute(
            "UPDATE secrets SET v=? WHERE k='jwt'", (b"synthetic-current-key-0000000000000002",)
        )
    yield archive, pool, old
    pool.close()


def assert_quarantined(path):
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert (
            conn.execute("SELECT name FROM organizations").fetchone()[0] == "archived business data"
        )
        assert conn.execute(
            "SELECT disabled,password_hash,permissions,role FROM users"
        ).fetchone() == (1, None, "[]", "user")
        metadata = json.loads(
            conn.execute("SELECT metadata_json FROM octop_recovery_quarantine").fetchone()[0]
        )
        assert metadata["application_start_allowed"] is False
        assert (
            conn.execute("SELECT session_digest FROM auth_session_revocations").fetchone()[0]
            == "a" * 64
        )
        key = conn.execute("SELECT v FROM secrets WHERE k='jwt'").fetchone()[0]
    before = sha(path)
    with pytest.raises(OctopError) as error:
        SqlitePool(path)
    assert error.value.code == ErrorCode.BACKUP_RECOVERY_QUARANTINED
    assert sha(path) == before
    return key


def test_success_preserves_original_and_recovers_data_under_new_key(source, tmp_path):
    archive, original, old_key = source
    with original.connect() as conn:
        before = hashlib.sha256(conn.serialize()).hexdigest()
    original_hash = sha(archive)
    output = tmp_path / "recovered.sqlite"
    result = recover(archive, output)
    key = assert_quarantined(output)
    assert key != old_key and len(key) == 32
    old_token = sign_token(old_key, sub=1, uname="fixture", role="admin")
    with pytest.raises(InvalidToken):
        decode_token(key, old_token)
    with original.connect() as conn:
        assert hashlib.sha256(conn.serialize()).hexdigest() == before
    assert sha(archive) == original_hash == result["source_sha256"]
    assert result["image_sha256"] == sha(output)
    assert output.stat().st_mode & 0o777 == 0o600
    assert not Path(str(output) + "-wal").exists()
    assert not list(tmp_path.glob(".octop-recovery-*"))


def test_cli_success_and_required_policy(source, tmp_path):
    archive, _, _ = source
    output = tmp_path / "cli.sqlite"
    args = [
        "recover-image",
        str(archive),
        "--output",
        str(output),
        "--archive-sha256",
        sha(archive),
    ]
    result = CliRunner().invoke(backup, args)
    assert result.exit_code != 0 and not output.exists()
    result = CliRunner().invoke(backup, args + ["--identity-policy", "quarantine-all"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["users_quarantined"] == 1
    assert_quarantined(output)


@pytest.mark.parametrize("phase", ["prepare", "publish", "pre_sync", "post_sync"])
def test_failure_before_or_after_publication_is_explicit(source, tmp_path, monkeypatch, phase):
    archive, _, _ = source
    output = tmp_path / "failed.sqlite"
    original_hash = sha(archive)

    def fail(*args):
        raise OSError("synthetic disk failure")

    if phase == "prepare":
        prepare = recovery._prepare_database

        def prepared_then_fail(*args):
            prepare(*args)
            fail()

        monkeypatch.setattr(recovery, "_prepare_database", prepared_then_fail)
    elif phase == "publish":
        monkeypatch.setattr(recovery, "_publish", fail)
    else:
        sync = recovery._sync_directory
        calls = 0

        def failed_sync(fd):
            nonlocal calls
            calls += 1
            if calls == (1 if phase == "pre_sync" else 2):
                fail()
            sync(fd)

        monkeypatch.setattr(recovery, "_sync_directory", failed_sync)
    with pytest.raises(OctopError) as error:
        recover(archive, output)
    assert sha(archive) == original_hash
    if phase == "post_sync":
        assert error.value.code == ErrorCode.BACKUP_RECOVERY_COMMIT_UNCERTAIN
        assert_quarantined(output)
    else:
        assert error.value.code == ErrorCode.BACKUP_RECOVERY_REJECTED
        assert not output.exists()


def test_noncooperating_creator_cannot_be_overwritten(source, tmp_path, monkeypatch):
    archive, _, _ = source
    output = tmp_path / "raced.sqlite"
    publish = recovery._publish

    def competing(prepared, name, fd):
        other = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=fd)
        os.write(other, b"other writer")
        os.close(other)
        publish(prepared, name, fd)

    monkeypatch.setattr(recovery, "_publish", competing)
    with pytest.raises(OctopError):
        recover(archive, output)
    assert output.read_bytes() == b"other writer"


@pytest.mark.parametrize("phase", ["before", "after"])
def test_process_crash_leaves_absent_or_complete_quarantined_target(source, tmp_path, phase):
    archive, _, _ = source
    output = tmp_path / "crashed.sqlite"
    script = """
import os, sys
from pathlib import Path
from octop.infra.backup import recovery_image as r
original = r._publish
def crash(*args):
    if sys.argv[4] == "after":
        original(*args)
    os._exit(73)
r._publish = crash
r.create_recovery_image(Path(sys.argv[1]), Path(sys.argv[2]), identity_policy="quarantine-all", expected_sha256=sys.argv[3])
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(archive), str(output), sha(archive), phase], timeout=20
    )
    assert result.returncode == 73
    if phase == "after":
        assert_quarantined(output)
    else:
        assert not output.exists()
        # Process death releases flock, even though its lock inode remains.
        recover(archive, output)
        assert_quarantined(output)


def test_separate_process_builder_lock_is_enforced(source, tmp_path):
    archive, _, _ = source
    lock = tmp_path / ".octop-recovery.lock"
    script = """
import fcntl, os, sys
fd=os.open(sys.argv[1], os.O_RDWR|os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
print("locked", flush=True)
sys.stdin.read(1)
"""
    with subprocess.Popen(
        [sys.executable, "-c", script, str(lock)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as child:
        try:
            assert child.stdout.readline().strip() == "locked"
            with pytest.raises(OctopError) as error:
                recover(archive, tmp_path / "locked.sqlite")
            assert error.value.details["reason"] == "recovery_builder_busy"
        finally:
            child.communicate("x", timeout=5)


def rewrite_archive(source, dest, change):
    with tarfile.open(source) as archive:
        members = {
            member.name: archive.extractfile(member).read() for member in archive if member.isfile()
        }
    change(members)
    with tarfile.open(dest, "w:gz") as archive:
        for name, data in members.items():
            item = tarfile.TarInfo(name)
            item.size = len(data)
            archive.addfile(item, io.BytesIO(data))


@pytest.mark.parametrize("kind", ["invalid", "future", "watermark", "pg", "wal", "history"])
def test_invalid_or_unsupported_archives_never_publish(source, tmp_path, kind):
    archive, _, _ = source
    changed = tmp_path / "bad.tar.gz"

    def change(members):
        manifest = json.loads(members["manifest.json"])
        if kind == "future":
            manifest["schema_version"] = 9999
        if kind == "watermark":
            manifest["schema_version"] = 37
        if kind == "pg":
            manifest["database_driver"] = "postgresql"
        if kind == "wal":
            members["db/octop.db-wal"] = b"sidecar"
        if kind == "history":
            members["history/history_v2.required"] = b"required"
        members["manifest.json"] = json.dumps(manifest).encode()

    rewrite_archive(archive, changed, change)
    if kind == "invalid":
        changed.write_bytes(b"not a tar")
    with pytest.raises(OctopError):
        recover(changed, tmp_path / "bad.sqlite")
    assert not (tmp_path / "bad.sqlite").exists()


def test_old_schema_upgrades_only_private_image(source, tmp_path, monkeypatch):
    from octop.infra.db import migrate

    paths = PathLayout(tmp_path / "old")
    old = SqlitePool(paths.db)
    discover = migrate._discover
    try:
        with monkeypatch.context() as scoped:
            scoped.setattr(
                migrate,
                "_discover",
                lambda dialect="sqlite": [(v, p) for v, p in discover(dialect) if v <= 37],
            )
            run_migrations(old)
        with old.transaction() as conn:
            conn.execute(
                "INSERT INTO users(username,password_hash,role,created_at) VALUES('legacy','old','admin',1)"
            )
        archive = tmp_path / "legacy.tar.gz"
        create_system_backup(
            paths=paths,
            agent_rows=[],
            pool=old,
            db_config=DatabaseConfig(),
            dest=archive,
            include_config=False,
            include_workspaces=False,
            include_skill_packages=False,
            include_plugins=False,
            include_knowledge=False,
        )
        output = tmp_path / "legacy.sqlite"
        result = recover(archive, output)
        assert result["schema_version"] == 42
        with old.connect() as conn:
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 37
        with sqlite3.connect(output) as conn:
            assert conn.execute("SELECT disabled,password_hash FROM users").fetchone() == (1, None)
    finally:
        old.close()


@pytest.mark.parametrize(
    "case",
    ["existing", "destination_link", "source_link", "checksum", "policy", "public_directory"],
)
def test_preconditions_fail_without_modifying_existing_data(source, tmp_path, case):
    archive, original, _ = source
    output = tmp_path / "precondition.sqlite"
    checksum = sha(archive)
    policy = "quarantine-all"
    if case == "existing":
        output = original.path
    elif case == "destination_link":
        output.symlink_to(original.path)
    elif case == "source_link":
        linked = tmp_path / "linked.tar.gz"
        linked.symlink_to(archive)
        archive = linked
    elif case == "checksum":
        checksum = "0" * 64
    elif case == "policy":
        policy = "preserve-users"
    elif case == "public_directory":
        parent = tmp_path / "public"
        parent.mkdir(mode=0o777)
        parent.chmod(0o777)
        output = parent / "image.sqlite"
    with original.connect() as conn:
        before = hashlib.sha256(conn.serialize()).hexdigest()
    with pytest.raises(OctopError):
        recovery.create_recovery_image(
            archive, output, identity_policy=policy, expected_sha256=checksum
        )
    with original.connect() as conn:
        assert hashlib.sha256(conn.serialize()).hexdigest() == before
    if case not in ("existing", "destination_link"):
        assert not output.exists()


def test_network_filesystem_probe_refuses(source, tmp_path, monkeypatch):
    import ctypes
    from types import SimpleNamespace

    archive, _, _ = source

    def nfs(_fd, buffer):
        ctypes.c_long.from_buffer(buffer).value = 0x6969
        return 0

    monkeypatch.setattr(
        recovery.ctypes, "CDLL", lambda *args, **kwargs: SimpleNamespace(fstatfs=nfs)
    )
    with pytest.raises(OctopError) as error:
        recover(archive, tmp_path / "network.sqlite")
    assert error.value.details["reason"] == "filesystem_not_supported"


def test_error_after_hard_link_is_reported_as_uncertain(source, tmp_path, monkeypatch):
    archive, _, _ = source
    output = tmp_path / "linked.sqlite"
    publish = recovery._publish

    def link_then_fail(*args):
        publish(*args)
        raise OSError("synthetic lost publication acknowledgment")

    monkeypatch.setattr(recovery, "_publish", link_then_fail)
    with pytest.raises(OctopError) as error:
        recover(archive, output)
    assert error.value.code == ErrorCode.BACKUP_RECOVERY_COMMIT_UNCERTAIN
    assert_quarantined(output)


def test_marker_boolean_cannot_activate_image(source, tmp_path):
    archive, _, _ = source
    output = tmp_path / "marker.sqlite"
    recover(archive, output)
    with sqlite3.connect(output) as conn:
        conn.execute(
            "UPDATE octop_recovery_quarantine SET metadata_json=?",
            ('{"application_start_allowed":true}',),
        )
    with pytest.raises(OctopError) as error:
        SqlitePool(output)
    assert error.value.code == ErrorCode.BACKUP_RECOVERY_QUARANTINED


def test_duplicate_database_member_rejected(source, tmp_path):
    archive, _, _ = source
    duplicate = tmp_path / "duplicate.tar.gz"
    with tarfile.open(archive) as inp, tarfile.open(duplicate, "w:gz") as out:
        for member in inp:
            data = inp.extractfile(member).read()
            out.addfile(member, io.BytesIO(data))
            if member.name == "db/octop.db":
                out.addfile(member, io.BytesIO(data))
    with pytest.raises(OctopError) as error:
        recover(duplicate, tmp_path / "duplicate.sqlite")
    assert error.value.details["reason"] == "duplicate_or_nonregular_member"
