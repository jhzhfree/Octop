"""Offline SQLite progress aggregation, without a model or external service."""

import pytest

from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.organization_progress import OrganizationProgressRepo, ProgressAccessError
from octop.infra.scenes.progress import OrganizationProgressService


@pytest.fixture
def setup(tmp_path):
    db = SqlitePool(tmp_path / "progress.db")
    with db.connect() as c:
        c.executescript("""
        CREATE TABLE users(id INTEGER PRIMARY KEY,role TEXT,disabled INTEGER);
        CREATE TABLE organization_members(organization_id TEXT,user_id INTEGER,role TEXT);
        CREATE TABLE enterprise_scene_runs(id INTEGER PRIMARY KEY,run_id TEXT UNIQUE,
          organization_id TEXT,status TEXT,plan_id TEXT,created_at INTEGER);
        CREATE INDEX idx_progress_org ON enterprise_scene_runs(organization_id,created_at);
        CREATE TABLE scene_run_acceptances(run_id TEXT PRIMARY KEY,organization_id TEXT,decision TEXT);
        INSERT INTO users VALUES(1,'user',0),(2,'user',0),(3,'admin',0);
        INSERT INTO organization_members VALUES('o',1,'manager'),('o',2,'member');
        INSERT INTO enterprise_scene_runs VALUES
          (1,'r1','o','succeeded','p',100),(2,'r2','o','succeeded','p',101),
          (3,'r3','o','succeeded','p',102),(4,'r4','o','queued','p',103),
          (5,'r5','o','failed',NULL,104),(6,'r6','o','awaiting_approval',NULL,105),
          (7,'r7','o','rejected',NULL,106),(8,'other','other','succeeded',NULL,103),
          (9,'old','o','succeeded',NULL,99),(10,'end','o','succeeded',NULL,107);
        INSERT INTO scene_run_acceptances VALUES('r1','o','accepted'),('r2','o','changes_requested');
        """)
    repo = OrganizationProgressRepo(db)
    yield db, repo, OrganizationProgressService(repo, 1)
    db.close()


def test_counts_keep_execution_and_delivery_separate(setup):
    db, _, service = setup
    result = service.inspect("o", start=100, end=107)
    assert result["denominator"] == 7
    assert result["counts"]["execution"] == {
        "awaiting_approval": 1,
        "rejected": 1,
        "queued": 1,
        "succeeded": 3,
        "failed": 1,
    }
    assert result["counts"]["delivery"] == {
        "unreviewed_success": 1,
        "accepted": 1,
        "changes_requested": 1,
    }
    assert result["unit"] == "run"  # Three executions of p are not three completed plans.
    assert result["execution_authorized"] is False
    assert result["external_systems_verified"] is False
    assert result["inconsistent_records"] == 0
    assert "plan_id" not in str(result) and "reviewer" not in str(result)
    with db.connect() as c:
        assert (
            c.execute("SELECT status FROM enterprise_scene_runs WHERE run_id='r4'").fetchone()[0]
            == "queued"
        )


@pytest.mark.parametrize("org", ["o", "other", "nonexistent"])
def test_member_forbidden_same_error(setup, org):
    _, repo, _ = setup
    with pytest.raises(ProgressAccessError, match="organization manager access required"):
        OrganizationProgressService(repo, 2).inspect(org, start=100, end=107)


def test_revoked_and_disabled_identity_from_database(setup):
    db, _, service = setup
    with db.connect() as c:
        c.execute("UPDATE users SET disabled=1 WHERE id=1")
    with pytest.raises(ProgressAccessError):
        service.inspect("o", start=100, end=107)


def test_manager_cannot_query_other_org(setup):
    _, _, service = setup
    with pytest.raises(ProgressAccessError):
        service.inspect("other", start=100, end=107)


def test_admin_counts_only_requested_org(setup):
    _, repo, _ = setup
    result = OrganizationProgressService(repo, 3).inspect("other", start=100, end=107)
    assert result["denominator"] == 1
    assert result["counts"]["delivery"]["accepted"] == 0


