"""Live PG tests for 033/034, not a full migration-chain or service test.

Requires OCTOP_TEST_DATABASE_URL and OCTOP_TEST_DATABASE_DISPOSABLE=1, explicitly
attesting that the URL names a dedicated disposable database. Database names must
start with octop_test. Never use production credentials. Each test creates its own
random schema, excludes public from search_path, and drops ONLY that schema.
No connection is opened during collection or when either opt-in is absent.
"""

from __future__ import annotations

import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from octop.infra.db.migrate import _split_pg_sql
from octop.infra.db.pool import PostgresPool
from octop.infra.db.repos.scene_acceptance import (
    AcceptanceStoreError,
    SceneAcceptanceRepo,
    snapshot_hash,
)
from octop.infra.db.repos.worker_knowledge_mappings import WorkerKnowledgeMappingRepo

pytestmark = pytest.mark.postgresql


@pytest.fixture
def pg_governance():
    url = os.environ.get("OCTOP_TEST_DATABASE_URL")
    if not url or os.environ.get("OCTOP_TEST_DATABASE_DISPOSABLE") != "1":
        pytest.skip("requires dedicated disposable PostgreSQL URL and explicit disposable opt-in")
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    info = conninfo_to_dict(url)
    if not info.get("dbname", "").startswith("octop_test"):
        pytest.skip("dedicated test database name must start with octop_test")
    schema = "octop_governance_" + uuid.uuid4().hex
    # Override caller-supplied search_path/options; no fallback to public.
    scoped = make_conninfo(
        url,
        connect_timeout=5,
        options=f"-c search_path={schema} -c statement_timeout=10000 -c lock_timeout=5000",
    )
    pool = None
    with psycopg.connect(make_conninfo(url, connect_timeout=5), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            pool = PostgresPool(scoped, min_size=1, max_size=4)
            with pool.transaction() as c:
                assert c.execute("SELECT current_schema()").fetchone()[0] == schema
                # Minimal predecessor schema: these tests target 033/034 and repo SQL.
                statements = """
                CREATE TABLE users(id INTEGER PRIMARY KEY,role TEXT,disabled INTEGER);
                CREATE TABLE organizations(organization_id TEXT PRIMARY KEY);
                CREATE TABLE organization_members(organization_id TEXT,user_id INTEGER,role TEXT);
                CREATE TABLE digital_workers(worker_id TEXT PRIMARY KEY,organization_id TEXT,
                  version INTEGER,capability_version INTEGER);
                CREATE TABLE external_knowledge_connections(connection_id TEXT PRIMARY KEY,
                  revision INTEGER,enabled INTEGER);
                CREATE TABLE external_knowledge_sources(source_id TEXT PRIMARY KEY,
                  connection_id TEXT,resource_revision INTEGER,enabled INTEGER);
                CREATE TABLE enterprise_scene_runs(run_id TEXT PRIMARY KEY,organization_id TEXT,
                  requested_by INTEGER,approved_by INTEGER,status TEXT,scene_version INTEGER,
                  plan_snapshot_json TEXT,updated_at INTEGER);
                CREATE TABLE audit_log(ts BIGINT,actor TEXT,action TEXT,target TEXT,payload TEXT);
                CREATE TABLE _schema_version(version INTEGER);
                INSERT INTO _schema_version VALUES(32);
                INSERT INTO organizations VALUES('o');
                INSERT INTO users VALUES(1,'user',0),(2,'user',0),(3,'admin',0);
                INSERT INTO organization_members VALUES('o',2,'manager');
                INSERT INTO digital_workers VALUES('w','o',1,1);
                INSERT INTO external_knowledge_connections VALUES('c',1,1);
                INSERT INTO external_knowledge_sources VALUES('s','c',1,1);
                INSERT INTO enterprise_scene_runs VALUES('r','o',1,2,'succeeded',1,'{}',1);
                """
                for statement in _split_pg_sql(statements):
                    c.execute(statement)
                migrations = Path(__file__).resolve().parents[2] / "src/octop/infra/db/migrations"
                for name in (
                    "033_worker_knowledge_mappings.pg.sql",
                    "034_scene_run_acceptance.pg.sql",
                ):
                    for statement in _split_pg_sql((migrations / name).read_text()):
                        c.execute(statement)
                assert c.execute("SELECT version FROM _schema_version").fetchone()[0] == 34
            yield pool
        finally:
            if pool is not None:
                pool.close()
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def _review_args(db):
    with db.connect() as c:
        run = dict(c.execute("SELECT * FROM enterprise_scene_runs WHERE run_id='r'").fetchone())
    return {
        "organization_id": "o",
        "run_id": "r",
        "reviewer_id": 2,
        "decision": "accepted",
        "evidence_sha256": "a" * 64,
        "evidence_length": 9,
        "expected_snapshot_hash": snapshot_hash(run),
    }


def _counts(db):
    with db.connect() as c:
        return tuple(
            c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("scene_run_acceptances", "audit_log")
        )


def _assert_review_audit(db, review):
    with db.connect() as c:
        rows = c.execute("SELECT * FROM audit_log").fetchall()
    assert len(rows) == 1
    audit = dict(rows[0])
    assert audit["actor"] == str(review["reviewer_id"])
    assert audit["action"] == "scene.run.delivery_reviewed"
    assert audit["target"] == review["run_id"]
    assert audit["ts"] == review["reviewed_at"]
    # Exact keys also prevent accidentally persisting the evidence response body.
    assert json.loads(audit["payload"]) == {
        "organization_id": review["organization_id"],
        "decision": review["decision"],
        "evidence_sha256": review["evidence_sha256"],
        "snapshot_sha256": review["snapshot_sha256"],
    }


def test_mapping_unique_draft_and_recreate_after_revoke(pg_governance):
    repo = WorkerKnowledgeMappingRepo(pg_governance)
    values = {
        "organization_id": "o",
        "worker_id": "w",
        "capability_key": "knowledge",
        "capability_json": "{}",
        "capability_version": 1,
        "worker_version": 1,
        "external_source_id": "s",
        "expected_connection_id": "c",
        "connection_revision": 1,
        "resource_revision": 1,
    }
    first = repo.create_draft(values, 1)
    assert first is not None
    assert repo.create_draft(values, 1) is None
    assert not repo.revoke("o", "w", first, 99, 1)
    assert repo.revoke("o", "w", first, 1, 1)
    second = repo.create_draft(values, 1)
    assert second is not None and second != first
    rows = repo.list_page("o", "w", limit=10, offset=0)
    assert sorted(row["status"] for row in rows) == ["draft", "revoked"]


def test_acceptance_idempotent_and_conflicting_decision(pg_governance):
    repo = SceneAcceptanceRepo(pg_governance)
    args = _review_args(pg_governance)
    first = repo.decide(**args)
    assert repo.decide(**args) == first
    with pytest.raises(AcceptanceStoreError) as error:
        repo.decide(**(args | {"decision": "changes_requested"}))
    assert error.value.reason == "conflict"
    assert _counts(pg_governance) == (1, 1)
    _assert_review_audit(pg_governance, first)


@pytest.mark.parametrize("different_reviewer", [False, True])
def test_concurrent_reviews_commit_once(pg_governance, different_reviewer):
    """Concurrent-start smoke test, not deterministic proof of lock contention."""
    args = _review_args(pg_governance)
    # Synchronizes entry only; OS/DB scheduling may still serialize the transactions.
    barrier = Barrier(2, timeout=10)

    def decide(index):
        params = args.copy()
        if different_reviewer and index:
            params.update(reviewer_id=3, decision="changes_requested")
        barrier.wait()
        try:
            return SceneAcceptanceRepo(pg_governance).decide(**params)
        except AcceptanceStoreError as error:
            return error.reason

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(decide, i) for i in range(2)]
        results = [future.result(timeout=20) for future in futures]
    if different_reviewer:
        assert sum(isinstance(result, dict) for result in results) == 1
        assert "conflict" in results
    else:
        assert isinstance(results[0], dict) and results[0] == results[1]
    assert _counts(pg_governance) == (1, 1)
    winner = next(result for result in results if isinstance(result, dict))
    _assert_review_audit(pg_governance, winner)


def test_audit_failure_rolls_back_acceptance(pg_governance):
    import psycopg

    with pg_governance.transaction() as c:
        c.execute("ALTER TABLE audit_log ADD CONSTRAINT reject_test_audit CHECK (action='never')")
    with pytest.raises(psycopg.errors.CheckViolation):
        SceneAcceptanceRepo(pg_governance).decide(**_review_args(pg_governance))
    assert _counts(pg_governance) == (0, 0)
