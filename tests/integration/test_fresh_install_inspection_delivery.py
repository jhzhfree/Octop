"""Fresh SQLite through public ASGI APIs, deterministic read-only inspection.

No seeded users, organizations, plans, runs, evidence or acceptance states.
Network and telemetry are disabled. Only the inspection HTTP response and LLM
harness are substitutes; model invocation itself is prohibited for this mode.
"""

import asyncio
import socket
import time

import httpx
import pytest

from octop.infra.setup.password_file import read_password
from tests.support.app import octop_client
from tests.support.auth import create_agent, seed_openai_provider


@pytest.mark.asyncio
async def test_fresh_sqlite_inspection_delivery_via_http(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "false")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")

    def denied(*a, **k):
        raise AssertionError("OUTBOUND SOCKET FORBIDDEN")

    for k in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, k, denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    from octop.infra.proactive.scheduler import ProactiveCareScheduler

    async def no_start(*a):
        pass

    monkeypatch.setattr(ProactiveCareScheduler, "start_all", no_start)
    monkeypatch.setattr(ProactiveCareScheduler, "ensure_scheduled", lambda *a: None)
    monkeypatch.setattr(ProactiveCareScheduler, "_schedule", lambda *a: None)
    from tests.support.fakes import FakeHarnessAgent

    async def forbidden_model(*args, **kwargs):
        raise AssertionError("Deterministic inspection must not call a model")
        yield  # make this an async iterator matching stream's interface

    monkeypatch.setattr(FakeHarnessAgent, "stream", forbidden_model)
    home = tmp_path / ".octop"
    home.mkdir()
    async with octop_client(home, bind_database=False) as (c, s):
        auth = {}

        async def req(method, path, body=None, headers=None, code=200):
            r = await c.request(
                method, "/api" + path, json=body, headers=auth if headers is None else headers
            )
            assert r.status_code == code, r.text
            return r.json()

        assert not s.database_bound
        await req("GET", "/setup/status")
        tok = (await req("POST", "/setup/verify-password", {"password": read_password(tmp_path)}))[
            "wizard_token"
        ]
        await req("POST", "/setup/database", {"driver": "sqlite", "sqlite_path": "fresh.db"})
        wh = {"Authorization": "Bearer " + tok}
        await req(
            "POST",
            "/setup/initial-admin",
            {"username": "admin", "password": "TestPass12"},
            headers=wh,
            code=201,
        )
        await req("POST", "/setup/finish", {"provider_draft": None}, headers=wh)
        auth = {
            "Authorization": "Bearer "
            + (await req("POST", "/auth/login", {"username": "admin", "password": "TestPass12"}))[
                "access_token"
            ]
        }
        reviewer = await req(
            "POST",
            "/users",
            {"username": "reviewer", "password": "TestPass12", "role": "user"},
            code=201,
        )
        reviewauth = {
            "Authorization": "Bearer "
            + (
                await req("POST", "/auth/login", {"username": "reviewer", "password": "TestPass12"})
            )["access_token"]
        }
        org = await req("POST", "/organizations", {"name": "Fresh Synthetic Operations"}, code=201)
        op = "/organizations/" + org["organization_id"]
        await req("POST", op + "/members", {"user_id": reviewer["id"], "role": "manager"}, code=201)
        pos = await req("POST", op + "/positions", {"title": "运维数字员工"}, code=201)
        await seed_openai_provider(c, auth)
        aid = await create_agent(c, auth, name="Fresh inspection agent")
        await req(
            "POST",
            op + "/positions/" + pos["position_id"] + "/appointments",
            {"subject_type": "agent", "subject_id": aid},
            code=201,
        )
        worker = await req(
            "POST",
            op + "/digital-workers",
            {"position_id": pos["position_id"], "agent_id": aid, "name": "Ops digital worker"},
            code=201,
        )
        wp = op + "/digital-workers/" + worker["worker_id"]
        await req("PATCH", wp, {"expected_version": 1, "status": "active"})
        goal = await req(
            "POST",
            wp + "/goals",
            {"title": "每日服务巡检", "success_criteria": "提供可独立审阅的状态报告"},
            code=201,
        )
        gp = wp + "/goals/" + goal["goal_id"]
        await req("PATCH", gp, {"expected_version": goal["version"], "status": "active"})
        plan = await req(
            "POST",
            gp + "/plans",
            {
                "title": "只读巡检",
                "approach": "只读HTTP查询并汇总",
                "steps": [{"action": "Inspect HTTP", "expected_output": "Report"}],
            },
            code=201,
        )
        await req(
            "PATCH", gp + "/plans/" + plan["plan_id"], {"expected_version": 1, "status": "ready"}
        )
        scene = await req(
            "POST",
            op + "/scenes",
            {
                "name": "Daily inspection",
                "position_id": pos["position_id"],
                "agent_id": aid,
                "prompt": "Read-only HTTP inspection; fixture only",
                "mcp_servers": [],
            },
            code=201,
        )
        await req(
            "PUT",
            "/enterprise-assistant/features",
            {
                "enabled": True,
                "inspection": True,
                "reports": True,
                "allowed_origins": ["https://fixture.invalid"],
            },
        )
        conn = await req(
            "POST",
            "/enterprise-assistant/connections",
            {
                "config": {
                    "name": "fixture",
                    "kind": "inspection",
                    "url": "https://fixture.invalid/status",
                }
            },
            code=200,
        )
        job = await req(
            "POST",
            "/enterprise-assistant/jobs",
            {
                "config": {
                    "name": "fixture",
                    "kind": "inspection",
                    "connection_id": conn["id"],
                    "agent_id": aid,
                }
            },
            code=200,
        )
        from octop.infra.enterprise_assistant import adapters

        original = httpx.AsyncClient
        calls = []

        def endpoint(r):
            calls.append(str(r.url))
            assert str(r.url) == "https://fixture.invalid/status"
            return httpx.Response(200, json={"status": "healthy", "disk_percent": 45})

        monkeypatch.setattr(
            adapters.httpx,
            "AsyncClient",
            lambda **kw: original(**({"transport": httpx.MockTransport(endpoint)} | kw)),
        )
        run = await req(
            "POST",
            op + "/scenes/" + scene["scene_id"] + "/runs",
            {
                "plan_id": plan["plan_id"],
                "use_memory": False,
                "capture_memory": False,
                "inspection_job_ids": [job["id"]],
                "share_inspection_results": True,
            },
            code=201,
        )
        rp = op + "/scene-runs/" + run["run_id"]
        await req("POST", rp + "/approve", {"note": "self approval must fail"}, code=403)
        await req(
            "POST", rp + "/approve", {"note": "Independent synthetic review"}, headers=reviewauth
        )
        for _ in range(100):
            r = await c.get("/api" + rp, headers=auth)
            assert r.status_code == 200, r.text
            state = r.json()
            if state["status"] in ("succeeded", "failed"):
                break
            await asyncio.sleep(0.1)
        assert state["status"] == "succeeded", state
        cron = s.app_runtime.cron_manager.get(state["cron_id"])
        assert cron is not None and cron.last_status == "ok"
        inspection = await req("GET", rp + "/inspection-result")
        assert calls == ["https://fixture.invalid/status"]
        assert inspection["counts"] == {
            "sources": 1,
            "attempted": 1,
            "succeeded": 1,
            "failed": 0,
            "unknown": 0,
            "health_unknown": 0,
            "reports_persisted": 1,
            "scene_bound": 1,
            "not_started": 0,
        }
        # Read-only persistence check; never fabricate successful state.
        from octop.infra.db.repos.scene_inspection import SceneInspectionRepo

        persisted = SceneInspectionRepo(s.services.db).get(run["run_id"])
        assert persisted["status"] == "reported"
        assert persisted["counts"] == inspection["counts"]
        assert inspection["sources"][0]["run_id"]

        review = await req("GET", rp + "/acceptance/review", headers=reviewauth)
        assert "45" in review["answer"]
        assert review["answer"] == persisted["content"]
        assert review["evidence_sha256"] == inspection["content_sha256"]
        assert review["acceptance"] is None
        await req(
            "POST",
            rp + "/acceptance",
            {
                "decision": "accepted",
                "expected_evidence_sha256": review["evidence_sha256"],
                "expected_snapshot_sha256": review["snapshot_sha256"],
            },
            headers=reviewauth,
        )
        progress = await req(
            "GET", op + f"/progress?start={int(time.time()) - 60}&end={int(time.time()) + 60}"
        )
        assert progress["denominator"] == 1
        assert progress["counts"] == {
            "execution": {
                "awaiting_approval": 0,
                "rejected": 0,
                "queued": 0,
                "succeeded": 1,
                "failed": 0,
            },
            "delivery": {"unreviewed_success": 0, "accepted": 1, "changes_requested": 0},
        }
        assert progress["external_systems_verified"] is False
        assert progress["execution_authorized"] is False
        final_review = await req("GET", rp + "/acceptance/review", headers=reviewauth)
        assert final_review["acceptance"]["decision"] == "accepted"
        assert final_review["acceptance"]["reviewer_id"] == reviewer["id"]
        assert final_review["evidence_sha256"] == review["evidence_sha256"]
