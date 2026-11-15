"""Synthetic ASGI clients only; no external providers or real credentials."""

import pytest

from octop.api.deps import ACCESS_TOKEN_RESPONSE_HEADER, decode_token, sign_token
from octop.infra.auth import sessions
from tests.support.auth import bearer, bootstrap_admin, login


async def short_session(env):
    client, server, home = env
    await bootstrap_admin(client, home)
    original = await login(client)
    secret = server.services.secret_repo.get("jwt")
    user = decode_token(secret, original)
    token = sign_token(secret, sub=user["sub"], uname="admin", role="admin", ttl_seconds=60)
    return client, server, secret, token


async def test_all_ordinary_requests_never_renew_even_with_activity_claim(patched_app_client):
    client, _, _, token = await short_session(patched_app_client)
    for method, path in [
        ("GET", "/api/auth/access-state"),
        ("GET", "/api/auth/me"),
        ("GET", "/api/agents"),
        ("GET", "/api/not-a-route"),
        ("POST", "/api/auth/change-password"),
        ("HEAD", "/api/auth/me"),
        ("GET", "/api/auth/renew"),
    ]:
        response = await client.request(
            method, path, headers=bearer(token) | {"X-Octop-User-Activity": "true"}
        )
        assert ACCESS_TOKEN_RESPONSE_HEADER not in response.headers


async def test_explicit_renew_no_store_same_family_and_fresh_noop(patched_app_client):
    client, server, secret, token = await short_session(patched_app_client)
    response = await client.post("/api/auth/renew", headers=bearer(token))
    assert response.status_code == 204
    assert response.headers["Cache-Control"] == "no-store"
    renewed = response.headers[ACCESS_TOKEN_RESPONSE_HEADER]
    assert decode_token(secret, renewed)["sid"] == decode_token(secret, token)["sid"]
    again = await client.post("/api/auth/renew", headers=bearer(renewed))
    assert again.status_code == 204
    assert ACCESS_TOKEN_RESPONSE_HEADER not in again.headers
    from octop.api.app import build_app

    spec = build_app(server).openapi()
    assert "Explicitly renew" in spec["paths"]["/api/auth/renew"]["post"]["summary"]


@pytest.mark.parametrize("kind", ["expired", "revoked", "disabled", "deleted", "query_only"])
async def test_renew_fails_closed(patched_app_client, kind):
    client, server, secret, token = await short_session(patched_app_client)
    payload = decode_token(secret, token)
    if kind == "expired":
        token = sign_token(secret, sub=payload["sub"], uname="admin", role="admin", ttl_seconds=-1)
    elif kind == "revoked":
        sessions.revoke(server.services.db, payload, token)
    elif kind in ("disabled", "deleted"):
        with server.services.db.transaction() as conn:
            if kind == "disabled":
                conn.execute("UPDATE users SET disabled=1 WHERE id=?", (payload["sub"],))
            else:
                conn.execute(
                    "INSERT INTO users(username,password_hash,role,created_at) VALUES('remaining-admin','synthetic','admin',1)"
                )
                conn.execute("DELETE FROM users WHERE id=?", (payload["sub"],))
    response = await client.post(
        "/api/auth/renew",
        headers={} if kind == "query_only" else bearer(token),
        params={"access_token": token} if kind == "query_only" else {},
    )
    assert response.status_code in (401, 403)
    assert ACCESS_TOKEN_RESPONSE_HEADER not in response.headers


async def test_renew_uses_persisted_role_not_cached_admin(patched_app_client):
    client, server, secret, token = await short_session(patched_app_client)
    with server.services.db.transaction() as conn:
        conn.execute(
            "UPDATE users SET role='user' WHERE id=?", (decode_token(secret, token)["sub"],)
        )
    response = await client.post("/api/auth/renew", headers=bearer(token))
    assert response.status_code == 204
    assert decode_token(secret, response.headers[ACCESS_TOKEN_RESPONSE_HEADER])["role"] == "user"


async def test_logout_racing_token_issuance_still_revokes_returned_token(
    patched_app_client, monkeypatch
):
    from octop.api import deps

    client, server, secret, token = await short_session(patched_app_client)
    original = deps.sign_token

    def racing_sign(*args, **kwargs):
        renewed = original(*args, **kwargs)
        sessions.revoke(server.services.db, decode_token(secret, token), token)
        return renewed

    monkeypatch.setattr(deps, "sign_token", racing_sign)
    response = await client.post("/api/auth/renew", headers=bearer(token))
    renewed = response.headers[ACCESS_TOKEN_RESPONSE_HEADER]
    for value in (token, renewed):
        rejected = await client.post("/api/auth/renew", headers=bearer(value))
        assert rejected.status_code == 401
        assert ACCESS_TOKEN_RESPONSE_HEADER not in rejected.headers


async def test_short_ttl_cadence_is_bounded(patched_app_client):
    client, server, _, token = await short_session(patched_app_client)
    from dataclasses import replace

    server.services = replace(
        server.services, config=replace(server.services.config, access_token_ttl_seconds=60)
    )
    response = await client.post("/api/auth/renew", headers=bearer(token))
    assert response.status_code == 204
    assert response.headers["X-Octop-Renew-Check-After"] == "10"
    assert ACCESS_TOKEN_RESPONSE_HEADER not in response.headers
