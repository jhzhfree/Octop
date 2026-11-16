"""Real PostgreSQL only; each test owns a random schema in an opted-in disposable DB."""

from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest

from octop.infra.db.migrate import run_migrations, schema_baseline_version
from octop.infra.db.pool import PostgresPool
from octop.infra.db.services import RepoBundle
from octop.infra.digital_workers.handoffs import GoalHandoffService
from octop.infra.digital_workers.service import DigitalWorkerError
from octop.infra.users.identity import User

pytestmark = pytest.mark.postgresql


@pytest.fixture
def pg():
    url = os.environ.get("OCTOP_TEST_DATABASE_URL")
    if not url or os.environ.get("OCTOP_TEST_DATABASE_DISPOSABLE") != "1":
        pytest.skip("requires explicit disposable PostgreSQL opt-in")
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    if not conninfo_to_dict(url).get("dbname", "").startswith("octop_test"):
        pytest.skip("database name must start with octop_test")
    schema = "handoff_" + uuid.uuid4().hex
    scoped = make_conninfo(
        url,
        connect_timeout=3,
        options=f"-c search_path={schema} -c statement_timeout=10000 -c lock_timeout=5000",
    )
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        pool = PostgresPool(scoped, min_size=1, max_size=8)
        try:
            run_migrations(pool)
            run_migrations(pool)
            repos = RepoBundle.from_pool(pool)
            with pool.transaction() as c:
                assert c.execute("SELECT current_schema()").fetchone()[0] == schema
                assert c.execute("SELECT version FROM _schema_version").fetchone()[
                    0
                ] == schema_baseline_version("postgresql")
                for ident in (1, 2):
                    c.execute(
                        "INSERT INTO users(id,username,password_hash,role,created_at) VALUES(?,?,'synthetic','user',1)",
                        (ident, f"u{ident}"),
                    )
                for org in ("o", "other"):
                    c.execute(
                        "INSERT INTO organizations(organization_id,owner_user_id,name,created_at,updated_at) VALUES(?,1,?,1,1)",
                        (org, org),
                    )
                    c.execute(
                        "INSERT INTO organization_members(organization_id,user_id,role,created_at) VALUES(?,1,'owner',1)",
                        (org,),
                    )
                for suffix in ("1", "2"):
                    c.execute(
                        "INSERT INTO agents(agent_id,name,user_id,created_at,updated_at) VALUES(?,?,1,1,1)",
                        ("a" + suffix, "Agent " + suffix),
                    )
                    c.execute(
                        "INSERT INTO organization_positions(position_id,organization_id,title,created_at,updated_at) VALUES(?,'o',?,1,1)",
                        ("p" + suffix, "Role " + suffix),
                    )
                    c.execute(
                        "INSERT INTO organization_assignments(assignment_id,position_id,subject_type,subject_id,created_at) VALUES(?,?,'agent',?,1)",
                        ("assignment" + suffix, "p" + suffix, "a" + suffix),
                    )
                    c.execute(
                        "INSERT INTO digital_workers(worker_id,organization_id,position_id,agent_id,owner_user_id,name,status,created_at,updated_at) VALUES(?,'o',?,?,1,'Worker','active',1,1)",
                        ("w" + suffix, "p" + suffix, "a" + suffix),
                    )
            goal = repos.digital_worker_repo.create_goal(
                worker_id="w1",
                title="Inspect",
                description="",
                success_criteria="Evidence",
                period_start=None,
                period_end=None,
                created_by=1,
            )
            repos.digital_worker_repo.update_goal(goal["goal_id"], {"status": "active"})
            service = GoalHandoffService(repos, User(1, "u1", "user", None))
            body = {
                "request_id": "test-request-001",
                "expected_source_version": 2,
                "expected_target_version": 1,
                "target_worker_id": "w2",
                "title": "Review",
                "description": "",
                "reason": "Review evidence",
                "success_criteria": "Independent review",
            }
            yield pool, service, goal["goal_id"], body, scoped
        finally:
            pool.close()
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_real_pg_handoff_migration_and_idempotence(pg):
    pool, service, goal, body, _ = pg
    record = service.create_handoff("o", "w1", goal, body)
    assert service.create_handoff("o", "w1", goal, body) == record
    assert record["target_goal_status"] == "draft"
    assert (
        service.handoffs("o", record["target_goal_id"], None)["items"][0]["handoff_id"]
        == record["handoff_id"]
    )
    with pool.connect() as c:
        assert c.execute("SELECT count(*) FROM goal_handoffs").fetchone()[0] == 1
        assert (
            c.execute(
                "SELECT count(*) FROM audit_log WHERE action='digital_worker.goal_handoff'"
            ).fetchone()[0]
            == 1
        )
        assert c.execute("SELECT count(*) FROM enterprise_scene_runs").fetchone()[0] == 0
    with pytest.raises(DigitalWorkerError) as error:
        service.create_handoff("o", "w1", goal, {**body, "reason": "different"})
    assert error.value.status == 409


