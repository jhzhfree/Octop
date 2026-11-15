"""Persisted access-state reads, no runtime or external services."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from tests.unit.digital_workers.test_readiness import setup as setup

from octop.api.deps import current_user, get_server
from octop.api.routers.auth import router
from octop.infra.auth.access_state import AccessStateUnavailable, read_access_state


def test_current_persisted_scope_changes_revision_without_private_payload(setup):
    db, repos, _, _, _ = setup
    original = read_access_state(repos, 1)
    assert set(original) == {"revision", "user"}
    assert len(original["revision"]) == 64
    assert set(original["user"]) == {
        "id",
        "username",
        "display_name",
        "role",
        "permissions",
        "locale",
    }
    assert read_access_state(repos, 1) == original
    repos.user_repo.set_permissions(1, ["tasks"])
    permitted = read_access_state(repos, 1)
    assert permitted["revision"] != original["revision"]
    with db.transaction() as conn:
        conn.execute(
            "UPDATE organization_members SET role='manager' WHERE user_id=1 AND organization_id='other'"
        )
    changed_other_org = read_access_state(repos, 1)
    assert changed_other_org["revision"] != permitted["revision"]
    with db.transaction() as conn:
        conn.execute("DELETE FROM organization_members WHERE user_id=1 AND organization_id='o'")
    assert read_access_state(repos, 1)["revision"] != changed_other_org["revision"]


def test_unrelated_users_and_organization_content_do_not_trigger_remount(setup):
    db, repos, _, _, _ = setup
    original = read_access_state(repos, 1)
    with db.transaction() as conn:
        conn.execute("UPDATE organization_members SET role='manager' WHERE user_id=2")
        conn.execute("UPDATE organizations SET name='PRIVATE ORG NAME'")
    assert read_access_state(repos, 1) == original


@pytest.mark.parametrize(
    "mutation",
    ["UPDATE users SET disabled=1 WHERE id=1", "UPDATE users SET role='admin' WHERE id=1"],
)
def test_changed_account_during_read_fails_closed(setup, monkeypatch, mutation):
    db, repos, _, _, _ = setup
    original = repos.organization_repo.access_memberships

    def change(user_id):
        result = original(user_id)
        with db.transaction() as conn:
            conn.execute(mutation)
        return result

    monkeypatch.setattr(repos.organization_repo, "access_memberships", change)
    with pytest.raises(AccessStateUnavailable):
        read_access_state(repos, 1)


async def test_endpoint_ignores_cached_admin_and_client_claims_and_is_readonly(setup):
    db, repos, service, _, _ = setup
    service.actor.role = "admin"  # stale in-process identity must not govern response
    app = FastAPI()
    app.include_router(router, prefix="/api/auth")
    app.dependency_overrides[current_user] = lambda: service.actor
    app.dependency_overrides[get_server] = lambda: SimpleNamespace(
        services=SimpleNamespace(repos=repos)
    )
    with db.connect() as conn:
        before = conn.total_changes
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/auth/access-state?user_id=3&role=admin")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["user"]["role"] == "user"
        assert response.json()["user"]["id"] == 1
        assert (await client.post("/api/auth/access-state")).status_code == 405
        with db.connect() as conn:
            assert conn.total_changes == before
        with db.transaction() as conn:
            conn.execute("UPDATE users SET disabled=1 WHERE id=1")
        response = await client.get("/api/auth/access-state")
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"
        assert "revision" not in response.json()


async def test_changing_account_is_retryable_not_a_false_session_revocation(setup, monkeypatch):
    db, repos, service, _, _ = setup
    app = FastAPI()
    app.include_router(router, prefix="/api/auth")
    app.dependency_overrides[current_user] = lambda: service.actor
    app.dependency_overrides[get_server] = lambda: SimpleNamespace(
        services=SimpleNamespace(repos=repos)
    )
    original = repos.organization_repo.access_memberships

    def changed(user_id):
        result = original(user_id)
        repos.user_repo.set_permissions(user_id, ["tasks"])
        return result

    monkeypatch.setattr(repos.organization_repo, "access_memberships", changed)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/auth/access-state")
        assert response.status_code == 409
        assert response.headers["cache-control"] == "no-store"
        monkeypatch.setattr(repos.organization_repo, "access_memberships", original)
        assert (await client.get("/api/auth/access-state")).status_code == 200
        schema = (await client.get("/openapi.json")).json()
        assert schema["paths"]["/api/auth/access-state"]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]["$ref"].endswith("AccessStateRecord")
