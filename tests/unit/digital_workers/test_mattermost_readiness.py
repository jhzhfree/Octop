"""Offline authenticated local protocol checks, never Mattermost calls."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from octop.api.deps import current_user, get_server
from octop.api.routers.digital_workers import router
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.services import RepoBundle
from octop.infra.digital_workers.mattermost import MattermostReadinessService
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
    yield db, repos
    db.close()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network is forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def inspect(setup, actor=1, organization="o", version=1):
    _, repos = setup
    return MattermostReadinessService(
        repos, User(actor, "untrusted display name", "user", None)
    ).inspect(organization, "w", expected_worker_version=version)


def test_local_check_is_audited_without_binding_or_permission(setup):
    report = inspect(setup)
    assert report.human_actor_id == 1
    assert report.worker_id == "w" and report.agent_id == "a"
    assert report.identity_status == "unbound" and report.mattermost_bot_id is None
    assert not report.connected and not report.execution_authorized
    assert not report.human_initiated_send_allowed and not report.automatic_send_allowed
    db, _ = setup
    with db.connect() as c:
        row = c.execute("SELECT actor,action,target,payload FROM audit_log").fetchone()
    assert row["actor"] == "1" and row["target"] == "w"
    assert row["action"] == "digital_worker.mattermost_readiness"
    assert json.loads(row["payload"]) == report.model_dump()
    assert "untrusted display name" not in row["payload"]


@pytest.mark.parametrize(
    "actor,organization,version,status",
    [
        (2, "o", 1, 403),
        (3, "o", 1, 403),
        (3, "missing", 1, 403),
        (1, "other", 1, 404),
        (1, "o", 2, 409),
    ],
)
def test_rejected_checks_do_not_create_success_audit(setup, actor, organization, version, status):
    with pytest.raises(DigitalWorkerError) as caught:
        inspect(setup, actor, organization, version)
    assert caught.value.status == status
    with setup[0].connect() as c:
        assert c.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0


def test_org_manager_without_agent_access_is_denied(setup):
    with setup[0].transaction() as c:
        c.execute("UPDATE agents SET user_id=2 WHERE agent_id='a'")
    with pytest.raises(DigitalWorkerError) as caught:
        inspect(setup)
    assert caught.value.status == 403


async def test_http_rejects_spoofed_identity_and_credentials(setup, caplog):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[current_user] = lambda: User(1, "manager", "user", None)
    app.dependency_overrides[get_server] = lambda: SimpleNamespace(
        services=SimpleNamespace(repos=setup[1])
    )
    url = "/organizations/o/digital-workers/w/mattermost-readiness"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for field, value in {
            "token": "SECRET_SENTINEL_DO_NOT_ECHO",
            "human_actor_id": 2,
            "mattermost_bot_id": "claimed-id",
            "is_bot": True,
            "operation_mode": "automatic",
            "run_id": "claimed-run",
        }.items():
            response = await client.post(url, json={"expected_worker_version": 1, field: value})
            assert response.status_code == 422
            assert "SECRET_SENTINEL_DO_NOT_ECHO" not in response.text
        response = await client.post(url, json={"expected_worker_version": "1"})
        assert response.status_code == 422
        response = await client.post(url, json={"expected_worker_version": 1})
        assert response.status_code == 200
        assert response.json()["human_actor_id"] == 1
        assert response.json()["connected"] is False
    with setup[0].connect() as c:
        assert c.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 1

    assert "SECRET_SENTINEL_DO_NOT_ECHO" not in caplog.text


@pytest.mark.parametrize("change", ["demote", "revoke", "unshare"])
def test_each_check_rechecks_current_access(setup, change):
    db, _ = setup
    if change == "unshare":
        with db.transaction() as c:
            c.execute("UPDATE agents SET user_id=2,is_shared=1 WHERE agent_id='a'")
    inspect(setup)
    with db.transaction() as c:
        if change == "demote":
            c.execute(
                "UPDATE organization_members SET role='member' WHERE user_id=1 AND organization_id='o'"
            )
        elif change == "revoke":
            c.execute("DELETE FROM organization_members WHERE user_id=1 AND organization_id='o'")
        else:
            c.execute("UPDATE agents SET is_shared=0 WHERE agent_id='a'")
    with pytest.raises(DigitalWorkerError) as caught:
        inspect(setup)
    assert caught.value.status == 403
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 1
