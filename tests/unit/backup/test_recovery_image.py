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


def assert_quarantined(directory):
    path = directory / "octop.db"
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
    assert result["image_sha256"] == sha(output / "octop.db")
    assert output.stat().st_mode & 0o777 == 0o700
    assert (output / "octop.db").stat().st_mode & 0o777 == 0o600
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
        assert result["schema_version"] == 43
        with old.connect() as conn:
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 37
        with sqlite3.connect(output / "octop.db") as conn:
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


def test_error_after_directory_rename_is_reported_as_uncertain(source, tmp_path, monkeypatch):
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
    with sqlite3.connect(output / "octop.db") as conn:
        conn.execute(
            "UPDATE octop_recovery_quarantine SET metadata_json=?",
            ('{"application_start_allowed":true}',),
        )
    with pytest.raises(OctopError) as error:
        SqlitePool(output / "octop.db")
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


def hot_journal(archive, target):
    """Leave a real hot rollback journal using an abruptly terminated writer."""
    with tarfile.open(archive) as source:
        target.write_bytes(source.extractfile("db/octop.db").read())
    script = """
import os, sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
conn.execute('PRAGMA journal_mode=DELETE')
conn.execute('PRAGMA synchronous=FULL')
conn.execute('PRAGMA cache_size=1')
conn.execute('BEGIN IMMEDIATE')
conn.execute('UPDATE users SET disabled=1')
conn.execute('CREATE TABLE spill(data BLOB)')
for _ in range(32):
    conn.execute('INSERT INTO spill VALUES(zeroblob(4096))')
os._exit(74)
"""
    result = subprocess.run([sys.executable, "-c", script, str(target)], timeout=20)
    assert result.returncode == 74
    journal = Path(str(target) + "-journal")
    assert journal.read_bytes()[:8] == bytes.fromhex("d9d505f920a163d7")
    target.unlink()
    return journal


def test_real_hot_journal_replays_on_bare_file_but_not_new_directory(source, tmp_path):
    archive, _, _ = source
    bare = tmp_path / "unsafe-control.sqlite"
    journal = hot_journal(archive, bare)
    # Establish that this journal actually reverses quarantine, not just an inert sentinel.
    manifest = recovery._extract_database(archive, bare)
    recovery._prepare_database(bare, manifest, sha(archive))
    # Preparation already encountered the old journal. Recreate the unsafe arrangement:
    replay = tmp_path / "replay-source.sqlite"
    hot = hot_journal(archive, replay)
    journal.write_bytes(hot.read_bytes())
    with sqlite3.connect(bare) as conn:
        assert conn.execute("SELECT disabled,role FROM users").fetchone() == (0, "admin")
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='octop_recovery_quarantine'"
            ).fetchone()
            is None
        )
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    # Exactly the old public basename/sidecar arrangement is harmless to a new namespace.
    output = tmp_path / "safe.sqlite"
    sibling = hot_journal(archive, output)
    before = sibling.read_bytes()
    result = recover(archive, output)
    assert result["database"] == str(output / "octop.db")
    assert_quarantined(output)
    assert sibling.read_bytes() == before


@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
@pytest.mark.parametrize("link", [False, True])
def test_legacy_sibling_sidecars_are_never_opened(source, tmp_path, suffix, link):
    archive, original, _ = source
    output = tmp_path / "image.sqlite"
    sidecar = Path(str(output) + suffix)
    if link:
        sidecar.symlink_to(original.path)
    else:
        sidecar.write_bytes(b"old sidecar must not be interpreted")
    before = sha(sidecar)
    recover(archive, output)
    assert_quarantined(output)
    assert sha(sidecar) == before
    assert sidecar.is_symlink() == link


