"""Damaged SQLite guards fail before repairs or service initialization."""

from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock

import pytest

from octop.config import OctopConfig
from octop.infra.db import migrate
from octop.infra.db.pool import SqlitePool


@pytest.fixture
def guarded(tmp_path):
    db = SqlitePool(tmp_path / "guarded.db")
    migrate.run_migrations(db)
    yield db
    db.close()


@pytest.mark.parametrize(
    "kind",
    [
        "missing_one",
        "missing_two",
        "missing_all",
        "modified",
        "wrong_table",
        "wrong_type",
        "lower_watermark",
    ],
)
def test_invalid_guards_fail_before_any_repairs_and_are_not_rebuilt(guarded, monkeypatch, kind):
    db = guarded
    with db.connect() as conn:
        conn.execute("DROP TRIGGER octop_schema_floor_update")
        if kind in {"missing_two", "missing_all"}:
            conn.execute("DROP TRIGGER mattermost_retired_broker_no_enable")
        if kind == "missing_all":
            conn.execute("DROP TRIGGER octop_schema_floor_insert")
            conn.execute("DROP TRIGGER octop_schema_floor_delete")
        if kind == "modified":
            conn.execute(
                "CREATE TRIGGER octop_schema_floor_update BEFORE UPDATE ON _schema_version WHEN NEW.version<48 BEGIN SELECT RAISE(ABORT,'SCHEMA_DOWNGRADE_BLOCKED'); END"
            )
        if kind == "wrong_table":
            conn.execute(
                "CREATE TRIGGER octop_schema_floor_update BEFORE UPDATE ON users BEGIN SELECT 1; END"
            )
        if kind == "wrong_type":
            conn.execute("CREATE VIEW octop_schema_floor_update AS SELECT 1")
        if kind == "lower_watermark":
            conn.execute("UPDATE _schema_version SET version=47")
        before = conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name"
        ).fetchall()
        version = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
    monkeypatch.setattr(
        migrate, "_repair_legacy_schema", lambda *_: pytest.fail("repair must not run")
    )
    with pytest.raises(RuntimeError, match="DATABASE_SCHEMA_GUARDS_INVALID"):
        migrate.run_migrations(db)
    with db.connect() as conn:
        assert (
            conn.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name"
            ).fetchall()
            == before
        )
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == version
        assert not conn.in_transaction


def test_complete_guards_allow_repeat_startup(guarded):
    migrate.run_migrations(guarded)
    with guarded.connect() as conn:
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[
            0
        ] == migrate.schema_baseline_version("sqlite")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["start", "bind_control_plane"])
async def test_service_paths_fail_before_plugins_or_runtime(guarded, tmp_path, monkeypatch, path):
    import octop.infra.server as server_module
    from octop.infra.server import OctopServer

    with guarded.connect() as conn:
        conn.execute("DROP TRIGGER octop_schema_floor_update")
        conn.execute("DROP TRIGGER mattermost_retired_broker_no_enable")
    with guarded.connect() as conn:
        before = conn.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()
    # The server closes a failed pool; use a separate real connection to assert
    # both its cleanup and the untouched original database afterwards.
    opened = SqlitePool(guarded.path)
    server = OctopServer(home=tmp_path / "synthetic-home")
    monkeypatch.setattr(server_module, "load_config", lambda *_: OctopConfig())
    monkeypatch.setattr(server_module, "open_database", lambda *_: opened)
    monkeypatch.setattr(server_module, "should_defer_control_plane_db", lambda *_: False)
    monkeypatch.setattr("octop.infra.utils.env_file.apply_env_file", lambda *_: None)

    def forbidden(*args, **kwargs):
        pytest.fail("probes, plugins, shared services and runtime must not start")

    monkeypatch.setattr("octop.infra.connectors.gateway.cli_install.ensure_cli_path", forbidden)
    monkeypatch.setattr(server_module, "ensure_mobile_capabilities_probed", forbidden)
    monkeypatch.setattr(server_module, "PluginManager", forbidden)
    monkeypatch.setattr(server_module, "build_shared_services", forbidden)
    monkeypatch.setattr(migrate, "_repair_legacy_schema", forbidden)
    boot = AsyncMock(side_effect=AssertionError("runtime must not start"))
    monkeypatch.setattr(server, "_boot_runtime", boot)
    with pytest.raises(RuntimeError, match="DATABASE_SCHEMA_GUARDS_INVALID"):
        await getattr(server, path)()
    assert not server.database_bound and server.services is None
    boot.assert_not_awaited()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"), opened.connect() as conn:
        conn.execute("SELECT 1")
    with guarded.connect() as conn:
        assert conn.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall() == before
