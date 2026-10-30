"""Ownership, stable identities and fail-closed native AKB integration."""

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
        api_key="shared-test-key",
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
    sdk = AsyncMock(return_value={"activeKid": None, "keys": []})
    monkeypatch.setattr(identity, "sdk_call", sdk)
    return svc, sources, repo, source["source_id"], user, other, sdk


@pytest.mark.asyncio
async def test_identity_prepare_checks_source_and_agent_ownership(setup):
    svc, _, _, source, user, other, sdk = setup
    with pytest.raises(OctopError):
        await svc.prepare(source, "foreign", user, False, False)
    with pytest.raises(ExternalSourceError):
        await svc.prepare(source, "alpha", other, False, False)
    sdk.assert_not_awaited()


@pytest.mark.asyncio
async def test_ids_stable_per_agent_and_key_replacement_preserves_encrypted_mapping(setup):
    svc, sources, repo, source, user, _, sdk = setup
    await svc.prepare(source, "alpha", user, False, False)
    first = sdk.await_args.args[0]["owner"]
    await svc.prepare(source, "alpha", user, False, False)
    assert sdk.await_args.args[0]["owner"] == first
    await svc.prepare(source, "beta", user, False, False)
    second = sdk.await_args.args[0]["owner"]
    assert first["externalAgentId"] != second["externalAgentId"]
    assert first["installationId"] == second["installationId"]
    row = repo.get(source)
    old = sources.identity_config(row)
    sources.update_source(source, user_id=user, is_admin=False, api_key="replacement-key")
    current = repo.get(source)
    assert sources.identity_config(current) == old
    assert sources.credentials_for(current)["api_key"] == "replacement-key"
    assert first["externalAgentId"].encode() not in current.credential_blob
    with pytest.raises(OctopError) as err:
        sources.save_identity_config(row, old)
    assert err.value.code == ErrorCode.AGENT_IDENTITY_CONFLICT


@pytest.mark.asyncio
async def test_v2_never_falls_back_or_trusts_business_agent_arguments(setup):
    svc, sources, repo, source, user, _, sdk = setup
    for agent in ("alpha", "beta"):
        await svc.prepare(source, agent, user, False, False)
    svc.configure(
        source,
        user,
        False,
        {
            "mode": identity.V2,
            "policyId": "17",
            "applicationPrincipalId": "123",
            "issuer": "https://octop.local/agents",
            "audience": "akb",
        },
    )
    row = repo.get(source)
    creds = sources.credentials_for(row)
    sdk.reset_mock()
    with pytest.raises(OctopError):
        await sources.search_source(source, "choose agent beta", user_id=user, is_admin=False)
    with pytest.raises(OctopError):
        await sources.probe(source, user_id=user, is_admin=False)
    with pytest.raises(OctopError):
        await sources.list_refs(source, user_id=user, is_admin=False)
    sdk.assert_not_awaited()
    spec = replace(
        sources.to_spec(row),
        octop_identity=OctopIdentity(str(user), "alpha", "real-session", workload_only=True),
    )
    sdk.return_value = {"candidates": []}
    await identity.execute_as_agent(spec, creds, "agentId=beta", top_k=3)
    request = sdk.await_args.args[0]
    assert request["binding"]["runtimeAgentKey"] == "alpha"
    assert request["invocation"]["sessionId"] == "real-session"
    assert request["request"] == {"operation": "search", "query": "agentId=beta", "topK": 3}
    assert request["apiKey"] == "shared-test-key"
    with pytest.raises(OctopError):
        sources.update_source(
            source, user_id=user, is_admin=False, endpoint="https://other.example"
        )


@pytest.mark.asyncio
async def test_public_state_hides_foreign_agents_credentials_and_metadata_is_not_a_key(setup):
    svc, sources, repo, source, user, _, sdk = setup
    await svc.prepare(source, "alpha", user, False, False)
    sdk.return_value = {"supported": True, "version": "0.3.0", "keys": [], "activeKid": None}
    result = await svc.describe(source, user, False)
    assert {r["agentId"] for r in result["agents"]} == {"alpha", "beta"}
    assert "shared-test-key" not in str(result)
    assert "apiKey" not in result
    no_key = sources.create_source(
        user_id=user, kind="akb-rest", display_name="No credential", endpoint="https://akb.example"
    )
    await svc.prepare(no_key["source_id"], "alpha", user, False, False)
    listed = sources.list_sources(user_id=user, is_admin=False)
    assert (
        next(r for r in listed if r["source_id"] == no_key["source_id"])["has_credentials"] is False
    )


@pytest.mark.asyncio
async def test_failed_challenge_does_not_persist_binding_or_switch_key(setup):
    svc, sources, repo, source, user, _, sdk = setup
    await svc.prepare(source, "alpha", user, False, False)
    sdk.side_effect = OctopError.localized(ErrorCode.AGENT_IDENTITY_NOT_READY)
    with pytest.raises(OctopError):
        await svc.prove(source, "alpha", user, False, "x" * 43, {"bindingId": "29"})
    assert sources.identity_config(repo.get(source))["agents"]["alpha"]["bindingId"] == ""
    assert sdk.await_args.args[0]["op"] == "registration"