@pytest.mark.parametrize(
    "sql,status",
    [
        ("DELETE FROM organization_members WHERE user_id=1", 403),
        ("UPDATE users SET disabled=1 WHERE id=1", 403),
        ("UPDATE agents SET user_id=2,is_shared=0 WHERE agent_id='a2'", 403),
        ("UPDATE agents SET enabled=0 WHERE agent_id='a2'", 403),
        ("DELETE FROM organization_assignments WHERE subject_id='a2'", 409),
        ("UPDATE digital_workers SET status='paused' WHERE worker_id='w2'", 409),
        ("UPDATE digital_workers SET organization_id='other' WHERE worker_id='w2'", 404),
        ("UPDATE digital_worker_goals SET version=version+1", 409),
    ],
)
def test_pg_current_authority_and_versions(pg, sql, status):
    pool, service, goal, body, _ = pg
    with pool.transaction() as c:
        c.execute(sql)
    with pytest.raises(DigitalWorkerError) as error:
        service.create_handoff("o", "w1", goal, body)
    assert error.value.status == status
    with pool.connect() as c:
        assert c.execute("SELECT count(*) FROM goal_handoffs").fetchone()[0] == 0


@pytest.mark.parametrize("same_key", [True, False])
def test_pg_separate_pools_concurrent_idempotence_and_cas(pg, same_key):
    pool, _, goal, body, scoped = pg
    barrier = Barrier(2)

    def create(index):
        other = PostgresPool(scoped)
        try:
            service = GoalHandoffService(RepoBundle.from_pool(other), User(1, "u1", "user", None))
            request = {**body, "request_id": body["request_id"] if same_key else f"request-{index}"}
            barrier.wait(timeout=10)
            try:
                return service.create_handoff("o", "w1", goal, request)["handoff_id"]
            except DigitalWorkerError as exc:
                assert exc.status == 409
                return "conflict"
        finally:
            other.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create, range(2)))
    if same_key:
        assert results[0] == results[1] != "conflict"
    else:
        assert results.count("conflict") == 1
    with pool.connect() as c:
        assert c.execute("SELECT count(*) FROM goal_handoffs").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM digital_worker_goals").fetchone()[0] == 2
        assert (
            c.execute(
                "SELECT count(*) FROM audit_log WHERE action='digital_worker.goal_handoff'"
            ).fetchone()[0]
            == 1
        )


def test_pg_audit_failure_rolls_back_and_connection_recovers(pg):
    import psycopg

    pool, service, goal, body, _ = pg
    with pool.transaction() as c:
        c.execute(
            "CREATE FUNCTION reject_handoff() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected audit failure'; END; $$"
        )
        c.execute(
            "CREATE TRIGGER reject_handoff BEFORE INSERT ON audit_log FOR EACH ROW EXECUTE FUNCTION reject_handoff()"
        )
    with pytest.raises(psycopg.errors.RaiseException, match="injected audit failure"):
        service.create_handoff("o", "w1", goal, body)
    with pool.transaction() as c:
        assert c.execute("SELECT count(*) FROM goal_handoffs").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM digital_worker_goals").fetchone()[0] == 1
        assert (
            c.execute(
                "SELECT version FROM digital_worker_goals WHERE goal_id=?", (goal,)
            ).fetchone()[0]
            == 2
        )
        c.execute("DROP TRIGGER reject_handoff ON audit_log")
    assert service.create_handoff("o", "w1", goal, body)["target_goal_status"] == "draft"


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE users SET disabled=1 WHERE id=1",
        "DELETE FROM organization_members WHERE organization_id='o' AND user_id=1",
        "UPDATE agents SET enabled=0 WHERE agent_id='a2'",
        "DELETE FROM organization_assignments WHERE subject_id='a2'",
        "UPDATE digital_workers SET status='paused' WHERE worker_id='w2'",
    ],
)
def test_pg_ordinary_revocations_wait_for_handoff_commit(pg, monkeypatch, mutation):
    import psycopg

    from octop.infra.db.pool import _PgConnectionProxy

    pool, service, goal, body, scoped = pg
    paused, release = Event(), Event()
    original = _PgConnectionProxy.execute

    def execute(self, sql, params=None):
        result = original(self, sql, params)
        if sql.startswith("INSERT INTO audit_log"):
            paused.set()
            assert release.wait(5), "test did not release paused commit"
        return result

    monkeypatch.setattr(_PgConnectionProxy, "execute", execute)
    other = PostgresPool(scoped)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(service.create_handoff, "o", "w1", goal, body)
            try:
                assert paused.wait(5)
                with pytest.raises(psycopg.errors.LockNotAvailable), other.transaction() as c:
                    c.execute("SET LOCAL lock_timeout = '100ms'")
                    c.execute(mutation)
            finally:
                release.set()
            assert future.result(timeout=5)["target_goal_status"] == "draft"
        with other.transaction() as c:
            c.execute(mutation)
        with pytest.raises(DigitalWorkerError):
            service.create_handoff(
                "o",
                "w1",
                goal,
                {**body, "request_id": "after-revocation", "expected_source_version": 3},
            )
    finally:
        release.set()
        other.close()


