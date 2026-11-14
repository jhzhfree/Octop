"""Offline R1–R5 composition; synthetic dispatch/evidence are NOT external proof."""

import asyncio
import json
import socket

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.organization_progress import OrganizationProgressRepo
from octop.infra.db.repos.scene_acceptance import SceneAcceptanceRepo
from octop.infra.db.services import RepoBundle
from octop.infra.digital_workers.knowledge_mappings import WorkerKnowledgeMappingService
from octop.infra.digital_workers.readiness import WorkerPlanReadinessService
from octop.infra.scenes.acceptance import SceneAcceptanceService
from octop.infra.scenes.progress import OrganizationProgressService
from octop.infra.scenes.service import EnterpriseSceneService, SceneError
from octop.infra.users.identity import User


@pytest.fixture
def milestone(tmp_path, monkeypatch):
    def deny_network(*args, **kwargs):
        raise AssertionError("offline milestone must not access external services")

    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_network)
    monkeypatch.setattr(socket.socket, "sendto", deny_network)
    monkeypatch.setattr(socket, "getaddrinfo", deny_network)
    db = SqlitePool(tmp_path / "synthetic-milestone.db")
    run_migrations(db)
    repos = RepoBundle.from_pool(db)
    capabilities = {
        "knowledge_sources": [
            {
                "provider": "akb",
                "mcp_server": "akb",
                "knowledge_base_ref": "synthetic-kb",
                "access": "read",
                "read_tools": ["synthetic_read"],
            }
        ]
    }
    with db.connect() as c:
        for name in ("requester", "reviewer"):
            c.execute(
                "INSERT INTO users(username,password_hash,role,created_at) VALUES(?,'test-only','user',1)",
                (name,),
            )
        c.executescript("""
        INSERT INTO agents(agent_id,name,user_id,created_at,updated_at)
          VALUES('a','Synthetic agent',1,1,1);
        INSERT INTO organizations(organization_id,owner_user_id,name,created_at,updated_at)
          VALUES('o',1,'Synthetic organization',1,1);
        INSERT INTO organization_members(organization_id,user_id,role,created_at)
          VALUES('o',1,'owner',1),('o',2,'manager',1);
        INSERT INTO organization_positions(position_id,organization_id,title,created_at,updated_at)
          VALUES('p','o','Synthetic analyst',1,1);
        INSERT INTO organization_assignments(assignment_id,position_id,subject_type,subject_id,created_at)
          VALUES('assignment','p','agent','a',1);
        """)
        c.execute(
            "INSERT INTO digital_workers(worker_id,organization_id,position_id,agent_id,"
            "owner_user_id,name,status,capabilities_json,created_at,updated_at) "
            "VALUES('w','o','p','a',1,'Synthetic worker','active',?,1,1)",
            (json.dumps(capabilities),),
        )
    repos.enterprise_scene_repo.create(
        scene_id="s",
        organization_id="o",
        position_id="p",
        agent_id="a",
        owner_user_id=1,
        name="Synthetic scene",
        prompt="Offline fixture only",
        mcp_servers=["akb"],
    )
    repos.external_knowledge_repo.create(
        source_id="e",
        owner_user_id=1,
        kind="akb-mcp",
        display_name="Synthetic KB",
        endpoint="https://example.invalid",
        ref="synthetic-kb",
        auth_type="agent",
    )
    repos.external_knowledge_repo.set_binding(
        source_id="e",
        target_type="agent",
        target_id="a",
        enabled=True,
    )
    goal = repos.digital_worker_repo.create_goal(
        worker_id="w",
        title="Synthetic goal",
        description="Offline simulation",
        success_criteria="Reviewer accepts fixture report",
        period_start=None,
        period_end=None,
        created_by=1,
    )
    repos.digital_worker_repo.update_goal(goal["goal_id"], {"status": "active"})
    plan = repos.digital_worker_repo.create_plan(
        goal_id=goal["goal_id"],
        title="Synthetic plan",
        approach="No external I/O",
        steps=[{"action": "Simulate", "expected_output": "Fixture report"}],
        created_by=1,
    )
    repos.digital_worker_repo.update_plan(plan["plan_id"], {"status": "ready"}, expected_version=1)
    try:
        yield db, repos, goal["goal_id"], plan["plan_id"]
    finally:
        db.close()


