"""Synthetic SQLite tests; no model, connector or external dispatch."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from tests.unit.digital_workers.test_readiness import setup  # noqa: F401

from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.goal_handoffs import GoalHandoffRepo, HandoffError
from octop.infra.digital_workers.handoffs import GoalHandoffService
from octop.infra.digital_workers.service import DigitalWorkerError
from octop.infra.users.identity import User


@pytest.fixture
def handoff(setup):  # noqa: F811
    db, repos, _, goal, _ = setup
    with db.transaction() as c:
        c.execute(
            "INSERT INTO agents(agent_id,name,user_id,created_at,updated_at) VALUES('a2','Receiver',1,1,1)"
        )
        c.execute(
            "INSERT INTO organization_positions(position_id,organization_id,title,created_at,updated_at) VALUES('p2','o','Reviewer',1,1)"
        )
        c.execute(
            "INSERT INTO organization_assignments(assignment_id,position_id,subject_type,subject_id,created_at) VALUES('assignment2','p2','agent','a2',1)"
        )
        c.execute(
            "INSERT INTO digital_workers(worker_id,organization_id,position_id,agent_id,owner_user_id,name,status,created_at,updated_at) VALUES('w2','o','p2','a2',1,'Receiver','active',1,1)"
        )
    service = GoalHandoffService(repos, User(1, "manager", "user", None))
    body = {
        "request_id": "request-001",
        "expected_source_version": 2,
        "expected_target_version": 1,
        "target_worker_id": "w2",
        "reason": "Review evidence",
        "title": "Review",
        "description": "",
        "success_criteria": "Signed review",
    }
    return db, service, goal, body


def test_draft_trace_and_idempotent_retry(handoff):
    db, service, goal, body = handoff
    result = service.create_handoff("o", "w", goal, body)
    assert service.create_handoff("o", "w", goal, body) == result
    with db.connect() as c:
        target = c.execute(
            "SELECT * FROM digital_worker_goals WHERE goal_id=?", (result["target_goal_id"],)
        ).fetchone()
        assert (target["worker_id"], target["status"], target["created_by"]) == ("w2", "draft", 1)
        source = c.execute(
            "SELECT status,version FROM digital_worker_goals WHERE goal_id=?", (goal,)
        ).fetchone()
        assert tuple(source) == ("active", 3)
        assert (
            c.execute(
                "SELECT count(*) FROM audit_log WHERE action='digital_worker.goal_handoff'"
            ).fetchone()[0]
            == 1
        )
        assert c.execute("SELECT count(*) FROM enterprise_scene_runs").fetchone()[0] == 0
    assert result["source_snapshot"]["version"] == 2
    assert result["reason"] == "Review evidence"
    assert result["created_by"] == 1
    assert service.handoffs("o", goal, None)["items"][0]["target_goal_status"] == "draft"
    with pytest.raises(DigitalWorkerError) as error:
        service.create_handoff("o", "w", goal, {**body, "reason": "changed"})
    assert error.value.status == 409


@pytest.mark.parametrize(
    "sql,status",
    [
        ("DELETE FROM organization_members WHERE user_id=1", 403),
        ("UPDATE users SET disabled=1 WHERE id=1", 403),
        ("UPDATE agents SET user_id=2,is_shared=0 WHERE agent_id='a2'", 403),
        ("UPDATE agents SET enabled=0 WHERE agent_id='a2'", 403),
        ("UPDATE digital_workers SET status='paused' WHERE worker_id='w2'", 409),
        ("DELETE FROM organization_assignments WHERE subject_id='a2'", 409),
        ("UPDATE digital_worker_goals SET version=version+1", 409),
        ("UPDATE digital_workers SET version=version+1 WHERE worker_id='w2'", 409),
        ("UPDATE digital_workers SET organization_id='other' WHERE worker_id='w2'", 404),
    ],
)
def test_current_authority_and_versions(handoff, sql, status):
    db, service, goal, body = handoff
    with db.transaction() as c:
        c.execute(sql)
    with pytest.raises(DigitalWorkerError) as error:
        service.create_handoff("o", "w", goal, body)
    assert error.value.status == status
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM goal_handoffs").fetchone()[0] == 0


def test_replay_and_read_fail_after_revocation(handoff):
    db, service, goal, body = handoff
    service.create_handoff("o", "w", goal, body)
    with db.transaction() as c:
        c.execute("DELETE FROM organization_members WHERE user_id=1")
    for action in (
        lambda: service.create_handoff("o", "w", goal, body),
        lambda: service.handoffs("o", None, None),
    ):
        with pytest.raises(DigitalWorkerError) as error:
            action()
        assert error.value.status == 403


def test_audit_failure_rolls_back_every_write(handoff):
    db, service, goal, body = handoff
    with db.transaction() as c:
        c.execute(
            "CREATE TRIGGER fail_handoff BEFORE INSERT ON audit_log WHEN NEW.action='digital_worker.goal_handoff' BEGIN SELECT RAISE(ABORT,'injected'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        service.create_handoff("o", "w", goal, body)
    with db.transaction() as c:
        assert c.execute("SELECT count(*) FROM goal_handoffs").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM digital_worker_goals").fetchone()[0] == 1
        assert (
            c.execute(
                "SELECT version FROM digital_worker_goals WHERE goal_id=?", (goal,)
            ).fetchone()[0]
            == 2
        )
        c.execute("DROP TRIGGER fail_handoff")
    assert service.create_handoff("o", "w", goal, body)["target_worker_id"] == "w2"


@pytest.mark.parametrize("same_key", [True, False])
def test_cross_connection_concurrency(handoff, same_key):
    db, _, goal, body = handoff
    with db.connect() as c:
        path = c.execute("PRAGMA database_list").fetchone()[2]
    barrier = Barrier(2)

    def create(index):
        other = SqlitePool(path)
        try:
            barrier.wait(timeout=10)
            request = {**body, "request_id": body["request_id"] if same_key else f"request-{index}"}
            try:
                return GoalHandoffRepo(other).create("o", "w", goal, 1, request)["handoff_id"]
            except HandoffError as exc:
                return exc.reason
        finally:
            other.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create, range(2)))
    if same_key:
        assert results[0] == results[1] != "conflict"
    else:
        assert results.count("conflict") == 1
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM goal_handoffs").fetchone()[0] == 1


def test_history_retained_with_current_status_and_missing_target(handoff):
    db, service, goal, body = handoff
    result = service.create_handoff("o", "w", goal, body)
    with db.transaction() as c:
        c.execute(
            "UPDATE digital_worker_goals SET status='cancelled' WHERE goal_id=?",
            (result["target_goal_id"],),
        )
    assert service.handoffs("o", None, None)["items"][0]["target_goal_status"] == "cancelled"
    assert service.create_handoff("o", "w", goal, body)["target_goal_status"] == "cancelled"
    with db.transaction() as c:
        c.execute("DELETE FROM digital_worker_goals WHERE goal_id=?", (result["target_goal_id"],))
    row = service.handoffs("o", None, None)["items"][0]
    assert row["target_goal_status"] is None
    assert row["target_snapshot"]["name"] == "Receiver"
    assert service.handoffs("other", None, None)["items"] == []


def test_draft_delete_preserves_handoff_history(handoff):
    _, service, goal, body = handoff
    result = service.create_handoff("o", "w", goal, body)
    service.delete_goal("o", "w2", result["target_goal_id"], expected_version=1)
    row = service.handoffs("o", None, None)["items"][0]
    assert row["target_goal_status"] is None
    assert row["source_goal_id"] == goal


def test_unknown_database_creation_is_explicitly_unavailable(handoff, monkeypatch):
    db, service, goal, body = handoff
    monkeypatch.setattr(type(db), "dialect", property(lambda self: "unsupported"))
    with pytest.raises(DigitalWorkerError) as error:
        service.create_handoff("o", "w", goal, body)
    assert error.value.status == 503


def test_non_manager_cannot_create_and_same_position_rejected(handoff):
    _, service, goal, body = handoff
    member = GoalHandoffService(service.repos, User(2, "member", "user", None))
    with pytest.raises(DigitalWorkerError) as error:
        member.create_handoff("o", "w", goal, body)
    assert error.value.status == 403
    with pytest.raises(DigitalWorkerError) as error:
        service.create_handoff("o", "w", goal, {**body, "target_worker_id": "w"})
    assert error.value.status == 409


def test_history_pages_do_not_repeat_records(handoff):
    _, service, goal, body = handoff
    for index in range(21):
        service.create_handoff(
            "o",
            "w",
            goal,
            {**body, "request_id": f"request-{index}", "expected_source_version": 2 + index},
        )
    first = service.handoffs("o", goal, None)
    second = service.handoffs("o", goal, first["next_before"])
    assert len(first["items"]) == 20
    assert len(second["items"]) == 1
    assert second["next_before"] is None
    assert len({item["handoff_id"] for item in first["items"] + second["items"]}) == 21
