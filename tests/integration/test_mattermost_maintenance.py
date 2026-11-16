"""Synthetic SQLite operator migration and actual isolated ASGI admission."""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from click.testing import CliRunner

from octop.cli.commands.mattermost_ingress import mattermost_ingress
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.mattermost_ingress import IntakeError
from octop.infra.db.repos.mattermost_maintenance import MattermostMaintenanceRepo
from tests.integration.test_mattermost_ingress import (
    PATH,
    cancellation,
    send,
)
from tests.integration.test_mattermost_ingress import (
    ingress as ingress,
)
from tests.integration.test_scene_execution_offline import scene_runtime as scene_runtime


def disable(e):
    with e.db.transaction() as conn:
        conn.execute("UPDATE mattermost_brokers SET enabled=0")
    return MattermostMaintenanceRepo(e.db)


def rows(e, table):
    with e.db.connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM " + table)]


@pytest.mark.asyncio
async def test_operator_cli_new_only_admission_and_compensating_suspend(ingress):
    e = ingress
    assert (await send(e)).status_code == 201
    original = rows(e, "mattermost_intakes")
    disable(e)
    runner = CliRunner()
    base = ["--database", str(e.db.path), "--broker", "fixture-broker"]
    preview = runner.invoke(mattermost_ingress, [*base, "plan"])
    assert preview.exit_code == 0, preview.output
    plan = json.loads(preview.output)
    assert plan["delivery_keys_to_retire"] == [e.key]
    result = runner.invoke(
        mattermost_ingress,
        [
            *base,
            "retire-and-resume",
            "--plan-digest",
            plan["plan_digest"],
            "--reason",
            "Synthetic operator abandons old deliveries",
        ],
    )
    assert result.exit_code == 0, result.output
    resumed = json.loads(result.output)
    assert resumed["revision"] == 2
    assert rows(e, "mattermost_intakes") == original
    assert len(rows(e, "mattermost_broker_nonces")) == 1
    assert (await send(e)).status_code == 410
    assert (await send(e, "GET", PATH + "/" + e.key)).status_code == 410
    path, body, headers = cancellation(e)
    assert (await send(e, "POST", path, body, headers)).status_code == 410
    # New source identity, newly signed proof; old identity is never resurrected.
    e.claims["intent"]["receipt"] = "f" * 64
    new_proof = e.proof()
    new_response = await send(e, body=new_proof)
    assert new_response.status_code == 201, new_response.text
    assert new_response.json()["execution_authorized"] is False
    assert new_response.json()["delivery_key"] != e.key
    rollback = runner.invoke(
        mattermost_ingress,
        [
            *base,
            "suspend",
            "--revision",
            "2",
            "--reason",
            "Synthetic operator compensates activation",
        ],
    )
    assert rollback.exit_code == 0, rollback.output
    assert (await send(e)).status_code == 401
    assert len(rows(e, "mattermost_retired_intakes")) == 1
    audit = [r for r in rows(e, "audit_log") if r["action"].startswith("mattermost.maintenance.")]
    assert [r["actor"] for r in audit] == ["local-maintenance", "local-maintenance"]
    assert json.loads(audit[0]["payload"])["plan_digest"] == plan["plan_digest"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["mapping", "intake", "nonce", "permission"])
async def test_stale_plan_or_revoked_acl_never_enables_broker(ingress, mutation):
    e = ingress
    assert (await send(e)).status_code == 201
    repo = disable(e)
    plan = repo.plan("fixture-broker")
    with e.db.transaction() as conn:
        sql = {
            "mapping": "UPDATE mattermost_brokers SET revision=2",
            "intake": "UPDATE mattermost_intakes SET observed_source_version=10",
            "nonce": "INSERT INTO mattermost_broker_nonces VALUES('fixture-broker','new-nonce',101)",
            "permission": "UPDATE users SET disabled=1 WHERE id=1",
        }[mutation]
        conn.execute(sql)
    with pytest.raises(IntakeError):
        repo.retire_and_resume("fixture-broker", plan["plan_digest"], "Operator review reason", 102)
    assert rows(e, "mattermost_brokers")[0]["enabled"] == 0
    assert rows(e, "mattermost_retired_intakes") == []


