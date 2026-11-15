"""Manual report sharing across real, offline execution and reporting services.

A deterministic model and HTTP transport replace external systems. Neither run
success nor report content/acceptance is seeded. No outbound approval is called.
"""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from octop.infra.db.repos.scene_acceptance import AcceptanceStoreError, snapshot_hash
from octop.infra.enterprise_assistant import adapters
from octop.infra.enterprise_assistant.models import (
    ConnectionConfig,
    ConnectionInput,
    JobConfig,
    JobInput,
)
from octop.infra.enterprise_assistant.service import FLAGS_KEY, AssistantService
from octop.infra.scenes.report_attachment import SceneReportAttachmentService
from octop.infra.scenes.service import EnterpriseSceneService, SceneError
from octop.infra.users.identity import User
from tests.integration.test_scene_execution_offline import (
    approve_and_wait,
)
from tests.integration.test_scene_execution_offline import (
    scene_runtime as offline_runtime,
)

scene_runtime = offline_runtime


async def prepare(runtime, monkeypatch):
    svc = AssistantService(runtime.repos, runtime.requester.actor)
    runtime.repos.user_repo.set_permissions(1, ["tasks"])
    runtime.repos.settings_repo.set(
        FLAGS_KEY,
        json.dumps(
            {
                "enabled": True,
                "inspection": True,
                "reports": True,
                "allowed_origins": ["https://fixture.invalid", "https://qyapi.weixin.qq.com"],
            }
        ),
    )
    client_type = httpx.AsyncClient

    def endpoint(request):
        assert request.url.host == "fixture.invalid", "outbound report send is forbidden"
        if request.url.path == "/timeout":
            raise httpx.ReadTimeout("OFFLINE FIXTURE", request=request)
        if request.url.path == "/degraded":
            return httpx.Response(200, json={"status": "degraded", "disk_percent": 95})
        return httpx.Response(200, json={"status": "healthy"})

    monkeypatch.setattr(
        adapters.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(**({"transport": httpx.MockTransport(endpoint)} | kwargs)),
    )
    source_ids = []
    for name in ("healthy", "degraded", "timeout"):
        conn = svc.save_connection(
            ConnectionInput(
                config=ConnectionConfig(
                    name=name, kind="inspection", url="https://fixture.invalid/" + name
                )
            )
        )
        job = svc.save_job(
            JobInput(
                config=JobConfig(
                    name=name, kind="inspection", agent_id="a", connection_id=conn["id"]
                )
            )
        )
        run = svc.enqueue(job["id"])
        await svc.process(run["id"])
        source_ids.append(job["id"])
    conn = svc.save_connection(
        ConnectionInput(
            config=ConnectionConfig(
                name="Not sent",
                kind="wecom",
                url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send",
            ),
            secret="offline-fixture-only",
        )
    )
    job = svc.save_job(
        JobInput(
            config=JobConfig(
                name="OFFLINE morning report",
                kind="report",
                agent_id="a",
                connection_id=conn["id"],
                source_job_ids=source_ids,
            )
        )
    )
    report = svc.enqueue(job["id"])
    await svc.process(report["id"])
    report = svc.run_owned(report["id"])
    assert report["status"] == "awaiting_approval"
    run = runtime.requester.request_run("o", "s", plan_id=runtime.plan_id)
    cron = await approve_and_wait(runtime, run)
    assert cron.last_status == "ok"
    runtime.requester.get_run("o", run["run_id"])
    return SimpleNamespace(
        assistant=svc,
        report=report,
        run=run,
        sharing=SceneReportAttachmentService(runtime.requester),
    )


def attach_args(p):
    return {
        "report_run_id": p.report["id"],
        "expected_report_version": p.report["version"],
        "expected_content_hash": p.report["content_hash"],
        "share_with_reviewer": True,
    }


