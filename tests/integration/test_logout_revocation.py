"""Isolated ASGI session-family logout regression tests (no external services)."""

import time

import jwt
import pytest

from octop.api.deps import ACCESS_TOKEN_RESPONSE_HEADER, decode_token, sign_token
from octop.infra.auth import sessions
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.auth_session_revocations import AuthSessionRevocationRepo
from octop.infra.errors import OctopError
from tests.support.auth import bearer, bootstrap_admin, login


@pytest.mark.parametrize("legacy", [False, True])
async def test_logout_revokes_entire_sliding_family_only(patched_app_client, legacy):
    c, srv, home = patched_app_client
    await bootstrap_admin(c, home)
    independent = await login(c)
    secret = srv.services.secret_repo.get("jwt")
    payload = decode_token(secret, independent)
    if legacy:
        payload.pop("sid")
        payload["exp"] = int(time.time()) + 60
        short = jwt.encode(payload | {"sub": str(payload["sub"])}, secret, algorithm="HS256")
    else:
        short = sign_token(secret, sub=payload["sub"], uname="admin", role="admin", ttl_seconds=60)
    r = await c.get("/api/auth/me", headers=bearer(short))
    assert r.status_code == 200
    renewed = r.headers[ACCESS_TOKEN_RESPONSE_HEADER]
    assert decode_token(secret, renewed)["sid"] == sessions.session_id(
        decode_token(secret, short), short
    )
    r = await c.post("/api/auth/logout", headers=bearer(short))
    assert r.status_code == 204
    assert ACCESS_TOKEN_RESPONSE_HEADER not in r.headers
    # A worker that had already checked the old token may sign after logout.
    raced = sign_token(
        secret,
        sub=payload["sub"],
        uname="admin",
        role="admin",
        session_id=decode_token(secret, renewed)["sid"],
    )
    for token in (short, renewed, raced):
        r = await c.get("/api/auth/me", headers=bearer(token))
        assert r.status_code == 401
        assert ACCESS_TOKEN_RESPONSE_HEADER not in r.headers
    assert (await c.get("/api/auth/me", headers=bearer(independent))).status_code == 200
    fresh_login = await login(c)
    assert fresh_login != independent
    assert (await c.get("/api/auth/me", headers=bearer(fresh_login))).status_code == 200


@pytest.mark.parametrize("operation", ["read", "write"])
async def test_revocation_database_failure_is_closed(patched_app_client, monkeypatch, operation):
    c, _srv, home = patched_app_client
    await bootstrap_admin(c, home)
    token = await login(c)

    def broken(*args):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(
        AuthSessionRevocationRepo, "is_revoked" if operation == "read" else "revoke", broken
    )
    r = await c.post("/api/auth/logout", headers=bearer(token))
    assert r.status_code == 503
    assert token not in r.text
    assert ACCESS_TOKEN_RESPONSE_HEADER not in r.headers
    if operation == "read":
        assert (await c.get("/api/auth/me", headers=bearer(token))).status_code == 503


def test_revocation_shared_connections_and_restart(tmp_path):
    path = tmp_path / "sessions.db"
    first = SqlitePool(path)
    second = SqlitePool(path)
    with first.connect() as conn:
        conn.execute(
            "CREATE TABLE auth_session_revocations (session_digest TEXT PRIMARY KEY, revoked_at BIGINT NOT NULL)"
        )
    payload = {"sub": 1, "sid": "one"}
    sessions.require_active(second, payload, "test-token")
    sessions.revoke(first, payload, "test-token")
    sessions.revoke(second, payload, "test-token")  # idempotent tombstone
    first.close()
    second.close()
    restarted = SqlitePool(path)
    try:
        with pytest.raises(OctopError, match="session revoked"):
            sessions.require_active(restarted, payload, "test-token")
        sessions.require_active(restarted, {"sub": 2, "sid": "one"}, "test-token")
        sessions.require_active(restarted, {"sub": 1, "sid": "two"}, "test-token")
        with restarted.connect() as conn:
            row = conn.execute("SELECT * FROM auth_session_revocations").fetchone()
            assert len(row["session_digest"]) == 64
            assert "test-token" not in str(tuple(row))
    finally:
        restarted.close()


@pytest.mark.parametrize("sid", [None, "", 12, [], "x" * 129])
def test_malformed_session_id_rejected(sid):
    with pytest.raises(OctopError, match="invalid session"):
        sessions.session_id({"sid": sid}, "test-token")


def test_upgrade_37_to_38_preserves_data(tmp_path, monkeypatch):
    from octop.infra.db import migrate

    db = SqlitePool(tmp_path / "upgrade.db")
    discover = migrate._discover
    try:
        monkeypatch.setattr(
            migrate,
            "_discover",
            lambda dialect="sqlite": [(v, p) for v, p in discover(dialect) if v <= 37],
        )
        migrate.run_migrations(db)
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO users(username,password_hash,role,disabled,created_at) VALUES('old','unused','admin',0,1)"
            )
        monkeypatch.setattr(migrate, "_discover", discover)
        migrate.run_migrations(db)
        migrate.run_migrations(db)
        sessions.revoke(db, {"sub": 1, "sid": "upgraded"}, "opaque-token")
        with db.connect() as conn:
            assert conn.execute("SELECT username FROM users").fetchone()[0] == "old"
        assert {p.read_text() for v, p in discover("sqlite") if v == 38} == {
            p.read_text() for v, p in discover("postgresql") if v == 38
        }
    finally:
        db.close()


async def test_new_websocket_handshake_rejects_logged_out_family(patched_app_client):
    from starlette.websockets import WebSocketDisconnect

    from tests.support.http import ws_connect

    c, _srv, home = patched_app_client
    await bootstrap_admin(c, home)
    token = await login(c)
    path = f"/api/notifications/ws?token={token}"
    async with ws_connect(c._octop_app, path) as ws:
        await ws.send_json({"type": "ping"})
        assert await ws.receive_json() == {"type": "pong"}
    assert (await c.post("/api/auth/logout", headers=bearer(token))).status_code == 204
    with pytest.raises(WebSocketDisconnect):
        async with ws_connect(c._octop_app, path):
            pytest.fail("revoked session accepted")


async def test_logout_during_renewal_cannot_revive_session(patched_app_client, monkeypatch):
    from octop.api import deps

    c, srv, home = patched_app_client
    await bootstrap_admin(c, home)
    token = await login(c)
    secret = srv.services.secret_repo.get("jwt")
    payload = decode_token(secret, token)
    short = sign_token(secret, sub=payload["sub"], uname="admin", role="admin", ttl_seconds=60)
    user = srv.user_manager.get("admin")

    def logout_then_sign(*args, **kwargs):
        # Deterministic interleaving: another worker commits logout after the
        # renewal's DB check but before signing. No timing-dependent sleeps.
        sessions.revoke(srv.services.db, decode_token(secret, short), short)
        return sign_token(*args, **kwargs)

    monkeypatch.setattr(deps, "sign_token", logout_then_sign)
    raced = deps.maybe_sliding_renew_token(srv, short, user)
    assert raced is not None
    assert (await c.get("/api/auth/me", headers=bearer(raced))).status_code == 401