@pytest.mark.asyncio
async def test_resume_audit_failure_rolls_back_fences_and_enable(ingress):
    e = ingress
    assert (await send(e)).status_code == 201
    repo = disable(e)
    plan = repo.plan("fixture-broker")
    with e.db.connect() as conn:
        conn.execute(
            "CREATE TRIGGER fail_maintenance BEFORE INSERT ON audit_log "
            "WHEN NEW.action='mattermost.maintenance.retire_and_resume' "
            "BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        repo.retire_and_resume("fixture-broker", plan["plan_digest"], "Operator review reason", 102)
    assert rows(e, "mattermost_brokers")[0]["enabled"] == 0
    assert rows(e, "mattermost_retired_intakes") == []
    assert repo.plan("fixture-broker")["plan_digest"] == plan["plan_digest"]


@pytest.mark.asyncio
async def test_cross_connection_same_plan_only_commits_once(ingress):
    e = ingress
    assert (await send(e)).status_code == 201
    repo = disable(e)
    plan = repo.plan("fixture-broker")
    barrier = Barrier(2)

    def resume(_):
        db = SqlitePool(e.db.path)
        try:
            barrier.wait(timeout=10)
            try:
                MattermostMaintenanceRepo(db).retire_and_resume(
                    "fixture-broker", plan["plan_digest"], "Operator review reason", 102
                )
                return "resumed"
            except IntakeError as exc:
                return exc.code
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(resume, (1, 2)))
    assert outcomes.count("resumed") == 1
    assert len(rows(e, "mattermost_retired_intakes")) == 1
    assert rows(e, "mattermost_brokers")[0]["revision"] == 2


@pytest.mark.asyncio
async def test_schema_47_fences_disabled_history_atomically(ingress):
    e = ingress
    assert (await send(e)).status_code == 201
    disable(e)
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
    assert rows(e, "mattermost_retired_intakes")[0]["delivery_key"] == e.key
    # Even a manual enabled toggle cannot reopen the quarantined identity.
    with e.db.transaction() as conn:
        conn.execute("UPDATE mattermost_brokers SET enabled=1")
    assert (await send(e)).status_code == 410


@pytest.mark.asyncio
async def test_resume_invalidates_inflight_mapping_and_retains_nonce_replay_guard(ingress):
    e = ingress
    headers = e.headers()
    assert (await send(e, headers=headers)).status_code == 201
    cached = e.repo.mapping("fixture-broker")
    repo = disable(e)
    repo.retire_and_resume(
        "fixture-broker",
        repo.plan("fixture-broker")["plan_digest"],
        "Operator migration reason",
        102,
    )
    with pytest.raises(IntakeError, match="BROKER_AUTHORITY_CHANGED"):
        e.repo.inspect(cached, e.key)
    assert (await send(e, headers=headers)).status_code == 409