def test_wrong_org_acceptance_does_not_affect_counts(setup):
    db, _, service = setup
    with db.connect() as c:
        c.execute("UPDATE scene_run_acceptances SET organization_id='other' WHERE run_id='r1'")
    assert service.inspect("o", start=100, end=107)["counts"]["delivery"]["accepted"] == 0


def test_limit_fails_closed_without_partial_counts(setup, monkeypatch):
    _, repo, service = setup
    monkeypatch.setattr(repo, "MAX_RUNS", 3)
    result = service.inspect("o", start=100, end=107)
    assert result["limit_exceeded"] is True
    assert result["counts"] is None and result["denominator"] is None


@pytest.mark.parametrize("start,end", [(0, 0), (10, 9), (-1, 1), (True, 10), (0, 90 * 86400 + 1)])
def test_window_validation(setup, start, end):
    with pytest.raises(ValueError):
        setup[2].inspect("o", start=start, end=end)


def test_zero_window_has_zero_counts(setup):
    result = setup[2].inspect("o", start=200, end=201)
    assert result["denominator"] == 0 and result["limit_exceeded"] is False


def test_reports_inconsistent_acceptance_not_as_success(setup):
    db, _, service = setup
    with db.connect() as c:
        c.execute("INSERT INTO scene_run_acceptances VALUES('r5','o','accepted')")
    result = service.inspect("o", start=100, end=107)
    assert result["inconsistent_records"] == 1
    assert result["counts"]["delivery"]["accepted"] == 1


def test_revocation_during_query_rejected(setup, monkeypatch):
    db, repo, service = setup
    original = repo._authorize
    calls = 0

    def authorize(conn, organization_id, user_id):
        nonlocal calls
        calls += 1
        if calls == 2:
            conn.execute("DELETE FROM organization_members WHERE user_id=1")
        original(conn, organization_id, user_id)

    monkeypatch.setattr(repo, "_authorize", authorize)
    with pytest.raises(ProgressAccessError):
        service.inspect("o", start=100, end=107)


def test_unknown_status_counted_not_silently_lost(setup):
    db, _, service = setup
    with db.connect() as c:
        c.execute("UPDATE enterprise_scene_runs SET status='unexpected' WHERE run_id='r1'")
    result = service.inspect("o", start=100, end=107)
    assert result["inconsistent_records"] == 1
    assert result["denominator"] == 7


def test_http_contract_and_no_evidence(setup):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from octop.api.deps import current_user, get_server
    from octop.api.routers.organization_progress import router

    db, _, _ = setup
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=1)
    app.dependency_overrides[get_server] = lambda: SimpleNamespace(services=SimpleNamespace(db=db))
    with TestClient(app) as client:
        response = client.get("/organizations/o/progress?start=100&end=107")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        data = response.json()
        assert data["as_of"] > 0 and data["persisted_state_may_lag"] is True
        assert data["denominator"] == 7
        assert "evidence" not in str(data) and "observed_at" not in data
        denied = client.get("/organizations/other/progress?start=100&end=107")
        assert denied.status_code == 403
        assert denied.headers["cache-control"] == "no-store"
        assert client.get("/organizations/o/progress?start=100&end=99").status_code == 422
        assert client.get("/organizations/o/progress?start=0&end=999999999").status_code == 422
        assert client.get("/organizations/o/progress?start=no&end=107").status_code == 422
        assert (
            client.get(f"/organizations/o/progress?start={10**30}&end={10**30 + 1}").status_code
            == 422
        )
        app.dependency_overrides[get_server] = lambda: SimpleNamespace(services=None)
        assert client.get("/organizations/o/progress?start=100&end=107").status_code == 503


def test_huge_timestamp_rejected_before_sql(setup):
    _, _, service = setup
    with pytest.raises(ValueError):
        service.inspect("o", start=10**30, end=10**30 + 1)