@pytest.mark.asyncio
async def test_manual_report_snapshot_is_reviewed_and_not_sent(scene_runtime, monkeypatch):
    p = await prepare(scene_runtime, monkeypatch)
    run_id = p.run["run_id"]
    preview = p.sharing.preview("o", run_id, p.report["id"])
    prior_review = await scene_runtime.acceptance.review("o", run_id)
    record = p.sharing.attach("o", run_id, **attach_args(p))
    assert record["content"] == preview["content"]
    assert "95" in record["content"]
    assert p.assistant.run_owned(p.report["id"])["status"] == "awaiting_approval"
    reviewer_share = SceneReportAttachmentService(scene_runtime.reviewer)
    assert reviewer_share.get("o", run_id) == record
    with pytest.raises(SceneError) as stale:
        await scene_runtime.acceptance.decide(
            "o",
            run_id,
            decision="accepted",
            expected_evidence_sha256=prior_review["evidence_sha256"],
            expected_snapshot_sha256=prior_review["snapshot_sha256"],
        )
    assert stale.value.status == 409
    review = await scene_runtime.acceptance.review("o", run_id)
    assert review["evidence_sources"] == [
        {
            "kind": "assistant_summary",
            "source_run_id": run_id,
            "content_sha256": prior_review["evidence_sha256"],
        },
        {
            "kind": "owner_shared_report",
            "source_run_id": p.report["id"],
            "content_sha256": hashlib.sha256(record["content"].encode()).hexdigest(),
        },
    ]
    assert record["content"] in review["answer"]
    assert record["attachment_sha256"] in review["answer"]
    assert review["snapshot_sha256"] != prior_review["snapshot_sha256"]
    # Source draft can evolve, but the reviewed snapshot is immutable.
    p.assistant.edit_draft(p.report["id"], p.report["version"], "Edited private report")
    assert (await scene_runtime.acceptance.review("o", run_id))["answer"] == review["answer"]
    accepted = await scene_runtime.acceptance.decide(
        "o",
        run_id,
        decision="accepted",
        expected_evidence_sha256=review["evidence_sha256"],
        expected_snapshot_sha256=review["snapshot_sha256"],
    )
    assert accepted["decision"] == "accepted"
    assert scene_runtime.gateway._channel_manager is None
    assert (
        scene_runtime.repos.digital_worker_repo.get_goal(scene_runtime.goal_id)["status"]
        == "active"
    )


@pytest.mark.asyncio
async def test_attachment_rejects_ownership_and_stale_preview(scene_runtime, monkeypatch):
    p = await prepare(scene_runtime, monkeypatch)
    run_id = p.run["run_id"]
    for org, service in (
        ("foreign", p.sharing),
        ("o", SceneReportAttachmentService(scene_runtime.reviewer)),
    ):
        with pytest.raises(SceneError):
            service.preview(org, run_id, p.report["id"])
    p.assistant.edit_draft(p.report["id"], p.report["version"], "Changed")
    with pytest.raises(SceneError) as stale:
        p.sharing.attach("o", run_id, **attach_args(p))
    assert stale.value.status == 409
    assert p.sharing.get("o", run_id) is None


