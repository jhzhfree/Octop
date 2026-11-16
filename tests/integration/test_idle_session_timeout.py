"""Signed credential inactivity checks using synthetic ASGI accounts and a fixed clock."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import jwt
import pytest

from octop.api import deps
from octop.infra.auth import tokens
from tests.support.auth import bearer, bootstrap_admin, login


async def prepare(env, monkeypatch):
    client, server, home = env
    await bootstrap_admin(client, home)
    original = await login(client)
    secret = server.services.secret_repo.get("jwt")
    payload = tokens.decode_token(secret, original)
    clock = [payload["iat"]]

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(clock[0], tz=tz or UTC)

    monkeypatch.setattr(jwt.api_jwt, "datetime", FixedDateTime)
    monkeypatch.setattr(tokens, "time", SimpleNamespace(time=lambda: clock[0]))
    monkeypatch.setattr(deps, "time", SimpleNamespace(time=lambda: clock[0]))
    server.services = replace(
        server.services, config=replace(server.services.config, session_idle_timeout_seconds=120)
    )
    return client, server, secret, original, clock


async def test_polling_and_client_activity_headers_cannot_extend_idle(
    patched_app_client, monkeypatch
):
    client, _, _, token, clock = await prepare(patched_app_client, monkeypatch)
    start = clock[0]
    for elapsed in [30, 60, 119, 120]:
        clock[0] = start + elapsed
        for path in ["/api/auth/me", "/api/auth/access-state"]:
            response = await client.get(
                path, headers=bearer(token) | {"X-Octop-User-Activity": "true"}
            )
            assert response.status_code == (200 if elapsed < 120 else 401)
            assert deps.ACCESS_TOKEN_RESPONSE_HEADER not in response.headers
    response = await client.post("/api/auth/renew", headers=bearer(token))
    assert response.status_code == 401
    assert deps.ACCESS_TOKEN_RESPONSE_HEADER not in response.headers
    fresh = await login(client)
    assert (await client.get("/api/auth/me", headers=bearer(fresh))).status_code == 200


async def test_concurrent_activity_refreshes_credentials_but_not_absolute_start(
    patched_app_client, monkeypatch
):
    client, server, secret, original, clock = await prepare(patched_app_client, monkeypatch)
    start = clock[0]
    server.services = replace(
        server.services,
        config=replace(server.services.config, session_absolute_timeout_seconds=150),
    )
    clock[0] = start + 60
    responses = await asyncio.gather(
        *[client.post("/api/auth/renew", headers=bearer(original)) for _ in range(3)]
    )
    refreshed = []
    for response in responses:
        assert response.status_code == 204
        token = response.headers[deps.ACCESS_TOKEN_RESPONSE_HEADER]
        payload = tokens.decode_token(secret, token)
        assert payload["iat"] == start + 60
        assert payload["session_started_at"] == start
        assert payload["sid"] == tokens.decode_token(secret, original)["sid"]
        refreshed.append(token)
    clock[0] = start + 120
    assert (await client.get("/api/auth/me", headers=bearer(original))).status_code == 401
    clock[0] = start + 149
    for token in refreshed:
        assert (await client.get("/api/auth/me", headers=bearer(token))).status_code == 200
    clock[0] = start + 150
    for token in refreshed:
        assert (await client.post("/api/auth/renew", headers=bearer(token))).status_code == 401


@pytest.mark.parametrize("issued", [None, True, "123", -1])
async def test_untrusted_activity_claims_fail_closed(patched_app_client, monkeypatch, issued):
    client, _, secret, token, _ = await prepare(patched_app_client, monkeypatch)
    payload = tokens.decode_token(secret, token)
    payload["sub"] = str(payload["sub"])
    if issued is None:
        payload.pop("iat")
    else:
        payload["iat"] = issued
    forged = jwt.encode(payload, secret, algorithm="HS256")
    assert (await client.get("/api/auth/me", headers=bearer(forged))).status_code == 401


async def test_storage_failure_does_not_issue_activity_evidence(patched_app_client, monkeypatch):
    from octop.infra.db.repos.auth_session_revocations import AuthSessionRevocationRepo

    client, _, _, token, _ = await prepare(patched_app_client, monkeypatch)

    def unavailable(*args):
        raise OSError("synthetic failure")

    monkeypatch.setattr(AuthSessionRevocationRepo, "is_revoked", unavailable)
    response = await client.post("/api/auth/renew", headers=bearer(token))
    assert response.status_code == 503
    assert deps.ACCESS_TOKEN_RESPONSE_HEADER not in response.headers
