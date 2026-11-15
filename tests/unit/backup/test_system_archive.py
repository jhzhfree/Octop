"""Unit tests for system backup archives."""

from __future__ import annotations

import json
import sqlite3
import tarfile
from pathlib import Path

import pytest

from octop.config import DatabaseConfig
from octop.infra.backup.manifest import BackupManifest
from octop.infra.backup.system_archive import create_system_backup
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.utils.paths import PathLayout


@pytest.fixture
def layout(tmp_path: Path) -> PathLayout:
    root = tmp_path / ".octop"
    root.mkdir()
    return PathLayout(root)


def test_manifest_roundtrip_includes_driver_fields() -> None:
    m = BackupManifest(
        manifest_version=1,
        octop_version="0.0.0",
        schema_version=1,
        created_at="t",
        home="/tmp",
        db_file="db/octop.dump",
        database_driver="postgresql",
        database_dump_format="pg_custom",
    )
    loaded = BackupManifest.load_text(m.to_json())
    assert loaded.database_driver == "postgresql"
    assert loaded.database_dump_format == "pg_custom"
    assert loaded.db_file == "db/octop.dump"


def test_legacy_manifest_defaults_plugins_omitted() -> None:
    loaded = BackupManifest.from_dict(
        {
            "manifest_version": 1,
            "octop_version": "0.0.0",
            "schema_version": 1,
            "created_at": "t",
            "home": "/tmp",
        }
    )
    assert loaded.includes_skill_packages is True
    assert loaded.includes_plugins is False
    assert loaded.includes_knowledge is False
    assert loaded.includes_chats is True


def test_backup_contains_selected_files(layout: PathLayout, tmp_path: Path) -> None:
    db_path = layout.db
    pool = SqlitePool(db_path)
    run_migrations(pool)
    with pool.connect() as conn:
        conn.execute(
            "INSERT INTO users(username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
            ("alice", "hash", "admin", 1),
        )

    ws = layout.ensure_agent_workspace("agent01")
    (ws / "SOUL.md").write_text("# soul", encoding="utf-8")
    pkg_skill = layout.skill_packages_dir / "pkg01" / "skills" / "writer" / "SKILL.md"
    pkg_skill.parent.mkdir(parents=True, exist_ok=True)
    pkg_skill.write_text("---\nname: writer\ndescription: x\n---\n", encoding="utf-8")
    plugin_yaml = layout.plugins_dir / "weather" / "plugin.yaml"
    plugin_yaml.parent.mkdir(parents=True, exist_ok=True)
    plugin_yaml.write_text("id: weather\n", encoding="utf-8")
    kb_doc = layout.knowledge_dir / "kb01" / "docs" / "doc1.md"
    kb_doc.parent.mkdir(parents=True, exist_ok=True)
    kb_doc.write_text("# note", encoding="utf-8")
    layout.config.write_text('{"port": 8088}', encoding="utf-8")

    class Row:
        agent_id = "agent01"
        name = "Test"

    archive = tmp_path / "roundtrip.tar.gz"
    create_system_backup(
        paths=layout,
        agent_rows=[Row()],
        pool=pool,
        db_config=DatabaseConfig(),
        dest=archive,
    )
    pool.close()

    with tarfile.open(archive, mode="r:gz") as tf:
        assert tf.extractfile("workspaces/agent01/SOUL.md").read() == b"# soul"
        assert tf.extractfile("plugins/weather/plugin.yaml").read() == b"id: weather\n"
        assert tf.extractfile("knowledge/kb01/docs/doc1.md").read() == b"# note"
        assert (
            tf.extractfile("skill-packages/pkg01/skills/writer/SKILL.md").read().startswith(b"---")
        )
        assert json.loads(tf.extractfile("config/config.json").read()) == {"port": 8088}


def test_backup_packs_config_workspace_dir(layout: PathLayout, tmp_path: Path) -> None:
    db_path = layout.db
    pool = SqlitePool(db_path)
    run_migrations(pool)
    custom = tmp_path / "custom-ws"
    custom.mkdir()
    (custom / "SOUL.md").write_text("# custom", encoding="utf-8")

    class Row:
        agent_id = "agent01"
        name = "Test"
        config_json = json.dumps({"workspace_dir": str(custom)})

    archive = tmp_path / "custom-ws-backup.tar.gz"
    create_system_backup(
        paths=layout,
        agent_rows=[Row()],
        pool=pool,
        db_config=DatabaseConfig(),
        dest=archive,
    )
    pool.close()

    with tarfile.open(archive, mode="r:gz") as tf:
        names = tf.getnames()
    assert "workspaces/agent01/SOUL.md" in names
    assert not (layout.agent_workspace("agent01") / "SOUL.md").exists()


