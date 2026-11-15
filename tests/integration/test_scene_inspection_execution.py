"""Same-run inspection with real services and HTTP transport fixtures, zero model calls."""

from __future__ import annotations

import json

import httpx
import pytest

from octop.infra.db.repos.scene_inspection import SceneInspectionRepo
from octop.infra.enterprise_assistant import adapters
from octop.infra.enterprise_assistant.models import (
    ConnectionConfig,
    ConnectionInput,
    JobConfig,
    JobInput,
)
from octop.infra.enterprise_assistant.service import FLAGS_KEY, AssistantService
from octop.infra.scenes.inspection import candidates, execute_inspection
from octop.infra.scenes.service import SceneError
from tests.integration.test_scene_execution_offline import approve_and_wait
from tests.integration.test_scene_execution_offline import scene_runtime as scene_runtime


def setup_sources(runtime, monkeypatch):
    svc = AssistantService(runtime.repos, runtime.requester.actor)
    runtime.repos.user_repo.set_permissions(1, ["tasks"])
    runtime.repos.settings_repo.set(
        FLAGS_KEY,
        json.dumps(
            {
                "enabled": True,
                "inspection": True,
                "reports": True,
                "allowed_origins": ["https://fixture.invalid"],
            }
        ),
    )
    client = httpx.AsyncClient
    calls = []

    def endpoint(request):
        calls.append(request.url.path)
        assert request.url.host == "fixture.invalid"
        if request.url.path == "/timeout":
            raise httpx.ReadTimeout("offline", request=request)
        return httpx.Response(
            200,
            json={"status": "degraded", "disk_percent": 95}
            if request.url.path == "/degraded"
            else {"status": "healthy"},
        )

    monkeypatch.setattr(
        adapters.httpx,
        "AsyncClient",
        lambda **kwargs: client(**({"transport": httpx.MockTransport(endpoint)} | kwargs)),
    )
    jobs = []
    for name in ("healthy", "degraded", "timeout"):
        connection = svc.save_connection(
            ConnectionInput(
                config=ConnectionConfig(
                    name=name, kind="inspection", url="https://fixture.invalid/" + name
                )
            )
        )
        job = svc.save_job(
            JobInput(
                config=JobConfig(
                    name=name, kind="inspection", agent_id="a", connection_id=connection["id"]
                )
            )
        )
        jobs.append(job["id"])
    return svc, jobs, calls


def request(runtime, jobs):
    return runtime.requester.request_run(
        "o",
        "s",
        plan_id=runtime.plan_id,
        use_memory=False,
        capture_memory=False,
        inspection_job_ids=jobs,
        share_inspection_results=True,
    )


@pytest.mark.asyncio
async def test_same_run_fresh_sources_report_and_acceptance(scene_runtime, monkeypatch):
    svc, jobs, calls = setup_sources(scene_runtime, monkeypatch)
    # A historical successful source must not substitute for the new timeout.
    historical = svc.enqueue(jobs[0])
    await svc.process(historical["id"])
    calls.clear()
    run = request(scene_runtime, jobs)
    cron = await approve_and_wait(scene_runtime, run)
    assert cron.last_status == "ok"
    current = scene_runtime.requester.get_run("o", run["run_id"])
    assert current["status"] == "succeeded"
    batch = SceneInspectionRepo(scene_runtime.db).get(run["run_id"])
    assert batch["status"] == "reported"
    assert batch["counts"] == {
        "sources": 3,
        "attempted": 3,
        "succeeded": 2,
        "failed": 1,
        "unknown": 0,
        "health_unknown": 0,
        "reports_persisted": 1,
        "scene_bound": 1,
        "not_started": 0,
    }
    assert sorted(calls) == ["/degraded", "/healthy", "/timeout"]
    assert len({s["run_id"] for s in batch["sources"]}) == 3
    assert historical["id"] not in [s["run_id"] for s in batch["sources"]]
    assert "95" in batch["content"] and run["run_id"] in batch["content"]
    assert not svc.repo.pending()
    assert not [r for r in svc.repo.runs(1) if r["snapshot"]["job"]["kind"] == "report"]
    review = await scene_runtime.acceptance.review("o", run["run_id"])
    assert review["answer"] == batch["content"]
    assert review["evidence_sources"] == [
        {
            "kind": "same_run_http_report",
            "source_run_id": run["run_id"],
            "content_sha256": batch["content_sha256"],
        }
    ]
    accepted = await scene_runtime.acceptance.decide(
        "o",
        run["run_id"],
        decision="accepted",
        expected_evidence_sha256=review["evidence_sha256"],
        expected_snapshot_sha256=review["snapshot_sha256"],
    )
    assert accepted["decision"] == "accepted"
    assert scene_runtime.gateway._channel_manager is None
    assert scene_runtime.model.calls == []


