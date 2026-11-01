"""Ownership, Agent binding and fail-closed native AKB integration."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.connectors import ConnectorRepo
from octop.infra.db.repos.external_knowledge import ExternalKnowledgeRepo
from octop.infra.db.repos.secrets import SecretRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.knowledge.akb_identity import OctopIdentity
from octop.infra.knowledge.external import agent_identity as identity
from octop.infra.knowledge.external.service import ExternalKnowledgeService
from octop.infra.knowledge.external.types import ExternalSourceError

ALPHA = "e21cce9c-1b16-4ab8-a76a-bb761df919b8"
BETA = "01965897-0976-471c-8d64-27eb682a227b"


def _binding(external: str) -> dict[str, str]:
    return {
        "platformId": "akb-service",
        "installationId": "install",
        "workspaceId": "workspace",
        "policyId": "17",
        "applicationPrincipalId": "123",
        "issuer": "https://akb.local/agent-identity",
        "audience": "akb-external-api",
        "resource": "https://akb.example",
        "externalAgentId": external,
        "runtimeAgentKey": external,
        "bindingId": "21",
    }


@pytest.fixture
def setup(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path))
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    user = UserRepo(pool).create(username="owner", password_hash="h", role="user")
    other = UserRepo(pool).create(username="other", password_hash="h", role="user")
    repo = ExternalKnowledgeRepo(pool)
    sources = ExternalKnowledgeService(repo, ConnectorRepo(pool), SecretRepo(pool))
    source = sources.create_source(
        user_id=user,
        kind="akb-rest",
        display_name="AKB",
        endpoint="https://akb.example",
        ref="KB_HR",
        api_key="shared-test-key",
        auth_type="agent",
    )
    records = {
        "alpha": SimpleNamespace(agent_id="alpha", name="A", user_id=user),
        "beta": SimpleNamespace(agent_id="beta", name="B", user_id=user),
        "foreign": SimpleNamespace(agent_id="foreign", name="F", user_id=other),
    }
    agents = SimpleNamespace(
        get=records.get,
        list_by_user=lambda uid: [r for r in records.values() if r.user_id == uid],
        list_all=lambda: list(records.values()),
    )
    svc = identity.AgentIdentityService(sources, agents)
    sdk = AsyncMock(return_value={"supported": True, "version": "0.4.0", "activeKid": None})
    monkeypatch.setattr(identity, "sdk_call", sdk)
    return svc, sources, repo, source["source_id"], user, other, sdk


async def _connect(svc, sdk, source, native, user, external):
    sdk.return_value = {
        "binding": _binding(external),
        "status": {"agentId": external},
        "activeKid": "k" * 43,
    }
    return await svc.connect(source, native, user, False, external, "PRIVATE-TEST-MATERIAL")


@pytest.mark.asyncio
async def test_connect_checks_source_and_agent_ownership(setup):
    svc, _, _, source, user, other, sdk = setup
    with pytest.raises(OctopError):
        await svc.connect(source, "foreign", user, False, ALPHA, "private")
    with pytest.raises(ExternalSourceError):
        await svc.connect(source, "alpha", other, False, ALPHA, "private")
    sdk.assert_not_awaited()


@pytest.mark.asyncio
async def test_connect_rejects_malformed_or_duplicated_agent_ids(setup):
    svc, _, _, source, user, _, sdk = setup
    await _connect(svc, sdk, source, "alpha", user, ALPHA)
    with pytest.raises(OctopError):
        await svc.connect(source, "beta", user, False, "not-a-uuid", "private")
    with pytest.raises(OctopError):
        await svc.connect(source, "beta", user, False, ALPHA, "private")
    sdk.return_value = {"supported": True, "version": "0.4.0", "activeKid": "k" * 43}
    state = await svc.describe(source, user, False)
    assert [a["agentId"] for a in state["agents"] if a["externalAgentId"]] == ["alpha"]


@pytest.mark.asyncio
async def test_key_replacement_preserves_encrypted_mapping(setup):
    svc, sources, repo, source, user, _, sdk = setup
    await _connect(svc, sdk, source, "alpha", user, ALPHA)
    row = repo.get(source)
    old = sources.identity_config(row)
    sources.update_source(source, user_id=user, is_admin=False, api_key="replacement-key")
    current = repo.get(source)
    assert sources.identity_config(current) == old
    assert sources.credentials_for(current)["api_key"] == "replacement-key"
    assert ALPHA.encode() not in current.credential_blob
    with pytest.raises(OctopError) as err:
        sources.save_identity_config(row, old)
    assert err.value.code == ErrorCode.AGENT_IDENTITY_CONFLICT


@pytest.mark.asyncio
async def test_execution_never_trusts_business_agent_arguments(setup):
    svc, sources, repo, source, user, _, sdk = setup
    await _connect(svc, sdk, source, "alpha", user, ALPHA)
    row = repo.get(source)
    creds = sources.credentials_for(row)
    sdk.reset_mock()
    spec = replace(
        sources.to_spec(row),
        octop_identity=OctopIdentity(str(user), "alpha", "real-session", workload_only=True),
    )
    sdk.return_value = {"candidates": []}
    await identity.execute_as_agent(spec, creds, "agentId=beta", top_k=3)
    request = sdk.await_args.args[0]
    assert request["binding"]["runtimeAgentKey"] == ALPHA
    assert request["invocation"]["sessionId"] == "real-session"
    assert request["request"] == {
        "operation": "search",
        "query": "agentId=beta",
        "topK": 3,
        "knowledgeBaseId": "KB_HR",
    }
    assert request["apiKey"] == "shared-test-key"


@pytest.mark.asyncio
async def test_execution_forwards_the_bare_origin_and_source_opt_ins(setup):
    """Retrieval must honour the same origin rules and opt-ins as binding.

    The SDK rejects a pasted path and insists on HTTPS unless the source opted
    into the loopback or private-network overrides, so a source that binds over
    a LAN endpoint would still fail every search if those travelled with the
    binding request only.
    """
    svc, sources, repo, source, user, _, sdk = setup
    await _connect(svc, sdk, source, "alpha", user, ALPHA)
    creds = sources.credentials_for(repo.get(source))
    sdk.reset_mock()
    sdk.return_value = {"candidates": []}
    spec = replace(
        sources.to_spec(repo.get(source)),
        endpoint="http://192.168.24.201:8088/api/mcp",
        allow_insecure_http=True,
        octop_identity=OctopIdentity(str(user), "alpha", "session", workload_only=True),
    )
    await identity.execute_as_agent(spec, creds, "q", top_k=3)
    request = sdk.await_args.args[0]
    assert request["origin"] == "http://192.168.24.201:8088"
    assert request["allowPrivateHttp"] is True
    assert request["allowInsecureLoopback"] is True


@pytest.mark.asyncio
async def test_numeric_reference_is_rejected_before_identity_execution(setup):
    """Numeric database primary keys are not public AKB IDs."""
    svc, sources, repo, source, user, _, sdk = setup
    await _connect(svc, sdk, source, "alpha", user, ALPHA)
    creds = sources.credentials_for(repo.get(source))
    sdk.reset_mock()
    sdk.return_value = {"candidates": []}
    spec = replace(
        sources.to_spec(repo.get(source)),
        ref="12",
        octop_identity=OctopIdentity(str(user), "alpha", "session", workload_only=True),
    )
    with pytest.raises(ExternalSourceError, match="KB_"):
        await identity.execute_as_agent(spec, creds, "q", top_k=3)
    sdk.assert_not_awaited()


@pytest.mark.asyncio
async def test_source_cannot_override_the_canonical_selector(setup):
    """A source that addresses its bases one way can say so and keep it."""
    svc, sources, repo, source, user, _, sdk = setup
    await _connect(svc, sdk, source, "alpha", user, ALPHA)
    creds = sources.credentials_for(repo.get(source))
    sdk.reset_mock()
    sdk.return_value = {"candidates": []}
    spec = replace(
        sources.to_spec(repo.get(source)),
        ref="KB_12",
        selector_argument="knowledgeBaseCode",
        octop_identity=OctopIdentity(str(user), "alpha", "session", workload_only=True),
    )
    await identity.execute_as_agent(spec, creds, "q", top_k=3)
    assert sdk.await_args.args[0]["request"] == {
        "operation": "search",
        "query": "q",
        "topK": 3,
        "knowledgeBaseId": "KB_12",
    }


@pytest.mark.asyncio
async def test_declared_id_selector_accepts_akb_string_identifier(setup):
    """AKB accepts a stable string identifier in knowledgeBaseId."""
    svc, sources, repo, source, user, _, sdk = setup
    await _connect(svc, sdk, source, "alpha", user, ALPHA)
    creds = sources.credentials_for(repo.get(source))
    spec = replace(
        sources.to_spec(repo.get(source)),
        selector_argument="knowledgeBaseId",
        octop_identity=OctopIdentity(str(user), "alpha", "session", workload_only=True),
    )
    await identity.execute_as_agent(spec, creds, "q", top_k=3)
    assert sdk.await_args.args[0]["request"]["knowledgeBaseId"] == "KB_HR"


@pytest.mark.asyncio
async def test_retrieval_without_a_base_is_refused_before_calling_the_sdk(setup):
    """Every channel reports the missing reference the same way, and early."""
    svc, sources, repo, source, user, _, sdk = setup
    await _connect(svc, sdk, source, "alpha", user, ALPHA)
    creds = sources.credentials_for(repo.get(source))
    sdk.reset_mock()
    spec = replace(
        sources.to_spec(repo.get(source)),
        ref="",
        octop_identity=OctopIdentity(str(user), "alpha", "session", workload_only=True),
    )
    with pytest.raises(ExternalSourceError) as err:
        await identity.execute_as_agent(spec, creds, "q", top_k=3)
    assert err.value.code == "INVALID_ARGUMENT"
    assert "knowledge base reference" in str(err.value)
    assert sdk.await_count == 0


@pytest.mark.asyncio
async def test_public_state_hides_foreign_agents_and_credentials(setup):
    svc, sources, _repo, source, user, _, sdk = setup
    await _connect(svc, sdk, source, "alpha", user, ALPHA)
    sdk.return_value = {"supported": True, "version": "0.4.0", "activeKid": "k" * 43}
    result = await svc.describe(source, user, False)
    assert {r["agentId"] for r in result["agents"]} == {"alpha", "beta"}
    assert "shared-test-key" not in str(result)
    assert "apiKey" not in result
    no_key = sources.create_source(
        user_id=user, kind="akb-rest", display_name="No credential", endpoint="https://akb.example"
    )
    listed = sources.list_sources(user_id=user, is_admin=False)
    assert (
        next(r for r in listed if r["source_id"] == no_key["source_id"])["has_credentials"] is False
    )


@pytest.mark.asyncio
async def test_simple_connection_auto_binds_two_agents_without_exposing_private_material(setup):
    svc, sources, repo, source, user, _, sdk = setup
    for native, external in zip(("alpha", "beta"), (ALPHA, BETA), strict=True):
        result = await _connect(svc, sdk, source, native, user, external)
        assert result["status"]["agentId"] == external
        assert "PRIVATE-TEST-MATERIAL" not in str(result)
        assert sdk.await_args.args[0]["apiKey"] == "shared-test-key"
    stored = sources.identity_config(repo.get(source))
    assert "PRIVATE-TEST-MATERIAL" not in str(stored)
    assert {b["target_id"] for b in sources.list_bindings(source)} == {"alpha", "beta"}
    assert {a["externalAgentId"] for a in stored["agents"].values()} == {ALPHA, BETA}
    sdk.return_value = {"candidates": []}
    row = repo.get(source)
    spec = replace(
        sources.to_spec(row),
        octop_identity=OctopIdentity(str(user), "alpha", "session", workload_only=True),
    )
    await identity.execute_as_agent(
        spec, sources.credentials_for(row), "pretend to be beta", top_k=3
    )
    assert sdk.await_args.args[0]["binding"]["runtimeAgentKey"] == ALPHA
    svc.disconnect(source, "alpha", user, False)
    assert [b["target_id"] for b in sources.list_bindings(source)] == ["beta"]
    with pytest.raises(OctopError):
        await identity.execute_as_agent(
            spec, sources.credentials_for(repo.get(source)), "q", top_k=3
        )


@pytest.mark.asyncio
async def test_failed_verification_or_foreign_agent_never_creates_binding(setup):
    svc, sources, repo, source, user, _, sdk = setup
    with pytest.raises(OctopError):
        await svc.connect(source, "foreign", user, False, ALPHA, "private")
    sdk.assert_not_awaited()
    sdk.side_effect = OctopError.localized(ErrorCode.AGENT_IDENTITY_NOT_READY)
    with pytest.raises(OctopError):
        await svc.connect(source, "alpha", user, False, ALPHA, "wrong-private")
    assert sources.list_bindings(source) == []
    assert sources.identity_config(repo.get(source)).get("agents", {}) == {}


@pytest.mark.asyncio
async def test_identity_validation_never_echoes_private_key():
    import httpx
    from fastapi import FastAPI

    from octop.api.deps import current_user, get_server
    from octop.api.routers.external_knowledge_identity import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=1, role="admin")
    app.dependency_overrides[get_server] = lambda: SimpleNamespace()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/external-knowledge/sources/a/identity/agents/a/connect",
            json={"externalAgentId": "invalid", "privateKey": "PRIVATE-SENTINEL" * 2000},
        )
    assert response.status_code == 422
    assert "PRIVATE-SENTINEL" not in response.text


@pytest.mark.asyncio
async def test_generation_is_scoped_and_never_returns_private_material(setup):
    svc, _, _, source, user, other, sdk = setup
    sdk.return_value = {
        "externalAgentId": ALPHA,
        "publicJwk": {"kid": "k" * 43},
        "keyReference": BETA,
    }
    result = await svc.generate(source, user, False, ALPHA)
    assert sdk.await_args.args[0] == {
        "op": "generate",
        "externalAgentId": ALPHA,
        "keyScope": source + ":" + str(user),
    }
    assert "private" not in str(result).lower()
    sdk.reset_mock()
    with pytest.raises(ExternalSourceError):
        await svc.generate(source, other, False, ALPHA)
    sdk.assert_not_awaited()


@pytest.mark.asyncio
async def test_management_probe_records_failure_without_removing_binding(setup):
    svc, sources, repo, source, user, _, sdk = setup
    await _connect(svc, sdk, source, "alpha", user, ALPHA)
    sdk.side_effect = OctopError.localized(ErrorCode.AGENT_IDENTITY_NOT_READY)
    with pytest.raises(OctopError):
        await svc.search(source, "alpha", user, False, "q", 3)
    stored = sources.identity_config(repo.get(source))["agents"]["alpha"]
    assert stored["lastVerificationStatus"] == "failure"
    assert stored["lastVerifiedAt"]
    assert len(sources.list_bindings(source)) == 1


@pytest.mark.asyncio
async def test_runtime_retrieval_records_verification_without_rebinding(setup):
    svc, sources, repo, source, user, _, sdk = setup
    await _connect(svc, sdk, source, "alpha", user, ALPHA)
    row = repo.get(source)
    spec = replace(
        sources.to_spec(row),
        octop_identity=OctopIdentity(str(user), "alpha", "chat", workload_only=True),
    )
    sdk.side_effect = OctopError.localized(ErrorCode.AGENT_IDENTITY_NOT_READY)
    with pytest.raises(OctopError):
        await sources._search_spec(spec, "q", top_k=3)
    assert (
        sources.identity_config(repo.get(source))["agents"]["alpha"]["lastVerificationStatus"]
        == "failure"
    )
    sdk.side_effect = None
    sdk.return_value = {"candidates": []}
    await sources._search_spec(spec, "q", top_k=3)
    assert (
        sources.identity_config(repo.get(source))["agents"]["alpha"]["lastVerificationStatus"]
        == "success"
    )
    assert len(sources.list_bindings(source)) == 1


@pytest.mark.asyncio
async def test_generate_api_filters_private_fields_and_documents_public_response(monkeypatch):
    import httpx
    from fastapi import FastAPI

    from octop.api.deps import current_user, get_server
    from octop.api.routers import external_knowledge_identity as routes

    generator = AsyncMock(
        return_value={
            "externalAgentId": ALPHA,
            "kid": "k" * 43,
            "keyReference": BETA,
            "privateKey": "PRIVATE-SENTINEL",
            "publicJwk": {
                "kty": "RSA",
                "n": "public-n",
                "e": "AQAB",
                "kid": "k" * 43,
                "alg": "RS256",
                "use": "sig",
                "d": "PRIVATE-SENTINEL",
            },
        }
    )
    monkeypatch.setattr(routes, "_identity", lambda server: SimpleNamespace(generate=generator))
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=1, role="admin")
    app.dependency_overrides[get_server] = lambda: SimpleNamespace()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/external-knowledge/sources/source-a/identity/generate",
            json={"externalAgentId": ALPHA},
        )
    assert response.status_code == 200
    assert "PRIVATE-SENTINEL" not in response.text
    assert response.json()["keyReference"] == BETA
    schema = app.openapi()["paths"]["/external-knowledge/sources/{source_id}/identity/generate"][
        "post"
    ]["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema["$ref"].endswith("GeneratedAgentKeyResponse")


@pytest.mark.asyncio
async def test_describe_reports_saved_state_without_exposing_secrets(setup):
    svc, _sources, _repo, source, user, _, sdk = setup
    sdk.return_value = {"supported": True, "version": "0.4.0", "activeKid": None}
    fresh = await svc.describe(source, user, False)
    assert fresh["serviceKeySaved"] is True
    assert all(a["publicJwk"] is None and a["keySaved"] is False for a in fresh["agents"])
    await _connect(svc, sdk, source, "alpha", user, ALPHA)
    kid = "k" * 43
    sdk.return_value = {
        "supported": True,
        "activeKid": kid,
        "keys": [{"kid": kid, "publicJwk": {"kty": "RSA", "n": "public-n", "e": "AQAB"}}],
    }
    result = await svc.describe(source, user, False)
    alpha = next(a for a in result["agents"] if a["agentId"] == "alpha")
    assert alpha["activeKid"] == kid
    assert alpha["publicJwk"]["n"] == "public-n"
    assert alpha["keySaved"] is True
    assert "shared-test-key" not in str(result)


@pytest.mark.asyncio
async def test_agent_id_change_requires_fresh_key_material(setup):
    svc, sources, repo, source, user, _, sdk = setup
    await _connect(svc, sdk, source, "alpha", user, ALPHA)
    sdk.return_value = {
        "binding": _binding(BETA),
        "status": {"agentId": BETA},
        "activeKid": "k" * 43,
    }
    with pytest.raises(OctopError):
        await svc.connect(source, "alpha", user, False, BETA, "")
    assert sources.identity_config(repo.get(source))["agents"]["alpha"]["externalAgentId"] == ALPHA
    await svc.connect(source, "alpha", user, False, BETA, "ROTATED-PRIVATE-MATERIAL")
    stored = sources.identity_config(repo.get(source))["agents"]["alpha"]
    assert stored["externalAgentId"] == BETA
    assert "ROTATED-PRIVATE-MATERIAL" not in str(stored)


@pytest.mark.asyncio
async def test_native_identity_capabilities_without_external_runtime(monkeypatch):
    monkeypatch.setenv("PATH", "")
    assert identity._sdk_problem() is None
    assert (await identity.sdk_call({"op": "capabilities"}))["runtime"] == "python"


@pytest.mark.asyncio
async def test_describe_reports_why_the_host_runtime_is_unavailable(setup):
    svc, _sources, _repo, source, user, _, sdk = setup
    sdk.side_effect = OctopError.localized(ErrorCode.AGENT_IDENTITY_UNAVAILABLE)
    result = await svc.describe(source, user, False)
    assert result["runtimeReady"] is False
    assert result["runtimeReason"] == "native-unavailable"
    sdk.side_effect = None
    sdk.return_value = {"supported": False}
    result = await svc.describe(source, user, False)
    assert result["runtimeReady"] is False
    assert result["runtimeReason"] == "unsupported"
    sdk.return_value = {"supported": True}
    assert (await svc.describe(source, user, False))["runtimeReason"] is None


def _row(endpoint: str, insecure: bool) -> SimpleNamespace:
    return SimpleNamespace(endpoint=endpoint, allow_insecure_http=insecure)


@pytest.mark.parametrize(
    "endpoint,insecure,allowed",
    [
        ("https://akb.example", False, True),
        ("http://127.0.0.1:8088", True, True),
        ("http://localhost:8088", True, True),
        # A trusted LAN often cannot terminate TLS at the AKB host.
        ("http://172.16.2.47:8088", True, True),
        ("http://192.168.24.201:8088", True, True),
        ("http://10.0.0.5", True, True),
        # ...but the override never extends to the public internet.
        ("http://8.8.8.8", True, False),
        ("http://172.32.0.1", True, False),
        ("http://akb.example", True, False),
        ("http://172.16.2.47:8088", False, False),
        # A pasted MCP endpoint is stripped, the way plain retrieval already does.
        ("http://192.168.24.201:8088/api/mcp", True, True),
        ("https://akb.example/api", False, True),
        ("https://user:pw@akb.example", False, False),
    ],
)
def test_origin_check_matches_the_sdk(endpoint: str, insecure: bool, allowed: bool) -> None:
    if allowed:
        identity._require_origin(_row(endpoint, insecure))
        return
    with pytest.raises(OctopError) as err:
        identity._require_origin(_row(endpoint, insecure))
    assert err.value.details["reason"] == "origin-unsupported"


@pytest.mark.parametrize(
    "endpoint,expected",
    [
        ("http://192.168.24.201:8088/api/mcp", "http://192.168.24.201:8088"),
        ("https://akb.example/api/v1/external", "https://akb.example"),
        ("https://akb.example:8443", "https://akb.example:8443"),
        ("http://[fd00::1]:8088/api/mcp", "http://[fd00::1]:8088"),
        ("https://akb.example", "https://akb.example"),
    ],
)
def test_sdk_origin_strips_the_pasted_path(endpoint: str, expected: str) -> None:
    assert identity._sdk_origin(endpoint) == expected


def test_authentication_type_is_explicit_and_protocol_constrained(setup):
    svc, sources, repo, source, user, _, _ = setup
    assert sources.list_sources(user_id=user, is_admin=False)[0]["auth_type"] == "agent"
    changed = sources.update_source(source, user_id=user, is_admin=False, auth_type="service")
    assert changed["auth_type"] == "service"
    assert sources.credentials_for(repo.get(source))["api_key"] == "shared-test-key"
    with pytest.raises(OctopError):
        svc.source(source, user, False)
    with pytest.raises(OctopError):
        sources.create_source(
            user_id=user,
            kind="ragflow",
            display_name="bad",
            endpoint="https://example.com",
            auth_type="agent",
        )


@pytest.mark.asyncio
async def test_agent_mode_without_binding_never_calls_unsigned_adapter(setup, monkeypatch):
    _, sources, _, source, user, _, _ = setup
    adapter = SimpleNamespace(search=AsyncMock())
    monkeypatch.setattr("octop.infra.knowledge.external.service.get_adapter", lambda _: adapter)
    with pytest.raises(OctopError) as err:
        await sources.search_source(source, "question", user_id=user, is_admin=False)
    assert err.value.code == ErrorCode.AGENT_IDENTITY_NOT_READY
    adapter.search.assert_not_awaited()


@pytest.mark.asyncio
async def test_bound_agent_cannot_be_silently_changed_to_service_mode(setup):
    svc, sources, _, source, user, _, sdk = setup
    await _connect(svc, sdk, source, "alpha", user, ALPHA)
    with pytest.raises(OctopError):
        sources.update_source(source, user_id=user, is_admin=False, auth_type="service")


@pytest.mark.asyncio
async def test_service_mode_calls_adapter_without_agent_assertion(setup, monkeypatch):
    from octop.infra.knowledge.external.types import ExternalSearchResult

    _, sources, repo, source, user, _, sdk = setup
    sources.update_source(source, user_id=user, is_admin=False, auth_type="service")
    adapter = SimpleNamespace(search=AsyncMock(return_value=ExternalSearchResult(chunks=[])))
    monkeypatch.setattr("octop.infra.knowledge.external.service.get_adapter", lambda _: adapter)
    spec = replace(
        sources.to_spec(repo.get(source)), octop_identity=OctopIdentity(str(user), "alpha", "s")
    )
    await sources._search_spec(spec, "question", top_k=3)
    assert adapter.search.await_args.args[0].octop_identity is None
    assert adapter.search.await_args.args[1]["auth_type"] == "service"
    sdk.assert_not_awaited()


@pytest.mark.asyncio
async def test_connection_binding_does_not_mount_and_connection_removal_covers_all_resources(setup):
    svc, sources, repo, source, user, _, sdk = setup
    first = repo.get(source)
    second = sources.create_source(
        user_id=user,
        kind="akb-rest",
        display_name="Second",
        endpoint=first.endpoint,
        connection_id=first.connection_id,
        ref="OTHER",
    )
    sdk.return_value = {
        "binding": _binding(ALPHA),
        "status": {"agentId": ALPHA},
        "activeKid": "k" * 43,
    }
    context = "connection:" + first.connection_id
    await svc.connect(
        context,
        "alpha",
        user,
        False,
        ALPHA,
        "PRIVATE-TEST-MATERIAL",
        revision=first.revision,
        mount_resource=False,
    )
    assert not repo.list_bindings()
    current = repo.get(source)
    assert repo.checks(current.connection_id, "")[0]["status"] == "success"
    # Existing direct mounts under both resources must be removed together, while resources survive.
    repo.set_binding(source_id=source, target_type="agent", target_id="alpha", enabled=True)
    repo.set_binding(
        source_id=second["source_id"], target_type="agent", target_id="alpha", enabled=True
    )
    with pytest.raises(OctopError):
        svc.disconnect(context, "alpha", user, False, revision=first.revision)
    svc.disconnect(context, "alpha", user, False, revision=current.revision)
    assert not repo.list_bindings()
    assert repo.get(source) and repo.get(second["source_id"])


@pytest.mark.asyncio
async def test_reverifying_one_agent_preserves_other_agents_checks(setup):
    svc, _, repo, source, user, _, sdk = setup
    await _connect(svc, sdk, source, "alpha", user, ALPHA)
    await _connect(svc, sdk, source, "beta", user, BETA)
    connection = repo.get(source).connection_id
    assert {row["agent_id"] for row in repo.checks(connection, "")} == {"alpha", "beta"}
    await _connect(svc, sdk, source, "alpha", user, ALPHA)
    assert {row["agent_id"] for row in repo.checks(connection, "")} == {"alpha", "beta"}
    svc.disconnect(source, "alpha", user, False)
    assert {row["agent_id"] for row in repo.checks(connection, "")} == {"beta"}


@pytest.mark.asyncio
async def test_connection_identity_http_contract_has_no_resource_requirement(setup, monkeypatch):
    import httpx
    from fastapi import FastAPI

    from octop.api.deps import current_user, get_server
    from octop.api.routers import external_knowledge_identity as routes
    from octop.infra.knowledge.external.connections import Connections

    svc, sources, _, _, user, _, _ = setup
    connection = Connections(sources).create(
        user,
        kind="akb-rest",
        display_name="Empty",
        endpoint="https://akb.example",
        auth_type="agent",
        api_key="synthetic",
    )
    monkeypatch.setattr(routes, "_identity", lambda _: svc)
    app = FastAPI()
    app.include_router(routes.connection_router)
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=user, role="user")
    app.dependency_overrides[get_server] = lambda: SimpleNamespace()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/external-knowledge/connections/{connection['connection_id']}/identity"
        )
    assert response.status_code == 200
    assert response.json()["resources"] == []
    assert response.json()["connectionId"] == connection["connection_id"]
    assert "privateKey" not in response.json()
    assert app.openapi()["paths"][
        "/external-knowledge/connections/{connection_id}/identity/agents/{agent_id}/connect"
    ]["post"]
