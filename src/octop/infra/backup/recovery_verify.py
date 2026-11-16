"""Read-only, externally anchored inventory verification. Never open SQLite or activate."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

from octop.infra.backup.recovery_workspace import _parts, _reject
from octop.infra.db.sqlite_path import RECOVERY_QUARANTINE_FILE, RECOVERY_QUARANTINE_XATTR
from octop.infra.errors import OctopError

_REPORT = "workspace-recovery.json"
_MARKER = b"Quarantined recovery image. Application activation is unavailable.\n"


def _sha(value: object) -> bool:
    return (
        isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    )


def _stamp(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _private(info: os.stat_result, *, directory: bool) -> None:
    correct = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not correct or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise _reject("unsafe_inventory_entry")
    if not directory and info.st_nlink != 1:
        raise _reject("linked_inventory_file")


def _read(fd: int, limit: int, *, capture: bool = False) -> tuple[str, bytes]:
    before = os.fstat(fd)
    _private(before, directory=False)
    if before.st_size > limit:
        raise _reject("inventory_size_limit")
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    total = 0
    while data := os.read(fd, 1024 * 1024):
        total += len(data)
        if total > limit:
            raise _reject("inventory_size_limit")
        digest.update(data)
        if capture:
            chunks.append(data)
    if total != before.st_size or _stamp(before) != _stamp(os.fstat(fd)):
        raise _reject("inventory_changed")
    return digest.hexdigest(), b"".join(chunks)


def _inventory(report: Any, report_hash: str) -> tuple[dict[str, Any], int]:
    if not isinstance(report, dict) or report.get("format") != 1:
        raise _reject("unsupported_inventory")
    for flag in (
        "application_start_allowed",
        "point_in_time_consistency_verified",
        "completeness_verified",
    ):
        if report.get(flag) is not False:
            raise _reject("unsupported_inventory_claim")
    if not _sha(report.get("source_sha256")) or not _sha(report.get("database_sha256")):
        raise _reject("invalid_inventory_digest")
    tree: dict[str, Any] = {
        "octop.db": (report["database_sha256"], 512 * 1024 * 1024, None),
        _REPORT: (report_hash, 8 * 1024 * 1024, None),
        RECOVERY_QUARANTINE_FILE: (hashlib.sha256(_MARKER).hexdigest(), len(_MARKER), len(_MARKER)),
    }
    files = report.get("files")
    if not isinstance(files, list) or len(files) > 10000:
        raise _reject("invalid_inventory_files")
    total = 0
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise _reject("invalid_inventory_file")
        parts = _parts(item["path"])
        size = item.get("size")
        if (
            len(parts) < 3
            or parts[0] != "workspaces"
            or type(size) is not int
            or not 0 <= size <= 128 * 1024 * 1024
            or not _sha(item.get("sha256"))
        ):
            raise _reject("invalid_inventory_file")
        total += size
        node = tree
        for part in parts[:-1]:
            child = node.setdefault(part, {})
            if not isinstance(child, dict):
                raise _reject("inventory_path_conflict")
            node = child
        if parts[-1] in node:
            raise _reject("duplicate_inventory_path")
        node[parts[-1]] = (item["sha256"], size, size)
    if (
        total > 512 * 1024 * 1024
        or type(report.get("total_bytes")) is not int
        or report["total_bytes"] != total
    ):
        raise _reject("inventory_total_mismatch")
    return tree, len(files)


def _walk(
    fd: int, tree: dict[str, Any], *, hash_files: bool, prefix: str = ""
) -> dict[str, tuple[int, ...]]:
    before = os.fstat(fd)
    _private(before, directory=True)
    if set(os.listdir(fd)) != set(tree):
        raise _reject("inventory_members_mismatch")
    stamps = {prefix: _stamp(before)}
    for name, expected in sorted(tree.items()):
        directory = isinstance(expected, dict)
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        if directory:
            flags |= os.O_DIRECTORY
        child_fd = os.open(name, flags, dir_fd=fd)
        try:
            info = os.fstat(child_fd)
            _private(info, directory=directory)
            path = f"{prefix}/{name}"
            if directory:
                stamps.update(_walk(child_fd, expected, hash_files=hash_files, prefix=path))
            else:
                digest, limit, size = expected
                if size is not None and info.st_size != size:
                    raise _reject("inventory_file_size_mismatch")
                if path == "/octop.db" and os.getxattr(child_fd, RECOVERY_QUARANTINE_XATTR) != b"3":
                    raise _reject("inventory_quarantine_missing")
                if hash_files and _read(child_fd, limit)[0] != digest:
                    raise _reject("inventory_hash_mismatch")
                stamps[path] = _stamp(info)
            if _stamp(os.stat(name, dir_fd=fd, follow_symlinks=False)) != _stamp(info):
                raise _reject("inventory_path_changed")
        finally:
            os.close(child_fd)
    if _stamp(os.fstat(fd)) != _stamp(before):
        raise _reject("inventory_directory_changed")
    return stamps


def verify_recovery_image(image: Path, *, expected_report_sha256: str) -> dict[str, Any]:
    """Observe byte matches to a separately retained report hash, never snapshot safety.

    Two metadata passes detect ordinary concurrent edits but are not a filesystem
    snapshot or protection against an adversarial same-UID writer.
    """
    if sys.platform != "linux" or not _sha(expected_report_sha256):
        raise _reject("platform_or_inventory_anchor_invalid")
    fd: int | None = None
    try:
        fd = os.open(image, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        root = os.fstat(fd)
        _private(root, directory=True)
        report_fd = os.open(_REPORT, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            digest, data = _read(report_fd, 8 * 1024 * 1024, capture=True)
        finally:
            os.close(report_fd)
        if digest != expected_report_sha256:
            raise _reject("inventory_anchor_mismatch")
        report = json.loads(data)
        tree, count = _inventory(report, digest)
        observed = _walk(fd, tree, hash_files=True)
        if observed != _walk(fd, tree, hash_files=False):
            raise _reject("inventory_changed")
        if _stamp(image.lstat()) != _stamp(root):
            raise _reject("inventory_root_changed")
        return {
            "inventory_match_observed": True,
            "workspace_files": count,
            "source_sha256": report["source_sha256"],
            "report_sha256": digest,
            "application_start_allowed": False,
            "point_in_time_consistency_verified": False,
            "completeness_verified": False,
            "activation_blockers": [
                "source_snapshot_not_proven",
                "current_identity_mapping_missing",
                "deployment_cutover_not_proven",
            ],
        }
    except OctopError:
        raise
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        raise _reject("inventory_unavailable_or_changed") from exc
    finally:
        if fd is not None:
            os.close(fd)
