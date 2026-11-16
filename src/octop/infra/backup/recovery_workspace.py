"""Inert workspace salvage into a private recovery bundle; never load agent config."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tarfile
from contextlib import closing
from pathlib import Path
from typing import Any

from octop.infra.errors import ErrorCode, OctopError

_MAX_FILE = 128 * 1024 * 1024
_MAX_TOTAL = 512 * 1024 * 1024


def _reject(reason: str) -> OctopError:
    return OctopError.localized(ErrorCode.BACKUP_RECOVERY_REJECTED, details={"reason": reason})


def _parts(name: str) -> list[str]:
    parts = name.split("/")
    if "\\" in name or "\x00" in name or any(p in ("", ".", "..") for p in parts):
        raise _reject("unsafe_workspace_path")
    return parts


def stage_workspaces(
    archive: Path, bundle: Path, *, source_hash: str, database_hash: str
) -> dict[str, Any]:
    """Write bounded regular files and an inventory bound to the prepared database.

    Only called in an unpublished private directory after database validation.
    Archive content is untrusted data, never a config or executable to load.
    """
    database = bundle / "octop.db"
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
        database_agents = {row[0] for row in conn.execute("SELECT agent_id FROM agents")}
    directories: set[Path] = set()
    files: list[dict[str, Any]] = []
    with tarfile.open(archive, "r:*") as tf:
        stream = tf.extractfile("manifest.json")
        if stream is None:
            raise _reject("missing_manifest")
        with stream:
            raw = json.load(stream)
        entries = raw.get("agents", [])
        if not isinstance(entries, list):
            raise _reject("invalid_workspace_manifest")
        agents: dict[str, bool] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise _reject("invalid_workspace_manifest")
            agent_id = entry.get("agent_id")
            included = entry.get("workspace_included", True)
            if (
                not isinstance(agent_id, str)
                or len(_parts(agent_id)) != 1
                or type(included) is not bool
                or agent_id in agents
            ):
                raise _reject("invalid_workspace_manifest")
            agents[agent_id] = included
        if set(agents) != database_agents:
            raise _reject("workspace_database_agent_mismatch")
        seen: set[str] = set()
        total = 0
        for member in tf:
            if not member.name.startswith("workspaces/"):
                continue
            parts = _parts(member.name)
            if len(parts) < 3 or parts[1] not in agents or not agents[parts[1]]:
                raise _reject("workspace_not_declared")
            if member.name in seen or not member.isfile() or member.sparse is not None:
                raise _reject("duplicate_or_nonregular_workspace")
            seen.add(member.name)
            total += member.size
            if member.size < 0 or member.size > _MAX_FILE or total > _MAX_TOTAL:
                raise _reject("workspace_size_limit")
            dest = bundle.joinpath(*parts)
            # Every parent belongs to our new private tree, with no archive links.
            for parent in reversed(dest.parents):
                if parent == bundle or bundle not in parent.parents:
                    continue
                parent.mkdir(mode=0o700, exist_ok=True)
                directories.add(parent)
            stream = tf.extractfile(member)
            if stream is None:
                raise _reject("missing_workspace_data")
            digest = hashlib.sha256()
            written = 0
            fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with stream, os.fdopen(fd, "wb") as output:
                while data := stream.read(1024 * 1024):
                    written += len(data)
                    if written > member.size:
                        raise _reject("workspace_size_mismatch")
                    output.write(data)
                    digest.update(data)
                if written != member.size:
                    raise _reject("workspace_size_mismatch")
                output.flush()
                os.fsync(output.fileno())
            files.append({"path": member.name, "size": written, "sha256": digest.hexdigest()})
    counts = dict.fromkeys(agents, 0)
    for entry in files:
        counts[entry["path"].split("/")[1]] += 1
    report = {
        "format": 1,
        "source_sha256": source_hash,
        "database_sha256": database_hash,
        "application_start_allowed": False,
        "point_in_time_consistency_verified": False,
        "completeness_verified": False,
        "agents": [
            {
                "agent_id": agent_id,
                "status": "files_recovered"
                if counts[agent_id]
                else ("not_observed" if agents[agent_id] else "omitted_by_manifest"),
                "files": counts[agent_id],
            }
            for agent_id in sorted(agents)
        ],
        "files": sorted(files, key=lambda entry: entry["path"]),
        "total_bytes": total,
    }
    data = (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    path = bundle / "workspace-recovery.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return {
        "workspace_report_sha256": hashlib.sha256(data).hexdigest(),
        "workspace_files": len(files),
    }