@pytest.mark.asyncio
async def test_suspend_failure_keeps_previous_commit_and_permanent_fence(ingress):
    e = ingress
    assert (await send(e)).status_code == 201
    repo = disable(e)
    repo.retire_and_resume(
        "fixture-broker",
        repo.plan("fixture-broker")["plan_digest"],
        "Operator migration reason",
        102,
    )
    with pytest.raises(IntakeError, match="MAINTENANCE_REVISION_CHANGED"):
        repo.suspend("fixture-broker", 1, "Operator rollback reason", 103)
    with e.db.connect() as conn:
        conn.execute(
            "CREATE TRIGGER fail_suspend BEFORE INSERT ON audit_log "
            "WHEN NEW.action='mattermost.maintenance.suspended' "
            "BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        repo.suspend("fixture-broker", 2, "Operator rollback reason", 103)
    assert rows(e, "mattermost_brokers")[0]["enabled"] == 1
    assert len(rows(e, "mattermost_retired_intakes")) == 1
    assert (await send(e)).status_code == 410


@pytest.mark.asyncio
async def test_exhausted_nonce_capacity_is_not_reset_by_migration(ingress):
    e = ingress
    assert (await send(e)).status_code == 201
    repo = disable(e)
    with e.db.transaction() as conn:
        conn.executemany(
            "INSERT INTO mattermost_broker_nonces VALUES('fixture-broker',?,101)",
            [(str(n),) for n in range(4095)],
        )
    with pytest.raises(IntakeError, match="MAINTENANCE_CAPACITY_EXHAUSTED"):
        repo.retire_and_resume(
            "fixture-broker",
            repo.plan("fixture-broker")["plan_digest"],
            "Operator migration reason",
            102,
        )
    assert rows(e, "mattermost_brokers")[0]["enabled"] == 0
    assert len(rows(e, "mattermost_broker_nonces")) == 4096


@pytest.mark.asyncio
async def test_lost_cli_acknowledgement_keeps_auditable_committed_fence(ingress, monkeypatch):
    from octop.cli.commands import mattermost_ingress as command

    e = ingress
    assert (await send(e)).status_code == 201
    repo = disable(e)
    plan = repo.plan("fixture-broker")
    original = command.maintain_broker

    def lost_ack(**kwargs):
        original(**kwargs)
        raise OSError("simulated lost acknowledgement")

    monkeypatch.setattr(command, "maintain_broker", lost_ack)
    result = CliRunner().invoke(
        mattermost_ingress,
        [
            "--database",
            str(e.db.path),
            "--broker",
            "fixture-broker",
            "retire-and-resume",
            "--plan-digest",
            plan["plan_digest"],
            "--reason",
            "Operator migration reason",
        ],
    )
    assert result.exit_code != 0
    assert "CHECK_LOCAL_AUDIT" in result.output
    assert rows(e, "mattermost_brokers")[0]["enabled"] == 1
    assert len(rows(e, "mattermost_retired_intakes")) == 1
    assert any(
        r["action"] == "mattermost.maintenance.retire_and_resume" for r in rows(e, "audit_log")
    )
    assert (await send(e)).status_code == 410


@pytest.mark.asyncio
async def test_status_reconciles_resume_without_exposing_keys(ingress):
    e = ingress
    assert (await send(e)).status_code == 201
    repo = disable(e)
    plan = repo.plan("fixture-broker")
    repo.retire_and_resume("fixture-broker", plan["plan_digest"], "Operator migration reason", 102)
    result = CliRunner().invoke(
        mattermost_ingress, ["--database", str(e.db.path), "--broker", "fixture-broker", "status"]
    )
    assert result.exit_code == 0, result.output
    status = json.loads(result.output)
    assert status["enabled"] == 1 and status["revision"] == 2
    assert status["maintenance_events"][0]["payload"]["plan_digest"] == plan["plan_digest"]
    assert "public_key" not in result.output and "receipt" not in result.output


@pytest.mark.asyncio
async def test_schema_47_preserves_enabled_broker_known_history(ingress):
    e = ingress
    assert (await send(e)).status_code == 201
    e.state["version"] = 10
    assert (await send(e)).status_code == 200
    with e.db.transaction() as conn:
        conn.execute("DROP TABLE mattermost_retired_intakes")
        conn.execute("UPDATE _schema_version SET version=46")
    run_migrations(e.db)
    assert rows(e, "mattermost_retired_intakes") == []
    response = await send(e, "GET", PATH + "/" + e.key)
    assert response.status_code == 200
    assert response.json()["source_version"] == "10"


@pytest.mark.asyncio
async def test_missing_or_incorrect_review_and_schema_cannot_enable(ingress):
    e = ingress
    assert (await send(e)).status_code == 201
    repo = disable(e)
    plan = repo.plan("fixture-broker")
    with pytest.raises(IntakeError, match="MAINTENANCE_PLAN_CHANGED"):
        repo.retire_and_resume("fixture-broker", "bad-digest", "Operator migration reason", 102)
    with pytest.raises(IntakeError, match="MAINTENANCE_REASON_REQUIRED"):
        repo.retire_and_resume("fixture-broker", plan["plan_digest"], "", 102)
    with e.db.transaction() as conn:
        conn.execute("UPDATE _schema_version SET version=46")
    with pytest.raises(IntakeError, match="MAINTENANCE_SCHEMA_REQUIRED"):
        repo.retire_and_resume(
            "fixture-broker", plan["plan_digest"], "Operator migration reason", 102
        )
    assert rows(e, "mattermost_brokers")[0]["enabled"] == 0
    assert rows(e, "mattermost_retired_intakes") == []
