"""In-process HTTP contract only: real harness/OpenAI SDK, no socket or live gateway.

Authentication is injected and repositories are fakes. This does not establish
JWT, real LiteLLM budgets, external ACLs, database isolation or billing accuracy.
SDK retries deliberately retain production defaults.
"""

import json
import socket
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from langchain_openai import ChatOpenAI
from octop_harness.llm import factory as harness_factory
from tests.unit.digital_workers.test_planning import DRAFT, body, setup_planner
from tests.unit.digital_workers.test_planning import (
    isolated_limiter as isolated_limiter,  # noqa: F401
)

from octop.api.deps import current_user, get_server
from octop.api.routers.digital_workers import router
from octop.infra.agents.providers.store import ProviderStore

URL = "/api/organizations/org/digital-workers/worker/goals/goal/planning-dialogue"


@pytest.fixture
async def contract(monkeypatch):
    # Run after the pytest event loop exists; refuse every subsequent socket.
    def denied(*args, **kwargs):
        raise AssertionError("HTTP contract tests must never open sockets")

    for key in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "LANGSMITH_API_KEY",
        "LANGCHAIN_API_KEY",
        "OPENAI_BASE_URL",
        "LANGSMITH_ENDPOINT",
        "LANGCHAIN_ENDPOINT",
    ):
        monkeypatch.delenv(key, raising=False)
    for key in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"):
        monkeypatch.setenv(key, "false")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    monkeypatch.setattr(socket, "socket", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    calls = []
    state = {"status": 200, "content": json.dumps(DRAFT), "connection_error": False}

    def respond(request):
        assert str(request.url) == "https://gateway.invalid/v1/chat/completions"
        assert request.method == "POST"
        calls.append(request)
        if state["connection_error"]:
            raise httpx.ConnectError("synthetic-sensitive-marker", request=request)
        if state["status"] != 200:
            return httpx.Response(
                state["status"],
                json={"error": {"message": "synthetic-sensitive-marker", "type": "contract_error"}},
            )
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-offline",
                "object": "chat.completion",
                "created": 0,
                "model": "model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": state["content"]},
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
            },
        )

    transport = httpx.MockTransport(respond)
    with httpx.Client(transport=transport, trust_env=False) as sync_client:
        async with httpx.AsyncClient(transport=transport, trust_env=False) as async_client:
            # Preserve the real harness subclass and SDK; inject transport only.
            original_class = harness_factory._get_reasoning_aware_chat_openai(ChatOpenAI)

            class OfflineChatOpenAI(original_class):
                def __init__(self, **kwargs):
                    super().__init__(
                        **kwargs, http_client=sync_client, http_async_client=async_client
                    )

            monkeypatch.setattr(
                harness_factory, "_get_reasoning_aware_chat_openai", lambda base: OfflineChatOpenAI
            )
            service, registry, _, repos = setup_planner()
            models = [{"id": "model", "enabled": True}]
            row = SimpleNamespace(
                name="provider",
                kind="openai",
                enabled=True,
                base_url="https://gateway.invalid/v1",
                api_key="synthetic-contract-key",
                models_json=json.dumps(models),
                extra_json=json.dumps({"headers": {"X-Contract": "offline"}}),
                get_models=lambda: models,
            )
            repos.provider_repo.list_all.return_value = [row]
            repos.provider_repo.get_by_name.return_value = row
            factory = harness_factory.ChatModelFactory(
                ProviderStore(repos.provider_repo).build_harness_configs(), get_protocol=denied
            )
            model = factory.get("provider/model").chat_model  # Eager build exposes fixture errors.
            assert model.root_async_client.max_retries == 2
            registry.get_agent.return_value.model_factory = factory
            app = FastAPI()
            app.include_router(router, prefix="/api")
            app.dependency_overrides[current_user] = lambda: service.actor
            app.dependency_overrides[get_server] = lambda: SimpleNamespace(
                services=SimpleNamespace(repos=repos),
                app_runtime=SimpleNamespace(agent_registry=registry),
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://app.invalid"
            ) as client:
                yield SimpleNamespace(
                    client=client, calls=calls, state=state, repos=repos, factory=factory
                )


async def test_real_sdk_serializes_and_parses_planning(contract):
    response = await contract.client.post(URL, json=body().model_dump())
    assert response.status_code == 200, response.text
    assert response.json()["draft"] == DRAFT["draft"]
    assert len(contract.calls) == 1
    request = contract.calls[0]
    payload = json.loads(request.content)
    assert payload["model"] == "model"
    assert payload.get("max_tokens", payload.get("max_completion_tokens")) == 1000
    assert "tools" not in payload and "max_retries" not in payload
    assert request.headers["authorization"] == "Bearer synthetic-contract-key"
    assert request.headers["x-contract"] == "offline"
    assert "user" not in payload  # No per-user billing identity is currently stamped.
    assert not contract.repos.digital_worker_repo.create_plan.called
    assert not contract.repos.audit_repo.write.called
    # Usage is parsed by SDK, but current route does not expose/account it.
    assert "usage" not in response.json()


@pytest.mark.parametrize("status,expected_calls", [(401, 1), (429, 3), (500, 3)])
async def test_production_sdk_retry_behavior_is_visible(contract, status, expected_calls):
    contract.state["status"] = status
    response = await contract.client.post(URL, json=body().model_dump())
    assert response.status_code == 502
    assert "synthetic-sensitive-marker" not in response.text
    assert len(contract.calls) == expected_calls
    assert contract.factory.get("provider/model").chat_model.root_async_client.max_retries == 2


async def test_connection_failure_has_production_retries_without_fallback(contract):
    contract.state["connection_error"] = True
    response = await contract.client.post(URL, json=body().model_dump())
    assert response.status_code == 502
    assert len(contract.calls) == 3
    assert "synthetic-sensitive-marker" not in response.text


async def test_invalid_model_output_is_not_retried_or_saved(contract):
    contract.state["content"] = "not valid planning JSON"
    response = await contract.client.post(URL, json=body().model_dump())
    assert response.status_code == 502
    assert len(contract.calls) == 1
    assert not contract.repos.digital_worker_repo.create_plan.called


@pytest.mark.parametrize("change", ["manager", "agent", "provider", "model"])
async def test_denials_send_no_http_request(contract, change):
    if change == "manager":
        contract.repos.organization_repo.get_member.return_value = {"role": "member"}
    elif change == "agent":
        contract.repos.agent_repo.get.return_value.user_id = 99
    elif change == "provider":
        contract.repos.provider_repo.get_by_name.return_value.enabled = False
    else:
        contract.repos.provider_repo.get_by_name.return_value.get_models = lambda: []
    response = await contract.client.post(URL, json=body().model_dump())
    assert response.status_code in (403, 503)
    assert not contract.calls
