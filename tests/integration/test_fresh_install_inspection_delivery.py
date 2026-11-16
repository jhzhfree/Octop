"""Fresh SQLite through public ASGI APIs, deterministic read-only inspection.

No seeded users, organizations, plans, runs, evidence or acceptance states.
Non-test network and telemetry are disabled; one case uses a real loopback
Uvicorn listener and serves the built dashboard. Only the inspection HTTP response and LLM
harness are substitutes; model invocation itself is prohibited for this mode.
"""

import asyncio
import os
import re
import socket
import time
from contextlib import asynccontextmanager

import httpx
import pytest

from octop.infra.setup.password_file import read_password
from tests.support.app import octop_client
from tests.support.auth import create_agent, seed_openai_provider
from tests.support.loopback_http import loopback_http_client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "after_failure", "wire_http"),
    [
        ("accepted", False, False),
        ("changes_requested", False, False),
        ("accepted", True, False),
        pytest.param(
            "accepted",
            False,
            True,
            marks=pytest.mark.skipif(
                os.environ.get("OCTOP_TEST_DELIVERY_HTTP") != "1",
                reason="delivery HTTP check requires built dashboard; set OCTOP_TEST_DELIVERY_HTTP=1",
            ),
        ),
    ],
)
async def test_fresh_sqlite_inspection_delivery_via_http(
    tmp_path, monkeypatch, decision, after_failure, wire_http
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "false")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")

    original_connect = socket.socket.connect
    original_getaddrinfo = socket.getaddrinfo

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

    @asynccontextmanager
    async def delivery_client():
        async with octop_client(home, bind_database=False) as (asgi, server):
            if not wire_http:
                yield asgi, server
            else:
                async with loopback_http_client(
                    asgi._octop_app, monkeypatch, original_connect, original_getaddrinfo
                ) as client:
                    yield client, server

    async with delivery_client() as (c, s):
        auth = {}

        async def req(method, path, body=None, headers=None, code=200):
            r = await c.request(
                method, "/api" + path, json=body, headers=auth if headers is None else headers
            )
            assert r.status_code == code, r.text
            return r.json()

        if wire_http:
            page = await c.get("/")
            assert page.status_code == 200 and '<div id="root">' in page.text
            assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', page.text)
            assert assets, "build the dashboard before the HTTP delivery test"
            for asset in assets:
                response = await c.get(asset)
                assert response.status_code == 200 and len(response.content) > 0
                assert "text/html" not in response.headers.get("content-type", "")
        assert not s.database_bound
        # Liveness is available even before setup; it is not model readiness.
        health = await req("GET", "/health")
        assert health["ok"] is True and health["db"] is False
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
        health = await req("GET", "/health")
        assert health["ok"] is True and health["db"] is True
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
        # A separate position/worker makes attribution observable without an
        # autonomous delegation mechanism or another model call.
        lead_position = await req("POST", op + "/positions", {"title": "Coordination"}, code=201)
        lead_agent = await create_agent(c, auth, name="Coordination worker")
        await req(
            "POST",
            op + "/positions/" + lead_position["position_id"] + "/appointments",
            {"subject_type": "agent", "subject_id": lead_agent},
            code=201,
        )
        lead_worker = await req(
            "POST",
            op + "/digital-workers",
            {
                "position_id": lead_position["position_id"],
                "agent_id": lead_agent,
                "name": "Coordination",
            },
            code=201,
        )
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
        lead_wp = op + "/digital-workers/" + lead_worker["worker_id"]
        await req("PATCH", lead_wp, {"expected_version": 1, "status": "active"})
        source_goal = await req(
            "POST",
            lead_wp + "/goals",
            {"title": "协调每日巡检", "success_criteria": "岗位交接后完成独立验收"},
            code=201,
        )
        source_gp = lead_wp + "/goals/" + source_goal["goal_id"]
        await req("PATCH", source_gp, {"expected_version": 1, "status": "active"})
        handoff_body = {
            "request_id": "delivery-handoff-001",
            "expected_source_version": 2,
            "expected_target_version": 2,
            "target_worker_id": worker["worker_id"],
            "reason": "由运维岗位执行只读巡检并提交独立验收",
            "title": "每日服务巡检",
            "description": "",
            "success_criteria": "提供可独立审阅的状态报告",
        }
        await req(
            "POST",
            source_gp + "/handoffs",
            {**handoff_body, "created_by": reviewer["id"]},
            code=422,
        )
        handoff = await req("POST", source_gp + "/handoffs", handoff_body)
        assert await req("POST", source_gp + "/handoffs", handoff_body) == handoff
        goals = await req("GET", wp + "/goals")
        goal = next(g for g in goals if g["goal_id"] == handoff["target_goal_id"])
        assert goal["status"] == "draft"
        trace = await req("GET", op + "/goal-handoffs?goal_id=" + goal["goal_id"])
        assert trace["items"][0]["source_goal_id"] == source_goal["goal_id"]
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
        failed_path = None
        if after_failure:
            # Drift invalidates the approved snapshot: no real collection is attempted.
            await req(
                "PUT",
                "/enterprise-assistant/jobs/" + job["id"],
                {
                    "expected_version": job["version"],
                    "config": {
                        "name": "updated fixture",
                        "kind": "inspection",
                        "connection_id": conn["id"],
                        "agent_id": aid,
                    },
                },
            )
            await req("POST", rp + "/approve", {"note": "Independent approval"}, headers=reviewauth)
            for _ in range(100):
                failed = await req("GET", rp)
                if failed["status"] == "failed":
                    break
                await asyncio.sleep(0.1)
            assert failed["status"] == "failed"
            assert calls == []
            failed_path = rp
            plan = await req(
                "POST",
                gp + "/plans",
                {
                    "title": "Follow-up after failure",
                    "approach": "Use reviewed current source",
                    "steps": failed["plan_snapshot"]["plan"]["steps"],
                },
                code=201,
            )
            assert plan["status"] == "draft"
            await req(
                "PATCH",
                gp + "/plans/" + plan["plan_id"],
                {"expected_version": plan["version"], "status": "ready"},
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
            assert rp != failed_path and run["status"] == "awaiting_approval"
            assert calls == []
        queue = await req("GET", op + "/run-queue?category=awaiting_approval", headers=reviewauth)
        assert any(item["run_id"] == run["run_id"] for item in queue["items"])
        detail = await req("GET", op + "/run-queue/" + run["run_id"], headers=reviewauth)
        assert detail["status"] == "awaiting_approval"
        reviewed = await req("GET", rp, headers=reviewauth)
        assert reviewed["plan_snapshot"]["inspection"]["sources"][0]["job_id"] == job["id"]
        await req("POST", rp + "/approve", {"note": "unauthenticated"}, headers={}, code=401)
        await req("POST", rp + "/approve", {"note": "self approval must fail"}, code=403)
        # A stale open reviewer page must not retain approval authority.
        member_path = "/api" + op + "/members/" + str(reviewer["id"])
        assert (await c.delete(member_path, headers=auth)).status_code == 204
        await req(
            "GET",
            op + "/run-queue?category=awaiting_approval&worker_id=" + worker["worker_id"],
            headers=reviewauth,
            code=403,
        )
        await req("GET", op + "/run-queue/" + run["run_id"], headers=reviewauth, code=403)
        await req("GET", op + "/goal-handoffs", headers=reviewauth, code=403)
        await req(
            "POST",
            source_gp + "/handoffs",
            {**handoff_body, "request_id": "revoked-handoff"},
            headers=reviewauth,
            code=403,
        )
        for action in ("approve", "reject"):
            await req(
                "POST",
                rp + "/" + action,
                {"note": "revoked reviewer"},
                headers=reviewauth,
                code=403,
            )
        await req("POST", op + "/members", {"user_id": reviewer["id"], "role": "member"}, code=201)
        await req(
            "POST", rp + "/approve", {"note": "member is not manager"}, headers=reviewauth, code=403
        )
        assert (await c.delete(member_path, headers=auth)).status_code == 204
        await req("POST", op + "/members", {"user_id": reviewer["id"], "role": "manager"}, code=201)
        other = await req(
            "POST", "/organizations", {"name": "Other synthetic organization"}, code=201
        )
        other_path = "/organizations/" + other["organization_id"]
        await req(
            "POST",
            other_path + "/members",
            {"user_id": reviewer["id"], "role": "manager"},
            code=201,
        )
        await req("GET", other_path + "/scene-runs/" + run["run_id"], headers=reviewauth, code=404)
        assert calls == []
        decisions = await asyncio.gather(
            *[
                c.post(
                    "/api" + rp + "/approve",
                    json={"note": "Independent synthetic review"},
                    headers=reviewauth,
                )
                for _ in range(2)
            ]
        )
        assert sorted(response.status_code for response in decisions) == [200, 409], [
            r.text for r in decisions
        ]
        pending = await req("GET", op + "/run-queue?category=awaiting_approval", headers=reviewauth)
        assert all(item["run_id"] != run["run_id"] for item in pending["items"])
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
                "decision": decision,
                "review_note": "Please include the retry evidence"
                if decision == "changes_requested"
                else "Evidence reviewed",
                "expected_evidence_sha256": review["evidence_sha256"],
                "expected_snapshot_sha256": review["snapshot_sha256"],
            },
            headers=reviewauth,
        )
        progress = await req(
            "GET", op + f"/progress?start={int(time.time()) - 60}&end={int(time.time()) + 60}"
        )
        assert progress["denominator"] == 1 + int(after_failure)
        assert progress["counts"] == {
            "execution": {
                "awaiting_approval": 0,
                "rejected": 0,
                "queued": 0,
                "succeeded": 1,
                "failed": int(after_failure),
            },
            "delivery": {
                "unreviewed_success": 0,
                "accepted": int(decision == "accepted"),
                "changes_requested": int(decision == "changes_requested"),
            },
        }
        assert progress["external_systems_verified"] is False
        assert progress["execution_authorized"] is False
        final_review = await req("GET", rp + "/acceptance/review", headers=reviewauth)
        assert final_review["acceptance"]["decision"] == decision
        assert final_review["acceptance"]["reviewer_id"] == reviewer["id"]
        assert final_review["evidence_sha256"] == review["evidence_sha256"]

        detail = await req("GET", op + "/run-queue/" + run["run_id"], headers=reviewauth)
        assert detail["decision"] == decision
        assigned = await req(
            "GET",
            op + "/run-queue?category=" + decision + "&worker_id=" + worker["worker_id"],
            headers=reviewauth,
        )
        assert assigned["worker_id"] == worker["worker_id"]
        goal_results = await req(
            "GET", op + "/run-queue?category=" + decision + "&goal_id=" + goal["goal_id"]
        )
        assert goal_results["goal_id"] == goal["goal_id"]
        assert [r["run_id"] for r in goal_results["items"]] == [run["run_id"]]
        source_results = await req(
            "GET", op + "/run-queue?category=" + decision + "&goal_id=" + source_goal["goal_id"]
        )
        assert source_results["items"] == []
        await req("GET", op + "/run-queue?category=" + decision + "&goal_id=missing", code=403)

        assert [item["run_id"] for item in assigned["items"]] == [run["run_id"]]
        other_worker_queue = await req(
            "GET",
            op + "/run-queue?category=" + decision + "&worker_id=" + lead_worker["worker_id"],
            headers=reviewauth,
        )
        assert other_worker_queue["items"] == []
        await req(
            "GET",
            op + "/run-queue?category=" + decision + "&worker_id=missing",
            headers=reviewauth,
            code=403,
        )
        old_queue = await req(
            "GET", op + "/run-queue?category=unreviewed_success", headers=reviewauth
        )
        assert not any(item["run_id"] == run["run_id"] for item in old_queue["items"])
        decided_queue = await req("GET", op + "/run-queue?category=" + decision, headers=reviewauth)
        assert any(item["run_id"] == run["run_id"] for item in decided_queue["items"])

        feedback = await req("GET", rp + "/acceptance")
        assert feedback["review_note"] == final_review["acceptance"]["review_note"]
        assert feedback["reviewer_id"] == reviewer["id"]
        assert feedback["decision"] == decision
        assert (
            next(
                item
                for item in await req("GET", wp + "/goals")
                if item["goal_id"] == goal["goal_id"]
            )["status"]
            == "active"
        )
        if failed_path:
            assert (await req("GET", failed_path))["status"] == "failed"
            failed_queue = await req(
                "GET",
                op + "/run-queue?category=failed&worker_id=" + worker["worker_id"],
                headers=reviewauth,
            )
            assert [item["run_id"] for item in failed_queue["items"]] == [
                failed_path.rsplit("/", 1)[1]
            ]
        if decision == "changes_requested":
            # A separate plan and run, using the same public calls as the revision UI.
            original_feedback = feedback.copy()
            revised = await req(
                "POST",
                gp + "/plans",
                {
                    "title": "Revised inspection",
                    "approach": "Repeat collection after feedback",
                    "steps": [{"action": "Inspect HTTP again", "expected_output": "Fresh report"}],
                },
                code=201,
            )
            assert revised["status"] == "draft" and revised["plan_id"] != plan["plan_id"]
            request_body = {
                "plan_id": revised["plan_id"],
                "use_memory": False,
                "capture_memory": False,
                "inspection_job_ids": [job["id"]],
                "share_inspection_results": True,
            }
            request_path = op + "/scenes/" + scene["scene_id"] + "/runs"
            await req("POST", request_path, request_body, code=409)
            await req(
                "PATCH",
                gp + "/plans/" + revised["plan_id"],
                {"expected_version": revised["version"], "status": "ready"},
            )
            next_run = await req("POST", request_path, request_body, code=201)
            assert next_run["run_id"] != run["run_id"]
            assert next_run["status"] == "awaiting_approval"
            assert len(calls) == 1  # No collection merely from creating/requesting a revision.
            next_path = op + "/scene-runs/" + next_run["run_id"]
            await req("POST", next_path + "/approve", {"note": "No self approval"}, code=403)
            await req(
                "POST",
                next_path + "/approve",
                {"note": "Independent revision approval"},
                headers=reviewauth,
            )
            for _ in range(100):
                state = await req("GET", next_path)
                if state["status"] in ("succeeded", "failed"):
                    break
                await asyncio.sleep(0.1)
            assert state["status"] == "succeeded"
            assert len(calls) == 2
            second_review = await req("GET", next_path + "/acceptance/review", headers=reviewauth)
            assert second_review["acceptance"] is None
            await req(
                "POST",
                next_path + "/acceptance",
                {
                    "decision": "accepted",
                    "review_note": "Revised delivery reviewed",
                    "expected_evidence_sha256": second_review["evidence_sha256"],
                    "expected_snapshot_sha256": second_review["snapshot_sha256"],
                },
                headers=reviewauth,
            )
            assert (await req("GET", next_path + "/acceptance"))["decision"] == "accepted"
            assert await req("GET", rp + "/acceptance") == original_feedback

    if wire_http:
        # Reopen the same temporary installation after full server shutdown.
        # Undo only the synthetic inspection transport, keeping outbound guards.
        monkeypatch.setattr(httpx, "AsyncClient", original)
        async with (
            octop_client(home) as (asgi, restarted),
            loopback_http_client(
                asgi._octop_app, monkeypatch, original_connect, original_getaddrinfo
            ) as reopened,
        ):
            login = await reopened.post(
                "/api/auth/login", json={"username": "reviewer", "password": "TestPass12"}
            )
            assert login.status_code == 200
            headers = {"Authorization": "Bearer " + login.json()["access_token"]}
            saved = await reopened.get("/api" + rp + "/acceptance/review", headers=headers)
            assert saved.status_code == 200, saved.text
            assert saved.json()["acceptance"]["decision"] == "accepted"
            assert saved.json()["evidence_sha256"] == review["evidence_sha256"]
            progress = await reopened.get(
                "/api"
                + op
                + f"/progress?start={int(time.time()) - 60}&end={int(time.time()) + 60}",
                headers=headers,
            )
            assert progress.status_code == 200
            assert progress.json()["counts"]["delivery"]["accepted"] == 1
            assert len(calls) == 1
            assert restarted.database_bound
