"""Real scene lifecycle with deterministic model output, no network or live assets.

Unlike the milestone data-composition fixture, this module never seeds approved,
queued or succeeded runs and never injects evidence. Production services create
all execution state, project output and resolve the evidence used for acceptance.
Only AgentManager/model execution is replaced; Gateway is real and unbooted so
an attempt to use an external channel is a test failure, not a delivery sink.
"""

from __future__ import annotations

import asyncio
import socket
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from octop.infra.cron.delivery import CronDeliveryService
from octop.infra.cron.manager import CronManager
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.organization_progress import OrganizationProgressRepo
from octop.infra.db.repos.scene_acceptance import SceneAcceptanceRepo
from octop.infra.db.services import RepoBundle
from octop.infra.gateway.gateway import Gateway
from octop.infra.scenes.acceptance import SceneAcceptanceService
from octop.infra.scenes.evidence import scene_run_evidence
from octop.infra.scenes.progress import OrganizationProgressService
from octop.infra.scenes.service import EnterpriseSceneService, SceneError
from octop.infra.users.identity import User

REPORT = "OFFLINE FIXTURE: health=unknown; no live server checked"


@pytest.fixture
def scene_runtime(tmp_path, monkeypatch):
    def deny_network(*args, **kwargs):
        raise AssertionError("offline scene execution must not access the network")

    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, deny_network)
    monkeypatch.setattr(socket, "getaddrinfo", deny_network)
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path / "home"))
    db = SqlitePool(tmp_path / "scene-execution.db")
    run_migrations(db)
    repos = RepoBundle.from_pool(db)
    with db.connect() as connection:
        for name in ("requester", "reviewer"):
            connection.execute(
                "INSERT INTO users(username,password_hash,role,created_at) "
                "VALUES(?,'test-only','user',1)",
                (name,),
            )
        connection.executescript("""
        INSERT INTO agents(agent_id,name,user_id,created_at,updated_at)
          VALUES('a','Offline inspection agent',1,1,1);
        INSERT INTO organizations(organization_id,owner_user_id,name,created_at,updated_at)
          VALUES('o',1,'Synthetic operations company',1,1);
        INSERT INTO organization_members(organization_id,user_id,role,created_at)
          VALUES('o',1,'owner',1),('o',2,'manager',1);
        INSERT INTO organization_positions(position_id,organization_id,title,created_at,updated_at)
          VALUES('p','o','Read-only inspector',1,1);
        INSERT INTO organization_assignments(assignment_id,position_id,subject_type,subject_id,created_at)
          VALUES('assignment','p','agent','a',1);
        INSERT INTO digital_workers(worker_id,organization_id,position_id,agent_id,
          owner_user_id,name,status,capabilities_json,created_at,updated_at)
          VALUES('w','o','p','a',1,'Offline inspector','active','{}',1,1);
        """)
    repos.enterprise_scene_repo.create(
        scene_id="s",
        organization_id="o",
        position_id="p",
        agent_id="a",
        owner_user_id=1,
        name="Morning inspection",
        prompt="Offline fixture only",
        mcp_servers=[],
    )
    goal = repos.digital_worker_repo.create_goal(
        worker_id="w",
        title="Morning report",
        description="No live assets",
        success_criteria="Unknown facts stay unknown; independent reviewer checks report",
        period_start=None,
        period_end=None,
        created_by=1,
    )
    repos.digital_worker_repo.update_goal(goal["goal_id"], {"status": "active"})
    plan = repos.digital_worker_repo.create_plan(
        goal_id=goal["goal_id"],
        title="Offline inspection plan",
        approach="No external I/O",
        steps=[{"action": "Inspect fixture", "expected_output": "Explicitly offline report"}],
        created_by=1,
    )
    repos.digital_worker_repo.update_plan(plan["plan_id"], {"status": "ready"}, expected_version=1)

    model = SimpleNamespace(
        calls=[], failure=False, output=REPORT, chunk_type="token", text_key="content"
    )
    agent_manager = MagicMock()
    agent_manager.get_row.return_value = SimpleNamespace(default_model=None)
    agent_manager.default_mcp_servers.return_value = []
    agent_manager.default_knowledge_base_ids.return_value = []
    agent_manager.merge_turn_mcp_servers.return_value = []

    async def offline_stream(agent_id, request):
        model.calls.append((agent_id, request))
        if model.failure:
            raise RuntimeError("OFFLINE FIXTURE model failure")
        yield {"type": model.chunk_type, model.text_key: model.output}

    agent_manager.stream = offline_stream
    gateway = Gateway(agent_manager=agent_manager, repos=repos)
    delivery = CronDeliveryService(gateway=gateway, agent_manager=agent_manager, repos=repos)
    manager = CronManager(gateway=gateway, delivery_service=delivery, repos=repos, timezone="UTC")
    requester = EnterpriseSceneService(repos, User(1, "requester", "user", None))
    reviewer = EnterpriseSceneService(repos, User(2, "reviewer", "user", None))

    async def evidence(run):
        return await scene_run_evidence(
            run=run,
            cron_manager=manager,
            thread_registry=gateway.thread_registry,
            agent_registry=agent_manager,
            thread_message_repo=repos.thread_message_repo,
            history_archive=None,
        )

    try:
        yield SimpleNamespace(
            db=db,
            repos=repos,
            goal_id=goal["goal_id"],
            plan_id=plan["plan_id"],
            model=model,
            gateway=gateway,
            manager=manager,
            requester=requester,
            reviewer=reviewer,
            server=SimpleNamespace(app_runtime=SimpleNamespace(cron_manager=manager)),
            acceptance=SceneAcceptanceService(reviewer, SceneAcceptanceRepo(db), evidence),
        )
    finally:
        db.close()


