"""AKB adapter coverage: delegated identity assertion, error mapping, discovery."""

from __future__ import annotations

import json
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from octop.infra.knowledge.akb_identity import OctopIdentity, issue_assertion, jwks_document
from octop.infra.knowledge.external.adapters import akb_mcp, akb_rest
from octop.infra.knowledge.external.types import ExternalSourceError, ExternalSourceSpec

IDENTITY_HEADER = "X-Octop-Identity"

# Generated once: AKB verifies RS256, so the tests need a real key pair.
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIVATE_PEM = _KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()
_PUBLIC_PEM = (
    _KEY.public_key()
    .public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    .decode()
)


@pytest.fixture
def signing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOP_AKB_IDENTITY_ENABLED", "true")
    monkeypatch.setenv("OCTOP_AKB_IDENTITY_ISSUER", "octop")
    monkeypatch.setenv("OCTOP_AKB_IDENTITY_AUDIENCE", "akb")
    monkeypatch.setenv("OCTOP_AKB_INSTANCE_ID", "octop-prod")
    monkeypatch.setenv("OCTOP_AKB_IDENTITY_PRIVATE_KEY", _PRIVATE_PEM)


def _spec(**overrides: Any) -> ExternalSourceSpec:
    base = {
        "binding_id": "src-1",
        "display_name": "AKB",
        "kind": "akb-rest",
        "endpoint": "https://akb.example.com",
        "ref": "HR-POLICY",
    }
    base.update(overrides)
    return ExternalSourceSpec(**base)  # type: ignore[arg-type]


def test_assertion_carries_delegated_claims(signing_env: None) -> None:
    token = issue_assertion(OctopIdentity(user_id="42", agent_id="agent-7", trace_id="run-1"))
    claims = jwt.decode(token, _PUBLIC_PEM, algorithms=["RS256"], audience="akb")
    assert claims["iss"] == "octop"
    assert claims["sub"] == "42"
    assert claims["agent_id"] == "agent-7"
    assert claims["octop_instance_id"] == "octop-prod"
    assert claims["octop_trace_id"] == "run-1"
    assert claims["jti"]
    # AKB rejects assertions older than a minute.
    assert 0 < claims["exp"] - claims["iat"] <= 60


def test_assertion_requires_agent_identity(signing_env: None) -> None:
    # A delegated assertion without a trusted Agent cannot be scoped by AKB.
    with pytest.raises(ValueError):
        issue_assertion(OctopIdentity(user_id="42", agent_id="   "))


def test_assertion_fails_closed_without_signing_key(
    monkeypatch: pytest.MonkeyPatch, signing_env: None
) -> None:
    for name in (
        "OCTOP_AKB_IDENTITY_ISSUER",
        "OCTOP_AKB_IDENTITY_AUDIENCE",
        "OCTOP_AKB_INSTANCE_ID",
        "OCTOP_AKB_IDENTITY_PRIVATE_KEY",
    ):
        monkeypatch.delenv(name)
    with pytest.raises(ValueError):
        issue_assertion(OctopIdentity(user_id="42", agent_id="agent-7"))


def test_jwks_verifies_the_assertion_like_akb_does(signing_env: None) -> None:
    keys = jwks_document()["keys"]
    assert len(keys) == 1
    jwk = keys[0]
    assert (jwk["kty"], jwk["alg"], jwk["use"]) == ("RSA", "RS256", "sig")

    token = issue_assertion(OctopIdentity(user_id="42", agent_id="agent-7"))
    # AKB picks the key by kid, then verifies with it: do exactly that.
    assert jwt.get_unverified_header(token)["kid"] == jwk["kid"]
    public_key = RSAAlgorithm.from_jwk(json.dumps(jwk))
    claims = jwt.decode(token, public_key, algorithms=["RS256"], audience="akb", issuer="octop")
    assert claims["sub"] == "42"


def test_jwks_is_empty_without_a_signing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTOP_AKB_IDENTITY_PRIVATE_KEY", raising=False)
    assert jwks_document() == {"keys": []}


def test_akb_headers_carry_identity(signing_env: None) -> None:
    identity = OctopIdentity(user_id="42", agent_id="agent-7")
    headers = akb_rest._headers(_spec(octop_identity=identity), {"api_key": "kos_ak_x"})
    assert headers["X-API-Key"] == "kos_ak_x"
    assert jwt.decode(headers[IDENTITY_HEADER], _PUBLIC_PEM, algorithms=["RS256"], audience="akb")
    mcp_headers = akb_mcp._headers(_spec(kind="akb-mcp", octop_identity=identity), {"api_key": "k"})
    assert mcp_headers[IDENTITY_HEADER]


