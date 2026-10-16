"""Enterprise scene approval and Cron execution integration coverage."""

from __future__ import annotations

from octop.infra.gateway.threads import ThreadRegistry
from tests.support.auth import TEST_PASSWORD, bearer, create_agent, login


async def test_scene_run_is_snapshotted_and_requires_another_org_manager(env):
    client, server, owner_auth = env
    manager_user = await client.post(
        "/api/users",
        headers=owner_auth,
        json={"username": "scene_manager", "password": TEST_PASSWORD, "role": "user"},
    )
    assert manager_user.status_code == 201, manager_user.text
    manager_id = manager_user.json()["id"]

    organization = await client.post("/api/organizations", headers=owner_auth, json={"name": "Ops"})
    org_id = organization.json()["organization_id"]
    added = await client.post(
        f"/api/organizations/{org_id}/members",
        headers=owner_auth,
        json={"user_id": manager_id, "role": "manager"},
    )
    assert added.status_code == 201, added.text
    manager_auth = bearer(await login(client, username="scene_manager", password=TEST_PASSWORD))

    department = await client.post(
        f"/api/organizations/{org_id}/departments",
        headers=owner_auth,
        json={"name": "运维"},
    )
    position = await client.post(
        f"/api/organizations/{org_id}/positions",
        headers=owner_auth,
        json={"title": "运维工程师", "department_id": department.json()["department_id"]},
    )
    agent_id = await create_agent(client, owner_auth, name="Scene Operations Agent")
    appointment = await client.post(
        f"/api/organizations/{org_id}/positions/{position.json()['position_id']}/appointments",
        headers=owner_auth,
        json={"subject_type": "agent", "subject_id": agent_id},
    )
    assert appointment.status_code == 201, appointment.text

    created = await client.post(
        f"/api/organizations/{org_id}/scenes",
        headers=owner_auth,
        json={
            "name": "只读巡检",
            "position_id": position.json()["position_id"],
            "agent_id": agent_id,
            "prompt": "检查服务状态并生成摘要",
            "mcp_servers": [],
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["version"] == 1
    scene_id = created.json()["scene_id"]
    denied_run = await client.post(
        f"/api/organizations/{org_id}/scenes/{scene_id}/runs", headers=manager_auth
    )
    assert denied_run.status_code == 403
    requested = await client.post(
        f"/api/organizations/{org_id}/scenes/{scene_id}/runs", headers=owner_auth
    )
    assert requested.status_code == 201, requested.text
    run_id = requested.json()["run_id"]
    assert requested.json()["status"] == "awaiting_approval"

    self_approval = await client.post(
        f"/api/organizations/{org_id}/scene-runs/{run_id}/approve",
        headers=owner_auth,
        json={"note": "self"},
    )
    assert self_approval.status_code == 403

    updated = await client.patch(
        f"/api/organizations/{org_id}/scenes/{scene_id}",
        headers=owner_auth,
        json={"expected_version": 1, "prompt": "后续版本的任务内容"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2
    versions = await client.get(
        f"/api/organizations/{org_id}/scenes/{scene_id}/versions", headers=owner_auth
    )
    assert versions.status_code == 200, versions.text
    assert [item["version"] for item in versions.json()] == [2, 1]
    assert versions.json()[1]["prompt"] == "检查服务状态并生成摘要"
    stale_update = await client.patch(
        f"/api/organizations/{org_id}/scenes/{scene_id}",
        headers=owner_auth,
        json={"expected_version": 1, "prompt": "覆盖新版本"},
    )
    assert stale_update.status_code == 409

    manager = server.app_runtime.cron_manager
    old_run_now = manager.run_now

    async def hold_run(cron_id: str, *, wait: bool = False) -> None:
        assert wait is False

    manager.run_now = hold_run
    try:
        approved = await client.post(
            f"/api/organizations/{org_id}/scene-runs/{run_id}/approve",
            headers=manager_auth,
            json={"note": "已检查任务范围"},
        )
    finally:
        manager.run_now = old_run_now

    assert approved.status_code == 200, approved.text
    result = approved.json()
    assert result["status"] == "queued"
    assert result["prompt_snapshot"] == "检查服务状态并生成摘要"
    assert result["scene_version"] == 1
    assert result["approved_by"] == manager_id
    assert result["cron_id"]
    cron = manager.get(result["cron_id"])
    assert cron is not None and cron.enabled == 0 and cron.mcp_servers_exact == 1
    assert cron.session_key == ThreadRegistry.make_key(
        agent_id=agent_id,
        channel_type="cron",
        channel_subject_id=result["cron_id"],
        channel_chat_type=ThreadRegistry.CHAT_TYPE_DM,
    )

    reviewer_user = await client.post(
        "/api/users",
        headers=owner_auth,
        json={"username": "scene_reviewer", "password": TEST_PASSWORD, "role": "user"},
    )
    assert reviewer_user.status_code == 201, reviewer_user.text
    reviewer_id = reviewer_user.json()["id"]
    await client.post(
        f"/api/organizations/{org_id}/members",
        headers=owner_auth,
        json={"user_id": reviewer_id, "role": "member"},
    )
    reviewer_auth = bearer(await login(client, username="scene_reviewer", password=TEST_PASSWORD))
    evidence_denied = await client.get(
        f"/api/organizations/{org_id}/scene-runs/{run_id}/evidence",
        headers=reviewer_auth,
    )
    assert evidence_denied.status_code == 403

    requester_evidence = await client.get(
        f"/api/organizations/{org_id}/scene-runs/{run_id}/evidence",
        headers=owner_auth,
    )
    assert requester_evidence.status_code == 200, requester_evidence.text
    assert requester_evidence.json() == {
        "run_id": run_id,
        "available": False,
        "answer": None,
    }

    duplicate_approval = await client.post(
        f"/api/organizations/{org_id}/scene-runs/{run_id}/approve",
        headers=manager_auth,
        json={},
    )
    assert duplicate_approval.status_code == 409


async def test_scene_requires_appointed_agent_and_membership(env):
    client, _server, admin_auth = env
    org = await client.post("/api/organizations", headers=admin_auth, json={"name": "Ops"})
    org_id = org.json()["organization_id"]
    position = await client.post(
        f"/api/organizations/{org_id}/positions",
        headers=admin_auth,
        json={"title": "Operator"},
    )
    agent_id = await create_agent(client, admin_auth, name="Unassigned Agent")
    response = await client.post(
        f"/api/organizations/{org_id}/scenes",
        headers=admin_auth,
        json={
            "name": "Invalid",
            "position_id": position.json()["position_id"],
            "agent_id": agent_id,
            "prompt": "Run task",
        },
    )
    assert response.status_code == 409, response.text
