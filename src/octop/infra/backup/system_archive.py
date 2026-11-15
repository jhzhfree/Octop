"""Full-system backup and restore (database + local agent workspaces + config)."""

from __future__ import annotations

import json
import logging
import shutil
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from octop import __version__
from octop.config import DatabaseConfig
from octop.infra.agents.workspace.dir import (
    system_files_path_from_config,
    workspace_dir_from_config_json,
)
from octop.infra.backup.chats import (
    CHAT_TABLES_CHILD_FIRST,
    is_chat_workspace_rel,
    strip_chat_tables_from_sqlite_file,
)
from octop.infra.backup.manifest import MANIFEST_VERSION, AgentBackupEntry, BackupManifest
from octop.infra.backup.pg_dump import dump_postgres
from octop.infra.backup.snapshot import snapshot_sqlite_file
from octop.infra.db.migrate import _current_version
from octop.infra.db.pool import DatabasePool, SqlitePool
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.env_file import env_file_path
from octop.infra.utils.paths import PathLayout

logger = logging.getLogger(__name__)

_CONFIG_DIR = "config"
_DB_DIR = "db"
_WORKSPACES_DIR = "workspaces"
_SKILL_PACKAGES_DIR = "skill-packages"
_PLUGINS_DIR = "plugins"
_KNOWLEDGE_DIR = "knowledge"
_MANIFEST_NAME = "manifest.json"
_SQLITE_DB_ARC = f"{_DB_DIR}/octop.db"
_PG_DUMP_ARC = f"{_DB_DIR}/octop.dump"

# Align with workspace zip export; keep backups smaller / faster.
_SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".next",
        ".turbo",
        "dist",
        "build",
    }
)


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def suggested_backup_filename() -> str:
    """Canonical basename for a newly created manual backup archive."""
    return f"octop-backup-{_timestamp()}.tar.gz"


def _should_skip_path(
    rel: Path,
    *,
    skip_chats: bool,
    system_files_path: str = "",
) -> bool:
    if any(part in _SKIP_DIR_NAMES for part in rel.parts):
        return True
    return skip_chats and is_chat_workspace_rel(
        rel,
        system_files_path=system_files_path,
    )


def _add_dir(
    tf: tarfile.TarFile,
    src: Path,
    arc_root: str,
    *,
    skip_chats: bool,
    system_files_path: str = "",
) -> None:
    if not src.is_dir():
        return
    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(src)
        if _should_skip_path(
            rel,
            skip_chats=skip_chats,
            system_files_path=system_files_path,
        ):
            continue
        tf.add(path, arcname=f"{arc_root}/{rel.as_posix()}")


def _system_files_path_from_row(row: Any) -> str:
    raw = getattr(row, "config_json", None)
    if not isinstance(raw, str) or not raw.strip():
        return ""
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""
    return system_files_path_from_config(parsed if isinstance(parsed, dict) else None)


def _build_manifest(
    *,
    paths: PathLayout,
    agent_rows: list[Any],
    pool: DatabasePool,
    db_arc: str,
    database_driver: str,
    database_dump_format: str,
    env_path: Path,
    include_config: bool,
    include_workspaces: bool,
    include_skill_packages: bool,
    include_plugins: bool,
    include_knowledge: bool,
    include_chats: bool,
) -> BackupManifest:
    try:
        schema_version = _current_version(pool)
    except Exception:
        schema_version = 0
    agents = [
        AgentBackupEntry(
            agent_id=str(row.agent_id),
            name=str(row.name),
            workspace_included=include_workspaces,
        )
        for row in agent_rows
    ]
    return BackupManifest(
        manifest_version=MANIFEST_VERSION,
        octop_version=__version__,
        schema_version=schema_version,
        created_at=datetime.now(UTC).isoformat(),
        home=str(paths.root),
        db_file=db_arc,
        database_driver=database_driver,
        database_dump_format=database_dump_format,
        agents=agents,
        includes_config=include_config and paths.config.is_file(),
        includes_env=include_config and env_path.is_file(),
        includes_skill_packages=include_skill_packages,
        includes_plugins=include_plugins,
        includes_knowledge=include_knowledge,
        includes_chats=include_chats,
    )


