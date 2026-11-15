"""Real SQLite and offline execution; evidence must not outlive its authority."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from octop.api.deps import current_user, get_server
from octop.api.routers.enterprise_scenes import router
from octop.infra.scenes.delivery_evidence import SceneDeliveryEvidenceService
from octop.infra.scenes.service import SceneError
from tests.integration.test_scene_execution_offline import REPORT, approve_and_wait
from tests.integration.test_scene_execution_offline import scene_runtime as scene_runtime


@pytest.fixture
async def evidence_env(scene_runtime):
    r = scene_runtime
    for actor_id in (1, 2):
        r.repos.user_repo.set_permissions(actor_id, ["tasks"])
    run = r.requester.request_run(
        "o", "s", plan_id=r.plan_id, use_memory=False, capture_memory=False
    )
    assert (await approve_and_wait(r, run)).last_status == "ok"
    service = SceneDeliveryEvidenceService(r.reviewer, r.acceptance.evidence_loader)
    return r, run["run_id"], service


@pytest.mark.parametrize(
    "mutation,status",
    [
        ("UPDATE users SET disabled=1 WHERE id=2", 403),
        ("UPDATE users SET permissions='[]' WHERE id=2", 403),
        ("DELETE FROM organization_members WHERE user_id=2", 403),
        ("UPDATE enterprise_scene_runs SET approved_by=1", 403),
        ("UPDATE enterprise_scene_runs SET updated_at=updated_at+1", 409),
        ("UPDATE enterprise_scene_runs SET status='failed'", 409),
        ("DELETE FROM enterprise_scene_runs", 404),
    ],
)
async def test_changes_during_evidence_io_discard_content(evidence_env, mutation, status):
    runtime, run_id, service = evidence_env
    original = service.loader

    async def load(run):
        answer = await original(run)
        assert answer == REPORT
        with runtime.db.transaction() as conn:
            conn.execute(mutation)
        return answer

    service.loader = load
    with pytest.raises(SceneError) as caught:
        await service.read("o", run_id)
    assert caught.value.status == status
    assert REPORT not in str(caught.value)


async def test_current_database_role_overrides_stale_admin_identity(evidence_env):
    runtime, run_id, service = evidence_env
    runtime.reviewer.actor.role = "admin"
    with runtime.db.transaction() as conn:
        conn.execute("DELETE FROM organization_members WHERE user_id=2")
    with pytest.raises(SceneError) as caught:
        await service.read("o", run_id)
    assert caught.value.status == 403


async def test_denied_or_foreign_reads_never_load_transcript(evidence_env):
    runtime, run_id, service = evidence_env

    async def forbidden_loader(run):
        pytest.fail("unauthorized evidence loader called")

    service.loader = forbidden_loader
    with pytest.raises(SceneError):
        await service.read("other", run_id)
    runtime.repos.user_repo.set_permissions(2, [])
    with pytest.raises(SceneError):
        await service.read("o", run_id)


async def test_http_contract_no_store_and_post_read_revocation(evidence_env, monkeypatch):
    runtime, run_id, _ = evidence_env
    app = FastAPI(docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.include_router(router, prefix="/api")
    server = SimpleNamespace(
        services=SimpleNamespace(
            repos=runtime.repos, thread_message_repo=runtime.repos.thread_message_repo
        ),
        app_runtime=SimpleNamespace(
            cron_manager=runtime.manager,
            gateway=runtime.gateway,
            agent_registry=runtime.gateway._agent_manager,
        ),
    )
    app.dependency_overrides[current_user] = lambda: runtime.reviewer.actor
    app.dependency_overrides[get_server] = lambda: server
    path = f"/api/organizations/o/scene-runs/{run_id}/evidence"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {"run_id": run_id, "available": True, "answer": REPORT}
        assert (await client.get("/api/docs")).status_code == 200
        schema = (await client.get("/api/openapi.json")).json()
        operation = schema["paths"][
            "/api/organizations/{organization_id}/scene-runs/{run_id}/evidence"
        ]["get"]
        assert "Rechecks current identity" in operation["description"]

        async def revoked(**kwargs):
            runtime.repos.user_repo.set_permissions(2, [])
            return REPORT

        monkeypatch.setattr("octop.infra.scenes.evidence.scene_run_evidence", revoked)
        response = await client.get(path)
        assert response.status_code == 403
        assert response.headers["cache-control"] == "no-store"
        assert REPORT not in response.text


async def test_admin_demotion_during_io_cannot_reuse_request_role(evidence_env):
    runtime, run_id, service = evidence_env
    with runtime.db.transaction() as conn:
        conn.execute("UPDATE users SET role='admin' WHERE id=2")
        conn.execute("DELETE FROM organization_members WHERE user_id=2")
    runtime.reviewer.actor.role = "admin"

    async def demoted(run):
        with runtime.db.transaction() as conn:
            conn.execute("UPDATE users SET role='user' WHERE id=2")
        return REPORT

    service.loader = demoted
    with pytest.raises(SceneError) as caught:
        await service.read("o", run_id)
    assert caught.value.status == 403


async def test_unfinished_run_returns_unavailable_without_loading(evidence_env):
    runtime, run_id, service = evidence_env
    with runtime.db.transaction() as conn:
        conn.execute("UPDATE enterprise_scene_runs SET status='awaiting_approval'")

    async def forbidden(run):
        pytest.fail("unfinished runs must not read transcripts")

    service.loader = forbidden
    assert await service.read("o", run_id) == {"run_id": run_id, "available": False, "answer": None}