@pytest.mark.asyncio
async def test_configuration_changed_before_execution_does_not_collect(scene_runtime, monkeypatch):
    svc, jobs, calls = setup_sources(scene_runtime, monkeypatch)
    run = request(scene_runtime, jobs)
    with scene_runtime.db.connect() as conn:
        conn.execute(
            "UPDATE enterprise_assistant_resources SET version=version+1 WHERE id=?", (jobs[0],)
        )
    cron = await approve_and_wait(scene_runtime, run)
    assert cron.last_status == "error"
    assert calls == []
    assert SceneInspectionRepo(scene_runtime.db).get(run["run_id"]) is None


@pytest.mark.asyncio
async def test_all_source_failures_still_produce_honest_report(scene_runtime, monkeypatch):
    _, jobs, calls = setup_sources(scene_runtime, monkeypatch)
    run = request(scene_runtime, [jobs[-1]])
    cron = await approve_and_wait(scene_runtime, run)
    assert cron.last_status == "ok"
    batch = SceneInspectionRepo(scene_runtime.db).get(run["run_id"])
    assert batch["counts"]["succeeded"] == 0
    assert batch["counts"]["failed"] == 1
    assert batch["counts"]["reports_persisted"] == 1
    assert calls == ["/timeout"]


@pytest.mark.asyncio
async def test_duplicate_delivery_does_not_recollect(scene_runtime, monkeypatch):
    _, jobs, calls = setup_sources(scene_runtime, monkeypatch)
    run = request(scene_runtime, jobs)
    cron = await approve_and_wait(scene_runtime, run)
    row = scene_runtime.repos.enterprise_scene_repo.get_run(run["run_id"])
    before = list(calls)
    with pytest.raises(ValueError, match="already attempted"):
        await execute_inspection(
            scene_runtime.repos, row, cron_id=cron.cron_id, user_id=1, agent_id="a"
        )
    assert calls == before


