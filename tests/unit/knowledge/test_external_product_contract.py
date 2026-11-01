"""Product contracts: tenant isolation, native signing, connection reuse and budgets."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from octop.infra.connectors.crypto import encrypt_credentials
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.connectors import ConnectorRepo
from octop.infra.db.repos.external_knowledge import ExternalKnowledgeRepo
from octop.infra.db.repos.secrets import SecretRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import OctopError
from octop.infra.knowledge.akb_identity import OctopIdentity
from octop.infra.knowledge.external import http
from octop.infra.knowledge.external.agent_identity import execute_as_agent, sdk_call
from octop.infra.knowledge.external.native_identity import KeyStore
from octop.infra.knowledge.external.request_auth import public_jwk
from octop.infra.knowledge.external.service import ExternalKnowledgeService
from octop.infra.knowledge.external.types import (
    ExternalChunk,
    ExternalSearchResult,
    ExternalSourceError,
    ExternalSourceSpec,
)


@pytest.fixture
def product(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path))
    pool = SqlitePool(tmp_path / "db.sqlite")
    run_migrations(pool)
    users = UserRepo(pool)
    owner = users.create(username="owner", password_hash="test", role="user")
    other = users.create(username="other", password_hash="test", role="user")
    with pool.transaction() as db:
        for agent, user in (("owner-agent", owner), ("other-agent", other)):
            db.execute(
                "INSERT INTO agents(agent_id,user_id,name,created_at,updated_at) VALUES(?,?,?,1,1)",
                (agent, user, agent),
            )
    repo = ExternalKnowledgeRepo(pool)
    secrets = SecretRepo(pool)
    connectors = ConnectorRepo(pool)
    service = ExternalKnowledgeService(repo, connectors, secrets)
    yield service, repo, connectors, secrets, owner, other, pool
    pool.close()


def source(product, **kw):
    service, _, _, _, owner, _, _ = product
    return service.create_source(
        user_id=owner,
        kind="akb-mcp",
        display_name="Docs",
        endpoint="https://kb.example/gateway",
        api_key="synthetic",
        ref="KB_A",
        **kw,
    )


def test_private_connector_cannot_be_borrowed(product):
    service, _, connectors, secrets, owner, other, _ = product
    connectors.create(
        instance_id="foreign",
        user_id=other,
        kind="custom-mcp",
        display_name="Private",
        mcp_server_name="p",
    )
    connectors.upsert_credentials(
        instance_id="foreign", blob=encrypt_credentials(secrets, {"api_key": "private-test-secret"})
    )
    with pytest.raises(ExternalSourceError, match="connector"):
        service.create_source(
            user_id=owner,
            kind="akb-mcp",
            display_name="Bad",
            endpoint="https://attacker.invalid",
            instance_id="foreign",
        )


def test_mount_requires_target_ownership(product):
    service, _, _, _, owner, _, _ = product
    row = source(product)
    for target in ("other-agent", "nonexistent"):
        with pytest.raises(ExternalSourceError):
            service.set_binding(
                row["source_id"],
                user_id=owner,
                is_admin=False,
                target_type="agent",
                target_id=target,
            )
    service.set_binding(
        row["source_id"],
        user_id=owner,
        is_admin=False,
        target_type="agent",
        target_id="owner-agent",
    )


def test_connection_reused_without_copying_credentials_and_revocation_is_live(product):
    service, repo, _, _, owner, other, _ = product
    first = source(product, shared=True)
    second = service.create_source(
        user_id=other,
        kind="akb-mcp",
        display_name="Other library",
        endpoint="https://ignored.invalid",
        connection_id=first["connection_id"],
        ref="KB_B",
    )
    assert second["endpoint"] == first["endpoint"]
    assert second["connection_id"] == first["connection_id"]
    assert service.credentials_for(repo.get(second["source_id"]))["api_key"] == "synthetic"
    service.update_source(first["source_id"], user_id=owner, is_admin=False, shared=False)
    with pytest.raises(ExternalSourceError):
        service._require_use(second["source_id"], user_id=other, is_admin=False)


def test_stale_resource_and_connection_edits_are_rejected(product):
    service, _, _, _, owner, _, _ = product
    first = source(product)
    service.update_source(
        first["source_id"],
        user_id=owner,
        is_admin=False,
        ref="KB_B",
        resource_revision=first["resource_revision"],
    )
    with pytest.raises(OctopError):
        service.update_source(
            first["source_id"],
            user_id=owner,
            is_admin=False,
            ref="KB_C",
            resource_revision=first["resource_revision"],
        )
    service.update_source(first["source_id"], user_id=owner, is_admin=False, api_key="rotated")
    with pytest.raises(OctopError):
        service.update_source(
            first["source_id"],
            user_id=owner,
            is_admin=False,
            api_key="old",
            revision=first["revision"],
        )


@pytest.mark.asyncio
async def test_same_chunk_id_in_two_resources_is_not_merged(product, monkeypatch):
    service, repo, _, _, _, _, _ = product
    first = source(product)
    second = source(product)

    async def search(spec, query, *, top_k):
        return ExternalSearchResult(
            [ExternalChunk("same", spec.binding_id, 0.9, "doc", origin="akb-mcp", ref=spec.ref)]
        )

    monkeypatch.setattr(service, "_search_spec", search)
    result = await service.retrieve(
        query="q", specs=[service.to_spec(repo.get(r["source_id"])) for r in (first, second)]
    )
    assert len(result["chunks"]) == 2
    assert len({r["source_id"] for r in result["chunks"]}) == 2


@pytest.mark.asyncio
async def test_deadline_and_all_failed_are_not_empty_results(product, monkeypatch):
    service, repo, _, _, _, _, _ = product
    first = source(product)

    async def slow(*args, **kw):
        await asyncio.sleep(1)
        return ExternalSearchResult()

    monkeypatch.setattr(service, "_search_spec", slow)
    spec = replace(service.to_spec(repo.get(first["source_id"])), timeout_seconds=0.02)
    result = await service.retrieve(query="q", specs=[spec])
    assert result["status"] == "failed"
    assert result["warnings"][0]["code"] == "TIMEOUT"
    assert result["warnings"][0]["source_id"] == first["source_id"]


@pytest.mark.asyncio
async def test_native_runtime_does_not_need_node(monkeypatch):
    monkeypatch.setenv("PATH", "")
    assert (await sdk_call({"op": "capabilities"}))["supported"] is True


@pytest.fixture(scope="module")
def pem():
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


@pytest.mark.asyncio
async def test_native_agent_and_service_send_same_business_request_and_exact_signature(
    product, pem
):
    service, _, _, secrets, owner, _, _ = product
    binding = {
        "platformId": "p",
        "installationId": "i",
        "workspaceId": "w",
        "externalAgentId": "e21cce9c-1b16-4ab8-a76a-bb761df919b8",
        "runtimeAgentKey": "build",
        "bindingId": "3",
        "policyId": "17",
        "applicationPrincipalId": "123",
        "issuer": "https://issuer.example",
        "audience": "akb",
        "resource": "https://kb.example",
    }
    key_owner = {
        k: binding[k] for k in ("platformId", "installationId", "workspaceId", "externalAgentId")
    }
    store = KeyStore(secrets, "unused")
    imported = store.import_key(key_owner, pem)
    store.activate(key_owner, imported)
    binding["keyReference"] = imported["keyReference"]
    sent = []
    claims = []

    def upstream(request):
        assert request.headers["X-API-Key"] == "synthetic"
        body = json.loads(request.content)
        if "X-AKB-Workload-Assertion" in request.headers:
            claim = jwt.decode(
                request.headers["X-AKB-Workload-Assertion"],
                serialization.load_pem_private_key(pem.encode(), None).public_key(),
                algorithms=["RS256"],
                audience="akb",
            )
            assert (
                claim["request_binding"]["body_sha256"]
                == hashlib.sha256(request.content).hexdigest()
            )
            assert claim["request_binding"]["target"] == "/gateway/api/mcp"
            assert claim["credential_sha256"] == hashlib.sha256(b"synthetic").hexdigest()
            assert (
                jwt.get_unverified_header(request.headers["X-AKB-Workload-Assertion"])["kid"]
                == public_jwk(pem)["kid"]
            )
            claims.append(claim)
        if body["method"] == "initialize":
            result = {"protocolVersion": "2025-06-18"}
        else:
            sent.append(body["params"])
            result = {
                "structuredContent": {
                    "chunks": [{"id": "c", "content": "verified", "score": 0.9, "source": "Policy"}]
                }
            }
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    config = {
        **binding,
        "simple": True,
        "agents": {
            "owner-agent": {
                "externalAgentId": binding["externalAgentId"],
                "runtimeAgentKey": "build",
                "bindingId": "3",
                "keyReference": imported["keyReference"],
            }
        },
    }
    spec = ExternalSourceSpec(
        binding_id="s",
        display_name="Docs",
        kind="akb-mcp",
        endpoint="https://kb.example/gateway",
        ref="KB_A",
        selector_argument="knowledgeBaseId",
        tool_name="knowledge_search",
        tool_arguments={"minScore": 0.7},
        octop_identity=OctopIdentity(str(owner), "owner-agent", "session"),
    )
    from octop.infra.knowledge.external.adapters import akb_mcp

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        token = http._CLIENT.set(client)
        try:
            await akb_mcp.search(
                replace(spec, octop_identity=None), {"api_key": "synthetic"}, "规则🚀", top_k=3
            )
            result = await execute_as_agent(
                spec,
                {"api_key": "synthetic", "agent_identity": config, "_secret_repo": secrets},
                "规则🚀",
                top_k=3,
            )
        finally:
            http._CLIENT.reset(token)
    assert sent[0] == sent[1]
    assert sent[1]["arguments"]["knowledgeBaseId"] == "KB_A"
    assert sent[1]["arguments"]["minScore"] == 0.7
    assert result["chunks"][0]["content"] == "verified"
    assert len({c["jti"] for c in claims}) == 2
    assert not any(
        pem.encode() in value
        for value in [secrets.get("agent_identity:" + imported["keyReference"])]
    )


@pytest.mark.asyncio
async def test_streaming_size_limit_stops_reading():
    reads = []

    class Large(httpx.AsyncByteStream):
        async def __aiter__(self):
            for i in range(10):
                reads.append(i)
                yield b"x" * 32

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Large()))
    ) as client:
        token = http._CLIENT.set(client)
        try:
            with pytest.raises(ExternalSourceError) as error:
                await http.post_json(
                    "https://example.invalid",
                    headers={},
                    payload={},
                    timeout_seconds=1,
                    origin="test",
                    max_bytes=40,
                )
            assert error.value.code == "RESPONSE_TOO_LARGE"
        finally:
            http._CLIENT.reset(token)
    assert reads == [0, 1]


def test_migration_is_idempotent(product):
    _, repo, _, _, _, _, pool = product
    first = source(product)
    run_migrations(pool)
    run_migrations(pool)
    assert repo.get(first["source_id"]).connection_id == first["connection_id"]


def test_connection_lifecycle_is_independent_of_resources(product):
    from octop.infra.knowledge.external.connections import Connections

    service, repo, _, _, owner, _, _ = product
    manager = Connections(service)
    connection = manager.create(
        owner,
        kind="akb-mcp",
        display_name="Connection",
        endpoint="https://kb.example",
        api_key="test-key",
    )
    assert connection["resource_count"] == 0
    assert len(manager.list(owner, False)) == 1
    resource = service.create_source(
        user_id=owner,
        kind="",
        display_name="Library",
        endpoint="",
        connection_id=connection["connection_id"],
        ref="KB_A",
    )
    with pytest.raises(OctopError):
        manager.delete(connection["connection_id"], owner, False)
    service.delete_source(resource["source_id"], user_id=owner, is_admin=False)
    assert repo.connection(connection["connection_id"]) is not None
    manager.delete(connection["connection_id"], owner, False)
    assert manager.list(owner, False) == []


def test_legacy_identity_migrates_without_reclassification_or_key_loss(product):
    from octop.infra.connectors.crypto import decrypt_credentials

    service, repo, _, secrets, owner, _, _ = product
    config = {
        "simple": True,
        "policyId": "17",
        "agents": {
            "owner-agent": {
                "externalAgentId": "e21cce9c-1b16-4ab8-a76a-bb761df919b8",
                "runtimeAgentKey": "build",
                "bindingId": "3",
            }
        },
    }
    repo.create(
        source_id="legacy",
        owner_user_id=owner,
        kind="akb-mcp",
        display_name="Legacy",
        endpoint="https://kb.example",
        credential_blob=encrypt_credentials(
            secrets, {"api_key": "preserved", "agent_identity": config}
        ),
    )
    row = service._require_get("legacy")
    assert row.auth_type == "agent"
    assert "agent_identity" not in decrypt_credentials(secrets, row.credential_blob)
    assert (
        service.identity_config(row)["agents"]["owner-agent"]["externalAgentId"]
        == config["agents"]["owner-agent"]["externalAgentId"]
    )
    revision = row.revision
    assert service._require_get("legacy").revision == revision
    assert service.credentials_for(row)["api_key"] == "preserved"


def test_key_rotation_invalidates_previous_successful_checks(product):
    service, repo, _, _, owner, _, _ = product
    row = source(product)
    repo.record_check(row["connection_id"], row["source_id"], "owner-agent", "identity", "success")
    assert repo.checks(row["connection_id"], row["source_id"])
    service.update_source(row["source_id"], user_id=owner, is_admin=False, api_key="rotated")
    assert repo.checks(row["connection_id"], row["source_id"]) == []


@pytest.mark.asyncio
async def test_shared_connection_user_can_bind_own_agent_but_not_others(product, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from octop.infra.knowledge.external import agent_identity as identity

    service, repo, _, _, owner, other, _ = product
    row = source(product, auth_type="agent", shared=True)
    records = {
        "owner-agent": SimpleNamespace(user_id=owner),
        "other-agent": SimpleNamespace(user_id=other),
    }
    manager = identity.AgentIdentityService(service, SimpleNamespace(get=records.get))
    binding = {
        "platformId": "p",
        "installationId": "i",
        "workspaceId": "w",
        "externalAgentId": "e21cce9c-1b16-4ab8-a76a-bb761df919b8",
        "runtimeAgentKey": "build",
        "bindingId": "3",
        "policyId": "17",
        "applicationPrincipalId": "123",
        "issuer": "https://issuer.example",
        "audience": "akb",
        "resource": "https://kb.example",
    }
    sdk = AsyncMock(return_value={"binding": binding, "status": {}, "activeKid": "kid"})
    monkeypatch.setattr(identity, "sdk_call", sdk)
    with pytest.raises(OctopError):
        await manager.connect(
            row["source_id"], "owner-agent", other, False, binding["externalAgentId"], "private"
        )
    await manager.connect(
        row["source_id"], "other-agent", other, False, binding["externalAgentId"], "private"
    )
    assert "other-agent" in service.identity_config(repo.get(row["source_id"]))["agents"]


@pytest.mark.asyncio
async def test_rate_limited_retry_creates_a_new_proof(product, pem):
    from octop.infra.knowledge.external.request_auth import signed_requests

    binding = {
        "platformId": "p",
        "installationId": "i",
        "workspaceId": "w",
        "externalAgentId": "e21cce9c-1b16-4ab8-a76a-bb761df919b8",
        "runtimeAgentKey": "build",
        "policyId": "17",
        "applicationPrincipalId": "123",
        "issuer": "https://issuer.example",
        "audience": "akb",
        "resource": "https://kb.example",
    }
    proofs = []

    def upstream(request):
        proofs.append(
            jwt.decode(
                request.headers["X-AKB-Workload-Assertion"], options={"verify_signature": False}
            )
        )
        return httpx.Response(429 if len(proofs) == 1 else 200, json={"data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        token = http._CLIENT.set(client)
        try:
            with signed_requests(
                binding,
                pem,
                "test-key",
                {"sessionId": "s", "invocationId": "e21cce9c-1b16-4ab8-a76a-bb761df919b8"},
            ):
                await http.get_json(
                    "https://kb.example/api/v1/external/bases",
                    headers={},
                    timeout_seconds=1,
                    origin="akb",
                    status_map={429: "RATE_LIMITED"},
                )
        finally:
            http._CLIENT.reset(token)
    assert len(proofs) == 2
    assert proofs[0]["jti"] != proofs[1]["jti"]


@pytest.mark.asyncio
async def test_new_binding_cannot_borrow_an_existing_host_key_without_import(product, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from octop.infra.knowledge.external import agent_identity as identity

    service, _, _, _, owner, _, _ = product
    row = source(product, auth_type="agent")
    sdk = AsyncMock()
    monkeypatch.setattr(identity, "sdk_call", sdk)
    manager = identity.AgentIdentityService(
        service, SimpleNamespace(get=lambda _: SimpleNamespace(user_id=owner))
    )
    with pytest.raises(OctopError):
        await manager.connect(
            row["source_id"],
            "owner-agent",
            owner,
            False,
            "e21cce9c-1b16-4ab8-a76a-bb761df919b8",
            "",
        )
    sdk.assert_not_awaited()


def test_local_key_is_removed_only_when_last_binding_releases_it(product, pem):
    from types import SimpleNamespace

    from octop.infra.knowledge.external.agent_identity import AgentIdentityService

    service, repo, _, secrets, owner, _, _ = product
    a = source(product, auth_type="agent")
    b = source(product, auth_type="agent")
    coordinates = {
        "platformId": "p",
        "installationId": "i",
        "workspaceId": "w",
        "externalAgentId": "e21cce9c-1b16-4ab8-a76a-bb761df919b8",
    }
    store = KeyStore(secrets, "unused")
    key = store.import_key(coordinates, pem)
    store.activate(coordinates, key)
    config = {
        **coordinates,
        "simple": True,
        "agents": {
            "owner-agent": {
                "externalAgentId": coordinates["externalAgentId"],
                "keyReference": key["keyReference"],
            }
        },
    }
    for resource in (a, b):
        service.save_identity_config(
            repo.get(resource["source_id"]), config, agent_id="owner-agent"
        )
    manager = AgentIdentityService(
        service, SimpleNamespace(get=lambda _: SimpleNamespace(user_id=owner))
    )
    manager.disconnect(a["source_id"], "owner-agent", owner, False)
    assert secrets.get("agent_identity:" + key["keyReference"]) is not None
    manager.disconnect(b["source_id"], "owner-agent", owner, False)
    assert secrets.get("agent_identity:" + key["keyReference"]) is None


def test_connection_projection_has_no_ambiguous_column_names(product):
    service, repo, _, _, _, _, pool = product
    row = source(product)
    with pool.connect() as connection:
        cursor = connection.execute(repo._projection() + "WHERE s.source_id=?", (row["source_id"],))
        names = [column[0] for column in cursor.description]
        assert len(names) == len(set(names))
    assert repo.get(row["source_id"]).credential_blob is not None


def test_ambiguous_legacy_identity_stays_pending_instead_of_becoming_service(product):
    service, repo, _, secrets, owner, _, _ = product
    repo.create(
        source_id="ambiguous",
        owner_user_id=owner,
        kind="akb-mcp",
        display_name="Old config",
        endpoint="https://kb.example",
        credential_blob=encrypt_credentials(
            secrets, {"api_key": "old-test-key", "agent_identity": {"agents": {"old": {}}}}
        ),
    )
    listed = service.list_sources(user_id=owner, is_admin=False)
    assert listed[0]["needs_attention"] is True
    assert listed[0]["auth_type"] is None
    assert repo.get("ambiguous").auth_type == "legacy"


def test_one_corrupt_legacy_credential_does_not_break_catalog_and_can_be_repaired(product):
    from octop.infra.knowledge.external.connections import Connections

    service, repo, _, _, owner, _, _ = product
    repo.create(
        source_id="broken",
        owner_user_id=owner,
        kind="akb-mcp",
        display_name="Broken",
        endpoint="https://kb.example",
        credential_blob=b"unreadable",
    )
    assert service.list_sources(user_id=owner, is_admin=False)[0]["needs_attention"] is True
    manager = Connections(service)
    assert manager.list(owner, False)[0]["needs_attention"] is True
    manager.update(
        "broken",
        owner,
        False,
        {
            "auth_type": "service",
            "api_key": "replaced-test-key",
            "revision": repo.connection("broken")["revision"],
        },
    )
    assert service.credentials_for(repo.get("broken"))["api_key"] == "replaced-test-key"


def test_resource_change_invalidates_checks_and_late_result_is_discarded(product):
    service, repo, _, _, owner, _, _ = product
    saved = source(product)
    old = repo.get(saved["source_id"])
    repo.record_check(
        old.connection_id,
        old.source_id,
        "",
        "retrieval",
        "success",
        expected_revision=old.revision,
        expected_resource_revision=old.resource_revision,
    )
    assert repo.checks(old.connection_id, old.source_id)
    service.update_source(
        old.source_id,
        user_id=owner,
        is_admin=False,
        ref="KB_B",
        resource_revision=old.resource_revision,
    )
    assert repo.checks(old.connection_id, old.source_id) == []
    repo.record_check(
        old.connection_id,
        old.source_id,
        "",
        "retrieval",
        "success",
        expected_revision=old.revision,
        expected_resource_revision=old.resource_revision,
    )
    assert repo.checks(old.connection_id, old.source_id) == []
    with pytest.raises(OctopError):
        service.update_source(
            old.source_id,
            user_id=owner,
            is_admin=False,
            ref="KB_C",
            resource_revision=old.resource_revision,
        )


@pytest.mark.asyncio
async def test_connection_identity_without_resource_and_pending_keys_are_scoped(product):
    from types import SimpleNamespace

    from octop.infra.knowledge.external.agent_identity import AgentIdentityService
    from octop.infra.knowledge.external.connections import Connections

    service, repo, _, _, owner, other, _ = product
    connection = Connections(service).create(
        owner,
        kind="akb-rest",
        display_name="Independent",
        endpoint="https://kb.example",
        auth_type="agent",
        api_key="synthetic",
    )
    identity = AgentIdentityService(service, SimpleNamespace(list_by_user=lambda _: []))
    context = "connection:" + connection["connection_id"]
    key = await identity.generate(context, owner, False, "")
    state = await identity.describe(context, owner, False)
    assert state["resourceCount"] == 0 and state["resources"] == []
    assert state["pendingKeys"] == [key]
    assert "pem" not in str(state) and "PRIVATE KEY" not in str(state)
    with pytest.raises(OctopError):
        await identity.discard_generated(context, other, False, key["keyReference"])
    repo.update_connection(connection["connection_id"], connection["revision"], {"shared": 1})
    other_state = await identity.describe(context, other, False)
    assert other_state["pendingKeys"] == []
    await identity.discard_generated(context, other, False, key["keyReference"])
    assert (await identity.describe(context, owner, False))["pendingKeys"] == [key]
    await identity.discard_generated(context, owner, False, key["keyReference"])
    assert (await identity.describe(context, owner, False))["pendingKeys"] == []


def test_resource_visibility_does_not_change_connection_or_other_resources(product):
    service, repo, _, _, owner, _, _ = product
    first = source(product)
    row = repo.get(first["source_id"])
    second = service.create_source(
        user_id=owner,
        kind=row.kind,
        display_name="Private resource",
        endpoint=row.endpoint,
        connection_id=row.connection_id,
        ref="SECOND",
    )
    service.update_source(
        row.source_id,
        user_id=owner,
        is_admin=False,
        resource_shared=True,
        resource_revision=row.resource_revision,
    )
    assert repo.get(row.source_id).shared is True
    assert repo.get(second["source_id"]).shared is False
    assert not repo.connection(row.connection_id)["shared"]
    assert repo.connection(row.connection_id)["revision"] == row.revision


def test_upgrading_schema30_invalidates_old_resource_checks(product):
    service, repo, _, _, _, _, pool = product
    saved = source(product)
    row = repo.get(saved["source_id"])
    repo.record_check(row.connection_id, row.source_id, "", "retrieval", "success")
    with pool.transaction() as db:
        db.execute("ALTER TABLE external_connection_checks DROP COLUMN resource_revision")
        db.execute("UPDATE _schema_version SET version=30")
    run_migrations(pool)
    run_migrations(pool)
    assert repo.checks(row.connection_id, row.source_id) == []
    with pool.connect() as db:
        assert (
            db.execute("SELECT resource_revision FROM external_connection_checks").fetchone()[0]
            == 0
        )


def test_akb_connection_requires_a_real_service_credential(product):
    from octop.infra.knowledge.external.connections import Connections

    service, _, _, _, owner, _, _ = product
    with pytest.raises(ExternalSourceError) as failure:
        Connections(service).create(
            owner,
            kind="akb-mcp",
            display_name="Empty",
            endpoint="https://kb.example",
            api_key="   ",
        )
    assert failure.value.code == "INVALID_CREDENTIALS"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["akb-mcp", "akb-rest"])
@pytest.mark.parametrize("mode", ["service", "agent"])
async def test_connection_check_sends_key_without_identity_or_retrieval(product, kind, mode):
    from octop.infra.knowledge.external.connections import Connections

    service, repo, _, _, owner, other, _ = product
    manager = Connections(service)
    connection = manager.create(
        owner,
        kind=kind,
        auth_type=mode,
        display_name="Isolated",
        endpoint="https://kb.example/gateway",
        api_key="synthetic-check-key",
    )
    calls = []

    def upstream(request):
        assert request.headers["X-API-Key"] == "synthetic-check-key"
        assert "X-AKB-Workload-Assertion" not in request.headers
        calls.append(request)
        if kind == "akb-rest":
            assert (
                request.method == "GET"
                and request.url.path == "/gateway/api/v1/external/connection"
            )
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {"connected": True, "agentIdentityRequired": mode == "agent"},
                },
            )
        body = json.loads(request.content)
        assert body["method"] in {"initialize", "tools/list"}
        result = (
            {"protocolVersion": "2025-06-18"}
            if body["method"] == "initialize"
            else {"tools": [{"name": "knowledge_search"}]}
        )
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        token = http._CLIENT.set(client)
        try:
            result = await manager.probe(connection["connection_id"], owner, False)
            assert result["ok"] and result["stage"] == "connection"
            assert result["agent_identity_required"] == (mode == "agent")
            assert manager.list(owner, False)[0]["check"]["status"] == "success"
            with pytest.raises(OctopError):
                await manager.probe(connection["connection_id"], other, False)
        finally:
            http._CLIENT.reset(token)
    assert len(calls) == (1 if kind == "akb-rest" else 2)
    assert not repo.connection_resources(connection["connection_id"])


@pytest.mark.asyncio
async def test_missing_or_stripped_key_is_recorded_as_credentials_failure(product):
    from octop.infra.knowledge.external.connections import Connections

    service, _, _, _, owner, _, _ = product
    manager = Connections(service)
    connection = manager.create(
        owner,
        kind="akb-rest",
        display_name="Proxy",
        endpoint="https://kb.example",
        api_key="synthetic",
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(401, json={"message": "missing key"})
        )
    ) as client:
        token = http._CLIENT.set(client)
        try:
            result = await manager.probe(connection["connection_id"], owner, False)
        finally:
            http._CLIENT.reset(token)
    assert result["ok"] is False and result["code"] == "INVALID_CREDENTIALS"
    assert manager.list(owner, False)[0]["check"]["code"] == "INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_resource_catalog_can_be_loaded_before_creating_first_resource(product):
    from octop.infra.knowledge.external.connections import Connections

    service, repo, _, _, owner, _, _ = product
    manager = Connections(service)
    connection = manager.create(
        owner,
        kind="akb-rest",
        display_name="Catalog",
        endpoint="https://kb.example",
        api_key="synthetic",
        api_key_header="authorization",
    )

    def upstream(request):
        assert request.headers["Authorization"] == "Bearer synthetic"
        assert request.url.path == "/api/v1/external/bases"
        return httpx.Response(
            200, json={"success": True, "data": [{"knowledgeBaseId": "KB_FIRST", "name": "First"}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        token = http._CLIENT.set(client)
        try:
            assert await manager.refs(connection["connection_id"], owner, False) == [
                {"ref": "KB_FIRST", "name": "First"}
            ]
        finally:
            http._CLIENT.reset(token)
    assert not repo.connection_resources(connection["connection_id"])


@pytest.mark.parametrize("value", ["", " ", "\n\t"])
def test_management_retrieval_rejects_empty_questions(value):
    from pydantic import ValidationError

    from octop.api.routers.external_knowledge import SearchBody
    from octop.api.routers.external_knowledge_identity import IdentitySearchBody

    for schema in (SearchBody, IdentitySearchBody):
        with pytest.raises(ValidationError):
            schema(query=value)
