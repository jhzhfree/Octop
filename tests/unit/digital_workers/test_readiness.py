from __future__ import annotations

from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.services import RepoBundle
from octop.infra.digital_workers.readiness import WorkerPlanReadinessService
from octop.infra.digital_workers.service import DigitalWorkerError
from octop.infra.users.identity import User


@pytest.fixture
def setup(tmp_path: Path):
    db = SqlitePool(tmp_path / "readiness.db")
    run_migrations(db)
    repos = RepoBundle.from_pool(db)
    with db.transaction() as c:
        for username in ("manager", "member", "outsider"):
            c.execute(
                "INSERT INTO users(username,password_hash,role,created_at) VALUES(?,'x','user',1)",
                (username,),
            )
        c.execute(
            "INSERT INTO agents(agent_id,name,user_id,created_at,updated_at) VALUES('a','Agent',1,1,1)"
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
        c.execute(
            "INSERT INTO organization_members(organization_id,user_id,role,created_at) VALUES('o',2,'member',1)"
        )
        c.execute(
            "INSERT INTO organization_positions(position_id,organization_id,title,created_at,updated_at) VALUES('p','o','Analyst',1,1)"
        )
        c.execute(
            "INSERT INTO organization_assignments(assignment_id,position_id,subject_type,subject_id,created_at) VALUES('assignment','p','agent','a',1)"
        )
        c.execute(
            "INSERT INTO digital_workers(worker_id,organization_id,position_id,agent_id,owner_user_id,name,status,created_at,updated_at) VALUES('w','o','p','a',1,'Worker','active',1,1)"
        )
    goal = repos.digital_worker_repo.create_goal(
        worker_id="w",
        title="Goal",
        description="",
        success_criteria="Evidence",
        period_start=None,
        period_end=None,
        created_by=1,
    )
    repos.digital_worker_repo.update_goal(goal["goal_id"], {"status": "active"})
    plan = repos.digital_worker_repo.create_plan(
        goal_id=goal["goal_id"],
        title="Plan",
        approach="Read approved sources",
        steps=[{"action": "Read", "expected_output": "Report"}],
        created_by=1,
    )
    repos.digital_worker_repo.update_plan(plan["plan_id"], {"status": "ready"}, expected_version=1)
    repos.enterprise_scene_repo.create(
        scene_id="s",
        organization_id="o",
        position_id="p",
        agent_id="a",
        owner_user_id=1,
        name="Scene",
        prompt="Secret prompt not for this report",
        mcp_servers=[],
    )
    service = WorkerPlanReadinessService(repos, User(1, "manager", "user", None))
    yield db, repos, service, goal["goal_id"], plan["plan_id"]
    db.close()


def inspect(setup, **overrides):
    _, _, service, goal_id, plan_id = setup
    args = {"expected_worker_version": 1, "expected_plan_version": 2}
    args.update(overrides)
    return service.inspect("o", "w", goal_id, plan_id, **args)


def test_current_local_checks_never_grant_execution_and_do_not_write(setup):
    db, _, _, _, _ = setup
    with db.connect() as conn:
        before = conn.total_changes
    report = inspect(setup)
    statuses = {c.code: c.status for c in report.checks}
    assert list(statuses.values()).count("satisfied") == 7
    assert statuses["knowledge_user_acl"] == statuses["model_budget"] == "unknown"
    assert statuses["runtime_approval"] == "unknown"
    assert report.execution_authorized is False
    assert report.scope == "local_observation_not_atomic"
    assert "Secret" not in report.model_dump_json()
    with db.connect() as conn:
        assert conn.total_changes == before


@pytest.mark.parametrize("user_id", [2, 3])
def test_non_manager_rejected_before_plan_existence(setup, user_id):
    _, repos, _, _, _ = setup
    service = WorkerPlanReadinessService(repos, User(user_id, "user", "user", None))
    with pytest.raises(DigitalWorkerError) as error:
        service.inspect(
            "o", "absent", "absent", "absent", expected_worker_version=1, expected_plan_version=1
        )
    assert error.value.status == 403


def test_cross_organization_worker_and_wrong_goal_plan_rejected(setup):
    _, _, service, goal, plan = setup
    for org, worker, goal_id, plan_id in [
        ("other", "w", goal, plan),
        ("o", "w", "wrong", plan),
        ("o", "w", goal, "wrong"),
    ]:
        with pytest.raises(DigitalWorkerError) as error:
            service.inspect(
                org, worker, goal_id, plan_id, expected_worker_version=1, expected_plan_version=2
            )
        assert error.value.status == 404


@pytest.mark.parametrize("versions", [{"expected_worker_version": 2}, {"expected_plan_version": 1}])
def test_expected_versions_required(setup, versions):
    with pytest.raises(DigitalWorkerError) as error:
        inspect(setup, **versions)
    assert error.value.status == 409


@pytest.mark.parametrize(
    "sql,check",
    [
        ("UPDATE digital_workers SET status='paused'", "worker_active"),
        ("UPDATE digital_worker_goals SET status='draft'", "goal_active"),
        ("UPDATE digital_worker_plans SET status='draft'", "plan_ready"),
        ("UPDATE digital_worker_plans SET steps_json='[]'", "plan_content_complete"),
        ("UPDATE agents SET enabled=0", "agent_enabled"),
        ("DELETE FROM organization_assignments", "agent_appointed"),
        ("UPDATE enterprise_scenes SET enabled=0", "matching_scene_enabled"),
    ],
)
def test_observed_failures_are_not_ready(setup, sql, check):
    db, _, _, _, _ = setup
    with db.transaction() as conn:
        conn.execute(sql)
    report = inspect(setup)
    assert next(c.status for c in report.checks if c.code == check) == "unmet"
    assert not report.execution_authorized


def test_agent_access_revoked_is_forbidden(setup):
    db, _, _, _, _ = setup
    with db.transaction() as conn:
        conn.execute("UPDATE agents SET user_id=2,is_shared=0")
    with pytest.raises(DigitalWorkerError) as error:
        inspect(setup)
    assert error.value.status == 403


@pytest.mark.parametrize(
    "sql,expected",
    [
        ("UPDATE digital_worker_plans SET version=version+1", 409),
        ("DELETE FROM organization_assignments", 409),
        ("UPDATE digital_worker_goals SET success_criteria='changed'", 409),
        ("DELETE FROM organization_members WHERE user_id=1", 403),
    ],
)
def test_change_during_observation_rejected(setup, monkeypatch, sql, expected):
    db, _, service, _, _ = setup
    original = service._snapshot
    count = 0

    def snapshot(*args):
        nonlocal count
        count += 1
        if count == 2:
            with db.transaction() as conn:
                conn.execute(sql)
        return original(*args)

    monkeypatch.setattr(service, "_snapshot", snapshot)
    with pytest.raises(DigitalWorkerError) as error:
        inspect(setup)
    assert error.value.status == expected


def test_scene_lookup_is_scoped_and_does_not_load_full_list(setup, monkeypatch):
    db, repos, _, _, _ = setup
    with db.transaction() as conn:
        conn.execute("UPDATE enterprise_scenes SET organization_id='other'")

    def forbidden(*args):
        raise AssertionError("must not scan and decode all organization scenes")

    monkeypatch.setattr(repos.enterprise_scene_repo, "list_for_org", forbidden)
    report = inspect(setup)
    assert next(c.status for c in report.checks if c.code == "matching_scene_enabled") == "unmet"


def test_assignment_lookup_is_scoped_and_bounded(setup, monkeypatch):
    _, repos, _, _, _ = setup

    def forbidden(*args):
        raise AssertionError("must not materialize full organization assignments")

    monkeypatch.setattr(repos.organization_repo, "list_assignments", forbidden)
    report = inspect(setup)
    assert next(c.status for c in report.checks if c.code == "agent_appointed") == "satisfied"
    assert repos.organization_repo.has_agent_assignment("o", "p", "a")
    assert not repos.organization_repo.has_agent_assignment("other", "p", "a")
    assert not repos.organization_repo.has_agent_assignment("o", "p", "other-agent")
    assert not repos.organization_repo.has_agent_assignment("o", "other-position", "a")