@pytest.mark.asyncio
async def test_acceptance_wins_attachment_is_rejected(scene_runtime, monkeypatch):
    p = await prepare(scene_runtime, monkeypatch)
    run_id = p.run["run_id"]
    review = await scene_runtime.acceptance.review("o", run_id)
    await scene_runtime.acceptance.decide(
        "o",
        run_id,
        decision="accepted",
        expected_evidence_sha256=review["evidence_sha256"],
        expected_snapshot_sha256=review["snapshot_sha256"],
    )
    with pytest.raises(SceneError) as conflict:
        p.sharing.attach("o", run_id, **attach_args(p))
    assert conflict.value.status == 409
    assert p.sharing.get("o", run_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("revoke", ["disabled", "tasks", "flags", "agent", "member"])
async def test_transaction_rechecks_current_authority(scene_runtime, monkeypatch, revoke):
    p = await prepare(scene_runtime, monkeypatch)
    with scene_runtime.db.connect() as conn:
        sql = {
            "disabled": "UPDATE users SET disabled=1 WHERE id=1",
            "tasks": "UPDATE users SET permissions='[]' WHERE id=1",
            "flags": "UPDATE settings SET value='{}' WHERE key='enterprise_assistant.features.v1'",
            "agent": "UPDATE agents SET enabled=0 WHERE agent_id='a'",
            "member": "DELETE FROM organization_members WHERE user_id=1",
        }[revoke]
        conn.execute(sql)
    with pytest.raises(AcceptanceStoreError):
        p.sharing.repo.attach(
            organization_id="o", run_id=p.run["run_id"], actor_id=1, report=p.report
        )


def test_legacy_snapshot_hash_unchanged():
    assert snapshot_hash({"run_id": "r"}) == snapshot_hash(
        {"run_id": "r", "report_attachment_sha256": ""}
    )


@pytest.mark.asyncio
async def test_non_reviewer_manager_cannot_read_and_disabled_owner_denied(
    scene_runtime, monkeypatch
):
    p = await prepare(scene_runtime, monkeypatch)
    run_id = p.run["run_id"]
    p.sharing.attach("o", run_id, **attach_args(p))
    uid = scene_runtime.repos.user_repo.create(
        username="other", password_hash="unused", role="user"
    )
    with scene_runtime.db.connect() as conn:
        conn.execute(
            "INSERT INTO organization_members(organization_id,user_id,role,created_at) "
            "VALUES('o',?,'manager',1)",
            (uid,),
        )
    other = SceneReportAttachmentService(
        EnterpriseSceneService(scene_runtime.repos, User(uid, "other", "user", None))
    )
    with pytest.raises(SceneError):
        other.get("o", run_id)
    with scene_runtime.db.connect() as conn:
        conn.execute("UPDATE users SET disabled=1 WHERE id=1")
    with pytest.raises(SceneError):
        p.sharing.get("o", run_id)


@pytest.mark.asyncio
async def test_http_contract_and_explicit_sharing(scene_runtime, monkeypatch):
    from fastapi import FastAPI

    from octop.api.deps import current_user, get_server
    from octop.api.routers.scene_acceptance import router

    p = await prepare(scene_runtime, monkeypatch)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[current_user] = lambda: scene_runtime.requester.actor
    app.dependency_overrides[get_server] = lambda: SimpleNamespace(
        services=SimpleNamespace(repos=scene_runtime.repos)
    )
    url = f"/api/organizations/o/scene-runs/{p.run['run_id']}/report-attachment"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(url + "/preview", params={"report_run_id": p.report["id"]})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert set(response.json()) == {
            "report_run_id",
            "report_version",
            "report_content_hash",
            "content",
        }
        assert (
            await client.post(url, json=attach_args(p) | {"share_with_reviewer": False})
        ).status_code == 422
        response = await client.post(url, json=attach_args(p))
        assert response.status_code == 200
        assert set(response.json()) == {
            "run_id",
            "organization_id",
            "report_run_id",
            "report_version",
            "report_content_hash",
            "attachment_sha256",
            "attached_by",
            "attached_at",
            "content",
        }
        assert (await client.post(url, json=attach_args(p))).status_code == 409
        assert (await client.get(url)).json() == response.json()
    # Actual OpenAPI contract exists, without runtime service boot or network.
    schema = app.openapi()
    assert (
        url.replace(p.run["run_id"], "{run_id}").replace("/o/", "/{organization_id}/")
        in schema["paths"]
    )


@pytest.mark.asyncio
async def test_attachment_and_acceptance_cannot_both_win_old_snapshot(scene_runtime, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    p = await prepare(scene_runtime, monkeypatch)
    run_id = p.run["run_id"]
    review = await scene_runtime.acceptance.review("o", run_id)
    barrier = Barrier(2)

    def attach():
        barrier.wait()
        try:
            p.sharing.repo.attach(organization_id="o", run_id=run_id, actor_id=1, report=p.report)
            return "attached"
        except AcceptanceStoreError:
            return "rejected"

    def accept():
        barrier.wait()
        try:
            scene_runtime.acceptance.repo.decide(
                organization_id="o",
                run_id=run_id,
                reviewer_id=2,
                decision="accepted",
                evidence_sha256=review["evidence_sha256"],
                evidence_length=len(review["answer"]),
                expected_snapshot_hash=review["snapshot_sha256"],
            )
            return "accepted"
        except AcceptanceStoreError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(attach)
        b = pool.submit(accept)
        results = [a.result(), b.result()]
    assert results.count("rejected") == 1
    assert (p.sharing.get("o", run_id) is not None) != (
        scene_runtime.acceptance.repo.get("o", run_id) is not None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["agent", "kind", "sources", "content_hash", "status"])
async def test_source_contract_rejects_ineligible_report(scene_runtime, monkeypatch, change):
    p = await prepare(scene_runtime, monkeypatch)
    report = p.report
    snapshot = report["snapshot"]
    evidence = report["evidence"]
    if change == "agent":
        snapshot["job"]["agent_id"] = "another"
    elif change == "kind":
        snapshot["job"]["kind"] = "inspection"
    elif change == "sources":
        next(item for item in evidence if item.get("type") == "source")["kind"] = "jira"
    with scene_runtime.db.connect() as conn:
        conn.execute(
            "UPDATE enterprise_assistant_runs SET snapshot_json=?,evidence_json=?,content_hash=?,status=? WHERE id=?",
            (
                json.dumps(snapshot),
                json.dumps(evidence),
                "bad" if change == "content_hash" else report["content_hash"],
                "approved" if change == "status" else report["status"],
                report["id"],
            ),
        )
    with pytest.raises(SceneError):
        p.sharing.preview("o", p.run["run_id"], report["id"])


def test_migration_35_upgrades_existing_schema(tmp_path, monkeypatch):
    from octop.infra.db import migrate
    from octop.infra.db.pool import SqlitePool

    db = SqlitePool(tmp_path / "upgrade.db")
    discover = migrate._discover
    monkeypatch.setattr(
        migrate,
        "_discover",
        lambda dialect="sqlite": [(v, p) for v, p in discover(dialect) if v <= 34],
    )
    migrate.run_migrations(db)
    with db.connect() as conn:
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 34
    monkeypatch.setattr(
        migrate,
        "_discover",
        lambda dialect="sqlite": [(v, p) for v, p in discover(dialect) if v <= 35],
    )
    migrate.run_migrations(db)
    with db.connect() as conn:
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 35
        assert "report_attachment_sha256" in [
            r["name"] for r in conn.execute("PRAGMA table_info(enterprise_scene_runs)")
        ]
    assert {p.read_text() for v, p in discover("sqlite") if v == 35} == {
        p.read_text() for v, p in discover("postgresql") if v == 35
    }
    db.close()
