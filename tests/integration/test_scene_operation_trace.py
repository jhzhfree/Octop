"""Offline real execution/audit, with current identity and bounded safe projection."""

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from octop.api.deps import current_user, get_server
from octop.api.routers.enterprise_scenes import router
from octop.infra.scenes.operation_trace import SceneOperationTraceService
from octop.infra.scenes.service import SceneError
from tests.integration.test_scene_delivery_evidence import evidence_env as evidence_env
from tests.integration.test_scene_execution_offline import scene_runtime as scene_runtime


async def test_actual_offline_execution_has_distinct_account_and_system_facts(evidence_env):
    r, run_id, _ = evidence_env
    review = await r.acceptance.review("o", run_id)
    await r.acceptance.decide(
        "o",
        run_id,
        decision="accepted",
        expected_evidence_sha256=review["evidence_sha256"],
        expected_snapshot_sha256=review["snapshot_sha256"],
    )
    result = SceneOperationTraceService(r.reviewer).read("o", run_id)
    events = {e["action"]: e for e in result["events"]}
    assert events["scene.run.delivery_reviewed"]["actor_user_id"] == 2
    assert events["scene.run_requested"]["actor_user_id"] == 1
    assert events["scene.run_approved"]["actor_user_id"] == 2
    assert events["scene.run_approved"]["interaction_mode"] == "not_recorded"
    assert events["cron.run_ok"]["actor_kind"] == "system"
    assert events["cron.run_ok"]["actor_user_id"] is None
    assert result["execution_context"]["schedule_kind"] == "one_shot"
    assert result["execution_context"]["configured_executor"] == "agent_task"
    assert result["history_complete"] is False
    assert result["execution_authorized"] is False
    assert all(
        set(e)
        == {"record_id", "recorded_at", "action", "actor_kind", "actor_user_id", "interaction_mode"}
        for e in result["events"]
    )


@pytest.mark.parametrize(
    "mutation,status",
    [
        ("UPDATE users SET disabled=1 WHERE id=2", 403),
        ("UPDATE users SET permissions='[]' WHERE id=2", 403),
        ("DELETE FROM organization_members WHERE user_id=2", 403),
        ("UPDATE enterprise_scene_runs SET approved_by=1", 403),
        ("UPDATE enterprise_scene_runs SET updated_at=updated_at+1", 409),
        ("DELETE FROM enterprise_scene_runs", 404),
    ],
)
async def test_revocation_or_mutation_during_query_discards_trace(
    evidence_env, monkeypatch, mutation, status
):
    r, run_id, _ = evidence_env
    original = r.repos.audit_repo.for_target

    def query(*args, **kwargs):
        result = original(*args, **kwargs)
        with r.db.transaction() as conn:
            conn.execute(mutation)
        return result

    monkeypatch.setattr(r.repos.audit_repo, "for_target", query)
    with pytest.raises(SceneError) as caught:
        SceneOperationTraceService(r.reviewer).read("o", run_id)
    assert caught.value.status == status


async def test_foreign_organization_denied_before_query(evidence_env, monkeypatch):
    r, run_id, _ = evidence_env

    def forbidden(*args, **kwargs):
        pytest.fail("foreign audit queried")

    monkeypatch.setattr(r.repos.audit_repo, "for_target", forbidden)
    with pytest.raises(SceneError):
        SceneOperationTraceService(r.reviewer).read("other", run_id)


async def test_projection_is_bounded_and_excludes_raw_payload_and_unrelated_actions(evidence_env):
    r, run_id, _ = evidence_env
    for _ in range(105):
        r.repos.audit_repo.write(
            actor="2", action="scene.run_approved", target=run_id, payload="PRIVATE_RAW_ERROR"
        )
    r.repos.audit_repo.write(
        actor="2", action="unrelated", target=run_id, payload="PRIVATE_RAW_ERROR"
    )
    r.repos.audit_repo.write(
        actor="2",
        action="scene.run.delivery_reviewed",
        target=run_id,
        payload=json.dumps({"organization_id": "other"}),
    )
    result = SceneOperationTraceService(r.reviewer).read("o", run_id)
    assert result["truncated"] is True
    assert len(result["events"]) <= 100
    assert all(e["action"] == "scene.run_approved" for e in result["events"])
    ids = [e["record_id"] for e in result["events"]]
    assert ids == sorted(ids, reverse=True)
    assert "PRIVATE_RAW_ERROR" not in json.dumps(result)


