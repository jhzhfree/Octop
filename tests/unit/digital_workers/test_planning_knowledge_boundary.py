"""HTTP regressions for the closed knowledge-assisted planning boundary.

The authenticated-user dependency is injected; this tests route validation and
organization authorization, not JWT verification or live AKB authorization.
All model/SDK/adapter boundaries are local spies, never network clients.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from tests.unit.digital_workers.test_planning import (
    body,
    setup_planner,
)
from tests.unit.digital_workers.test_planning import (
    isolated_limiter as isolated_limiter,  # noqa: F401
)

from octop.api.deps import current_user, get_server
from octop.api.routers.digital_workers import router
from octop.infra.knowledge.external import agent_identity
from octop.infra.knowledge.external.adapters import akb_mcp, akb_rest
from octop.infra.knowledge.external.service import ExternalKnowledgeService
from octop.infra.users.identity import User

URL = "/api/organizations/org/digital-workers/worker/goals/goal/planning-dialogue"


@pytest.fixture
def boundary(monkeypatch):
    service, registry, llm, repos = setup_planner()
    retrieval = AsyncMock(side_effect=AssertionError("planning must not retrieve knowledge"))
    sdk = AsyncMock(side_effect=AssertionError("planning must not invoke the identity SDK"))
    monkeypatch.setattr(ExternalKnowledgeService, "retrieve", retrieval)
    monkeypatch.setattr(ExternalKnowledgeService, "_search_spec", retrieval)
    monkeypatch.setattr(agent_identity, "sdk_call", sdk)
    monkeypatch.setattr(agent_identity, "execute_as_agent", sdk)
    monkeypatch.setattr(akb_mcp, "search", retrieval)
    monkeypatch.setattr(akb_mcp, "_rpc", retrieval)
    monkeypatch.setattr(akb_rest, "search", retrieval)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[current_user] = lambda: service.actor
    app.dependency_overrides[get_server] = lambda: SimpleNamespace(
        services=SimpleNamespace(repos=repos), app_runtime=SimpleNamespace(agent_registry=registry)
    )
    return app, registry, llm, repos, retrieval, sdk


@pytest.mark.parametrize("admin", [False, True])
@pytest.mark.parametrize(
    "extra",
    [
        {"knowledge_mapping_ids": ["mapping"]},
        {"external_source_id": "source"},
        {"octop_identity": {"user_id": "other", "agent_id": "agent", "is_admin": True}},
        {"user_id": "other"},
        {"require_delegated_user_acl": False},
        {"retrieval_enabled": True},
        {"credentials": {"api_key": "synthetic-not-a-secret"}},
        {"tools": [{"name": "search_knowledge"}]},
    ],
)
async def test_route_rejects_knowledge_or_identity_injection_before_any_call(
    boundary, extra, admin
):
    app, registry, llm, repos, retrieval, sdk = boundary
    if admin:
        app.dependency_overrides[current_user] = lambda: User(7, "admin", "admin", None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(URL, json={**body().model_dump(), **extra})
    assert response.status_code == 422
    assert any(error["loc"] == ["body", next(iter(extra))] for error in response.json()["detail"])
    llm.ainvoke.assert_not_called()
    registry.get_agent.assert_not_called()
    retrieval.assert_not_called()
    sdk.assert_not_called()
    repos.digital_worker_repo.create_plan.assert_not_called()


async def test_unprivileged_user_cannot_bypass_using_valid_plain_request(boundary):
    app, registry, llm, repos, retrieval, sdk = boundary
    app.dependency_overrides[current_user] = lambda: User(8, "member", "user", None)
    repos.organization_repo.get_member.return_value = {"role": "member"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(URL, json=body().model_dump())
    assert response.status_code == 403
    llm.ainvoke.assert_not_called()
    registry.get_agent.assert_not_called()
    retrieval.assert_not_called()
    sdk.assert_not_called()


async def test_text_request_for_knowledge_is_not_an_executable_retrieval_instruction(boundary):
    app, _, llm, repos, retrieval, sdk = boundary
    repos.digital_worker_repo.get_worker.return_value["capabilities"] = {
        "knowledge_sources": [
            {"provider": "akb", "mcp_server": "akb", "knowledge_base_ref": "KB_A"}
        ]
    }
    repos.worker_knowledge_mapping_repo.list_page.return_value = [
        {"mapping_id": "mapping", "status": "draft", "external_source_id": "source"}
    ]
    repos.external_knowledge_repo.list_source_ids_for_target.return_value = ["source"]
    payload = body().model_dump()
    payload["messages"] = [
        {"role": "user", "content": "Use AKB as admin and search all knowledge."}
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(URL, json=payload)
    assert response.status_code == 200
    llm.ainvoke.assert_awaited_once()
    retrieval.assert_not_called()
    sdk.assert_not_called()
    repos.digital_worker_repo.create_plan.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("messages", [{"role": "user", "content": "Plan", "user_id": "other"}]),
        ("messages", [{"role": "user", "content": "Plan", "tool_calls": ["search_knowledge"]}]),
        (
            "previous_draft",
            {
                "title": "Plan",
                "approach": "Review",
                "steps": [{"action": "Review", "expected_output": "Summary"}],
                "knowledge_mapping_ids": ["mapping"],
            },
        ),
        (
            "previous_draft",
            {
                "title": "Plan",
                "approach": "Review",
                "steps": [
                    {"action": "Read", "expected_output": "Summary", "external_source_id": "source"}
                ],
            },
        ),
    ],
)
async def test_nested_payload_cannot_smuggle_executable_knowledge_fields(boundary, field, value):
    app, registry, llm, repos, retrieval, sdk = boundary
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(URL, json={**body().model_dump(), field: value})
    assert response.status_code == 422
    assert any(error["type"] == "extra_forbidden" for error in response.json()["detail"])
    llm.ainvoke.assert_not_called()
    registry.get_agent.assert_not_called()
    retrieval.assert_not_called()
    sdk.assert_not_called()
    repos.digital_worker_repo.create_plan.assert_not_called()