def test_queue_categories_and_no_side_effects(setup):
    db, _, service = setup
    expected = {
        "awaiting_approval": ["r6"],
        "failed": ["r5"],
        "changes_requested": ["r2"],
        "unreviewed_success": ["end", "old", "r3"],
    }
    for category, ids in expected.items():
        data = service.queue("o", category=category)
        assert [item["run_id"] for item in data["items"]] == ids
        assert data["execution_authorized"] is False
        assert data["next_before"] is None
        assert all(
            set(item) == {"run_id", "status", "created_at", "decision"} for item in data["items"]
        )
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM enterprise_scene_runs").fetchone()[0] == 10


def test_queue_keyset_pagination_and_new_insert(setup):
    db, _, service = setup
    first = service.queue("o", category="unreviewed_success", limit=1)
    assert first["items"][0]["run_id"] == "end"
    with db.connect() as c:
        c.execute("INSERT INTO enterprise_scene_runs VALUES(11,'new','o','succeeded',NULL,110)")
    second = service.queue("o", category="unreviewed_success", limit=1, before=first["next_before"])
    third = service.queue("o", category="unreviewed_success", limit=1, before=second["next_before"])
    assert second["items"][0]["run_id"] == "old"
    assert third["items"][0]["run_id"] == "r3"
    assert third["next_before"] is None


def test_queue_org_and_current_membership(setup):
    db, repo, service = setup
    with pytest.raises(ProgressAccessError):
        service.queue("other", category="failed")
    with pytest.raises(ProgressAccessError):
        OrganizationProgressService(repo, 2).queue("o", category="failed")
    with db.connect() as c:
        c.execute("DELETE FROM organization_members WHERE user_id=1")
    with pytest.raises(ProgressAccessError):
        service.queue("o", category="failed")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"category": "accepted"},
        {"category": "failed", "limit": 51},
        {"category": "failed", "limit": True},
        {"category": "failed", "before": 0},
        {"category": "failed", "before": 2**63},
    ],
)
def test_queue_invalid_arguments(setup, kwargs):
    with pytest.raises(ValueError):
        setup[2].queue("o", **kwargs)


def test_queue_rechecks_disabled_admin_and_revocation(setup, monkeypatch):
    db, repo, service = setup
    with db.connect() as c:
        c.execute("UPDATE users SET disabled=1 WHERE id=3")
    with pytest.raises(ProgressAccessError):
        OrganizationProgressService(repo, 3).queue("o", category="failed")
    original = repo._authorize
    calls = 0

    def authorize(conn, org, user):
        nonlocal calls
        calls += 1
        if calls == 2:
            conn.execute("DELETE FROM organization_members WHERE user_id=1")
        original(conn, org, user)

    monkeypatch.setattr(repo, "_authorize", authorize)
    with pytest.raises(ProgressAccessError):
        service.queue("o", category="failed")


def test_queue_http_contract(setup):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from octop.api.deps import current_user, get_server
    from octop.api.routers.organization_progress import router

    db, _, _ = setup
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=1)
    app.dependency_overrides[get_server] = lambda: SimpleNamespace(services=SimpleNamespace(db=db))
    with TestClient(app) as client:
        path = "/organizations/o/run-queue?category=unreviewed_success"
        response = client.get(path + "&limit=1")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["items"][0]["run_id"] == "end"
        assert client.get(path + "&before=10&limit=1").json()["items"][0]["run_id"] == "old"
        assert client.get(path.replace("/o/", "/other/")).status_code == 403
        for suffix in ["&limit=51", "&before=0", "&before=9223372036854775808"]:
            assert client.get(path + suffix).status_code == 422
        assert client.get(path.replace("unreviewed_success", "accepted")).status_code == 422
        app.dependency_overrides[get_server] = lambda: SimpleNamespace(services=None)
        assert client.get(path).status_code == 503