async def approve_and_wait(runtime, run):
    approved = await runtime.reviewer.decide_run(
        "o",
        run["run_id"],
        approve=True,
        note="Review offline plan",
        server=runtime.server,
    )

    async def finished():
        while True:
            row = runtime.repos.cron_repo.get(approved["cron_id"])
            if row.last_status:
                return row
            await asyncio.sleep(0.01)

    return await asyncio.wait_for(finished(), timeout=5)


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["accepted", "changes_requested"])
@pytest.mark.parametrize("chunk_type,text_key", [("token", "content"), ("delta", "text")])
async def test_real_dispatch_projection_and_independent_acceptance(
    scene_runtime,
    decision,
    chunk_type,
    text_key,
):
    runtime = scene_runtime
    runtime.model.chunk_type, runtime.model.text_key = chunk_type, text_key
    run = runtime.requester.request_run(
        "o",
        "s",
        plan_id=runtime.plan_id,
        use_memory=False,
        capture_memory=False,
    )
    assert run["status"] == "awaiting_approval"
    with pytest.raises(SceneError) as own_approval:
        await runtime.requester.decide_run(
            "o",
            run["run_id"],
            approve=True,
            note="",
            server=runtime.server,
        )
    assert own_approval.value.status == 403
    cron = await approve_and_wait(runtime, run)
    assert cron.last_status == "ok", cron.last_error
    assert len(runtime.model.calls) == 1
    assert runtime.gateway._channel_manager is None  # no external transport was booted
    result = runtime.reviewer.get_run("o", run["run_id"])
    assert result["status"] == "succeeded"
    with pytest.raises(SceneError) as duplicate:
        await runtime.reviewer.decide_run(
            "o",
            run["run_id"],
            approve=True,
            note="Duplicate",
            server=runtime.server,
        )
    assert duplicate.value.status == 409
    assert len(runtime.model.calls) == 1
    with pytest.raises(SceneError) as foreign:
        await runtime.acceptance.review("other", run["run_id"])
    assert foreign.value.status in {403, 404}
    review = await runtime.acceptance.review("o", run["run_id"])
    assert review["answer"] == REPORT
    accepted = await runtime.acceptance.decide(
        "o",
        run["run_id"],
        decision=decision,
        expected_evidence_sha256=review["evidence_sha256"],
        expected_snapshot_sha256=review["snapshot_sha256"],
    )
    assert accepted["decision"] == decision
    progress = OrganizationProgressService(OrganizationProgressRepo(runtime.db), 2).inspect(
        "o",
        start=run["created_at"],
        end=run["created_at"] + 60,
    )
    assert progress["counts"]["delivery"][decision] == 1
    assert progress["counts"]["execution"]["succeeded"] == 1
    assert runtime.repos.digital_worker_repo.get_goal(runtime.goal_id)["status"] == "active"
    await runtime.manager.shutdown()


def test_missing_plan_is_rejected_before_dispatch(scene_runtime):
    runtime = scene_runtime
    with pytest.raises(SceneError) as error:
        runtime.requester.request_run("o", "s", use_memory=False, capture_memory=False)
    assert error.value.status == 422
    assert error.value.code == "SCENE_PLAN_REQUIRED"
    assert runtime.repos.cron_repo.list_all() == []
    assert runtime.model.calls == []
    with runtime.db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM enterprise_scene_runs").fetchone()[0] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,output", [(True, REPORT), (False, "")])