@pytest.mark.parametrize("kind", ["empty_directory", "hot_journal_directory", "symlink"])
def test_concurrent_directory_namespace_claim_fails_closed(source, tmp_path, monkeypatch, kind):
    archive, _, _ = source
    output = tmp_path / "competing"
    occupied = tmp_path / "other"
    occupied.mkdir()
    publish = recovery._publish

    def competing(prepared, name, fd):
        # Independent process does not participate in the builder flock.
        script = """
import os, sys
from pathlib import Path
path = Path(sys.argv[1])
if sys.argv[2] == 'symlink':
    path.symlink_to(sys.argv[3], target_is_directory=True)
else:
    path.mkdir(mode=0o700)
"""
        subprocess.run(
            [sys.executable, "-c", script, str(output), kind, str(occupied)],
            check=True,
            timeout=10,
        )
        if kind == "hot_journal_directory":
            hot_journal(archive, output / "octop.db")
        publish(prepared, name, fd)

    monkeypatch.setattr(recovery, "_publish", competing)
    with pytest.raises(OctopError) as error:
        recover(archive, output)
    assert error.value.code == ErrorCode.BACKUP_RECOVERY_REJECTED
    assert not (output / "octop.db").exists()
    if kind == "hot_journal_directory":
        assert (output / "octop.db-journal").is_file()
    if kind == "symlink":
        assert output.is_symlink() and not list(occupied.iterdir())


def test_second_publication_does_not_touch_first_namespace(source, tmp_path):
    archive, _, _ = source
    output = tmp_path / "once"
    recover(archive, output)
    before = sha(output / "octop.db")
    with pytest.raises(OctopError) as error:
        recover(archive, output)
    assert error.value.details["reason"] == "output_exists"
    assert sha(output / "octop.db") == before
    assert_quarantined(output)


def test_private_directory_fsync_failure_never_publishes(source, tmp_path, monkeypatch):
    archive, _, _ = source
    output = tmp_path / "disk-failure"
    fsync = os.fsync
    count = 0

    def fail_directory(fd):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("synthetic directory persistence failure")
        fsync(fd)

    monkeypatch.setattr(recovery.os, "fsync", fail_directory)
    with pytest.raises(OctopError):
        recover(archive, output)
    assert not output.exists()


