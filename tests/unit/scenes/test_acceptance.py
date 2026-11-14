"""Offline acceptance invariants, real SQLite transactions and server-resolved evidence."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.scene_acceptance import (
    AcceptanceStoreError,
    SceneAcceptanceRepo,
    snapshot_hash,
)
from octop.infra.scenes.acceptance import SceneAcceptanceService
from octop.infra.scenes.service import SceneError


@pytest.fixture
def setup(tmp_path):
    db = SqlitePool(tmp_path / "acceptance.db")
    with db.connect() as conn:
        conn.executescript("""
        CREATE TABLE users(id INTEGER PRIMARY KEY,role TEXT,disabled INTEGER);
        CREATE TABLE organizations(organization_id TEXT PRIMARY KEY);
        CREATE TABLE organization_members(organization_id TEXT,user_id INTEGER,role TEXT);
        CREATE TABLE enterprise_scene_runs(run_id TEXT PRIMARY KEY,organization_id TEXT,
          requested_by INTEGER,approved_by INTEGER,status TEXT,scene_version INTEGER,
          plan_snapshot_json TEXT,updated_at INTEGER);
        CREATE TABLE audit_log(ts INTEGER,actor TEXT,action TEXT,target TEXT,payload TEXT);
        CREATE TABLE _schema_version(version INTEGER);
        INSERT INTO _schema_version VALUES(33);
        INSERT INTO organizations VALUES('o');
        INSERT INTO users VALUES(1,'user',0),(2,'user',0),(3,'user',0),(4,'admin',0);
        INSERT INTO organization_members VALUES('o',1,'manager'),('o',2,'manager'),('o',3,'manager');
        INSERT INTO enterprise_scene_runs VALUES('r','o',1,2,'succeeded',1,'{"criteria":["done"]}',1);
        """)
        migration = (
            Path(__file__).resolve().parents[3]
            / "src/octop/infra/db/migrations/034_scene_run_acceptance.sql"
        )
        conn.executescript(migration.read_text())
    repo = SceneAcceptanceRepo(db)
    with db.connect() as conn:
        run = dict(conn.execute("SELECT * FROM enterprise_scene_runs").fetchone())
    args = {
        "organization_id": "o",
        "run_id": "r",
        "reviewer_id": 2,
        "decision": "accepted",
        "evidence_sha256": "a" * 64,
        "evidence_length": 9,
        "expected_snapshot_hash": snapshot_hash(run),
    }
    yield db, repo, args, run
    db.close()


def test_success_is_pending_and_immutable_idempotent(setup):
    db, repo, args, _ = setup
    assert repo.get("o", "r") is None
    first = repo.decide(**args)
    assert repo.decide(**args) == first
    with pytest.raises(AcceptanceStoreError, match="already reviewed"):
        repo.decide(**(args | {"decision": "changes_requested"}))
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM audit_log").fetchone()[0] == 1
        assert conn.execute("SELECT status FROM enterprise_scene_runs").fetchone()[0] == "succeeded"
        assert "answer" not in json.loads(
            conn.execute("SELECT payload FROM audit_log").fetchone()[0]
        )


@pytest.mark.parametrize("reviewer", [1, 3])
def test_no_self_acceptance_or_unrelated_manager(setup, reviewer):
    _, repo, args, _ = setup
    with pytest.raises(AcceptanceStoreError) as err:
        repo.decide(**(args | {"reviewer_id": reviewer}))
    assert err.value.reason == "forbidden"


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE users SET disabled=1 WHERE id=2",
        "UPDATE organization_members SET role='member' WHERE user_id=2",
        "DELETE FROM organization_members WHERE user_id=2",
    ],
)
def test_current_authority_checked_at_commit(setup, sql):
    db, repo, args, _ = setup
    with db.transaction() as conn:
        conn.execute(sql)
    with pytest.raises(AcceptanceStoreError) as err:
        repo.decide(**args)
    assert err.value.reason == "forbidden"


def test_cross_org_and_stale_status(setup):
    db, repo, args, _ = setup
    with pytest.raises(AcceptanceStoreError) as err:
        repo.decide(**(args | {"organization_id": "other"}))
    assert err.value.reason == "not_found"
    with db.transaction() as conn:
        conn.execute("UPDATE enterprise_scene_runs SET status='failed'")
    with pytest.raises(AcceptanceStoreError) as err:
        repo.decide(**args)
    assert err.value.reason == "stale"


def test_atomic_audit_rollback(setup):
    db, repo, args, _ = setup
    with db.connect() as conn:
        conn.executescript(
            "CREATE TRIGGER fail_audit BEFORE INSERT ON audit_log BEGIN SELECT RAISE(ABORT,'audit unavailable'); END;"
        )
    with pytest.raises(Exception, match="audit unavailable"):
        repo.decide(**args)
    assert repo.get("o", "r") is None


def test_concurrent_decisions_one_winner(setup):
    db, repo, args, _ = setup

    def submit(decision):
        try:
            return repo.decide(**(args | {"decision": decision}))["decision"]
        except AcceptanceStoreError as exc:
            return exc.reason

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(submit, ["accepted", "changes_requested"]))
    assert results.count("conflict") == 1
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM audit_log").fetchone()[0] == 1


def test_service_refetches_evidence_and_rejects_forgery(setup):
    _, repo, _, run = setup
    scenes = SimpleNamespace(
        actor=SimpleNamespace(id=2),
        _require_manager=lambda org: None,
        get_run_evidence_access=lambda org, rid: run.copy(),
    )
    text = "delivery summary"

    async def loader(row):
        return text

    service = SceneAcceptanceService(scenes, repo, loader)

    async def exercise():
        nonlocal text
        preview = await service.review("o", "r")
        text = "changed delivery"
        with pytest.raises(SceneError) as err:
            await service.decide(
                "o",
                "r",
                decision="accepted",
                expected_evidence_sha256=preview["evidence_sha256"],
                expected_snapshot_sha256=preview["snapshot_sha256"],
            )
        assert err.value.status == 409
        assert repo.get("o", "r") is None
        text = ""
        with pytest.raises(SceneError, match="unavailable"):
            await service.review("o", "r")

    asyncio.run(exercise())


def test_admin_not_requester_and_snapshot_bound(setup):
    _, repo, args, _ = setup
    with pytest.raises(AcceptanceStoreError, match="changed"):
        repo.decide(**(args | {"expected_snapshot_hash": "f" * 64}))
    result = repo.decide(**(args | {"reviewer_id": 4, "decision": "changes_requested"}))
    assert result["decision"] == "changes_requested"


@pytest.mark.parametrize(
    "reason,status",
    [
        ("invalid", 422),
        ("not_found", 404),
        ("forbidden", 403),
        ("stale", 409),
        ("conflict", 409),
    ],
)
def test_store_errors_mapped_by_domain(setup, monkeypatch, reason, status):
    _, repo, _, run = setup
    scenes = SimpleNamespace(
        actor=SimpleNamespace(id=2),
        _require_manager=lambda org: None,
        get_run_evidence_access=lambda org, rid: run.copy(),
    )

    async def loader(row):
        return "summary"

    def fail(**kwargs):
        raise AcceptanceStoreError(reason, "storage precondition")

    service = SceneAcceptanceService(scenes, repo, loader)

    async def exercise():
        preview = await service.review("o", "r")
        monkeypatch.setattr(repo, "decide", fail)
        with pytest.raises(SceneError) as err:
            await service.decide(
                "o",
                "r",
                decision="accepted",
                expected_evidence_sha256=preview["evidence_sha256"],
                expected_snapshot_sha256=preview["snapshot_sha256"],
            )
        assert err.value.status == status
        assert isinstance(err.value.__cause__, AcceptanceStoreError)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "reviewer,mutation",
    [
        (2, "UPDATE users SET disabled=1 WHERE id=2"),
        (2, "DELETE FROM organization_members WHERE user_id=2"),
        (4, "UPDATE users SET role='user' WHERE id=4"),
    ],
)
def test_evidence_read_rechecks_identity_after_await(setup, reviewer, mutation):
    db, repo, _, run = setup
    scenes = SimpleNamespace(
        actor=SimpleNamespace(id=reviewer),
        _require_manager=lambda org: None,
        get_run_evidence_access=lambda org, rid: run.copy(),
    )

    async def loader(row):
        with db.transaction() as conn:
            conn.execute(mutation)
        return "sensitive summary"

    service = SceneAcceptanceService(scenes, repo, loader)
    with pytest.raises(SceneError) as err:
        asyncio.run(service.review("o", "r"))
    assert err.value.status == 403


def test_repo_has_no_scene_service_dependency():
    import ast

    import octop.infra.db.repos.scene_acceptance as module

    tree = ast.parse(Path(module.__file__).read_text())
    imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not any(name and name.startswith("octop.infra.scenes") for name in imports)
