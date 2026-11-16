"""Regression of implicit retirement and unsafe mixed-version resumption.

Archived repository modules are exact public development-branch source, loaded
against synthetic databases; no old server or external service is launched.
"""

from __future__ import annotations

import hashlib
import json
import runpy
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from octop.cli.commands.mattermost_ingress import mattermost_ingress
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.mattermost_ingress import IntakeError
from octop.infra.db.repos.mattermost_maintenance import MattermostMaintenanceRepo
from tests.integration.test_mattermost_ingress import PATH, send
from tests.integration.test_mattermost_ingress import ingress as ingress
from tests.integration.test_scene_execution_offline import scene_runtime as scene_runtime


def rows(e, table):
    with e.db.connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM " + table)]


def archive(name):
    path = Path(__file__).parents[1] / "fixtures" / name
    if name == "mattermost_ingress_0e950452.py.txt":
        assert (
            hashlib.sha256(path.read_bytes()).hexdigest()
            == "f17aaa5077ff45d0c9851f9a120bf574ee3cec0d55693c8ca1df118323be162b"
        )
    return runpy.run_path(str(path))


def simulate_unsafe_47(e):
    with e.db.transaction() as conn:
        conn.execute("UPDATE _schema_version SET version=47")
        conn.execute(
            "INSERT INTO mattermost_retired_intakes VALUES(?, 'schema047_unknown_legacy_history', 0)",
            (e.key,),
        )
        conn.execute("UPDATE mattermost_brokers SET enabled=1,revision=2")


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [0, 1])
async def test_upgrade_never_permanently_retires_without_operator_consent(ingress, enabled):
    e = ingress
    assert (await send(e)).status_code == 201
    original = rows(e, "mattermost_intakes")
    with e.db.transaction() as conn:
        conn.execute("DROP TABLE mattermost_retired_intakes")
        conn.execute("UPDATE _schema_version SET version=46")
        conn.execute("UPDATE mattermost_brokers SET enabled=?", (enabled,))
    run_migrations(e.db)
    assert rows(e, "mattermost_retired_intakes") == []
    assert rows(e, "mattermost_intakes") == original
    assert rows(e, "mattermost_brokers")[0]["enabled"] == enabled
    assert rows(e, "_schema_version")[0]["version"] == 48


@pytest.mark.asyncio
async def test_47_failure_rolls_back_empty_table_and_version(ingress):
    e = ingress
    assert (await send(e)).status_code == 201
    with e.db.transaction() as conn:
        conn.execute("DROP TABLE mattermost_retired_intakes")
        conn.execute("UPDATE _schema_version SET version=46")
        conn.execute(
            "CREATE TRIGGER fail47 BEFORE UPDATE ON _schema_version "
            "WHEN NEW.version=47 BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        run_migrations(e.db)
    with e.db.connect() as conn:
        assert not conn.in_transaction
        assert not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='mattermost_retired_intakes'"
        ).fetchone()
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 46
        conn.execute("DROP TRIGGER fail47")
    run_migrations(e.db)
    assert rows(e, "mattermost_retired_intakes") == []


@pytest.mark.asyncio
async def test_archived_receiver_bypasses_fence_but_48_disabled_guard_blocks_it(ingress):
    e = ingress
    assert (await send(e)).status_code == 201
    legacy = archive("mattermost_ingress_0e950452.py.txt")
    old_db = SqlitePool(e.db.path)
    old = legacy["MattermostIngressRepo"](old_db)
    try:
        simulate_unsafe_47(e)
        cached = old.mapping("fixture-broker")
        old_row = old.inspect(cached, e.key)
        assert old_row["delivery_key"] == e.key
        assert (await send(e, "GET", PATH + "/" + e.key)).status_code == 410
        # Keep the old connection and cached mapping alive across the repair.
        run_migrations(e.db)
        assert rows(e, "mattermost_brokers")[0]["enabled"] == 0
        assert rows(e, "mattermost_brokers")[0]["revision"] == 3
        with pytest.raises(legacy["IntakeError"], match="BROKER_UNAUTHENTICATED"):
            old.mapping("fixture-broker")
        with pytest.raises(legacy["IntakeError"], match="BROKER_AUTHORITY_CHANGED"):
            old.inspect(cached, e.key)
        with pytest.raises(legacy["IntakeError"], match="BROKER_AUTHORITY_CHANGED"):
            old.accept(cached, old_row, issued=100, expires=200, clock=lambda: 101)
        with pytest.raises(legacy["IntakeError"], match="BROKER_AUTHORITY_CHANGED"):
            old.cancel(cached, old_row, 1, "op", 10, lambda: 101)
        with pytest.raises(legacy["IntakeError"], match="BROKER_AUTHORITY_CHANGED"):
            old.consume_nonce(cached, "unconsumed", 100, lambda: 101)
        assert (await send(e, "GET", PATH + "/" + e.key)).status_code == 401
        assert len(rows(e, "mattermost_retired_intakes")) == 1
    finally:
        old_db.close()


