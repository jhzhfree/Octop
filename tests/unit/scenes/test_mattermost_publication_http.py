"""Real SQLite/domain HTTP lifecycle with injected auth identities, no real network.

JWT parsing is tested separately by integration/test_mattermost_publication_api.py.
Report/acceptance are seeded here, not a full inspection execution test.
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from tests.unit.digital_workers.test_mattermost_readiness import offline  # noqa: F401
from tests.unit.scenes.test_mattermost_publication import (
    preview_args,
    setup,  # noqa: F401
)
from tests.unit.scenes.test_mattermost_publication import (
    publication_setup as real_publication_setup,
)

from octop.api.deps import current_user, get_server
from octop.api.routers.mattermost_publication import router
from octop.infra.users.identity import User


@pytest.fixture
def publication_app(publication_setup):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_server] = lambda: SimpleNamespace(
        services=SimpleNamespace(repos=publication_setup[1])
    )
    app.dependency_overrides[current_user] = lambda: User(1, "requester", "user", None)
    return app


publication_setup = real_publication_setup

PATH = "/organizations/o/scene-runs/r/mattermost-publication"


async def test_local_http_lifecycle_independent_approval_and_restart_read(
    publication_app, publication_setup
):
    app = publication_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(PATH)).status_code == 404
        response = await client.post(PATH, json=preview_args())
        assert response.status_code == 200, response.text
        draft = response.json()
        assert draft["status"] == "pending_approval"
        assert draft["proposed_by"] == 1
        decision = {"expected_publication_sha256": draft["publication_sha256"]}
        assert (await client.post(PATH + "/approve", json=decision)).status_code == 403
        app.dependency_overrides[current_user] = lambda: User(2, "reviewer", "user", None)
        approved = await client.post(PATH + "/approve", json=decision)
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "blocked_not_connected"
        assert approved.json()["approved_by"] == 2
        assert not approved.json()["execution_authorized"]
        assert (await client.post(PATH + "/approve", json=decision)).json() == approved.json()
    # Recreate the HTTP app/service against persisted rows, without keeping request state.
    fresh = FastAPI()
    fresh.include_router(router)
    fresh.dependency_overrides.update(app.dependency_overrides)
    async with AsyncClient(transport=ASGITransport(app=fresh), base_url="http://test") as client:
        assert (await client.get(PATH)).json() == approved.json()
        revoked = await client.post(PATH + "/revoke", json=decision)
        assert revoked.status_code == 200
        assert revoked.json()["status"] == "revoked"
        assert (await client.post(PATH + "/approve", json=decision)).status_code == 409


async def test_http_scope_and_realtime_revoke_hide_report(publication_app, publication_setup):
    app = publication_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        draft = (await client.post(PATH, json=preview_args())).json()
        decision = {"expected_publication_sha256": draft["publication_sha256"]}
        response = await client.get(PATH.replace("/o/", "/other/"))
        assert response.status_code == 404
        assert "INTERNAL_REPORT_SENTINEL" not in response.text
        with publication_setup[0].transaction() as conn:
            conn.execute("UPDATE users SET role='user' WHERE id=3")
        app.dependency_overrides[current_user] = lambda: User(3, "outsider", "admin", None)
        response = await client.get(PATH)
        assert response.status_code == 403  # claimed/stale admin flag is not authority
        assert "INTERNAL_REPORT_SENTINEL" not in response.text
        app.dependency_overrides[current_user] = lambda: User(2, "reviewer", "user", None)
        with publication_setup[0].transaction() as conn:
            conn.execute("DELETE FROM organization_members WHERE user_id=2")
        for method, suffix, body in (("GET", "", None), ("POST", "/approve", decision)):
            response = await client.request(method, PATH + suffix, json=body)
            assert response.status_code in (403, 409)
            assert "INTERNAL_REPORT_SENTINEL" not in response.text