def test_pg_lock_timeout_is_atomic_and_same_key_can_retry(pg):
    pool, service, goal, body, scoped = pg
    other = PostgresPool(scoped)
    try:
        with other.transaction() as c:
            c.execute(
                "SELECT goal_id FROM digital_worker_goals WHERE goal_id=? FOR UPDATE", (goal,)
            ).fetchone()
            with pytest.raises(DigitalWorkerError) as error:
                service.create_handoff("o", "w1", goal, body)
            assert error.value.code == "GOAL_HANDOFF_BUSY"
        with pool.connect() as c:
            assert c.execute("SELECT count(*) FROM goal_handoffs").fetchone()[0] == 0
        assert service.create_handoff("o", "w1", goal, body)["target_goal_status"] == "draft"
    finally:
        other.close()


def test_pg_non_manager_and_revoked_replay_do_not_get_authority(pg):
    pool, service, goal, body, _ = pg
    member = GoalHandoffService(service.repos, User(2, "u2", "user", None))
    with pytest.raises(DigitalWorkerError) as error:
        member.create_handoff("o", "w1", goal, body)
    assert error.value.status == 403
    service.create_handoff("o", "w1", goal, body)
    with pool.transaction() as c:
        c.execute("DELETE FROM organization_members WHERE organization_id='o' AND user_id=1")
    for action in (
        lambda: service.create_handoff("o", "w1", goal, body),
        lambda: service.handoffs("o", None, None),
    ):
        with pytest.raises(DigitalWorkerError) as error:
            action()
        assert error.value.status == 403


def test_pg_draft_delete_retains_provenance_and_replay_current_status(pg):
    _, service, goal, body, _ = pg
    record = service.create_handoff("o", "w1", goal, body)
    service.delete_goal("o", "w2", record["target_goal_id"], expected_version=1)
    assert service.create_handoff("o", "w1", goal, body)["target_goal_status"] is None
    history = service.handoffs("o", record["target_goal_id"], None)["items"]
    assert history[0]["handoff_id"] == record["handoff_id"]
    assert history[0]["target_goal_status"] is None
    assert service.handoffs("other", None, None)["items"] == []


def test_pg_stronger_default_isolation_is_rejected_without_downgrade(pg):
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    _, _, goal, body, scoped = pg
    options = (
        conninfo_to_dict(scoped)["options"] + r" -c default_transaction_isolation=repeatable\ read"
    )
    other = PostgresPool(make_conninfo(scoped, options=options))
    try:
        service = GoalHandoffService(RepoBundle.from_pool(other), User(1, "u1", "user", None))
        with pytest.raises(DigitalWorkerError) as error:
            service.create_handoff("o", "w1", goal, body)
        assert error.value.code == "GOAL_HANDOFF_ISOLATION_REQUIRED"
        with other.connect() as c:
            assert c.execute("SHOW transaction_isolation").fetchone()[0] == "repeatable read"
            assert c.execute("SELECT count(*) FROM goal_handoffs").fetchone()[0] == 0
    finally:
        other.close()


def test_pg_shorter_operator_timeouts_are_preserved_and_local_settings_reset(pg, monkeypatch):
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    from octop.infra.db.pool import _PgConnectionProxy

    _, _, goal, body, scoped = pg
    options = (
        conninfo_to_dict(scoped)["options"] + " -c lock_timeout=150ms -c statement_timeout=1000ms"
    )
    other = PostgresPool(make_conninfo(scoped, options=options), min_size=1, max_size=1)
    original = _PgConnectionProxy.execute
    observed = []

    def execute(self, sql, params=None):
        if sql.startswith("INSERT INTO audit_log"):
            observed.append(
                (
                    original(self, "SHOW lock_timeout").fetchone()[0],
                    original(self, "SHOW statement_timeout").fetchone()[0],
                )
            )
        return original(self, sql, params)

    monkeypatch.setattr(_PgConnectionProxy, "execute", execute)
    try:
        service = GoalHandoffService(RepoBundle.from_pool(other), User(1, "u1", "user", None))
        service.create_handoff("o", "w1", goal, body)
        assert observed == [("150ms", "1s")]
        with other.connect() as c:
            assert c.execute("SHOW lock_timeout").fetchone()[0] == "150ms"
            assert c.execute("SHOW statement_timeout").fetchone()[0] == "1s"
    finally:
        other.close()