@pytest.mark.asyncio
async def test_current_and_archived_maintenance_cannot_resume_after_repair(ingress):
    e = ingress
    assert (await send(e)).status_code == 201
    simulate_unsafe_47(e)
    run_migrations(e.db)
    before = {
        name: rows(e, name)
        for name in (
            "mattermost_brokers",
            "mattermost_intakes",
            "mattermost_retired_intakes",
            "audit_log",
        )
    }
    repo = MattermostMaintenanceRepo(e.db)
    plan = repo.plan("fixture-broker")
    with pytest.raises(IntakeError, match="MAINTENANCE_PROCESS_ISOLATION_UNPROVEN"):
        repo.retire_and_resume(
            "fixture-broker", plan["plan_digest"], "Operator says old processes stopped", 102
        )
    old_db = SqlitePool(e.db.path)
    try:
        old = archive("mattermost_maintenance_c590d088.py.txt")["MattermostMaintenanceRepo"](old_db)
        with pytest.raises(IntakeError, match="MAINTENANCE_SCHEMA_REQUIRED"):
            old.retire_and_resume(
                "fixture-broker", plan["plan_digest"], "Operator review reason", 102
            )
    finally:
        old_db.close()
    assert before == {name: rows(e, name) for name in before}


@pytest.mark.asyncio
async def test_cli_plan_is_diagnostic_and_resume_refuses_without_side_effects(ingress):
    e = ingress
    assert (await send(e)).status_code == 201
    with e.db.transaction() as conn:
        conn.execute("UPDATE mattermost_brokers SET enabled=0")
    runner = CliRunner()
    base = ["--database", str(e.db.path), "--broker", "fixture-broker"]
    plan_result = runner.invoke(mattermost_ingress, [*base, "plan"])
    assert plan_result.exit_code == 0, plan_result.output
    plan = json.loads(plan_result.output)
    assert plan["policy"] == "resume_blocked_process_isolation_unproven"
    assert plan["delivery_keys"] == [e.key]
    before = rows(e, "audit_log")
    result = runner.invoke(
        mattermost_ingress,
        [
            *base,
            "retire-and-resume",
            "--plan-digest",
            plan["plan_digest"],
            "--reason",
            "Operator explicitly confirms retirement",
        ],
    )
    assert result.exit_code != 0
    assert "MAINTENANCE_PROCESS_ISOLATION_UNPROVEN" in result.output
    assert rows(e, "mattermost_retired_intakes") == []
    assert rows(e, "mattermost_brokers")[0]["enabled"] == 0
    assert rows(e, "audit_log") == before
    status = runner.invoke(mattermost_ingress, [*base, "status"])
    assert status.exit_code == 0
    assert json.loads(status.output)["enabled"] == 0


