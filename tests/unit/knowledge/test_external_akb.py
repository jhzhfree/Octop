"""AKB adapter coverage: identity assertion, error mapping, base discovery."""

from __future__ import annotations

import types
from typing import Any

import jwt
import pytest

from octop.infra.knowledge.external.adapters import akb_mcp, akb_rest
from octop.infra.knowledge.external.identity import IDENTITY_HEADER, identity_token
from octop.infra.knowledge.external.types import ExternalSourceError, ExternalSourceSpec


class _FakeSecretRepo:
    def __init__(self, secret: bytes) -> None:
        self._secret = secret
        self.created = 0

    def get_or_create(self, k: str, factory: Any) -> bytes:
        self.created += 1
        return self._secret


def _config(**overrides: Any) -> types.SimpleNamespace:
    identity = types.SimpleNamespace(enabled=False, issuer="octop", audience="akb", instance_id="")
    for key, value in overrides.items():
        setattr(identity, key, value)
    return types.SimpleNamespace(akb_identity=identity)


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


def test_identity_absent_until_enabled() -> None:
    repo = _FakeSecretRepo(b"k" * 32)
    assert identity_token(_config(), repo, subject="user:1") == ""
    assert repo.created == 0
    # Enabled without an instance id cannot identify this Octop to AKB.
    assert identity_token(_config(enabled=True), repo, subject="user:1") == ""


def test_identity_assertion_claims() -> None:
    secret = b"s" * 32
    token = identity_token(
        _config(enabled=True, instance_id="octop-prod"),
        _FakeSecretRepo(secret),
        subject="user:1",
    )
    assert token
    claims = jwt.decode(token, secret, algorithms=["HS256"], audience="akb")
    assert claims["iss"] == "octop"
    assert claims["octop_instance_id"] == "octop-prod"
    assert claims["sub"] == "user:1"
    assert claims["jti"]
    # AKB rejects anything older than five minutes.
    assert 0 < claims["exp"] - claims["iat"] <= 300


def test_akb_rest_headers_carry_identity() -> None:
    spec = _spec(identity_token="assertion.jwt")
    headers = akb_rest._headers(spec, {"api_key": "kos_ak_x"})
    assert headers[IDENTITY_HEADER] == "assertion.jwt"
    assert headers["X-API-Key"] == "kos_ak_x"
    assert IDENTITY_HEADER not in akb_rest._headers(_spec(), {"api_key": "kos_ak_x"})


def test_akb_mcp_headers_carry_identity() -> None:
    spec = _spec(kind="akb-mcp", identity_token="assertion.jwt")
    assert akb_mcp._headers(spec, {"api_key": "k"})[IDENTITY_HEADER] == "assertion.jwt"


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