def test_akb_headers_omit_identity_without_delegation() -> None:
    assert IDENTITY_HEADER not in akb_rest._headers(_spec(), {"api_key": "kos_ak_x"})
    assert IDENTITY_HEADER not in akb_mcp._headers(_spec(kind="akb-mcp"), {"api_key": "k"})


@pytest.mark.asyncio
async def test_akb_rest_maps_upstream_error_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_post_json(url: str, **kwargs: Any) -> Any:
        calls.append({"url": url, **kwargs})
        raise ExternalSourceError(
            kwargs["status_map"][403],
            "upstream returned HTTP 403",
            origin=akb_rest.ORIGIN,
        )

    monkeypatch.setattr(akb_rest, "post_json", fake_post_json)
    with pytest.raises(ExternalSourceError) as excinfo:
        await akb_rest.search(_spec(), {"api_key": "k"}, "q", top_k=3)
    assert excinfo.value.code == "FORBIDDEN"
    assert calls[0]["url"].endswith("/api/v1/external/kb/retrievals")
    assert calls[0]["payload"] == {"question": "q", "topK": 3}


@pytest.mark.asyncio
async def test_akb_rest_lists_bases(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_json(url: str, **kwargs: Any) -> Any:
        assert url.endswith("/api/v1/external/bases")
        return {
            "success": True,
            "data": [
                {"id": 101, "baseCode": "HR-POLICY", "name": "人事制度"},
                {"id": 102, "baseCode": "OPS-RUNBOOK", "name": "运维手册"},
                {"name": "no code"},
            ],
        }

    monkeypatch.setattr(akb_rest, "get_json", fake_get_json)
    refs = await akb_rest.list_refs(_spec(), {"api_key": "k"})
    assert refs == [
        {"ref": "HR-POLICY", "name": "人事制度"},
        {"ref": "OPS-RUNBOOK", "name": "运维手册"},
    ]


@pytest.mark.asyncio
async def test_akb_rest_probe_survives_missing_list_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_json(url: str, **kwargs: Any) -> Any:
        raise ExternalSourceError("FORBIDDEN", "upstream returned HTTP 403", origin="akb-rest")

    async def fake_post_json(url: str, **kwargs: Any) -> Any:
        return {"chunks": [{"id": "EVD_1", "content": "x", "score": 0.5}]}

    monkeypatch.setattr(akb_rest, "get_json", fake_get_json)
    monkeypatch.setattr(akb_rest, "post_json", fake_post_json)
    result = await akb_rest.probe(_spec(), {"api_key": "k"})
    assert result.ok is True
    assert result.refs == []


@pytest.mark.asyncio
async def test_akb_rest_probe_without_ref_only_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_json(url: str, **kwargs: Any) -> Any:
        return {"data": [{"id": 7, "baseCode": "KB_DEFAULT", "name": "默认知识库"}]}

    async def fake_post_json(url: str, **kwargs: Any) -> Any:
        raise AssertionError("must not retrieve before a base is chosen")

    monkeypatch.setattr(akb_rest, "get_json", fake_get_json)
    monkeypatch.setattr(akb_rest, "post_json", fake_post_json)
    result = await akb_rest.probe(_spec(ref=""), {"api_key": "k"})
    assert result.ok is True
    assert result.refs == [{"ref": "KB_DEFAULT", "name": "默认知识库"}]


@pytest.mark.asyncio
async def test_akb_mcp_signs_a_fresh_assertion_per_request(
    monkeypatch: pytest.MonkeyPatch, signing_env: None
) -> None:
    # AKB rejects a replayed jti, so initialize and the tool call cannot share
    # one assertion the way a single header set would make them.
    seen: list[str] = []

    async def fake_post_json(url: str, **kwargs: Any) -> Any:
        seen.append(kwargs["headers"].get(IDENTITY_HEADER, ""))
        if kwargs["payload"]["method"] == "initialize":
            return {"result": {"protocolVersion": "2025-06-18"}}
        return {"result": {"structuredContent": {"items": [{"baseCode": "KB1", "name": "库一"}]}}}

    monkeypatch.setattr(akb_mcp, "post_json", fake_post_json)
    spec = _spec(kind="akb-mcp", octop_identity=OctopIdentity(user_id="42", agent_id="agent-7"))
    refs = await akb_mcp.list_refs(spec, {"api_key": "k"})
    assert refs == [{"ref": "KB1", "name": "库一"}]
    assert len(seen) == 2
    jtis = [
        jwt.decode(token, _PUBLIC_PEM, algorithms=["RS256"], audience="akb")["jti"]
        for token in seen
    ]
    assert all(jtis) and jtis[0] != jtis[1]
