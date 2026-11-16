"""Absolute lifetime with synthetic accounts and in-process ASGI, no real providers."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import jwt
import pytest

from octop.api.deps import ACCESS_TOKEN_RESPONSE_HEADER, decode_token, sign_token
from octop.infra.auth import tokens
from tests.support.auth import bearer, bootstrap_admin, login


async def prepare(env):
    client, server, home = env
    await bootstrap_admin(client, home)
    original = await login(client)
    secret = server.services.secret_repo.get("jwt")
    user = decode_token(secret, original)
    server.services = replace(
        server.services,
        config=replace(server.services.config, session_absolute_timeout_seconds=120),
    )
    token = sign_token(secret, sub=user["sub"], uname="admin", role="admin", ttl_seconds=60)
    return client, server, secret, token


async def test_concurrent_renewals_preserve_start_and_expire_together(
    patched_app_client, monkeypatch
):
    client, _, secret, original = await prepare(patched_app_client)
    start = decode_token(secret, original)["session_started_at"]
    responses = await asyncio.gather(
        *[client.post("/api/auth/renew", headers=bearer(original)) for _ in range(3)]
    )
    renewed = []
    for response in responses:
        assert response.status_code == 204
        token = response.headers[ACCESS_TOKEN_RESPONSE_HEADER]
        assert decode_token(secret, token)["session_started_at"] == start
        renewed.append(token)
    monkeypatch.setattr(tokens, "time", SimpleNamespace(time=lambda: start + 119))
    assert (await client.get("/api/auth/me", headers=bearer(renewed[0]))).status_code == 200
    monkeypatch.setattr(tokens, "time", SimpleNamespace(time=lambda: start + 120))
    for token in [original, *renewed]:
        for method, path in [
            ("GET", "/api/auth/me"),
            ("GET", "/api/auth/access-state"),
            ("POST", "/api/auth/renew"),
        ]:
            response = await client.request(method, path, headers=bearer(token))
            assert response.status_code == 401
            assert ACCESS_TOKEN_RESPONSE_HEADER not in response.headers


@pytest.mark.parametrize("start", [None, True, "123", -1, "future"])
async def test_legacy_and_malformed_starts_fail_closed(patched_app_client, start):
    client, _, secret, token = await prepare(patched_app_client)
    payload = decode_token(secret, token)
    payload["sub"] = str(payload["sub"])
    if start is None:
        payload.pop("session_started_at")
    else:
        payload["session_started_at"] = payload["iat"] + 1 if start == "future" else start
    token = jwt.encode(payload, secret, algorithm="HS256")
    response = await client.post("/api/auth/renew", headers=bearer(token))
    assert response.status_code == 401
    assert ACCESS_TOKEN_RESPONSE_HEADER not in response.headers


async def test_disabled_policy_preserves_legacy_without_inventing_login_time(patched_app_client):
    client, server, secret, token = await prepare(patched_app_client)
    server.services = replace(
        server.services, config=replace(server.services.config, session_absolute_timeout_seconds=0)
    )
    payload = decode_token(secret, token)
    payload.pop("session_started_at")
    payload["sub"] = str(payload["sub"])
    legacy = jwt.encode(payload, secret, algorithm="HS256")
    response = await client.post("/api/auth/renew", headers=bearer(legacy))
    assert response.status_code == 204
    renewed = response.headers[ACCESS_TOKEN_RESPONSE_HEADER]
    assert "session_started_at" not in decode_token(secret, renewed)
    server.services = replace(
        server.services,
        config=replace(server.services.config, session_absolute_timeout_seconds=120),
    )
    assert (await client.get("/api/auth/me", headers=bearer(renewed))).status_code == 401
    fresh = await login(client)
    assert (await client.get("/api/auth/me", headers=bearer(fresh))).status_code == 200


async def test_revocation_storage_failure_still_fails_closed(patched_app_client, monkeypatch):
    from octop.infra.db.repos.auth_session_revocations import AuthSessionRevocationRepo

    client, _, _, token = await prepare(patched_app_client)

    def unavailable(*args):
        raise OSError("synthetic storage failure")

    monkeypatch.setattr(AuthSessionRevocationRepo, "is_revoked", unavailable)
    for method, path in [("GET", "/api/auth/me"), ("POST", "/api/auth/renew")]:
        response = await client.request(method, path, headers=bearer(token))
        assert response.status_code == 503
        assert ACCESS_TOKEN_RESPONSE_HEADER not in response.headers