def test_fifo_source_rejected_without_waiting_for_writer(tmp_path):
    fifo = tmp_path / "source.fifo"
    os.mkfifo(fifo)
    script = """
import sys
from pathlib import Path
from octop.infra.backup.recovery_image import create_recovery_image
from octop.infra.errors import OctopError
try:
    create_recovery_image(Path(sys.argv[1]), Path(sys.argv[2]), identity_policy='quarantine-all', expected_sha256='0'*64)
except OctopError:
    sys.exit(0)
sys.exit(1)
"""
    subprocess.run(
        [sys.executable, "-c", script, str(fifo), str(tmp_path / "output")],
        check=True,
        timeout=10,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", [False, True])
async def test_startup_rejects_before_plugins_probes_or_runtime(
    source, tmp_path, monkeypatch, configured
):
    from octop.infra import server

    archive, _, _ = source
    output = tmp_path / "startup-image"
    recover(archive, output)
    home = tmp_path / "configured-home" if configured else output
    home.mkdir(exist_ok=True)
    config = (
        {"database": {"driver": "sqlite", "sqlite_path": str(output / "octop.db")}}
        if configured
        else {}
    )
    (home / "config.json").write_text(json.dumps(config))
    before = sha(output / "octop.db")

    def forbidden(*args, **kwargs):
        pytest.fail("quarantined image reached startup side effects")

    monkeypatch.setattr(server.OctopServer, "_setup_logging", forbidden)
    monkeypatch.setattr(server, "ensure_mobile_capabilities_probed", forbidden)
    monkeypatch.setattr(server.PluginManager, "load_installed", forbidden)
    monkeypatch.setattr(server.OctopServer, "_boot_runtime", forbidden)
    instance = server.OctopServer(home=home)
    with pytest.raises(OctopError) as error:
        await instance.start()
    assert error.value.code == ErrorCode.BACKUP_RECOVERY_QUARANTINED
    assert instance.services is None and not instance._started
    assert sha(output / "octop.db") == before


@pytest.mark.asyncio
async def test_startup_preparation_failure_closes_early_database(tmp_path, monkeypatch):
    from octop.infra import server

    home = tmp_path / "normal-home"
    home.mkdir()
    SqlitePool(home / "octop.db").close()
    opened = []
    open_database = server.open_database

    def capture(*args):
        pool = open_database(*args)
        opened.append(pool)
        return pool

    def fail(_self):
        raise OSError("synthetic startup disk failure")

    monkeypatch.setattr(server, "open_database", capture)
    monkeypatch.setattr(server.OctopServer, "_setup_logging", fail)
    instance = server.OctopServer(home=home)
    with pytest.raises(OSError, match="synthetic startup"):
        await instance.start()
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"), opened[0].connect() as conn:
        conn.execute("SELECT 1")
    assert instance.services is None and not instance._started


@pytest.mark.asyncio
async def test_greenfield_still_defers_control_plane_without_opening_db(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from octop.config import load_config
    from octop.infra import server

    home = tmp_path / "greenfield"
    home.mkdir()
    monkeypatch.setattr(server, "ensure_mobile_capabilities_probed", load_config)
    monkeypatch.setattr(server.OctopServer, "_setup_logging", Mock())
    monkeypatch.setattr(server.OctopServer, "_emit_wizard_password", Mock())
    for name in ("ExpertCatalog", "SubagentCatalog", "PluginManager"):
        monkeypatch.setattr(server, name, Mock())
    opened = Mock(side_effect=AssertionError("greenfield must not open a pool"))
    monkeypatch.setattr(server, "open_database", opened)
    instance = server.OctopServer(home=home)
    await instance.start()
    assert instance._started and instance.services is None and instance.config is not None
    opened.assert_not_called()
    assert not (home / "octop.db").exists()


def test_postpublication_hot_journal_cannot_run_before_pool_gate(source, tmp_path):
    archive, _, _ = source
    output = tmp_path / "after-publication"
    recover(archive, output)
    hot = hot_journal(archive, tmp_path / "old-writer.sqlite")
    target = output / "octop.db"
    # Simulate a local process introducing a sidecar after publication. The owner
    # can still bypass this with raw sqlite3; the supported pool must not replay it.
    sidecar = Path(str(target) + "-journal")
    sidecar.write_bytes(hot.read_bytes())
    before, before_journal = sha(target), sha(sidecar)
    with pytest.raises(OctopError) as error:
        SqlitePool(target)
    assert error.value.code == ErrorCode.BACKUP_RECOVERY_QUARANTINED
    assert sha(target) == before and sha(sidecar) == before_journal


def test_external_marker_symlink_also_refuses_before_sqlite_connect(source, tmp_path, monkeypatch):
    archive, _, _ = source
    output = tmp_path / "marker-link"
    recover(archive, output)
    marker = output / recovery.RECOVERY_QUARANTINE_FILE
    marker.unlink()
    marker.symlink_to(tmp_path / "missing-marker-target")
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("SQLite must not open"))
    with pytest.raises(OctopError) as error:
        SqlitePool(output / "octop.db")
    assert error.value.code == ErrorCode.BACKUP_RECOVERY_QUARANTINED


def test_unsupported_atomic_rename_has_no_fallback(source, tmp_path, monkeypatch):
    import ctypes
    import errno
    from types import SimpleNamespace

    archive, _, _ = source
    output = tmp_path / "unsupported-rename"
    libc = ctypes.CDLL

    def unavailable(*args):
        ctypes.set_errno(errno.ENOSYS)
        return -1

    real = libc(None, use_errno=True)
    monkeypatch.setattr(
        recovery.ctypes,
        "CDLL",
        lambda *a, **kw: SimpleNamespace(fstatfs=real.fstatfs, renameat2=unavailable),
    )
    with pytest.raises(OctopError):
        recover(archive, output)
    assert not output.exists()


@pytest.mark.asyncio
async def test_normal_startup_reuses_checked_pool_with_runtime_mocked(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from octop.config import load_config
    from octop.infra import server
    from octop.infra.auth import captcha

    home = tmp_path / "normal-startup"
    home.mkdir()
    SqlitePool(home / "octop.db").close()
    monkeypatch.setattr(server, "ensure_mobile_capabilities_probed", load_config)
    monkeypatch.setattr(server.OctopServer, "_setup_logging", Mock())
    monkeypatch.setattr(server.OctopServer, "_emit_wizard_password", Mock())
    monkeypatch.setattr(server.OctopServer, "_ensure_jwt_secret", Mock())
    monkeypatch.setattr(captcha, "boot_from_services", Mock())
    for name in ("ExpertCatalog", "SubagentCatalog", "PluginManager"):
        monkeypatch.setattr(server, name, Mock())
    opened = []
    open_database = server.open_database

    def capture(*args):
        pool = open_database(*args)
        opened.append(pool)
        return pool

    async def boot(instance, config):
        assert instance.services.db is opened[0]
        instance.app_runtime = SimpleNamespace(user_manager=SimpleNamespace(count=lambda: 0))

    monkeypatch.setattr(server, "open_database", capture)
    monkeypatch.setattr(server.OctopServer, "_boot_runtime", boot)
    instance = server.OctopServer(home=home)
    try:
        await instance.start()
        assert instance._started and len(opened) == 1
        with instance.services.db.connect() as conn:
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 43
    finally:
        for pool in opened:
            pool.close()


@pytest.mark.parametrize("alias_kind", ["file_symlink", "directory_symlink", "symlink_chain"])
@pytest.mark.parametrize("inject_journal", [False, True])
def test_recovery_aliases_reject_before_journal_replay(
    source, tmp_path, alias_kind, inject_journal
):
    archive, _, _ = source
    image = tmp_path / "alias-image"
    recover(archive, image)
    target = image / "octop.db"
    if alias_kind == "directory_symlink":
        parent = tmp_path / "directory-alias"
        parent.symlink_to(image, target_is_directory=True)
        alias = parent / "octop.db"
    else:
        alias = tmp_path / "file-alias.db"
        destination = target
        if alias_kind == "symlink_chain":
            destination = tmp_path / "intermediate.db"
            destination.symlink_to(target)
        alias.symlink_to(destination)
    journal = Path(str(target) + "-journal")
    if inject_journal:
        hot = hot_journal(archive, tmp_path / "journal-source.db")
        journal.write_bytes(hot.read_bytes())
    before = sha(target)
    before_journal = sha(journal) if inject_journal else None
    with pytest.raises(OctopError) as error:
        SqlitePool(alias)
    assert error.value.code == ErrorCode.BACKUP_RECOVERY_QUARANTINED
    assert sha(target) == before
    if inject_journal:
        assert sha(journal) == before_journal


@pytest.mark.parametrize("opened_name", ["original", "alias"])
def test_hardlinked_image_is_rejected_without_sqlite_io(source, tmp_path, opened_name, monkeypatch):
    archive, _, _ = source
    image = tmp_path / "hardlinked-image"
    recover(archive, image)
    target = image / "octop.db"
    alias = tmp_path / "hardlink.db"
    os.link(target, alias)
    before = sha(target)
    monkeypatch.setattr(
        sqlite3, "connect", lambda *a, **kw: pytest.fail("must reject before SQLite")
    )
    with pytest.raises(OctopError) as error:
        SqlitePool(target if opened_name == "original" else alias)
    assert error.value.code in (ErrorCode.BACKUP_RECOVERY_QUARANTINED, ErrorCode.SQLITE_UNSAFE_PATH)
    assert sha(target) == before


@pytest.mark.parametrize("detached", ["copy", "unlinked_original"])
def test_inode_attribute_blocks_detached_inode_with_hot_journal(source, tmp_path, detached):
    archive, _, _ = source
    image = tmp_path / "detached-image"
    recover(archive, image)
    target = image / "octop.db"
    alias = tmp_path / "detached.db"
    if detached == "copy":
        import shutil

        shutil.copy2(target, alias)
    else:
        os.link(target, alias)
        target.unlink()
    assert alias.stat().st_nlink == 1
    hot = hot_journal(archive, tmp_path / "old-journal.db")
    journal = Path(str(alias) + "-journal")
    journal.write_bytes(hot.read_bytes())
    before, before_journal = sha(alias), sha(journal)
    with pytest.raises(OctopError) as error:
        SqlitePool(alias)
    assert error.value.code == ErrorCode.BACKUP_RECOVERY_QUARANTINED
    assert sha(alias) == before and sha(journal) == before_journal


@pytest.mark.parametrize("replacement", ["file", "directory", "symlink"])
def test_replacement_during_connect_rejected_before_first_query(
    source, tmp_path, monkeypatch, replacement
):
    archive, _, _ = source
    image = tmp_path / "replacement-image"
    recover(archive, image)
    directory = tmp_path / "normal-directory"
    directory.mkdir()
    target = directory / "normal.db"
    with sqlite3.connect(target) as conn:
        conn.execute("CREATE TABLE ordinary(value TEXT)")
    saved = tmp_path / "saved.db"
    target_copy = tmp_path / "replacement-content.db"
    target_copy.write_bytes((image / "octop.db").read_bytes())
    hot = hot_journal(archive, tmp_path / "replacement-journal.db")
    connect = sqlite3.connect
    before = sha(target_copy)
    before_journal = sha(hot)

    def replaced(name, *args, **kwargs):
        # Deterministic replacement after preflight, before SQLite's first SQL.
        if replacement == "directory":
            directory.rename(tmp_path / "saved-directory")
            directory.mkdir()
        else:
            target.rename(saved)
        if replacement == "symlink":
            target.symlink_to(target_copy)
            Path(str(target_copy) + "-journal").write_bytes(hot.read_bytes())
        else:
            target.write_bytes(target_copy.read_bytes())
            Path(str(target) + "-journal").write_bytes(hot.read_bytes())
        return connect(name, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", replaced)
    with pytest.raises(OctopError) as error:
        SqlitePool(target)
    assert error.value.code == ErrorCode.SQLITE_UNSAFE_PATH
    assert sha(target) == before
    journal_target = target_copy if replacement == "symlink" else target
    assert sha(Path(str(journal_target) + "-journal")) == before_journal


@pytest.mark.asyncio
async def test_configured_symlink_image_rejects_before_startup_side_effects(
    source, tmp_path, monkeypatch
):
    from octop.infra import server

    archive, _, _ = source
    image = tmp_path / "configured-alias-image"
    recover(archive, image)
    alias = tmp_path / "configured-alias.db"
    alias.symlink_to(image / "octop.db")
    home = tmp_path / "configured-alias-home"
    home.mkdir()
    (home / "config.json").write_text(
        json.dumps({"database": {"driver": "sqlite", "sqlite_path": str(alias)}})
    )
    monkeypatch.setattr(
        server.OctopServer, "_setup_logging", lambda *a: pytest.fail("startup side effect")
    )
    with pytest.raises(OctopError) as error:
        await server.OctopServer(home=home).start()
    assert error.value.code == ErrorCode.BACKUP_RECOVERY_QUARANTINED


def test_missing_xattr_support_refuses_publication(source, tmp_path, monkeypatch):
    import errno

    archive, _, _ = source
    output = tmp_path / "no-xattr"

    def unsupported(*args, **kwargs):
        raise OSError(errno.ENOTSUP, "synthetic xattr unsupported")

    monkeypatch.setattr(recovery.os, "setxattr", unsupported)
    with pytest.raises(OctopError) as error:
        recover(archive, output)
    assert error.value.code == ErrorCode.BACKUP_RECOVERY_REJECTED
    assert not output.exists()


def test_xattr_boolean_cannot_activate_detached_image(source, tmp_path):
    import shutil

    archive, _, _ = source
    image = tmp_path / "attribute-value"
    recover(archive, image)
    detached = tmp_path / "attribute.db"
    shutil.copy2(image / "octop.db", detached)
    os.setxattr(detached, recovery.RECOVERY_QUARANTINE_XATTR, b"false")
    with pytest.raises(OctopError) as error:
        SqlitePool(detached)
    assert error.value.code == ErrorCode.BACKUP_RECOVERY_QUARANTINED