@pytest.mark.parametrize("decision", ["accepted", "changes_requested"])
def test_offline_mapping_to_independent_review_and_progress(milestone, decision):
    db, repos, goal_id, plan_id = milestone
    requester = User(1, "requester", "user", None)
    reviewer = User(2, "reviewer", "user", None)
    mappings = WorkerKnowledgeMappingService(repos, requester)
    draft = mappings.create_draft(
        "o",
        "w",
        mcp_server="akb",
        knowledge_base_ref="synthetic-kb",
        external_source_id="e",
        expected_capability_version=1,
        expected_connection_revision=1,
        expected_resource_revision=1,
    )
    observed = mappings.inspect("o", "w", draft["mapping_id"])
    assert observed["status"] == "draft" and observed["local_snapshot_current"]
    assert observed["retrieval_enabled"] is False
    assert observed["authorization_status"] == "delegated_user_acl_unverified"
    readiness = WorkerPlanReadinessService(repos, requester).inspect(
        "o",
        "w",
        goal_id,
        plan_id,
        expected_worker_version=1,
        expected_plan_version=2,
    )
    assert readiness.execution_authorized is False
    checks = {check.code: check.status for check in readiness.checks}
    assert checks["knowledge_user_acl"] == checks["runtime_approval"] == "unknown"
    assert checks["model_budget"] == "unknown"

    scenes = EnterpriseSceneService(repos, requester)
    run = scenes.request_run("o", "s", plan_id=plan_id, use_memory=False, capture_memory=False)
    rid = run["run_id"]
    assert run["status"] == "awaiting_approval"
    with pytest.raises(SceneError) as error:
        asyncio.run(scenes.decide_run("o", rid, approve=True, note="", server=None))
    assert error.value.status == 403

    # Deliberate simulation boundary: seed independent approval + terminal execution.
    # This DOES NOT test actual cron dispatch, model execution, or connector ACL.
    assert repos.enterprise_scene_repo.decide_run(
        rid,
        approved_by=2,
        cron_id="synthetic-cron",
        status="queued",
        note="SIMULATED approval",
    )
    repos.enterprise_scene_repo.sync_cron_status(rid, cron_id="synthetic-cron", status="succeeded")
    progress = OrganizationProgressService(OrganizationProgressRepo(db), 2)
    window = {"start": run["created_at"], "end": run["created_at"] + 60}
    before = progress.inspect("o", **window)
    assert before["counts"]["execution"]["succeeded"] == 1
    assert before["counts"]["delivery"]["unreviewed_success"] == 1
    assert before["counts"]["delivery"]["accepted"] == 0

    async def synthetic_evidence(row):
        assert row["run_id"] == rid
        return "SIMULATED report, no AKB or model was contacted"

    acceptance_repo = SceneAcceptanceRepo(db)
    own_review = SceneAcceptanceService(scenes, acceptance_repo, synthetic_evidence)
    with pytest.raises(SceneError) as error:
        asyncio.run(own_review.review("o", rid))
    assert error.value.status == 403
    acceptance = SceneAcceptanceService(
        EnterpriseSceneService(repos, reviewer),
        acceptance_repo,
        synthetic_evidence,
    )
    with pytest.raises(SceneError) as error:
        asyncio.run(acceptance.review("other", rid))
    assert error.value.status in {403, 404}
    review = asyncio.run(acceptance.review("o", rid))
    assert review["acceptance"] is None
    kwargs = {
        "decision": decision,
        "expected_evidence_sha256": review["evidence_sha256"],
        "expected_snapshot_sha256": review["snapshot_sha256"],
    }
    with pytest.raises(SceneError) as error:
        asyncio.run(
            acceptance.decide("o", rid, **(kwargs | {"expected_evidence_sha256": "0" * 64}))
        )
    assert error.value.status == 409
    result = asyncio.run(acceptance.decide("o", rid, **kwargs))
    assert result["decision"] == decision
    assert asyncio.run(acceptance.decide("o", rid, **kwargs)) == result
    after = progress.inspect("o", **window)
    assert after["denominator"] == 1 and after["unit"] == "run"
    assert after["counts"]["delivery"][decision] == 1
    assert after["counts"]["delivery"]["unreviewed_success"] == 0
    assert after["counts"]["execution"]["succeeded"] == 1
    assert after["execution_authorized"] is False and after["external_systems_verified"] is False
    assert repos.digital_worker_repo.get_goal(goal_id)["status"] == "active"
    assert repos.digital_worker_repo.get_plan(plan_id)["status"] == "ready"
    assert mappings.inspect("o", "w", draft["mapping_id"])["retrieval_enabled"] is False
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM scene_run_acceptances").fetchone()[0] == 1
        audit_rows = [dict(row) for row in c.execute("SELECT * FROM audit_log").fetchall()]
        assert "SIMULATED report" not in json.dumps(audit_rows)
