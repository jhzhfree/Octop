"""HTTP module gating, encrypted connections and real durable execution."""

from __future__ import annotations

from unittest.mock import AsyncMock

from octop.infra.enterprise_assistant import adapters
from octop.infra.enterprise_assistant.service import AssistantService
from octop.infra.users.identity import User

BASE = "/api/enterprise-assistant"


async def test_module_gates_http_and_validates_destination(env):
    client, server, auth = env
    flags = await client.get(BASE + "/features", headers=auth)
    assert flags.status_code == 200 and flags.json()["enabled"] is False
    assert (await client.get(BASE + "/jobs", headers=auth)).status_code == 403
    enabled = await client.put(
        BASE + "/features",
        headers=auth,
        json={"enabled": True, "inspection": True, "allowed_origins": ["https://health.example"]},
    )
    assert enabled.status_code == 200, enabled.text
    blocked = await client.post(
        BASE + "/connections",
        headers=auth,
        json={"config": {"name": "wrong", "kind": "inspection", "url": "https://wrong.example"}},
    )
    assert blocked.status_code == 409 and blocked.json()["detail"] == "DESTINATION_BLOCKED"
    created = await client.post(
        BASE + "/connections",
        headers=auth,
        json={
            "config": {
                "name": "health",
                "kind": "inspection",
                "url": "https://health.example",
                "auth": "bearer",
            },
            "secret": "never-in-response",
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["has_secret"] and "never-in-response" not in created.text
    listing = await client.get(BASE + "/connections", headers=auth)
    assert "secret_blob" not in listing.text


async def test_agent_job_executes_and_exposes_read_only_evidence(env_with_agent, monkeypatch):
    client, server, auth, agent = env_with_agent
    await client.put(
        BASE + "/features",
        headers=auth,
        json={"enabled": True, "inspection": True, "allowed_origins": ["https://health.example"]},
    )
    c = await client.post(
        BASE + "/connections",
        headers=auth,
        json={"config": {"name": "health", "kind": "inspection", "url": "https://health.example"}},
    )
    j = await client.post(
        BASE + "/jobs",
        headers=auth,
        json={
            "config": {
                "name": "daily",
                "kind": "inspection",
                "agent_id": agent,
                "connection_id": c.json()["id"],
            }
        },
    )
    assert j.status_code == 200, j.text
    monkeypatch.setattr(
        adapters, "collect", AsyncMock(return_value=[{"type": "health", "status": "ok"}])
    )
    r = await client.post(BASE + f"/jobs/{j.json()['id']}/runs", headers=auth)
    assert r.status_code == 200, r.text
    assert server.services is not None
    owner = server.services.user_repo.get(j.json()["owner_id"])
    svc = AssistantService(
        server.services.repos, User(owner.id, owner.username, owner.role, owner.display_name)
    )
    await svc.process(r.json()["id"])
    result = await client.get(BASE + "/runs", headers=auth)
    assert result.status_code == 200
    assert result.json()[0]["status"] == "succeeded"
    assert result.json()[0]["evidence"][-1]["status"] == "ok"