def test_request_consent_scope_and_minimal_candidates(scene_runtime, monkeypatch):
    _, jobs, _ = setup_sources(scene_runtime, monkeypatch)
    with pytest.raises(SceneError):
        scene_runtime.requester.request_run(
            "o", "s", plan_id=scene_runtime.plan_id, inspection_job_ids=jobs
        )
    with pytest.raises(SceneError):
        request(scene_runtime, [jobs[0], jobs[0]])
    rows = candidates(scene_runtime.repos, scene_runtime.requester.actor, "a")
    assert len(rows) == 3
    assert all(set(row) == {"job_id", "name", "job_version", "connection_version"} for row in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("revoke", ["member", "agent", "tasks", "flags"])
async def test_last_source_revocation_does_not_publish(scene_runtime, monkeypatch, revoke):
    _, jobs, _ = setup_sources(scene_runtime, monkeypatch)
    original = adapters.collect

    async def revoke_after_collect(*args, **kwargs):
        result = await original(*args, **kwargs)
        with scene_runtime.db.connect() as conn:
            sql = {
                "member": "DELETE FROM organization_members WHERE user_id=1",
                "agent": "UPDATE agents SET enabled=0 WHERE agent_id='a'",
                "tasks": "UPDATE users SET permissions='[]' WHERE id=1",
                "flags": "UPDATE settings SET value='{}' WHERE key='enterprise_assistant.features.v1'",
            }[revoke]
            conn.execute(sql)
        return result

    monkeypatch.setattr(adapters, "collect", revoke_after_collect)
    run = request(scene_runtime, [jobs[0]])
    cron = await approve_and_wait(scene_runtime, run)
    assert cron.last_status == "error"
    batch = SceneInspectionRepo(scene_runtime.db).get(run["run_id"])
    assert batch["status"] == "failed" and batch["content"] == ""
    assert batch["counts"]["attempted"] == 1
    assert batch["counts"]["reports_persisted"] == 0


@pytest.mark.asyncio
async def test_report_persistence_failure_is_not_success(scene_runtime, monkeypatch):
    _, jobs, _ = setup_sources(scene_runtime, monkeypatch)

    def fail(*args, **kwargs):
        raise RuntimeError("simulated database write error")

    monkeypatch.setattr(SceneInspectionRepo, "finish", fail)
    run = request(scene_runtime, jobs)
    cron = await approve_and_wait(scene_runtime, run)
    assert cron.last_status == "error"
    batch = SceneInspectionRepo(scene_runtime.db).get(run["run_id"])
    assert batch["counts"]["reports_persisted"] == 0
    assert batch["counts"]["succeeded"] == 2
    assert scene_runtime.requester.get_run("o", run["run_id"])["status"] == "failed"


@pytest.mark.asyncio
async def test_http_request_forwards_mode_and_exact_sources(scene_runtime, monkeypatch):
    from types import SimpleNamespace

    from fastapi import FastAPI

    from octop.api.deps import current_user, get_server
    from octop.api.routers.enterprise_scenes import router

    _, jobs, _ = setup_sources(scene_runtime, monkeypatch)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[current_user] = lambda: scene_runtime.requester.actor
    app.dependency_overrides[get_server] = lambda: SimpleNamespace(
        services=SimpleNamespace(repos=scene_runtime.repos)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post(
            "/api/organizations/o/scenes/s/runs",
            json={
                "plan_id": scene_runtime.plan_id,
                "use_memory": False,
                "capture_memory": False,
                "inspection_job_ids": jobs,
                "share_inspection_results": True,
            },
        )
    assert result.status_code == 201, result.text
    snapshot = result.json()["plan_snapshot"]["inspection"]
    assert [v["job_id"] for v in snapshot["sources"]] == jobs
    assert "https://" not in json.dumps(snapshot)
    cron = await approve_and_wait(scene_runtime, result.json())
    assert cron.last_status == "ok"
    assert scene_runtime.model.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("revoke", ["worker", "goal", "assignment"])
async def test_worker_authority_changed_during_collection(scene_runtime, monkeypatch, revoke):
    _, jobs, _ = setup_sources(scene_runtime, monkeypatch)
    original = adapters.collect

    async def collect(*args, **kwargs):
        result = await original(*args, **kwargs)
        with scene_runtime.db.connect() as conn:
            conn.execute(
                {
                    "worker": "UPDATE digital_workers SET status='paused' WHERE worker_id='w'",
                    "goal": "UPDATE digital_worker_goals SET status='completed'",
                    "assignment": "DELETE FROM organization_assignments",
                }[revoke]
            )
        return result

    monkeypatch.setattr(adapters, "collect", collect)
    run = request(scene_runtime, [jobs[0]])
    cron = await approve_and_wait(scene_runtime, run)
    assert cron.last_status == "error"
    assert SceneInspectionRepo(scene_runtime.db).get(run["run_id"])["content"] == ""


@pytest.mark.asyncio
async def test_transcript_or_report_tamper_prevents_acceptance(scene_runtime, monkeypatch):
    _, jobs, _ = setup_sources(scene_runtime, monkeypatch)
    run = request(scene_runtime, jobs)
    assert (await approve_and_wait(scene_runtime, run)).last_status == "ok"
    with scene_runtime.db.connect() as conn:
        conn.execute(
            "UPDATE scene_inspection_batches SET content='tampered' WHERE run_id=?",
            (run["run_id"],),
        )
    with pytest.raises(SceneError, match="integrity"):
        await scene_runtime.acceptance.review("o", run["run_id"])


@pytest.mark.asyncio
async def test_source_errors_record_attempts_and_zero_success(scene_runtime, monkeypatch):

    _, jobs, calls = setup_sources(scene_runtime, monkeypatch)
    original = adapters.collect

    async def cancel(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("simulated collector interruption")

    # Individual source errors are coverage gaps; the next source continues.
    monkeypatch.setattr(adapters, "collect", cancel)
    run = request(scene_runtime, jobs)
    assert (await approve_and_wait(scene_runtime, run)).last_status == "ok"
    batch = SceneInspectionRepo(scene_runtime.db).get(run["run_id"])
    assert batch["counts"]["attempted"] == 3
    assert batch["counts"]["succeeded"] == 0
    assert len(calls) == 3


def test_migration_36_upgrades_35(tmp_path, monkeypatch):
    from octop.infra.db import migrate
    from octop.infra.db.pool import SqlitePool

    db = SqlitePool(tmp_path / "upgrade36.db")
    discover = migrate._discover
    monkeypatch.setattr(
        migrate,
        "_discover",
        lambda dialect="sqlite": [(v, p) for v, p in discover(dialect) if v <= 35],
    )
    migrate.run_migrations(db)
    monkeypatch.setattr(
        migrate,
        "_discover",
        lambda dialect="sqlite": [(v, p) for v, p in discover(dialect) if v <= 36],
    )
    migrate.run_migrations(db)
    with db.connect() as conn:
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 36
        assert "inspection_report_sha256" in [
            r["name"] for r in conn.execute("PRAGMA table_info(enterprise_scene_runs)")
        ]
        assert conn.execute("SELECT COUNT(*) FROM scene_inspection_batches").fetchone()[0] == 0
    assert {p.read_text() for v, p in discover("sqlite") if v == 36} == {
        p.read_text() for v, p in discover("postgresql") if v == 36
    }
    db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["error", "missing", "timeout", "api_error", "api_missing"])
async def test_reconciler_cleans_interrupted_batch_without_replay(
    scene_runtime, monkeypatch, terminal
):
    from types import SimpleNamespace

    from octop.infra.scenes.reconciler import reconcile_queued_scene_runs

    _, jobs, calls = setup_sources(scene_runtime, monkeypatch)
    run = request(scene_runtime, jobs)
    await approve_and_wait(scene_runtime, run)
    before = list(calls)
    # Simulate durable rows left by a process death, not a real process restart.
    with scene_runtime.db.connect() as conn:
        conn.execute(
            "UPDATE scene_inspection_batches SET status='running',content='',content_sha256='',counts_json='{}' WHERE run_id=?",
            (run["run_id"],),
        )
        conn.execute(
            "UPDATE enterprise_scene_runs SET status='queued',updated_at=1 WHERE run_id=?",
            (run["run_id"],),
        )
        conn.execute(
            "UPDATE enterprise_assistant_runs SET status='running',updated_at=1 WHERE id IN (SELECT source_run_id FROM scene_inspection_sources WHERE scene_run_id=?)",
            (run["run_id"],),
        )
    AssistantService(scene_runtime.repos, scene_runtime.requester.actor).repo.recover()
    with scene_runtime.db.connect() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM enterprise_assistant_runs WHERE status='running'"
            ).fetchone()[0]
            == 3
        )
    monkeypatch.setattr(
        scene_runtime.repos.cron_repo,
        "get",
        lambda _: (
            None
            if terminal in {"missing", "api_missing"}
            else SimpleNamespace(
                last_status="error" if terminal in {"error", "api_error"} else None
            )
        ),
    )
    if terminal.startswith("api_"):
        assert scene_runtime.requester.get_run("o", run["run_id"])["status"] == "failed"
    stats = reconcile_queued_scene_runs(scene_runtime.repos, timeout_seconds=60)
    batch = SceneInspectionRepo(scene_runtime.db).get(run["run_id"])
    assert stats["failed"] == (0 if terminal.startswith("api_") else 1)
    assert batch["status"] == "failed"
    assert batch["counts"]["attempted"] == 3
    assert batch["counts"]["unknown"] == 3
    assert batch["counts"]["reports_persisted"] == 0
    assert calls == before