@pytest.mark.asyncio
async def test_48_repair_failure_rolls_back_and_retry_disables(ingress):
    e = ingress
    assert (await send(e)).status_code == 201
    simulate_unsafe_47(e)
    with e.db.connect() as conn:
        conn.execute(
            "CREATE TRIGGER fail48 BEFORE UPDATE ON _schema_version "
            "WHEN NEW.version=48 BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )
    before = rows(e, "audit_log")
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        run_migrations(e.db)
    assert rows(e, "_schema_version")[0]["version"] == 47
    assert rows(e, "mattermost_brokers")[0]["enabled"] == 1
    assert rows(e, "audit_log") == before
    with e.db.connect() as conn:
        conn.execute("DROP TRIGGER fail48")
    run_migrations(e.db)
    assert rows(e, "mattermost_brokers")[0]["enabled"] == 0
    repair = [
        r for r in rows(e, "audit_log") if r["action"] == "mattermost.maintenance.resume_blocked"
    ]
    assert len(repair) == 1 and repair[0]["actor"] == "schema-migration"
    run_migrations(e.db)
    assert (
        len(
            [
                r
                for r in rows(e, "audit_log")
                if r["action"] == "mattermost.maintenance.resume_blocked"
            ]
        )
        == 1
    )


@pytest.mark.asyncio
async def test_suspend_remains_audited_and_transactional(ingress):
    e = ingress
    assert (await send(e)).status_code == 201
    repo = MattermostMaintenanceRepo(e.db)
    with e.db.connect() as conn:
        conn.execute(
            "CREATE TRIGGER fail_suspend BEFORE INSERT ON audit_log "
            "WHEN NEW.action='mattermost.maintenance.suspended' "
            "BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        repo.suspend("fixture-broker", 1, "Operator stops unsafe admission", 102)
    assert rows(e, "mattermost_brokers")[0]["enabled"] == 1
    with e.db.connect() as conn:
        conn.execute("DROP TRIGGER fail_suspend")
    result = repo.suspend("fixture-broker", 1, "Operator stops unsafe admission", 102)
    assert result["enabled"] is False
    assert rows(e, "mattermost_retired_intakes") == []
    assert (
        repo.status("fixture-broker")["maintenance_events"][0]["action"]
        == "mattermost.maintenance.suspended"
    )


def _legacy_reader_process(database, key, reports, commands):
    import socket

    def deny_network(*args, **kwargs):
        raise AssertionError("synthetic legacy reader must stay offline")

    for name in ("connect", "connect_ex", "sendto"):
        setattr(socket.socket, name, deny_network)
    socket.getaddrinfo = deny_network
    legacy = archive("mattermost_ingress_0e950452.py.txt")
    db = SqlitePool(Path(database))
    try:
        repo = legacy["MattermostIngressRepo"](db)
        cached = repo.mapping("fixture-broker")
        reports.send(repo.inspect(cached, key)["delivery_key"])
        assert commands.poll(15)
        assert commands.recv() == "migrated"
        errors = []
        for read in (lambda: repo.inspect(cached, key), lambda: repo.mapping("fixture-broker")):
            try:
                read()
                errors.append("UNEXPECTED_ACCESS")
            except legacy["IntakeError"] as exc:
                errors.append(exc.code)
        reports.send(errors)
    finally:
        db.close()
        reports.close()
        commands.close()


@pytest.mark.asyncio
async def test_live_separate_old_process_loses_cached_and_fresh_access_after_48(ingress):
    import multiprocessing

    e = ingress
    assert (await send(e)).status_code == 201
    simulate_unsafe_47(e)
    context = multiprocessing.get_context("spawn")
    receive, child_send = context.Pipe(duplex=False)
    child_receive, send_command = context.Pipe(duplex=False)
    process = context.Process(
        target=_legacy_reader_process,
        args=(str(e.db.path), e.key, child_send, child_receive),
    )
    process.start()
    child_send.close()
    child_receive.close()
    try:
        assert receive.poll(15), "legacy reader did not start"
        assert receive.recv() == e.key
        run_migrations(e.db)
        send_command.send("migrated")
        assert receive.poll(15), "legacy reader did not finish"
        assert receive.recv() == ["BROKER_AUTHORITY_CHANGED", "BROKER_UNAUTHENTICATED"]
        process.join(timeout=5)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        receive.close()
        send_command.close()


@pytest.mark.asyncio
async def test_48_fences_historical_resume_without_creating_retirement(ingress):
    e = ingress
    assert (await send(e)).status_code == 201
    with e.db.transaction() as conn:
        conn.execute("UPDATE _schema_version SET version=47")
        conn.execute(
            "INSERT INTO audit_log(ts,actor,action,target,payload) VALUES(101, 'local-maintenance', 'mattermost.maintenance.retire_and_resume','fixture-broker','{}')"
        )
    run_migrations(e.db)
    assert rows(e, "mattermost_retired_intakes") == []
    assert rows(e, "mattermost_brokers")[0]["enabled"] == 0
    assert (await send(e)).status_code == 401
    status = MattermostMaintenanceRepo(e.db).status("fixture-broker")
    assert status["maintenance_events"][0]["action"] == "mattermost.maintenance.resume_blocked"
