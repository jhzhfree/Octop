"""Offline HTTP intent persistence. No real Mattermost identity is asserted."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from tests.unit.digital_workers.test_mattermost_readiness import offline  # noqa: F401
from tests.unit.digital_workers.test_mattermost_readiness import setup as intent_setup

from octop.api.deps import current_user, get_server
from octop.api.routers.digital_workers import router
from octop.infra.digital_workers.mattermost import MattermostIntentRequest, MattermostIntentService
from octop.infra.digital_workers.service import DigitalWorkerError
from octop.infra.users.identity import User

setup = intent_setup


def body(**changes):
    return MattermostIntentRequest.model_validate(
        {
            "expected_worker_version": 1,
            "expected_revision": 0,
            "instance_key": "enterprise-mm",
            "team_id": "t" * 26,
            "channel_id": "c" * 26,
            "bot_id": "b" * 26,
            **changes,
        }
    )


def service(setup, actor=1):
    return MattermostIntentService(setup[1], User(actor, "ignored", "user", None))


def test_persist_revoke_restore_and_no_aba(setup):
    svc = service(setup)
    assert svc.get_intent("o", "w", expected_worker_version=1) is None
    first = svc.save_intent("o", "w", body())
    assert first.revision == 1 and first.updated_by == 1
    assert first.identity_status == "unverified" and not first.execution_authorized
    assert not first.automatic_send_allowed and not first.human_initiated_send_allowed
    assert service(setup).get_intent("o", "w", expected_worker_version=1) == first
    with pytest.raises(DigitalWorkerError):
        svc.save_intent("o", "w", body())
    revoked = svc.save_intent("o", "w", body(expected_revision=1, status="revoked"))
    assert revoked.status == "revoked" and revoked.revision == 2
    with pytest.raises(DigitalWorkerError):
        svc.save_intent("o", "w", body(expected_revision=1))
    restored = svc.save_intent("o", "w", body(expected_revision=2))
    assert restored.revision == 3 and not restored.connected
    with setup[0].connect() as c:
        rows = c.execute("SELECT actor, payload FROM audit_log").fetchall()
    assert len(rows) == 3
    assert all(
        row["actor"] == "1" and json.loads(row["payload"])["identity_status"] == "unverified"
        for row in rows
    )


@pytest.mark.parametrize("change", ["demote", "remove", "unshare", "worker_version", "disabled"])
def test_atomic_write_rechecks_access_and_version(setup, monkeypatch, change):
    svc = service(setup)
    svc.save_intent("o", "w", body())
    authorize = svc._authorize

    def racing_authorize(*args, **kwargs):
        worker = authorize(*args, **kwargs)
        with setup[0].transaction() as c:
            if change == "demote":
                c.execute(
                    "UPDATE organization_members SET role='member' WHERE organization_id='o' AND user_id=1"
                )
            elif change == "remove":
                c.execute(
                    "DELETE FROM organization_members WHERE organization_id='o' AND user_id=1"
                )
            elif change == "unshare":
                c.execute("UPDATE agents SET user_id=2,is_shared=0 WHERE agent_id='a'")
            elif change == "disabled":
                c.execute("UPDATE users SET disabled=1 WHERE id=1")
            else:
                c.execute("UPDATE digital_workers SET version=version+1 WHERE worker_id='w'")
        return worker

    monkeypatch.setattr(svc, "_authorize", racing_authorize)
    with pytest.raises(DigitalWorkerError) as error:
        svc.save_intent("o", "w", body(expected_revision=1))
    assert error.value.status == 409
    assert setup[1].mattermost_intent_repo.get("o", "w")["revision"] == 1
    with setup[0].connect() as c:
        assert c.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 1


@pytest.mark.parametrize("actor,org,status", [(2, "o", 403), (3, "o", 403), (1, "other", 404)])
def test_scope_and_reader_revoke(setup, actor, org, status):
    service(setup).save_intent("o", "w", body())
    for op in (
        lambda: service(setup, actor).save_intent(org, "w", body(expected_revision=1)),
        lambda: service(setup, actor).get_intent(org, "w", expected_worker_version=1),
    ):
        with pytest.raises(DigitalWorkerError) as error:
            op()
        assert error.value.status == status


def test_parent_deletion_cascades_intent_but_retains_audit(setup):
    service(setup).save_intent("o", "w", body())
    with setup[0].transaction() as c:
        c.execute("DELETE FROM digital_workers WHERE worker_id='w'")
    assert setup[1].mattermost_intent_repo.get("o", "w") is None
    with setup[0].connect() as c:
        assert c.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 1


async def test_http_contract_is_credential_free_and_closed(setup, caplog):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[current_user] = lambda: User(1, "manager", "user", None)
    app.dependency_overrides[get_server] = lambda: SimpleNamespace(
        services=SimpleNamespace(repos=setup[1])
    )
    url = "/organizations/o/digital-workers/w/mattermost-intent"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for field in (
            "token",
            "credentials",
            "human_actor_id",
            "identity_status",
            "automatic_send_allowed",
        ):
            response = await client.put(url, json={**body().model_dump(), field: "SECRET_SENTINEL"})
            assert response.status_code == 422 and "SECRET_SENTINEL" not in response.text
        for invalid in (
            "https://user:SECRET_SENTINEL@mm.test",
            "http://localhost",
            "foo/path?token=SECRET_SENTINEL",
        ):
            response = await client.put(url, json={**body().model_dump(), "instance_key": invalid})
            assert response.status_code == 422 and "SECRET_SENTINEL" not in response.text
        response = await client.put(url, content='{"token":"SECRET_SENTINEL",')
        assert response.status_code == 422 and "SECRET_SENTINEL" not in response.text
        response = await client.put(url, json=body().model_dump())
        assert response.status_code == 200 and response.json()["identity_status"] == "unverified"
        response = await client.get(url, params={"expected_worker_version": 1})
        assert response.status_code == 200 and response.json()["revision"] == 1
        assert app.openapi()["paths"][
            url.replace("/o/", "/{organization_id}/").replace("/w/", "/{worker_id}/")
        ]["put"]["responses"]["200"]
    assert "SECRET_SENTINEL" not in caplog.text


def test_audit_failure_rolls_back(setup):
    import sqlite3

    with setup[0].transaction() as c:
        c.execute(
            "CREATE TRIGGER deny_intent_audit BEFORE INSERT ON audit_log BEGIN SELECT RAISE(ABORT,'audit unavailable'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        service(setup).save_intent("o", "w", body())
    assert setup[1].mattermost_intent_repo.get("o", "w") is None


def test_two_connections_same_revision_one_winner(setup):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from octop.infra.db.pool import SqlitePool
    from octop.infra.db.services import RepoBundle

    service(setup).save_intent("o", "w", body())
    second = SqlitePool(setup[0].path)
    barrier = Barrier(2)

    def write(repos):
        barrier.wait()
        return repos.mattermost_intent_repo.save(
            "o", "w", actor_id=1, **body(expected_revision=1).model_dump()
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(write, repos) for repos in (setup[1], RepoBundle.from_pool(second))
            ]
            results = [future.result(timeout=10) for future in futures]
        assert sum(row is not None for row in results) == 1
        assert setup[1].mattermost_intent_repo.get("o", "w")["revision"] == 2
    finally:
        second.close()


def test_historical_editor_does_not_block_account_deletion(setup):
    with setup[0].transaction() as c:
        c.execute(
            "UPDATE organization_members SET role='manager' WHERE organization_id='o' AND user_id=2"
        )
        c.execute("UPDATE agents SET is_shared=1 WHERE agent_id='a'")
    service(setup, 2).save_intent("o", "w", body())
    with setup[0].transaction() as c:
        c.execute("DELETE FROM organization_members WHERE user_id=2")
        c.execute("DELETE FROM users WHERE id=2")
    assert service(setup).get_intent("o", "w", expected_worker_version=1).updated_by == 2