def create_system_backup(
    *,
    paths: PathLayout,
    agent_rows: list[Any],
    pool: DatabasePool,
    db_config: DatabaseConfig,
    dest: Path,
    include_config: bool = True,
    include_workspaces: bool = True,
    include_skill_packages: bool = True,
    include_plugins: bool = True,
    include_knowledge: bool = True,
    include_chats: bool = False,
) -> str:
    """Write a ``.tar.gz`` archive to *dest* (streamed to disk).

    Configuration, agent workspaces, skill packages, plugins, and knowledge
    files can be omitted independently. ``include_chats`` defaults to False
    because thread/session history usually dominates archive size. Restore
    keeps live chats and omitted directories when the manifest says they were
    not packed.

    Returns the suggested basename (``octop-backup-….tar.gz``). Callers that
    need a different name should rename/move *dest* afterward.
    """
    if pool.dialect == "postgresql":
        db_arc = _PG_DUMP_ARC
        database_driver = "postgresql"
        database_dump_format = "pg_custom"
    else:
        db_arc = _SQLITE_DB_ARC
        database_driver = "sqlite"
        database_dump_format = "sqlite_file"
        if not isinstance(pool, SqlitePool):
            raise OctopError(ErrorCode.INTERNAL_ERROR, "sqlite backup requires SqlitePool")
        if not pool.path.is_file():
            raise OctopError(ErrorCode.NOT_FOUND, f"database not found: {pool.path}")

    env_path = env_file_path(paths.root)
    manifest = _build_manifest(
        paths=paths,
        agent_rows=agent_rows,
        pool=pool,
        db_arc=db_arc,
        database_driver=database_driver,
        database_dump_format=database_dump_format,
        env_path=env_path,
        include_config=include_config,
        include_workspaces=include_workspaces,
        include_skill_packages=include_skill_packages,
        include_plugins=include_plugins,
        include_knowledge=include_knowledge,
        include_chats=include_chats,
    )
    filename = suggested_backup_filename()
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".partial")

    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_dest = root / db_arc
            history_path = paths.root / "history_v2.sqlite"
            history_dest = root / "history_v2.sqlite"
            marker = history_path.with_suffix(".required")
            if include_chats and marker.exists() and not history_path.exists():
                raise FileNotFoundError("Required history archive is missing")
            if include_chats and history_path.exists():
                # Snapshot the child archive before the main thread registry.
                # A fully consistent multi-file restore still requires quiescence.
                snapshot_sqlite_file(history_path, history_dest)
            if pool.dialect == "postgresql":
                dump_postgres(
                    db_config.postgresql_conninfo(),
                    db_dest,
                    exclude_table_data=() if include_chats else CHAT_TABLES_CHILD_FIRST,
                )
            else:
                assert isinstance(pool, SqlitePool)
                snapshot_sqlite_file(pool.path, db_dest)
                if not include_chats:
                    strip_chat_tables_from_sqlite_file(db_dest)

            manifest_path = root / _MANIFEST_NAME
            manifest_path.write_text(manifest.to_json(), encoding="utf-8")

            if include_config and paths.config.is_file():
                cfg_dir = root / _CONFIG_DIR
                cfg_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(paths.config, cfg_dir / "config.json")
            if include_config and env_path.is_file():
                cfg_dir = root / _CONFIG_DIR
                cfg_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(env_path, cfg_dir / "env")

            with tarfile.open(partial, mode="w:gz") as tf:
                tf.add(manifest_path, arcname=_MANIFEST_NAME)
                tf.add(db_dest, arcname=db_arc)
                if history_dest.exists():
                    tf.add(history_dest, arcname="history/history_v2.sqlite")
                    if marker.exists():
                        tf.add(marker, arcname="history/history_v2.required")
                if include_config and paths.config.is_file():
                    tf.add(
                        root / _CONFIG_DIR / "config.json",
                        arcname=f"{_CONFIG_DIR}/config.json",
                    )
                if include_config and env_path.is_file():
                    tf.add(root / _CONFIG_DIR / "env", arcname=f"{_CONFIG_DIR}/env")
                if include_workspaces:
                    for row in agent_rows:
                        agent_id = str(row.agent_id)
                        try:
                            ws = workspace_dir_from_config_json(
                                getattr(row, "config_json", None),
                                paths=paths,
                                agent_id=agent_id,
                                ensure=False,
                            )
                            if not ws.is_dir():
                                continue
                            _add_dir(
                                tf,
                                ws,
                                f"{_WORKSPACES_DIR}/{agent_id}",
                                skip_chats=not include_chats,
                                system_files_path=_system_files_path_from_row(row),
                            )
                        except OSError:
                            logger.warning(
                                "skipping workspace for agent %s",
                                agent_id,
                                exc_info=True,
                            )
                if include_skill_packages and paths.skill_packages_dir.is_dir():
                    _add_dir(
                        tf,
                        paths.skill_packages_dir,
                        _SKILL_PACKAGES_DIR,
                        skip_chats=False,
                    )
                if include_plugins and paths.plugins_dir.is_dir():
                    _add_dir(
                        tf,
                        paths.plugins_dir,
                        _PLUGINS_DIR,
                        skip_chats=False,
                    )
                if include_knowledge and paths.knowledge_dir.is_dir():
                    _add_dir(
                        tf,
                        paths.knowledge_dir,
                        _KNOWLEDGE_DIR,
                        skip_chats=False,
                    )

        partial.replace(dest)
    except Exception:
        partial.unlink(missing_ok=True)
        raise

    return filename


def restore_system_backup(
    source: Path | bytes,
    *,
    paths: PathLayout,
    pool: DatabasePool,
    db_config: DatabaseConfig,
    restore_config: bool = True,
    preserve_users: bool | None = None,
    owner_user_id: int | None = None,
) -> dict[str, Any]:
    """Compatibility entry point: refuse until coordinated recovery is implemented.

    No source, destination, credentials or filesystem content is inspected.
    Legacy preserve_users/owner/restore_config arguments are not maintenance
    proof. No force flag can opt into the retired non-atomic pipeline.
    """
    raise OctopError.localized(ErrorCode.BACKUP_RESTORE_UNAVAILABLE)
