"""Create-only, quarantined SQLite recovery images. Never restore a live database."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sqlite3
import stat
import sys
import tarfile
import tempfile
from contextlib import closing
from pathlib import Path
from typing import IO, Any

from octop.infra.backup.manifest import MANIFEST_VERSION, BackupManifest
from octop.infra.backup.recovery_workspace import stage_workspaces
from octop.infra.db.migrate import _max_discovered_version, run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.sqlite_path import RECOVERY_QUARANTINE_FILE, RECOVERY_QUARANTINE_XATTR
from octop.infra.errors import ErrorCode, OctopError

_MAX_ARCHIVE = 512 * 1024 * 1024
_MAX_DATABASE = 512 * 1024 * 1024
_MAX_MANIFEST = 64 * 1024
_MAX_MEMBERS = 10000
_MARKER = "octop_recovery_quarantine"


def _reject(reason: str) -> OctopError:
    return OctopError.localized(ErrorCode.BACKUP_RECOVERY_REJECTED, details={"reason": reason})


def _copy_bounded(source: IO[bytes], dest: IO[bytes], limit: int) -> str:
    digest = hashlib.sha256()
    total = 0
    while data := source.read(1024 * 1024):
        total += len(data)
        if total > limit:
            raise _reject("size_limit")
        dest.write(data)
        digest.update(data)
    return digest.hexdigest()


def _snapshot_archive(source: Path, dest: Path) -> str:
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_ARCHIVE:
            raise _reject("archive_not_regular_or_too_large")
        with dest.open("xb") as output:
            digest = _copy_bounded(stream, output, _MAX_ARCHIVE)
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise _reject("archive_changed")
    return digest


def _extract_database(archive: Path, dest: Path) -> BackupManifest:
    # Only two exact regular members are read. Never extract paths from a tar.
    selected: dict[str, tarfile.TarInfo] = {}
    with tarfile.open(archive, "r:*") as tf:
        expanded = 0
        for count, member in enumerate(tf, 1):
            expanded += member.size
            if expanded > 2 * 1024 * 1024 * 1024:
                raise _reject("expanded_size_limit")
            if member.name in {"db/octop.db-wal", "db/octop.db-shm", "db/octop.db-journal"}:
                raise _reject("archive_sidecar_not_supported")
            if count > _MAX_MEMBERS:
                raise _reject("member_limit")
            if member.name.startswith("history/"):
                raise _reject("multi_database_history_not_supported")
            if member.name not in ("manifest.json", "db/octop.db"):
                continue
            if member.name in selected or not member.isfile() or member.sparse is not None:
                raise _reject("duplicate_or_nonregular_member")
            selected[member.name] = member
        if set(selected) != {"manifest.json", "db/octop.db"}:
            raise _reject("missing_manifest_or_database")
        if selected["manifest.json"].size > _MAX_MANIFEST:
            raise _reject("manifest_size_limit")
        manifest_stream = tf.extractfile(selected["manifest.json"])
        if manifest_stream is None:
            raise _reject("missing_manifest")
        with manifest_stream:
            manifest = BackupManifest.load_text(manifest_stream.read(_MAX_MANIFEST + 1).decode())
        if (
            manifest.manifest_version != MANIFEST_VERSION
            or manifest.database_driver != "sqlite"
            or manifest.database_dump_format != "sqlite_file"
            or manifest.db_file != "db/octop.db"
            or manifest.octop_version.endswith("-migrated-from-lightclaw")
        ):
            raise _reject("unsupported_format_or_migration")
        if not 1 <= manifest.schema_version <= _max_discovered_version("sqlite"):
            raise _reject("unsupported_schema")
        if selected["db/octop.db"].size > _MAX_DATABASE:
            raise _reject("database_size_limit")
        db_stream = tf.extractfile(selected["db/octop.db"])
        if db_stream is None:
            raise _reject("missing_database")
        with db_stream, dest.open("xb") as output:
            _copy_bounded(db_stream, output, _MAX_DATABASE)
    return manifest


def _check_database(conn: sqlite3.Connection) -> None:
    if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise _reject("integrity_check")
    if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise _reject("foreign_key_check")


def _prepare_database(path: Path, manifest: BackupManifest, source_hash: str) -> dict[str, Any]:
    with closing(sqlite3.connect(path)) as check:
        check.execute("PRAGMA trusted_schema=OFF")
        _check_database(check)
        versions = check.execute("SELECT version FROM _schema_version").fetchall()
        if versions != [(manifest.schema_version,)]:
            raise _reject("schema_watermark_mismatch")
        if check.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view') OR name=?", (_MARKER,)
        ).fetchone():
            raise _reject("unsupported_schema_objects")
    pool = SqlitePool(path)
    try:
        run_migrations(pool)
        with pool.transaction() as conn:
            # Preserve data ownership/ACL rows for review, but grant no account access.
            conn.execute(
                "UPDATE users SET disabled=1,password_hash=NULL,permissions='[]',role='user'"
            )
            conn.execute("UPDATE mattermost_brokers SET enabled=0")
            conn.execute("DELETE FROM secrets WHERE k='jwt'")
            conn.execute(
                "INSERT INTO secrets(k,v,created_at) VALUES('jwt',?,strftime('%s','now'))",
                (os.urandom(32),),
            )
            metadata = {
                "format": 3,
                "identity_policy": "quarantine-all",
                "source_sha256": source_hash,
                "schema_version": int(
                    conn.execute("SELECT version FROM _schema_version").fetchone()[0]
                ),
                "users_quarantined": int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]),
                "application_start_allowed": False,
            }
            conn.execute(f"CREATE TABLE {_MARKER}(metadata_json TEXT NOT NULL)")
            conn.execute(
                f"INSERT INTO {_MARKER} VALUES(?)", (json.dumps(metadata, sort_keys=True),)
            )
    finally:
        pool.close()
    # Publish a standalone database: checkpoint/close are private to this staging file.
    with closing(sqlite3.connect(path)) as check:
        if check.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
            raise _reject("standalone_database_required")
        _check_database(check)
        if check.execute("SELECT 1 FROM mattermost_brokers WHERE enabled != 0").fetchone():
            raise _reject("broker_quarantine_incomplete")
        if check.execute(
            "SELECT 1 FROM users WHERE disabled != 1 OR password_hash IS NOT NULL OR permissions != '[]' OR role != 'user'"
        ).fetchone():
            raise _reject("identity_quarantine_failed")
    # Required inode-bound marker survives file/directory aliases and hard links.
    # Unsupported filesystems must fail before publication, without a fallback.
    os.setxattr(path, RECOVERY_QUARANTINE_XATTR, b"3", follow_symlinks=False)
    if os.getxattr(path, RECOVERY_QUARANTINE_XATTR, follow_symlinks=False) != b"3":
        raise _reject("quarantine_attribute_failed")
    os.chmod(path, 0o600)
    return metadata


def _require_local_filesystem(fd: int) -> None:
    # fstatfs is read-only. Reject unknown/network/FUSE filesystems, not merely a
    # caller's claim that a directory is local. Linux's struct fits this buffer.
    libc = ctypes.CDLL(None, use_errno=True)
    query = libc.fstatfs
    query.argtypes = [ctypes.c_int, ctypes.c_void_p]
    query.restype = ctypes.c_int
    buffer = ctypes.create_string_buffer(256)
    if query(fd, buffer) != 0:
        raise _reject("filesystem_probe_failed")
    kind = ctypes.c_long.from_buffer(buffer).value & 0xFFFFFFFF
    if kind not in {0xEF53, 0x58465342, 0x9123683E, 0x794C7630}:
        raise _reject("filesystem_not_supported")


def _sync_directory(fd: int) -> None:
    os.fsync(fd)


def _publish(prepared: Path, name: str, directory_fd: int) -> None:
    # Publish the entire private SQLite namespace, never a main file into a
    # directory where an old hot journal/WAL could rewrite it on first open.
    # No check-then-rename fallback: even an empty competing directory must win.
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(prepared), directory_fd, os.fsencode(name), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def create_recovery_image(
    source: Path,
    output: Path,
    *,
    identity_policy: str,
    expected_sha256: str,
    include_workspaces: bool = False,
) -> dict[str, Any]:
    """Build a new quarantined image in an existing private local POSIX directory.

    The directory lock coordinates builders only. No lock claims to stop an
    existing deployment: existing targets are never opened/replaced. Publication
    is one RENAME_NOREPLACE of a private directory containing a closed, fsynced
    standalone database. Output is a directory, not a SQLite filename.
    """
    if sys.platform != "linux" or not hasattr(os, "O_NOFOLLOW"):
        raise _reject("platform_not_supported")
    if identity_policy != "quarantine-all":
        raise _reject("explicit_quarantine_policy_required")
    if len(expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in expected_sha256):
        raise _reject("archive_checksum_required")
    import fcntl

    try:
        parent = output.absolute().parent.resolve(strict=True)
    except OSError as exc:
        raise _reject("output_directory_missing") from exc
    name = output.name
    if name in ("", ".", "..", ".octop-recovery.lock"):
        raise _reject("invalid_output_name")
    try:
        directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise _reject("output_directory_unavailable") from exc
    lock_fd: int | None = None
    published = False
    publication_attempted = False
    prepared_identity: tuple[int, int] | None = None
    try:
        _require_local_filesystem(directory_fd)
        directory = os.fstat(directory_fd)
        if directory.st_uid != os.geteuid() or directory.st_mode & 0o022:
            raise _reject("private_output_directory_required")
        try:
            os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise _reject("output_exists")
        lock_fd = os.open(
            ".octop-recovery.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        lock_stat = os.fstat(lock_fd)
        if (
            not stat.S_ISREG(lock_stat.st_mode)
            or lock_stat.st_nlink != 1
            or lock_stat.st_uid != os.geteuid()
        ):
            raise _reject("invalid_lock_file")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise _reject("recovery_builder_busy") from exc
        with tempfile.TemporaryDirectory(
            prefix=".octop-recovery-", dir=f"/proc/self/fd/{directory_fd}"
        ) as temporary:
            staging = Path(temporary)
            archive = staging / "input.tar.gz"
            source_hash = _snapshot_archive(source, archive)
            if source_hash != expected_sha256:
                raise _reject("archive_checksum_mismatch")
            bundle = staging / "image"
            bundle.mkdir(mode=0o700)
            prepared = bundle / "octop.db"
            manifest = _extract_database(archive, prepared)
            result = _prepare_database(prepared, manifest, source_hash)
            if any(
                Path(str(prepared) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")
            ):
                raise _reject("unexpected_sidecar")
            with prepared.open("rb") as stream:
                image_hash = hashlib.file_digest(stream, "sha256").hexdigest()
                os.fsync(stream.fileno())
            if include_workspaces:
                result.update(
                    stage_workspaces(
                        archive, bundle, source_hash=source_hash, database_hash=image_hash
                    )
                )
            marker_fd = os.open(
                bundle / RECOVERY_QUARANTINE_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            try:
                os.write(
                    marker_fd,
                    b"Quarantined recovery image. Application activation is unavailable.\n",
                )
                os.fsync(marker_fd)
            finally:
                os.close(marker_fd)
            bundle_fd = os.open(bundle, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(bundle_fd)
            finally:
                os.close(bundle_fd)
            # Confirm directory support for durability before making a target visible.
            _sync_directory(directory_fd)
            current = parent.stat()
            if (current.st_dev, current.st_ino) != (directory.st_dev, directory.st_ino):
                raise _reject("output_directory_changed")
            prepared_stat = bundle.stat()
            prepared_identity = (prepared_stat.st_dev, prepared_stat.st_ino)
            publication_attempted = True
            _publish(bundle, name, directory_fd)
            published = True
            _sync_directory(directory_fd)
            return result | {
                "output": str(parent / name),
                "database": str(parent / name / "octop.db"),
                "image_sha256": image_hash,
            }
    except Exception as exc:
        if publication_attempted and not published:
            try:
                target = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                published = (target.st_dev, target.st_ino) == prepared_identity
            except FileNotFoundError:
                pass
            except OSError:
                published = True  # Cannot safely establish publication outcome.
        if published:
            # Do not remove or overwrite a possibly committed, valid quarantined image.
            raise OctopError.localized(ErrorCode.BACKUP_RECOVERY_COMMIT_UNCERTAIN) from exc
        if isinstance(exc, OctopError):
            raise
        raise _reject("preparation_or_publication_failed") from exc
    finally:
        if lock_fd is not None:
            os.close(lock_fd)  # Keep the inode: unlink would split future lock ownership.
        os.close(directory_fd)