def test_backup_skips_unwritable_workspace_dir(layout: PathLayout, tmp_path: Path) -> None:
    """Stale host paths (e.g. former /root/.octop/agents/…) must not abort backup."""
    pool = SqlitePool(layout.db)
    run_migrations(pool)
    reachable = layout.ensure_agent_workspace("ok01")
    (reachable / "keep.txt").write_text("ok", encoding="utf-8")
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    stale = blocker / "YZQ7X4"

    class OkRow:
        agent_id = "ok01"
        name = "Ok"

    class StaleRow:
        agent_id = "YZQ7X4"
        name = "Stale"
        config_json = json.dumps({"workspace_dir": str(stale)})

    archive = tmp_path / "skip-stale-ws.tar.gz"
    create_system_backup(
        paths=layout,
        agent_rows=[StaleRow(), OkRow()],
        pool=pool,
        db_config=DatabaseConfig(),
        dest=archive,
    )
    pool.close()

    assert archive.is_file()
    assert not stale.exists()
    with tarfile.open(archive, mode="r:gz") as tf:
        names = tf.getnames()
    assert "workspaces/ok01/keep.txt" in names
    assert not any(name.startswith("workspaces/YZQ7X4/") for name in names)


def test_backup_skips_junk_directories(layout: PathLayout, tmp_path: Path) -> None:
    pool = SqlitePool(layout.db)
    run_migrations(pool)
    ws = layout.ensure_agent_workspace("agent01")
    (ws / "keep.txt").write_text("ok", encoding="utf-8")
    (ws / "node_modules").mkdir()
    (ws / "node_modules" / "pkg.js").write_text("skip", encoding="utf-8")
    (ws / ".git").mkdir()
    (ws / ".git" / "config").write_text("skip", encoding="utf-8")

    class Row:
        agent_id = "agent01"
        name = "Test"

    archive = tmp_path / "skip-junk.tar.gz"
    create_system_backup(
        paths=layout,
        agent_rows=[Row()],
        pool=pool,
        db_config=DatabaseConfig(),
        dest=archive,
    )
    pool.close()

    with tarfile.open(archive, mode="r:gz") as tf:
        names = set(tf.getnames())
    assert "workspaces/agent01/keep.txt" in names
    assert "workspaces/agent01/node_modules/pkg.js" not in names
    assert "workspaces/agent01/.git/config" not in names


def test_default_backup_omits_chat_files(
    tmp_path: Path,
) -> None:
    source_layout = PathLayout(tmp_path / "source")
    source_layout.root.mkdir()
    source_pool = SqlitePool(source_layout.db)
    run_migrations(source_pool)
    with source_pool.connect() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, created_at)"
            " VALUES (1, 'alice', 'hash', 'admin', 1)"
        )
        conn.execute(
            "INSERT INTO agents(agent_id, user_id, name, created_at, updated_at)"
            " VALUES ('agent01', 1, 'Agent', 1, 1)"
        )

    source_ws = source_layout.ensure_agent_workspace("agent01")
    (source_ws / "SOUL.md").write_text("# source", encoding="utf-8")
    source_sessions = source_ws / ".octop" / "sessions"
    source_sessions.mkdir(parents=True)
    (source_sessions / "large.jsonl").write_text("chat", encoding="utf-8")
    (source_ws / ".octop" / "checkpoints.sqlite").write_bytes(b"chat-db")
    user_sessions = source_ws / "project" / "sessions"
    user_sessions.mkdir(parents=True)
    (user_sessions / "notes.txt").write_text("keep", encoding="utf-8")

    class Row:
        agent_id = "agent01"
        name = "Agent"
        config_json = '{"system_files_path": ".octop"}'

    archive = tmp_path / "without-chats.tar.gz"
    create_system_backup(
        paths=source_layout,
        agent_rows=[Row()],
        pool=source_pool,
        db_config=DatabaseConfig(),
        dest=archive,
    )
    source_pool.close()

    with tarfile.open(archive, mode="r:gz") as tf:
        names = set(tf.getnames())
        manifest = json.loads(tf.extractfile("manifest.json").read().decode("utf-8"))
    assert manifest["includes_chats"] is False
    assert "workspaces/agent01/SOUL.md" in names
    assert "workspaces/agent01/project/sessions/notes.txt" in names
    assert "workspaces/agent01/.octop/sessions/large.jsonl" not in names
    assert "workspaces/agent01/.octop/checkpoints.sqlite" not in names


