"""Real SQLite CAS and audit rollback; no server/model/network startup."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from tests.unit.digital_workers.test_readiness import setup as setup

from octop.api.deps import current_user, get_server
from octop.api.routers.digital_workers import router
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.digital_workers import DigitalWorkerRepo, GoalMutationError
from octop.infra.digital_workers.service import DigitalWorkerError, DigitalWorkerService
from octop.infra.users.identity import User


@pytest.fixture
def goal_env(setup):
    db, repos, _, goal_id, _ = setup
    service = DigitalWorkerService(repos, User(1, "manager", "user", None))
    return db, repos, service, goal_id


def audit_rows(db, goal_id):
    with db.connect() as conn:
        return conn.execute(
            "SELECT * FROM audit_log WHERE target=? AND action LIKE 'digital_worker.goal_%'",
            (goal_id,),
        ).fetchall()


@pytest.mark.parametrize("statuses", [("completed", "cancelled"), ("completed", "completed")])
def test_concurrent_terminal_writes_have_one_winner_across_connections(goal_env, statuses):
    db, repos, _, goal_id = goal_env
    snapshot = repos.digital_worker_repo.get_goal(goal_id)
    barrier = Barrier(2)

    def finish(status):
        pool = SqlitePool(db.path)
        try:
            repo = DigitalWorkerRepo(pool)
            barrier.wait(timeout=5)
            try:
                return repo.mutate_goal(
                    organization_id="o", expected=snapshot, actor_id=1, updates={"status": status}
                )["status"]
            except GoalMutationError as exc:
                return exc.reason
        finally:
            pool.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(finish, statuses))
    assert results.count("conflict") == 1
    winner = next(value for value in results if value != "conflict")
    assert repos.digital_worker_repo.get_goal(goal_id)["status"] == winner
    logs = audit_rows(db, goal_id)
    assert len(logs) == 1
    assert logs[0]["actor"] == "1"
    assert json.loads(logs[0]["payload"]) == {
        "organization_id": "o",
        "from_status": "active",
        "to_status": winner,
    }


@pytest.mark.parametrize("operation", ["edit", "delete", "complete"])
def test_service_race_returns_conflict_without_overwrite(goal_env, monkeypatch, operation):
    db, repos, service, goal_id = goal_env
    if operation != "complete":
        repos.digital_worker_repo.update_goal(goal_id, {"status": "draft"})
    original = repos.digital_worker_repo.mutate_goal

    def competing(**kwargs):
        original(
            organization_id="o",
            expected=kwargs["expected"],
            actor_id=1,
            updates={"status": "cancelled" if operation == "complete" else "active"},
        )
        return original(**kwargs)

    monkeypatch.setattr(repos.digital_worker_repo, "mutate_goal", competing)
    with pytest.raises(DigitalWorkerError) as caught:
        if operation == "delete":
            service.delete_goal("o", "w", goal_id)
        else:
            service.update_goal(
                "o",
                "w",
                goal_id,
                {"status": "completed"}
                if operation == "complete"
                else {"title": "Stale overwrite"},
            )
    assert caught.value.status == 409
    goal = repos.digital_worker_repo.get_goal(goal_id)
    assert goal["status"] == ("cancelled" if operation == "complete" else "active")
    assert goal["title"] == "Goal"
    assert len(audit_rows(db, goal_id)) == 1


def test_same_second_draft_edit_blocks_stale_activation(goal_env):
    db, repos, _, goal_id = goal_env
    repo = repos.digital_worker_repo
    repo.update_goal(goal_id, {"status": "draft"})
    old = repo.get_goal(goal_id)
    repo.mutate_goal(
        organization_id="o",
        expected=old,
        actor_id=1,
        updates={"success_criteria": "New evidence required"},
    )
    # Equal timestamps must not hide a changed validated field.
    with db.transaction() as conn:
        conn.execute(
            "UPDATE digital_worker_goals SET updated_at=? WHERE goal_id=?",
            (old["updated_at"], goal_id),
        )
    with pytest.raises(GoalMutationError, match="conflict"):
        repo.mutate_goal(
            organization_id="o", expected=old, actor_id=1, updates={"status": "active"}
        )
    assert repo.get_goal(goal_id)["status"] == "draft"


@pytest.mark.parametrize("delete", [False, True])
def test_audit_failure_rolls_back_goal_change(goal_env, delete):
    db, repos, service, goal_id = goal_env
    if delete:
        repos.digital_worker_repo.update_goal(goal_id, {"status": "draft"})
    before = repos.digital_worker_repo.get_goal(goal_id)
    with db.transaction() as conn:
        conn.execute(
            "CREATE TRIGGER fail_goal_audit BEFORE INSERT ON audit_log BEGIN SELECT RAISE(ABORT, 'fixture audit failure'); END"
        )
    with pytest.raises(Exception, match="fixture audit failure"):
        if delete:
            service.delete_goal("o", "w", goal_id)
        else:
            service.update_goal("o", "w", goal_id, {"status": "completed"})
    assert repos.digital_worker_repo.get_goal(goal_id) == before
    assert audit_rows(db, goal_id) == []


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE users SET disabled=1 WHERE id=1",
        "DELETE FROM organization_members WHERE user_id=1",
        "UPDATE organization_members SET role='member' WHERE user_id=1",
    ],
)
def test_revocation_after_service_validation_is_rejected(goal_env, monkeypatch, mutation):
    db, repos, service, goal_id = goal_env
    original = repos.digital_worker_repo.mutate_goal

    def revoked(**kwargs):
        with db.transaction() as conn:
            conn.execute(mutation)
        return original(**kwargs)

    monkeypatch.setattr(repos.digital_worker_repo, "mutate_goal", revoked)
    with pytest.raises(DigitalWorkerError) as caught:
        service.update_goal("o", "w", goal_id, {"status": "completed"})
    assert caught.value.status == 403
    assert repos.digital_worker_repo.get_goal(goal_id)["status"] == "active"
    assert audit_rows(db, goal_id) == []


def test_stale_admin_and_foreign_scope_never_write(goal_env):
    db, repos, service, goal_id = goal_env
    service.actor.role = "admin"
    with db.transaction() as conn:
        conn.execute("DELETE FROM organization_members WHERE user_id=1")
    with pytest.raises(DigitalWorkerError) as caught:
        service.update_goal("o", "w", goal_id, {"status": "completed"})
    assert caught.value.status == 403
    with pytest.raises(GoalMutationError, match="forbidden"):
        repos.digital_worker_repo.mutate_goal(
            organization_id="other",
            expected=repos.digital_worker_repo.get_goal(goal_id),
            actor_id=1,
            updates={"status": "completed"},
        )
    assert audit_rows(db, goal_id) == []


def test_normal_lifecycle_and_draft_deletion_preserve_existing_contract(goal_env):
    db, repos, service, goal_id = goal_env
    repos.digital_worker_repo.update_goal(goal_id, {"status": "draft"})
    edited = service.update_goal(
        "o",
        "w",
        goal_id,
        {"title": "Updated goal", "period_start": "2026-01-01", "period_end": None},
    )
    assert edited["title"] == "Updated goal"
    assert service.update_goal("o", "w", goal_id, {}) == edited
    assert service.update_goal("o", "w", goal_id, {"status": "active"})["status"] == "active"
    assert service.update_goal("o", "w", goal_id, {"status": "completed"})["status"] == "completed"
    with pytest.raises(DigitalWorkerError):
        service.update_goal("o", "w", goal_id, {"status": "cancelled"})
    draft = repos.digital_worker_repo.create_goal(
        worker_id="w",
        title="Delete draft",
        description="",
        success_criteria="Evidence",
        period_start=None,
        period_end=None,
        created_by=1,
    )
    service.delete_goal("o", "w", draft["goal_id"])
    assert repos.digital_worker_repo.get_goal(draft["goal_id"]) is None
    logs = audit_rows(db, draft["goal_id"])
    assert len(logs) == 1 and logs[0]["action"] == "digital_worker.goal_delete"


async def test_http_conflict_and_authenticated_actor_contract(goal_env, monkeypatch):
    db, repos, service, goal_id = goal_env
    app = FastAPI(docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.include_router(router, prefix="/api")
    app.dependency_overrides[current_user] = lambda: service.actor
    app.dependency_overrides[get_server] = lambda: SimpleNamespace(
        services=SimpleNamespace(repos=repos)
    )
    path = f"/api/organizations/o/digital-workers/w/goals/{goal_id}"
    original = repos.digital_worker_repo.mutate_goal

    def competing(**kwargs):
        original(
            organization_id="o",
            expected=kwargs["expected"],
            actor_id=1,
            updates={"status": "cancelled"},
        )
        return original(**kwargs)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        monkeypatch.setattr(repos.digital_worker_repo, "mutate_goal", competing)
        response = await client.patch(path, json={"status": "completed", "actor_id": 999})
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "DIGITAL_WORKER_GOAL_STATUS_INVALID"
        monkeypatch.setattr(repos.digital_worker_repo, "mutate_goal", original)
        repos.digital_worker_repo.update_goal(goal_id, {"status": "draft"})
        updated = await client.patch(path, json={"status": "active", "actor_id": 999})
        assert updated.status_code == 200
        assert updated.json()["status"] == "active"
        assert (await client.delete(path)).status_code == 409
        assert (await client.get("/api/docs")).status_code == 200
        schema = (await client.get("/api/openapi.json")).json()
        assert (
            "409"
            in schema["paths"][
                "/api/organizations/{organization_id}/digital-workers/{worker_id}/goals/{goal_id}"
            ]["patch"]["description"]
        )
        assert all(row["actor"] == "1" for row in audit_rows(db, goal_id))


def test_persisted_admin_can_mutate_without_membership(goal_env):
    db, repos, _, goal_id = goal_env
    with db.transaction() as conn:
        conn.execute("UPDATE users SET role='admin' WHERE id=3")
    service = DigitalWorkerService(repos, User(3, "outsider", "admin", None))
    assert service.update_goal("o", "w", goal_id, {"status": "completed"})["status"] == "completed"
    assert audit_rows(db, goal_id)[0]["actor"] == "3"