async def test_failed_or_empty_model_output_cannot_be_accepted(scene_runtime, failure, output):
    runtime = scene_runtime
    runtime.model.failure, runtime.model.output = failure, output
    run = runtime.requester.request_run(
        "o",
        "s",
        plan_id=runtime.plan_id,
        use_memory=False,
        capture_memory=False,
    )
    cron = await approve_and_wait(runtime, run)
    assert cron.last_status == "error"
    assert runtime.reviewer.get_run("o", run["run_id"])["status"] == "failed"
    with pytest.raises(SceneError) as error:
        await runtime.acceptance.review("o", run["run_id"])
    assert error.value.status == 409
    assert runtime.repos.cron_repo.get(cron.cron_id).last_status == "error"
    await runtime.manager.shutdown()


@pytest.mark.asyncio
async def test_legacy_request_without_snapshot_is_readable_and_rejectable(scene_runtime):
    runtime = scene_runtime
    # Legacy compatibility fixture, not an approved/successful execution fixture.
    legacy = runtime.repos.enterprise_scene_repo.create_run(
        run_id="legacy-no-plan",
        scene=runtime.repos.enterprise_scene_repo.get("s"),
        requested_by=1,
    )
    assert runtime.reviewer.get_run("o", legacy["run_id"])["status"] == "awaiting_approval"
    with pytest.raises(SceneError) as error:
        await runtime.reviewer.decide_run(
            "o",
            legacy["run_id"],
            approve=True,
            note="",
            server=runtime.server,
        )
    assert error.value.code == "SCENE_PLAN_REQUIRED"
    assert error.value.status == 409
    assert runtime.repos.cron_repo.list_all() == []
    assert runtime.model.calls == []
    rejected = await runtime.reviewer.decide_run(
        "o",
        legacy["run_id"],
        approve=False,
        note="Resubmit with ready plan",
        server=None,
    )
    assert rejected["status"] == "rejected"
    assert runtime.repos.cron_repo.list_all() == []
    await runtime.manager.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["append_failure", "not_ready", "zero_inserted"])
async def test_internal_projection_fault_cannot_be_a_success(scene_runtime, monkeypatch, fault):
    runtime = scene_runtime
    if fault == "append_failure":

        def fail_append(*args, **kwargs):
            raise RuntimeError("OFFLINE FIXTURE projection storage failure")

        monkeypatch.setattr(runtime.repos.thread_message_repo, "append_if_ready", fail_append)
    elif fault == "zero_inserted":
        monkeypatch.setattr(runtime.repos.thread_message_repo, "append_if_ready", lambda *a, **k: 0)
    else:
        original_run_now = runtime.manager.run_now

        async def dispatch_with_pending_projection(cron_id, **kwargs):
            cron = runtime.repos.cron_repo.get(cron_id)
            session = runtime.gateway.thread_registry.get_session(cron.session_key)
            runtime.repos.thread_message_repo.mark_projection(session.thread_id, "pending")
            await original_run_now(cron_id, **kwargs)

        monkeypatch.setattr(runtime.manager, "run_now", dispatch_with_pending_projection)
    run = runtime.requester.request_run(
        "o",
        "s",
        plan_id=runtime.plan_id,
        use_memory=False,
        capture_memory=False,
    )
    cron = await approve_and_wait(runtime, run)
    assert cron.last_status == "error"
    assert ("not persisted" if fault == "zero_inserted" else "projection") in cron.last_error
    assert len(runtime.model.calls) == 1
    assert runtime.reviewer.get_run("o", run["run_id"])["status"] == "failed"
    with pytest.raises(SceneError) as error:
        await runtime.acceptance.review("o", run["run_id"])
    assert error.value.status == 409
    await runtime.manager.shutdown()


@pytest.mark.asyncio
async def test_outbound_bound_internal_session_fails_before_model(scene_runtime, monkeypatch):
    runtime = scene_runtime
    original_run_now = runtime.manager.run_now

    async def dispatch_with_corrupt_binding(cron_id, **kwargs):
        cron = runtime.repos.cron_repo.get(cron_id)
        # Fault injection into an otherwise real approved run: internal results
        # must never be redirected to any external channel identifier.
        with runtime.db.connect() as connection:
            connection.execute(
                "UPDATE sessions SET channel_id=? WHERE session_key=?",
                ("forbidden-external-channel", cron.session_key),
            )
        await original_run_now(cron_id, **kwargs)

    monkeypatch.setattr(runtime.manager, "run_now", dispatch_with_corrupt_binding)
    run = runtime.requester.request_run(
        "o",
        "s",
        plan_id=runtime.plan_id,
        use_memory=False,
        capture_memory=False,
    )
    cron = await approve_and_wait(runtime, run)
    assert cron.last_status == "error"
    assert runtime.model.calls == []
    assert runtime.gateway._channel_manager is None
    assert runtime.reviewer.get_run("o", run["run_id"])["status"] == "failed"
    await runtime.manager.shutdown()
