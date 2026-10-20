"""Virtual worker profile, goal, and organization access coverage."""

from __future__ import annotations

import json

from tests.support.auth import TEST_PASSWORD, bearer, create_agent, login


async def test_digital_worker_profile_and_stage_goals(env, monkeypatch):
    client, server, owner_auth = env

    async def allow_test_mcp_servers(_server, *, user_id, names):
        return names

    monkeypatch.setattr(
        "octop.api.common.validators.validate_chat_mcp_servers", allow_test_mcp_servers
    )
    user = await client.post(
        "/api/users",
        headers=owner_auth,
        json={"username": "worker_member", "password": TEST_PASSWORD, "role": "user"},
    )
    assert user.status_code == 201, user.text
    user_id = user.json()["id"]
    organization = await client.post(
        "/api/organizations", headers=owner_auth, json={"name": "Digital Workforce"}
    )
    organization_id = organization.json()["organization_id"]
    added = await client.post(
        f"/api/organizations/{organization_id}/members",
        headers=owner_auth,
        json={"user_id": user_id, "role": "member"},
    )
    assert added.status_code == 201, added.text
    manager_user = await client.post(
        "/api/users",
        headers=owner_auth,
        json={"username": "worker_manager", "password": TEST_PASSWORD, "role": "user"},
    )
    assert manager_user.status_code == 201, manager_user.text
    manager_added = await client.post(
        f"/api/organizations/{organization_id}/members",
        headers=owner_auth,
        json={"user_id": manager_user.json()["id"], "role": "manager"},
    )
    assert manager_added.status_code == 201, manager_added.text
    manager_auth = bearer(await login(client, username="worker_manager", password=TEST_PASSWORD))
    position = await client.post(
        f"/api/organizations/{organization_id}/positions",
        headers=owner_auth,
        json={"title": "Service Analyst", "duties": "Review service health and report risks"},
    )
    position_id = position.json()["position_id"]
    agent_id = await create_agent(client, owner_auth, name="Service Analyst Agent")
    agent_row = server.services.agent_repo.get(agent_id)
    assert agent_row is not None
    skill_package = server.services.skill_package_repo.create(
        id="worker-approved-skill",
        name="Metrics analysis",
        created_by=str(agent_row.user_id),
    )
    knowledge_base = server.services.knowledge_repo.create_base(
        owner_user_id=int(agent_row.user_id),
        name="Service metrics handbook",
    )
    server.services.agent_repo.update_config(
        agent_id,
        skill_package_ids=json.dumps([skill_package.id]),
        knowledge_base_ids=json.dumps([knowledge_base.id]),
    )

    not_appointed = await client.post(
        f"/api/organizations/{organization_id}/digital-workers",
        headers=owner_auth,
        json={"position_id": position_id, "agent_id": agent_id, "name": "Analyst"},
    )
    assert not_appointed.status_code == 409

    appointment = await client.post(
        f"/api/organizations/{organization_id}/positions/{position_id}/appointments",
        headers=owner_auth,
        json={"subject_type": "agent", "subject_id": agent_id},
    )
    assert appointment.status_code == 201, appointment.text
    blank_name = await client.post(
        f"/api/organizations/{organization_id}/digital-workers",
        headers=owner_auth,
        json={"position_id": position_id, "agent_id": agent_id, "name": "   "},
    )
    assert blank_name.status_code == 422
    created = await client.post(
        f"/api/organizations/{organization_id}/digital-workers",
        headers=owner_auth,
        json={"position_id": position_id, "agent_id": agent_id, "name": "虚拟服务分析师"},
    )
    assert created.status_code == 201, created.text
    worker = created.json()
    assert worker["responsibilities"] == "Review service health and report risks"
    assert worker["status"] == "draft"
    assert worker["version"] == 1
    blank_name_update = await client.patch(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}",
        headers=owner_auth,
        json={"expected_version": 1, "name": "   "},
    )
    assert blank_name_update.status_code == 422

    duplicate = await client.post(
        f"/api/organizations/{organization_id}/digital-workers",
        headers=owner_auth,
        json={"position_id": position_id, "agent_id": agent_id, "name": "Duplicate"},
    )
    assert duplicate.status_code == 409

    member_auth = bearer(await login(client, username="worker_member", password=TEST_PASSWORD))
    visible = await client.get(
        f"/api/organizations/{organization_id}/digital-workers", headers=member_auth
    )
    assert visible.status_code == 200, visible.text
    candidate_list = await client.get(
        f"/api/organizations/{organization_id}/memory-candidates",
        headers=manager_auth,
    )
    assert candidate_list.status_code == 200, candidate_list.text
    assert candidate_list.json() == []
    member_candidate_list = await client.get(
        f"/api/organizations/{organization_id}/memory-candidates",
        headers=member_auth,
    )
    assert member_candidate_list.status_code == 403
    assert visible.json()[0]["worker_id"] == worker["worker_id"]
    denied_patch = await client.patch(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}",
        headers=member_auth,
        json={"expected_version": 1, "status": "active"},
    )
    assert denied_patch.status_code == 403

    goal_response = await client.post(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}/goals",
        headers=owner_auth,
        json={
            "title": "建立服务周报",
            "description": "覆盖核心系统运行状况",
            "success_criteria": "每周提交一份含来源和风险项的周报",
            "period_start": "2026-10-01",
            "period_end": "2026-12-31",
        },
    )
    assert goal_response.status_code == 201, goal_response.text
    goal = goal_response.json()
    assert goal["status"] == "draft"
    blank_goal = await client.post(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}/goals",
        headers=owner_auth,
        json={"title": "  ", "success_criteria": "  "},
    )
    assert blank_goal.status_code == 422
    member_goals = await client.get(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}/goals",
        headers=member_auth,
    )
    assert member_goals.status_code == 200, member_goals.text
    assert member_goals.json()[0]["goal_id"] == goal["goal_id"]

    invalid_period = await client.patch(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}/goals/{goal['goal_id']}",
        headers=owner_auth,
        json={"period_start": "2027-01-01"},
    )
    assert invalid_period.status_code == 422

    activated_goal = await client.patch(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}/goals/{goal['goal_id']}",
        headers=owner_auth,
        json={"status": "active"},
    )
    assert activated_goal.status_code == 200, activated_goal.text
    plan_response = await client.post(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}"
        f"/goals/{goal['goal_id']}/plans",
        headers=owner_auth,
        json={"title": "每周服务巡检计划"},
    )
    assert plan_response.status_code == 201, plan_response.text
    plan = plan_response.json()
    assert plan["status"] == "draft"
    assert plan["steps"] == []
    blank_plan = await client.post(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}"
        f"/goals/{goal['goal_id']}/plans",
        headers=owner_auth,
        json={"title": "   "},
    )
    assert blank_plan.status_code == 422
    blank_step = await client.patch(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}"
        f"/goals/{goal['goal_id']}/plans/{plan['plan_id']}",
        headers=owner_auth,
        json={
            "expected_version": 1,
            "steps": [{"action": "  ", "expected_output": "report"}],
        },
    )
    assert blank_step.status_code == 422
    member_plans = await client.get(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}"
        f"/goals/{goal['goal_id']}/plans",
        headers=member_auth,
    )
    assert member_plans.status_code == 200
    assert member_plans.json()[0]["plan_id"] == plan["plan_id"]
    denied_plan_patch = await client.patch(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}"
        f"/goals/{goal['goal_id']}/plans/{plan['plan_id']}",
        headers=member_auth,
        json={"expected_version": 1, "status": "cancelled"},
    )
    assert denied_plan_patch.status_code == 403
    incomplete_plan = await client.patch(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}"
        f"/goals/{goal['goal_id']}/plans/{plan['plan_id']}",
        headers=owner_auth,
        json={"expected_version": 1, "status": "ready"},
    )
    assert incomplete_plan.status_code == 409

    activated = await client.patch(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}",
        headers=owner_auth,
        json={"expected_version": 1, "status": "active"},
    )
    assert activated.status_code == 200, activated.text
    ready_plan = await client.patch(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}"
        f"/goals/{goal['goal_id']}/plans/{plan['plan_id']}",
        headers=owner_auth,
        json={
            "expected_version": 1,
            "approach": "先检查监控和告警，再核对变更与未解决事件",
            "steps": [
                {
                    "action": "汇总核心服务指标和告警",
                    "expected_output": "带时间范围和来源链接的指标摘要",
                },
                {
                    "action": "核查异常变化并列出风险",
                    "expected_output": "包含影响、证据和建议的风险清单",
                },
            ],
            "status": "ready",
        },
    )
    assert ready_plan.status_code == 200, ready_plan.text
    assert ready_plan.json()["version"] == 2
    assert ready_plan.json()["status"] == "ready"
    scene_response = await client.post(
        f"/api/organizations/{organization_id}/scenes",
        headers=owner_auth,
        json={
            "name": "服务周报场景",
            "position_id": position_id,
            "agent_id": agent_id,
            "prompt": "执行服务状态检查",
            "mcp_servers": [],
        },
    )
    assert scene_response.status_code == 201, scene_response.text
    scene_id = scene_response.json()["scene_id"]
    # The fixture has no live MCP service. Keep this contract test offline and let the
    # request validator accept the scene's synthetic connector name.
    scene_with_akb = server.services.enterprise_scene_repo.update(
        scene_id,
        {"mcp_servers": ["akb__1"]},
        expected_version=1,
        actor_id=1,
    )
    assert scene_with_akb is not None
    unauthorized_source = await client.put(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}"
        "/akb-knowledge-sources",
        headers=owner_auth,
        json={
            "expected_capability_version": 1,
            "sources": [{"mcp_server": "akb__other", "knowledge_base_code": "ops-runbook"}],
        },
    )
    assert unauthorized_source.status_code == 409
    bound_sources = await client.put(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}"
        "/akb-knowledge-sources",
        headers=owner_auth,
        json={
            "expected_capability_version": 1,
            "sources": [{"mcp_server": "akb__1", "knowledge_base_code": "ops-runbook"}],
        },
    )
    assert bound_sources.status_code == 200, bound_sources.text
    assert bound_sources.json()["capabilities"]["akb_knowledge_sources"] == [
        {
            "provider": "akb",
            "mcp_server": "akb__1",
            "knowledge_base_code": "ops-runbook",
            "access": "read",
        }
    ]
    assert bound_sources.json()["capability_version"] == 2
    source_audit = server.services.audit_repo.query(
        action="digital_worker.akb_knowledge_sources_update", limit=1
    )
    assert source_audit[0].payload == json.dumps(
        [
            {
                "provider": "akb",
                "mcp_server": "akb__1",
                "knowledge_base_code": "ops-runbook",
                "access": "read",
            }
        ],
        ensure_ascii=False,
    )
    stale_source_update = await client.put(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}"
        "/akb-knowledge-sources",
        headers=owner_auth,
        json={"expected_capability_version": 1, "sources": []},
    )
    assert stale_source_update.status_code == 409
    other_position = await client.post(
        f"/api/organizations/{organization_id}/positions",
        headers=owner_auth,
        json={"title": "不同岗位"},
    )
    other_assignment = await client.post(
        f"/api/organizations/{organization_id}/positions/"
        f"{other_position.json()['position_id']}/appointments",
        headers=owner_auth,
        json={"subject_type": "agent", "subject_id": agent_id},
    )
    assert other_assignment.status_code == 201, other_assignment.text
    other_scene = await client.post(
        f"/api/organizations/{organization_id}/scenes",
        headers=owner_auth,
        json={
            "name": "不同岗位场景",
            "position_id": other_position.json()["position_id"],
            "agent_id": agent_id,
            "prompt": "执行无关任务",
            "mcp_servers": [],
        },
    )
    assert other_scene.status_code == 201, other_scene.text
    mismatched_run = await client.post(
        f"/api/organizations/{organization_id}/scenes/{other_scene.json()['scene_id']}/runs",
        headers=owner_auth,
        json={"plan_id": plan["plan_id"]},
    )
    assert mismatched_run.status_code == 409
    run_request = await client.post(
        f"/api/organizations/{organization_id}/scenes/{scene_id}/runs",
        headers=owner_auth,
        json={"plan_id": plan["plan_id"]},
    )
    assert run_request.status_code == 201, run_request.text
    run = run_request.json()
    assert run["status"] == "awaiting_approval"
    assert run["plan_id"] == plan["plan_id"]
    assert run["plan_snapshot"]["goal"]["goal_id"] == goal["goal_id"]
    assert run["plan_snapshot"]["plan"]["version"] == 2
    assert run["plan_snapshot"]["capabilities"]["akb_knowledge_sources"] == [
        {
            "provider": "akb",
            "mcp_server": "akb__1",
            "knowledge_base_code": "ops-runbook",
            "access": "read",
        }
    ]
    assert "汇总核心服务指标和告警" in run["prompt_snapshot"]
    manager = server.app_runtime.cron_manager
    old_create = manager.create
    old_run_now = manager.run_now
    started: list[str] = []
    assert started == []

    async def stub_create(_spec):
        await old_create(_spec)

    async def record_run(cron_id: str, *, wait: bool = False) -> None:
        assert wait is False
        started.append(cron_id)

    manager.create = stub_create
    manager.run_now = record_run
    try:
        approved_run = await client.post(
            f"/api/organizations/{organization_id}/scene-runs/{run['run_id']}/approve",
            headers=manager_auth,
            json={"note": "计划范围和预期产出已审核"},
        )
    finally:
        manager.create = old_create
        manager.run_now = old_run_now
    assert approved_run.status_code == 200, approved_run.text
    assert approved_run.json()["status"] == "queued"
    assert approved_run.json()["plan_id"] == plan["plan_id"]
    assert started == [approved_run.json()["cron_id"]]
    linked_run = server.services.enterprise_scene_repo.get_run_by_cron_id(
        approved_run.json()["cron_id"]
    )
    assert linked_run is not None and linked_run["run_id"] == run["run_id"]
    assert (
        linked_run["plan_snapshot"]["capabilities"]["knowledge_sources"]
        == run["plan_snapshot"]["capabilities"]["knowledge_sources"]
    )
    incomplete_evidence = await client.post(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}"
        "/capability-proposals",
        headers=owner_auth,
        json={
            "source_run_id": run["run_id"],
            "source_summary": "尚未完成",
            "observed_gaps": "尚未观察",
            "proposed_changes": {"skills": ["新增技能"]},
            "expected_benefit": "更好",
        },
    )
    assert incomplete_evidence.status_code == 409
    # Mark the approval-backed run successful so it can serve as improvement evidence.
    server.services.enterprise_scene_repo.sync_cron_status(
        run["run_id"], cron_id=approved_run.json()["cron_id"], status="succeeded"
    )
    proposal_payload = {
        "source_run_id": run["run_id"],
        "source_summary": "运行完成了服务指标汇总和风险核查。",
        "observed_gaps": "异常指标缺少统一的时间窗口径。",
        "proposed_changes": {
            "skills": ["统一指标时间窗口径"],
            "knowledge": ["核心服务指标定义"],
            "operating_guidance": ["报告中标注指标时间范围与数据来源"],
            "skill_package_ids": [skill_package.id],
            "knowledge_base_ids": [knowledge_base.id],
        },
        "expected_benefit": "减少周报中的口径歧义。",
        "risk_notes": "需确认指标定义仍适用于当前服务版本。",
    }
    blank_proposal = await client.post(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}"
        "/capability-proposals",
        headers=owner_auth,
        json={**proposal_payload, "source_summary": "   "},
    )
    assert blank_proposal.status_code == 422
    unauthorized_resource = await client.post(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}"
        "/capability-proposals",
        headers=owner_auth,
        json={
            **proposal_payload,
            "proposed_changes": {"skill_package_ids": ["not-authorized"]},
        },
    )
    assert unauthorized_resource.status_code == 409
    proposal_response = await client.post(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}"
        "/capability-proposals",
        headers=owner_auth,
        json=proposal_payload,
    )
    assert proposal_response.status_code == 201, proposal_response.text
    proposal = proposal_response.json()
    assert proposal["status"] == "submitted"
    assert proposal["base_capability_version"] == 2
    assert proposal["proposed_changes"]["skills"] == ["统一指标时间窗口径"]
    self_review = await client.post(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}"
        f"/capability-proposals/{proposal['proposal_id']}/review",
        headers=owner_auth,
        json={"approve": True, "review_note": "自审不应生效"},
    )
    assert self_review.status_code == 403
    published = await client.post(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}"
        f"/capability-proposals/{proposal['proposal_id']}/review",
        headers=manager_auth,
        json={"approve": True, "review_note": "证据和风险已审阅"},
    )
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"
    assert published.json()["reviewed_by"] == manager_user.json()["id"]
    workers_after_publish = await client.get(
        f"/api/organizations/{organization_id}/digital-workers", headers=owner_auth
    )
    worker_after_publish = workers_after_publish.json()[0]
    assert worker_after_publish["capability_version"] == 3
    assert worker_after_publish["capabilities"] == {
        **proposal_payload["proposed_changes"],
        "knowledge_sources": [
            {
                "provider": "akb",
                "mcp_server": "akb__1",
                "knowledge_base_ref": "ops-runbook",
                "selector_argument": "knowledgeBaseCode",
                "read_tools": ["knowledge_query", "knowledge_search"],
                "forbidden_arguments": ["knowledgeBaseId"],
                "access": "read",
            }
        ],
        "akb_knowledge_sources": [
            {
                "provider": "akb",
                "mcp_server": "akb__1",
                "knowledge_base_code": "ops-runbook",
                "access": "read",
            }
        ],
    }
    resources = await client.get(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}"
        "/authorized-resources",
        headers=owner_auth,
    )
    assert resources.status_code == 200
    assert resources.json() == {
        "skills": [{"id": skill_package.id, "name": skill_package.name}],
        "knowledge_bases": [{"id": knowledge_base.id, "name": knowledge_base.name}],
        "knowledge_sources": [
            {
                "provider": "akb",
                "mcp_server": "akb__1",
                "knowledge_base_ref": "ops-runbook",
                "selector_argument": "knowledgeBaseCode",
                "read_tools": ["knowledge_query", "knowledge_search"],
                "forbidden_arguments": ["knowledgeBaseId"],
                "access": "read",
            }
        ],
        "akb_knowledge_sources": [
            {
                "provider": "akb",
                "mcp_server": "akb__1",
                "knowledge_base_code": "ops-runbook",
                "access": "read",
            }
        ],
        "memory_sources": [],
    }
    member_resources = await client.get(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}"
        "/authorized-resources",
        headers=member_auth,
    )
    assert member_resources.status_code == 403
    next_run = await client.post(
        f"/api/organizations/{organization_id}/scenes/{scene_id}/runs",
        headers=owner_auth,
        json={"plan_id": plan["plan_id"]},
    )
    assert next_run.status_code == 201, next_run.text
    assert next_run.json()["plan_snapshot"]["worker"]["capability_version"] == 3
    assert next_run.json()["plan_snapshot"]["capabilities"] == {
        **worker_after_publish["capabilities"],
        "memory_sources": [],
        "memory_source_bindings": [],
        "memory_policy": {"use_memory": True, "capture_memory": True},
    }
    assert "capability profile version: 3" in next_run.json()["prompt_snapshot"]
    assert skill_package.id in next_run.json()["plan_snapshot"]["capabilities"]["skill_package_ids"]
    assert (
        knowledge_base.id in next_run.json()["plan_snapshot"]["capabilities"]["knowledge_base_ids"]
    )
    removed_akb_source = await client.put(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}"
        "/akb-knowledge-sources",
        headers=owner_auth,
        json={"expected_capability_version": 3, "sources": []},
    )
    assert removed_akb_source.status_code == 200, removed_akb_source.text
    revoked_akb_approval = await client.post(
        f"/api/organizations/{organization_id}/scene-runs/{next_run.json()['run_id']}/approve",
        headers=manager_auth,
        json={"note": "AKB source was removed after the run request"},
    )
    assert revoked_akb_approval.status_code == 409
    assert revoked_akb_approval.json()["detail"]["code"] == (
        "SCENE_KNOWLEDGE_SOURCE_AUTHORIZATION_CHANGED"
    )
    restored_akb_source = await client.put(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}"
        "/akb-knowledge-sources",
        headers=owner_auth,
        json={
            "expected_capability_version": 4,
            "sources": [{"mcp_server": "akb__1", "knowledge_base_code": "ops-runbook"}],
        },
    )
    assert restored_akb_source.status_code == 200, restored_akb_source.text
    memory_source = await client.put(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}"
        "/memory-sources",
        headers=owner_auth,
        json={
            "expected_capability_version": 5,
            "sources": [
                {
                    "provider": "akb",
                    "mcp_server": "akb__1",
                    "scope_type": "TASK",
                    "scope_code": "weekly-service-report",
                }
            ],
        },
    )
    assert memory_source.status_code == 200, memory_source.text
    assert memory_source.json()["capabilities"]["memory_sources"] == [
        {
            "provider": "akb",
            "mcp_server": "akb__1",
            "scope_type": "TASK",
            "scope_code": "weekly-service-report",
            "read_tools": ["memory_recall"],
            "read_enabled": True,
            "capture_enabled": True,
            "access": "read",
        }
    ]
    memory_run = await client.post(
        f"/api/organizations/{organization_id}/scenes/{scene_id}/runs",
        headers=owner_auth,
        json={"plan_id": plan["plan_id"]},
    )
    assert memory_run.status_code == 201, memory_run.text
    memory_snapshot = memory_run.json()["plan_snapshot"]["capabilities"]["memory_sources"]
    assert memory_snapshot == memory_source.json()["capabilities"]["memory_sources"]
    assert "memory_recall" in memory_run.json()["prompt_snapshot"]
    memory_opt_out = await client.post(
        f"/api/organizations/{organization_id}/scenes/{scene_id}/runs",
        headers=owner_auth,
        json={
            "plan_id": plan["plan_id"],
            "use_memory": False,
            "capture_memory": False,
        },
    )
    assert memory_opt_out.status_code == 201, memory_opt_out.text
    opt_out_capabilities = memory_opt_out.json()["plan_snapshot"]["capabilities"]
    assert opt_out_capabilities["memory_policy"] == {
        "use_memory": False,
        "capture_memory": False,
    }
    assert opt_out_capabilities["memory_sources"][0]["read_enabled"] is False
    assert opt_out_capabilities["memory_sources"][0]["capture_enabled"] is False
    assert "Before doing the work, recall" not in memory_opt_out.json()["prompt_snapshot"]
    cleared_memory_source = await client.put(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}"
        "/memory-sources",
        headers=owner_auth,
        json={"expected_capability_version": 6, "sources": []},
    )
    assert cleared_memory_source.status_code == 200, cleared_memory_source.text
    revoked_memory_approval = await client.post(
        f"/api/organizations/{organization_id}/scene-runs/{memory_run.json()['run_id']}/approve",
        headers=manager_auth,
        json={"note": "memory source was removed after the run request"},
    )
    assert revoked_memory_approval.status_code == 409
    assert revoked_memory_approval.json()["detail"]["code"] == (
        "SCENE_MEMORY_SOURCE_AUTHORIZATION_CHANGED"
    )
    resource_run = await client.post(
        f"/api/organizations/{organization_id}/scenes/{scene_id}/runs",
        headers=owner_auth,
        json={"plan_id": plan["plan_id"]},
    )
    assert resource_run.status_code == 201, resource_run.text
    server.services.agent_repo.update_config(agent_id, skill_package_ids=json.dumps([]))
    revoked_resource_approval = await client.post(
        f"/api/organizations/{organization_id}/scene-runs/{resource_run.json()['run_id']}/approve",
        headers=manager_auth,
        json={"note": "此时技能授权已撤销"},
    )
    assert revoked_resource_approval.status_code == 409
    assert revoked_resource_approval.json()["detail"]["code"] == (
        "SCENE_CAPABILITY_RESOURCE_CHANGED"
    )
    failed_run = await client.get(
        f"/api/organizations/{organization_id}/scene-runs/{resource_run.json()['run_id']}",
        headers=owner_auth,
    )
    assert failed_run.status_code == 200
    assert failed_run.json()["status"] == "failed"
    duplicate_proposal = await client.post(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}"
        "/capability-proposals",
        headers=owner_auth,
        json=proposal_payload,
    )
    assert duplicate_proposal.status_code == 409
    denied_tool_grant = await client.post(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}"
        "/capability-proposals",
        headers=owner_auth,
        json={**proposal_payload, "proposed_changes": {"tool_permissions": ["admin"]}},
    )
    assert denied_tool_grant.status_code == 422
    locked_plan = await client.patch(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}"
        f"/goals/{goal['goal_id']}/plans/{plan['plan_id']}",
        headers=owner_auth,
        json={"expected_version": 2, "title": "修改已就绪的计划"},
    )
    assert locked_plan.status_code == 409
    draft_again = await client.patch(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}"
        f"/goals/{goal['goal_id']}/plans/{plan['plan_id']}",
        headers=owner_auth,
        json={"expected_version": 2, "status": "draft"},
    )
    assert draft_again.status_code == 200
    stale_plan = await client.patch(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}"
        f"/goals/{goal['goal_id']}/plans/{plan['plan_id']}",
        headers=owner_auth,
        json={"expected_version": 2, "title": "过期写入"},
    )
    assert stale_plan.status_code == 409
    deleted_plan = await client.delete(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}"
        f"/goals/{goal['goal_id']}/plans/{plan['plan_id']}",
        headers=owner_auth,
    )
    assert deleted_plan.status_code == 204

    locked_goal = await client.patch(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}/goals/{goal['goal_id']}",
        headers=owner_auth,
        json={"success_criteria": "changed after activation"},
    )
    assert locked_goal.status_code == 409
    completed_goal = await client.patch(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}/goals/{goal['goal_id']}",
        headers=owner_auth,
        json={"status": "completed"},
    )
    assert completed_goal.status_code == 200, completed_goal.text

    assert activated.json()["version"] == 2
    stale_update = await client.patch(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}",
        headers=owner_auth,
        json={"expected_version": 1, "name": "Stale"},
    )
    assert stale_update.status_code == 409

    events = server.services.audit_repo.query(limit=50)
    actions = {event.action for event in events}
    assert {
        "digital_worker.create",
        "digital_worker.update",
        "digital_worker.goal_create",
        "digital_worker.plan_create",
        "digital_worker.plan_update",
        "digital_worker.capability_proposal_create",
        "digital_worker.capability_proposal_publish",
    }.issubset(actions)

    scene_with_ragflow = server.services.enterprise_scene_repo.update(
        scene_id,
        {"mcp_servers": ["akb__1", "ragflow__1"]},
        expected_version=2,
        actor_id=1,
    )
    assert scene_with_ragflow is not None
    product_binding = await client.put(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}"
        "/knowledge-sources",
        headers=owner_auth,
        json={
            "expected_capability_version": 7,
            "sources": [
                {
                    "provider": "ragflow",
                    "mcp_server": "ragflow__1",
                    "knowledge_base_ref": "service-dataset-8",
                    "selector_argument": "dataset_id",
                    "read_tools": ["retrieve"],
                    "forbidden_arguments": [],
                }
            ],
        },
    )
    assert product_binding.status_code == 200, product_binding.text
    assert product_binding.json()["capabilities"]["knowledge_sources"] == [
        {
            "provider": "ragflow",
            "mcp_server": "ragflow__1",
            "knowledge_base_ref": "service-dataset-8",
            "selector_argument": "dataset_id",
            "read_tools": ["retrieve"],
            "forbidden_arguments": [],
            "access": "read",
        }
    ]
    diagnostic = await client.post(
        f"/api/organizations/{organization_id}/digital-workers/{worker['worker_id']}"
        "/knowledge-sources/diagnose",
        headers=owner_auth,
    )
    assert diagnostic.status_code == 200, diagnostic.text
    assert diagnostic.json()["summary"] == {"total": 1, "healthy": 0, "attention_required": 1}
    assert diagnostic.json()["sources"][0]["health"] == "connector_unavailable"