def test_sqlite_strip_chats_reclaims_pages(tmp_path: Path) -> None:
    from octop.infra.backup.chats import strip_chat_tables_from_sqlite_file
    from octop.infra.backup.snapshot import snapshot_sqlite_file

    layout = PathLayout(tmp_path / "home")
    layout.root.mkdir()
    pool = SqlitePool(layout.db)
    run_migrations(pool)
    with pool.connect() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, created_at)"
            " VALUES (1, 'alice', 'hash', 'admin', 1)"
        )
        conn.execute(
            "INSERT INTO agents(agent_id, user_id, name, created_at, updated_at)"
            " VALUES ('agent01', 1, 'Agent', 1, 1)"
        )
        conn.execute(
            "INSERT INTO threads(thread_id, agent_id, user_id, channel_type, session_key,"
            " last_active, created_at) VALUES ('t1', 'agent01', 1, 'dashboard', 's', 1, 1)"
        )
        payload = "x" * 2048
        for seq in range(400):
            conn.execute(
                "INSERT INTO thread_messages(thread_id, seq, role, message_json, created_at)"
                " VALUES ('t1', ?, 'user', ?, 1)",
                (seq, json.dumps({"content": payload})),
            )
        conn.execute(
            "INSERT INTO trajectory_events("
            "event_id, agent_id, thread_id, seq, ts, kind, summary, payload_json"
            ") VALUES ('event-large', 'agent01', 't1', 1, 1, 'assistant', 'large', ?)",
            (json.dumps({"content": payload}),),
        )
    snap = tmp_path / "snap.db"
    snapshot_sqlite_file(pool.path, snap)
    pool.close()
    before = snap.stat().st_size
    strip_chat_tables_from_sqlite_file(snap)
    after = snap.stat().st_size
    assert after < before
    conn = sqlite3.connect(snap)
    count = conn.execute("SELECT COUNT(*) FROM thread_messages").fetchone()[0]
    trajectory_count = conn.execute("SELECT COUNT(*) FROM trajectory_events").fetchone()[0]
    conn.close()
    assert count == 0
    assert trajectory_count == 0


def test_backup_can_omit_config_and_workspaces(layout: PathLayout, tmp_path: Path) -> None:
    pool = SqlitePool(layout.db)
    run_migrations(pool)
    layout.config.write_text('{"port": 8088}', encoding="utf-8")
    workspace = layout.ensure_agent_workspace("agent01")
    (workspace / "SOUL.md").write_text("# soul", encoding="utf-8")

    class Row:
        agent_id = "agent01"
        name = "Agent"

    archive = tmp_path / "database-only.tar.gz"
    create_system_backup(
        paths=layout,
        agent_rows=[Row()],
        pool=pool,
        db_config=DatabaseConfig(),
        dest=archive,
        include_config=False,
        include_workspaces=False,
        include_skill_packages=False,
        include_plugins=False,
        include_knowledge=False,
    )
    pool.close()

    with tarfile.open(archive, mode="r:gz") as tf:
        names = set(tf.getnames())
        manifest = json.loads(tf.extractfile("manifest.json").read().decode("utf-8"))
    assert manifest["includes_config"] is False
    assert manifest["includes_skill_packages"] is False
    assert manifest["includes_plugins"] is False
    assert manifest["includes_knowledge"] is False
    assert manifest["agents"][0]["workspace_included"] is False
    assert "config/config.json" not in names
    assert "workspaces/agent01/SOUL.md" not in names


def test_backup_can_include_skill_packages_and_plugins_without_workspaces(
    layout: PathLayout, tmp_path: Path
) -> None:
    pool = SqlitePool(layout.db)
    run_migrations(pool)
    workspace = layout.ensure_agent_workspace("agent01")
    (workspace / "SOUL.md").write_text("# soul", encoding="utf-8")
    pkg = layout.skill_packages_dir / "pkg01" / "skills" / "a" / "SKILL.md"
    pkg.parent.mkdir(parents=True, exist_ok=True)
    pkg.write_text("---\nname: a\ndescription: x\n---\n", encoding="utf-8")
    plugin = layout.plugins_dir / "weather" / "plugin.yaml"
    plugin.parent.mkdir(parents=True, exist_ok=True)
    plugin.write_text("id: weather\n", encoding="utf-8")
    kb_doc = layout.knowledge_dir / "kb01" / "docs" / "doc1.md"
    kb_doc.parent.mkdir(parents=True, exist_ok=True)
    kb_doc.write_text("# note", encoding="utf-8")

    class Row:
        agent_id = "agent01"
        name = "Agent"

    archive = tmp_path / "dirs-only.tar.gz"
    create_system_backup(
        paths=layout,
        agent_rows=[Row()],
        pool=pool,
        db_config=DatabaseConfig(),
        dest=archive,
        include_config=False,
        include_workspaces=False,
        include_skill_packages=True,
        include_plugins=True,
        include_knowledge=True,
    )
    pool.close()

    with tarfile.open(archive, mode="r:gz") as tf:
        names = set(tf.getnames())
        manifest = json.loads(tf.extractfile("manifest.json").read().decode("utf-8"))
    assert manifest["includes_skill_packages"] is True
    assert manifest["includes_plugins"] is True
    assert manifest["includes_knowledge"] is True
    assert manifest["agents"][0]["workspace_included"] is False
    assert "workspaces/agent01/SOUL.md" not in names
    assert "skill-packages/pkg01/skills/a/SKILL.md" in names
    assert "plugins/weather/plugin.yaml" in names
    assert "knowledge/kb01/docs/doc1.md" in names