@pytest.mark.parametrize(
    "column,value", [("user_id", 2), ("agent_id", "foreign"), ("session_key", "foreign")]
)
async def test_wrong_cron_binding_does_not_attribute_execution(evidence_env, column, value):
    r, run_id, _ = evidence_env
    with r.db.transaction() as conn:
        if column == "agent_id":
            conn.execute(
                "INSERT INTO agents(agent_id,name,user_id,created_at,updated_at) VALUES ('foreign','Foreign fixture',1,1,1)"
            )
        conn.execute(f"UPDATE cron_jobs SET {column}=?", (value,))
    result = SceneOperationTraceService(r.reviewer).read("o", run_id)
    assert result["execution_context"]["bound_cron_id"] is None
    assert not any(e["action"].startswith("cron.") for e in result["events"])


async def test_missing_history_is_not_reconstructed_from_run(evidence_env):
    r, run_id, _ = evidence_env
    with r.db.transaction() as conn:
        conn.execute("DELETE FROM audit_log")
    result = SceneOperationTraceService(r.reviewer).read("o", run_id)
    assert result["events"] == []
    assert result["history_complete"] is False


async def test_http_contract_readonly_docs_and_no_store(evidence_env):
    r, run_id, _ = evidence_env
    app = FastAPI(docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.include_router(router, prefix="/api")
    app.dependency_overrides[current_user] = lambda: r.reviewer.actor
    app.dependency_overrides[get_server] = lambda: SimpleNamespace(
        services=SimpleNamespace(repos=r.repos)
    )
    path = f"/api/organizations/o/scene-runs/{run_id}/operation-trace"
    with r.db.connect() as conn:
        before = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(path + "?actor=999&interaction_mode=human")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert not any(e["actor_user_id"] == 999 for e in response.json()["events"])
        assert (await client.post(path)).status_code == 405
        assert (await client.get("/api/docs")).status_code == 200
        schema = (await client.get("/api/openapi.json")).json()
        assert schema["paths"][
            "/api/organizations/{organization_id}/scene-runs/{run_id}/operation-trace"
        ]["get"]["summary"]
        r.repos.user_repo.set_permissions(2, [])
        denied = await client.get(path)
        assert denied.status_code == 403
        assert denied.headers["cache-control"] == "no-store"
    with r.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == before


async def test_stale_admin_claim_cannot_read_removed_membership(evidence_env):
    r, run_id, _ = evidence_env
    r.reviewer.actor.role = "admin"
    with r.db.transaction() as conn:
        conn.execute("DELETE FROM organization_members WHERE user_id=2")
    with pytest.raises(SceneError) as caught:
        SceneOperationTraceService(r.reviewer).read("o", run_id)
    assert caught.value.status == 403


async def test_pending_read_does_not_reconcile_or_invent_executor(scene_runtime, monkeypatch):
    r = scene_runtime
    r.repos.user_repo.set_permissions(1, ["tasks"])
    run = r.requester.request_run(
        "o", "s", plan_id=r.plan_id, use_memory=False, capture_memory=False
    )

    def forbidden(*args):
        pytest.fail("trace must not reconcile run state")

    monkeypatch.setattr(type(r.requester), "_sync_status", forbidden)
    result = SceneOperationTraceService(r.requester).read("o", run["run_id"])
    assert [e["action"] for e in result["events"]] == ["scene.run_requested"]
    assert result["execution_context"]["configured_executor"] == "unknown"
    assert result["execution_context"]["schedule_kind"] == "unknown"


async def test_failure_projection_hides_error_and_nondate_schedule_stays_unknown(evidence_env):
    r, run_id, _ = evidence_env
    cron_id = r.repos.enterprise_scene_repo.get_run(run_id)["cron_id"]
    r.repos.audit_repo.system_event(
        "cron.run_failed", target=cron_id, payload="PRIVATE_ENDPOINT_ERROR"
    )
    with r.db.transaction() as conn:
        conn.execute("UPDATE cron_jobs SET schedule_spec='0 * * * *' WHERE cron_id=?", (cron_id,))
    result = SceneOperationTraceService(r.reviewer).read("o", run_id)
    assert result["events"][0]["action"] == "cron.run_failed"
    assert result["events"][0]["actor_kind"] == "system"
    assert result["execution_context"]["schedule_kind"] == "unknown"
    assert "PRIVATE_ENDPOINT_ERROR" not in json.dumps(result)
