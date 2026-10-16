"""Organization chart CRUD, membership isolation, and appointment validation."""

from __future__ import annotations

from tests.support.auth import TEST_PASSWORD, bearer, login


async def test_org_chart_supports_human_members_and_hierarchy(env):
    client, _server, admin_auth = env
    created_user = await client.post(
        "/api/users",
        headers=admin_auth,
        json={"username": "org_member", "password": TEST_PASSWORD, "role": "user"},
    )
    assert created_user.status_code == 201, created_user.text
    user_id = created_user.json()["id"]

    created_org = await client.post(
        "/api/organizations",
        headers=admin_auth,
        json={"name": "Platform Engineering", "description": "Software delivery"},
    )
    assert created_org.status_code == 201, created_org.text
    org_id = created_org.json()["organization_id"]
    creator_chart = await client.get(f"/api/organizations/{org_id}/chart", headers=admin_auth)
    assert creator_chart.status_code == 200
    assert creator_chart.json()["organization"]["member_role"] == "owner"
    null_patch = await client.patch(
        f"/api/organizations/{org_id}", headers=admin_auth, json={"name": None}
    )
    assert null_patch.status_code == 422

    added_member = await client.post(
        f"/api/organizations/{org_id}/members",
        headers=admin_auth,
        json={"user_id": user_id, "role": "member"},
    )
    assert added_member.status_code == 201, added_member.text

    department = await client.post(
        f"/api/organizations/{org_id}/departments",
        headers=admin_auth,
        json={"name": "研发中心", "code": "ENG"},
    )
    assert department.status_code == 201, department.text
    department_id = department.json()["department_id"]
    child = await client.post(
        f"/api/organizations/{org_id}/departments",
        headers=admin_auth,
        json={"name": "平台组", "parent_department_id": department_id},
    )
    assert child.status_code == 201, child.text

    position = await client.post(
        f"/api/organizations/{org_id}/positions",
        headers=admin_auth,
        json={
            "title": "平台工程师",
            "department_id": child.json()["department_id"],
            "duties": "维护开发与交付平台",
            "capabilities": ["CI/CD", "observability"],
        },
    )
    assert position.status_code == 201, position.text
    position_id = position.json()["position_id"]
    appointment = await client.post(
        f"/api/organizations/{org_id}/positions/{position_id}/appointments",
        headers=admin_auth,
        json={"subject_type": "user", "subject_id": str(user_id)},
    )
    assert appointment.status_code == 201, appointment.text

    outsider = await client.post(
        "/api/users",
        headers=admin_auth,
        json={"username": "org_outsider", "password": TEST_PASSWORD, "role": "user"},
    )
    assert outsider.status_code == 201, outsider.text
    outsider_auth = bearer(await login(client, username="org_outsider", password=TEST_PASSWORD))
    forbidden = await client.get(f"/api/organizations/{org_id}/chart", headers=outsider_auth)
    assert forbidden.status_code == 403

    member_auth = bearer(await login(client, username="org_member", password=TEST_PASSWORD))
    chart = await client.get(f"/api/organizations/{org_id}/chart", headers=member_auth)
    assert chart.status_code == 200, chart.text
    payload = chart.json()
    assert payload["organization"]["name"] == "Platform Engineering"
    assert len(payload["members"]) == 2
    child_department = next(item for item in payload["departments"] if item["name"] == "平台组")
    assert child_department["parent_department_id"] == department_id
    assert payload["positions"][0]["capabilities"] == ["CI/CD", "observability"]
    assert payload["positions"][0]["assignments"][0]["label"] == "org_member"

    denied_write = await client.post(
        f"/api/organizations/{org_id}/departments",
        headers=member_auth,
        json={"name": "Unauthorized"},
    )
    assert denied_write.status_code == 403

    cycle = await client.patch(
        f"/api/organizations/{org_id}/departments/{department_id}",
        headers=admin_auth,
        json={"parent_department_id": child.json()["department_id"]},
    )
    assert cycle.status_code == 409

    blocked_remove = await client.delete(
        f"/api/organizations/{org_id}/members/{user_id}", headers=admin_auth
    )
    assert blocked_remove.status_code == 409

    unassigned = await client.delete(
        f"/api/organizations/{org_id}/appointments/{appointment.json()['assignment_id']}",
        headers=admin_auth,
    )
    assert unassigned.status_code == 204
    removed = await client.delete(
        f"/api/organizations/{org_id}/members/{user_id}", headers=admin_auth
    )
    assert removed.status_code == 204

    deleted = await client.delete(f"/api/organizations/{org_id}", headers=admin_auth)
    assert deleted.status_code == 204


async def test_org_manager_cannot_manage_members_or_cross_org_references(env):
    client, _server, admin_auth = env
    org = await client.post("/api/organizations", headers=admin_auth, json={"name": "Org One"})
    org_id = org.json()["organization_id"]
    user = await client.post(
        "/api/users",
        headers=admin_auth,
        json={"username": "org_manager", "password": TEST_PASSWORD, "role": "user"},
    )
    uid = user.json()["id"]
    added = await client.post(
        f"/api/organizations/{org_id}/members",
        headers=admin_auth,
        json={"user_id": uid, "role": "manager"},
    )
    assert added.status_code == 201
    manager_auth = bearer(await login(client, username="org_manager", password=TEST_PASSWORD))

    other = await client.post("/api/organizations", headers=admin_auth, json={"name": "Org Two"})
    other_dept = await client.post(
        f"/api/organizations/{other.json()['organization_id']}/departments",
        headers=admin_auth,
        json={"name": "Other"},
    )
    cross_org = await client.post(
        f"/api/organizations/{org_id}/positions",
        headers=manager_auth,
        json={"title": "Invalid", "department_id": other_dept.json()["department_id"]},
    )
    assert cross_org.status_code == 404

    member_change = await client.post(
        f"/api/organizations/{org_id}/members",
        headers=manager_auth,
        json={"user_id": uid, "role": "member"},
    )
    assert member_change.status_code == 403

    await client.delete(f"/api/organizations/{org_id}", headers=admin_auth)
    await client.delete(f"/api/organizations/{other.json()['organization_id']}", headers=admin_auth)
