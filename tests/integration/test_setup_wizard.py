"""Integration tests for the 4-step wizard backend.

Covers: verify-password, token-protected initial-admin, finish endpoint,
and the 410 behavior on completed setups.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from octop.infra.setup.password_file import WIZARD_FILE_NAME, read_password
from tests.support.app import octop_client, write_octop_config


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket

    def blocked(*args, **kwargs):
        raise AssertionError("network forbidden in setup tests")

    async def probe(*args, **kwargs):
        return {"ok": False, "error": "offline test probe"}

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr("octop.api.routers.setup.probe_provider_row", probe)


@pytest.fixture
async def env(patched_app_client):
    yield patched_app_client


# ─── verify-password ────────────────────────────────────────────────


async def test_verify_password_returns_token_on_match(env: Any) -> None:
    c, _srv, home = env
    pw = read_password(Path.home())
    assert pw is not None
    r = await c.post("/api/setup/verify-password", json={"password": pw})
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["wizard_token"], str)
    assert body["expires_in"] > 0


async def test_verify_password_rejects_mismatch(env: Any) -> None:
    c, _srv, _home = env
    r = await c.post("/api/setup/verify-password", json={"password": "wrong"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "SETUP_PASSWORD_WRONG"


async def test_verify_password_rate_limited(env: Any) -> None:
    c, _srv, _home = env
    for _ in range(5):
        await c.post("/api/setup/verify-password", json={"password": "x"})
    r = await c.post("/api/setup/verify-password", json={"password": "x"})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "SETUP_RATE_LIMITED"


# ─── initial-admin token guard ──────────────────────────────────────


async def test_initial_admin_requires_wizard_token(env: Any) -> None:
    c, _srv, _home = env
    r = await c.post(
        "/api/setup/initial-admin",
        json={"username": "admin", "password": "TestPass12"},
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "SETUP_TOKEN_INVALID"


async def test_initial_admin_succeeds_with_token(env: Any) -> None:
    c, _srv, home = env
    pw = read_password(Path.home())
    tok = (await c.post("/api/setup/verify-password", json={"password": pw})).json()["wizard_token"]
    r = await c.post(
        "/api/setup/initial-admin",
        json={"username": "admin", "password": "TestPass12"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 201
    body = r.json()
    assert isinstance(body["access_token"], str)
    # The wizard password file is kept permanently, even after setup completes.
    assert (Path.home() / WIZARD_FILE_NAME).exists()


async def test_initial_admin_rejects_weak_password(env: Any) -> None:
    c, _srv, _home = env
    pw = read_password(Path.home())
    tok = (await c.post("/api/setup/verify-password", json={"password": pw})).json()["wizard_token"]
    r = await c.post(
        "/api/setup/initial-admin",
        json={"username": "admin", "password": "pw"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "PASSWORD_TOO_WEAK"


async def test_initial_admin_respects_locale_body(env: Any) -> None:
    c, srv, _home = env
    pw = read_password(Path.home())
    tok = (await c.post("/api/setup/verify-password", json={"password": pw})).json()["wizard_token"]
    r = await c.post(
        "/api/setup/initial-admin",
        json={"username": "admin", "password": "TestPass12", "locale": "en"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 201
    assert r.json()["locale"] == "en"
    user = srv.user_manager.get("admin")
    assert user is not None
    assert user.locale == "en"


# ─── /setup/status ─────────────────────────────────────────────────


async def test_status_reports_wizard_password_exists(env: Any) -> None:
    c, _srv, home = env
    r = await c.get("/api/setup/status")
    body = r.json()
    assert body["setup_required"] is True
    assert body["wizard_password_required"] is True
    assert body["wizard_password_exists"] is True
    assert body["wizard_password_path"] == str(Path.home() / WIZARD_FILE_NAME)


async def test_begin_issues_token_when_password_not_required(tmp_octop_home: Path) -> None:
    write_octop_config(tmp_octop_home, require_setup_password=False)

    async with octop_client(tmp_octop_home) as (c, _srv):
        r = await c.get("/api/setup/status")
        body = r.json()
        assert body["wizard_password_required"] is False
        assert body["wizard_password_exists"] is False
        assert body["wizard_password_path"] is None

        r = await c.post("/api/setup/begin")
        assert r.status_code == 200
        assert r.json()["wizard_token"]


async def test_validate_token_rejects_missing_header(env: Any) -> None:
    c, _srv, _home = env
    r = await c.get("/api/setup/validate-token")
    assert r.status_code == 200
    assert r.json()["valid"] is False


async def test_validate_token_accepts_fresh_token(env: Any) -> None:
    c, _srv, home = env
    pw = read_password(Path.home())
    tok = (await c.post("/api/setup/verify-password", json={"password": pw})).json()["wizard_token"]
    r = await c.get(
        "/api/setup/validate-token",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200
    assert r.json()["valid"] is True


async def test_validate_token_rejects_stale_token(env: Any) -> None:
    c, _srv, _home = env
    r = await c.get(
        "/api/setup/validate-token",
        headers={"Authorization": "Bearer stale-token"},
    )
    assert r.status_code == 200
    assert r.json()["valid"] is False


# ─── /setup/finish ─────────────────────────────────────────────────


async def test_finish_requires_created_admin(env: Any) -> None:
    c, _srv, _home = env
    r = await c.post("/api/setup/finish", json={"provider_draft": None})
    assert r.status_code == 410


async def test_finish_before_admin_is_rejected(env: Any) -> None:
    c, _srv, home = env
    pw = read_password(Path.home())
    tok = (await c.post("/api/setup/verify-password", json={"password": pw})).json()["wizard_token"]
    r = await c.post(
        "/api/setup/finish",
        json={"provider_draft": None},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 410


async def test_finish_works_after_admin_created(env: Any) -> None:
    c, _srv, home = env
    pw = read_password(Path.home())
    tok = (await c.post("/api/setup/verify-password", json={"password": pw})).json()["wizard_token"]
    await c.post(
        "/api/setup/initial-admin",
        json={"username": "admin", "password": "TestPass12"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    r = await c.post(
        "/api/setup/finish",
        json={"provider_draft": None},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


async def test_validate_token_still_valid_after_admin_created(env: Any) -> None:
    c, _srv, home = env
    pw = read_password(Path.home())
    tok = (await c.post("/api/setup/verify-password", json={"password": pw})).json()["wizard_token"]
    await c.post(
        "/api/setup/initial-admin",
        json={"username": "admin", "password": "TestPass12"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    r = await c.get(
        "/api/setup/validate-token",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200
    assert r.json()["valid"] is True


async def test_test_provider_requires_wizard_token(env: Any) -> None:
    c, _srv, _home = env
    r = await c.post(
        "/api/setup/test-provider",
        json={
            "name": "OpenAI",
            "type": "openai",
            "api_key": "sk-test",
            "model_id": "gpt-4o-mini",
        },
    )
    assert r.status_code == 401


async def test_test_provider_returns_error_for_bad_key(env: Any) -> None:
    c, _srv, home = env
    pw = read_password(Path.home())
    tok = (await c.post("/api/setup/verify-password", json={"password": pw})).json()["wizard_token"]
    r = await c.post(
        "/api/setup/test-provider",
        json={
            "name": "OpenAI",
            "type": "openai",
            "api_key": "sk-invalid",
            "model_id": "gpt-4o-mini",
        },
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is False


async def test_resume_wizard_after_admin_created(env: Any) -> None:
    c, _srv, home = env
    pw = read_password(Path.home())
    tok = (await c.post("/api/setup/verify-password", json={"password": pw})).json()["wizard_token"]
    await c.post(
        "/api/setup/initial-admin",
        json={"username": "admin", "password": "TestPass12"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert (await c.post("/api/setup/resume-wizard")).status_code == 401
    login = await c.post("/api/auth/login", json={"username": "admin", "password": "TestPass12"})
    r = await c.post(
        "/api/setup/resume-wizard",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["wizard_token"], str)
    assert body["expires_in"] > 0


async def test_test_provider_accepts_admin_jwt_after_admin_created(env: Any) -> None:
    c, srv, home = env
    pw = read_password(Path.home())
    tok = (await c.post("/api/setup/verify-password", json={"password": pw})).json()["wizard_token"]
    admin = (
        await c.post(
            "/api/setup/initial-admin",
            json={"username": "admin", "password": "TestPass12"},
            headers={"Authorization": f"Bearer {tok}"},
        )
    ).json()
    assert "access_token" in admin
    srv.wizard_tokens.clear()
    r = await c.post(
        "/api/setup/test-provider",
        json={
            "name": "OpenAI",
            "type": "openai",
            "api_key": "sk-invalid",
            "model_id": "gpt-4o-mini",
        },
        headers={"Authorization": f"Bearer {admin['access_token']}"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is False


async def test_finish_saves_provider_with_admin_jwt(env: Any) -> None:
    c, srv, home = env
    pw = read_password(Path.home())
    tok = (await c.post("/api/setup/verify-password", json={"password": pw})).json()["wizard_token"]
    admin = (
        await c.post(
            "/api/setup/initial-admin",
            json={"username": "admin", "password": "TestPass12"},
            headers={"Authorization": f"Bearer {tok}"},
        )
    ).json()
    srv.wizard_tokens.clear()
    r = await c.post(
        "/api/setup/finish",
        json={
            "provider_draft": {
                "name": "HAI",
                "type": "openai",
                "api_key": "sk-test",
                "base_url": "https://api.example.com/v1",
                "models": [
                    {
                        "id": "MiniMax-M2.7",
                        "name": "MiniMax",
                        "enabled": True,
                        "input": ["text"],
                    }
                ],
            }
        },
        headers={"Authorization": f"Bearer {admin['access_token']}"},
    )
    assert r.status_code == 200, r.text
    providers = srv.services.provider_repo.list_all()
    assert len(providers) == 1
    assert providers[0].api_key == "sk-test"
    provider_name, model_id = srv.services.settings_repo.get_active_model()
    assert provider_name == "HAI"
    assert model_id == "MiniMax-M2.7"
    registry = srv.app_runtime.agent_registry
    assert registry._harness_manager is not None
    assert registry._harness_manager.shared_factory is not None
    main = srv.services.repos.agent_repo.get("main")
    assert main is not None
    # The default agent boots asynchronously after finish(); wait for it to settle.
    last_state = main.last_state
    for _ in range(50):
        await asyncio.sleep(0.1)
        if (
            current := srv.services.repos.agent_repo.get("main")
        ) and current.last_state == "running":
            last_state = current.last_state
            break
        if current is not None:
            last_state = current.last_state
    assert last_state == "running"


# ─── 410 guard ─────────────────────────────────────────────────────


async def test_setup_410_after_admin_exists(env: Any) -> None:
    c, _srv, home = env
    pw = read_password(Path.home())
    tok = (await c.post("/api/setup/verify-password", json={"password": pw})).json()["wizard_token"]
    await c.post(
        "/api/setup/initial-admin",
        json={"username": "admin", "password": "TestPass12"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    # Plant a stale file to verify the 410 guard does not touch it.
    (Path.home() / WIZARD_FILE_NAME).write_text("stale\n", encoding="utf-8")
    r = await c.post("/api/setup/verify-password", json={"password": "stale"})
    assert r.status_code == 410
    # The wizard password file is never deleted by the app, even on the 410 path.
    assert (Path.home() / WIZARD_FILE_NAME).exists()


# ─── lockdown middleware ───────────────────────────────────────────


async def test_lockdown_blocks_non_setup_endpoints_when_no_users(env: Any) -> None:
    c, _srv, _home = env
    r = await c.get("/api/agents")
    assert r.status_code == 503
    body = r.json()
    assert body.get("setup_required") is True


async def test_lockdown_allows_setup_endpoints(env: Any) -> None:
    c, _srv, _home = env
    r = await c.get("/api/setup/status")
    assert r.status_code == 200


async def test_lockdown_allows_health_path(env: Any) -> None:
    c, _srv, _home = env
    r = await c.get("/api/health")
    assert r.status_code == 200


async def test_lockdown_lifts_after_admin_created(env: Any) -> None:
    c, _srv, home = env
    pw = read_password(Path.home())
    tok = (await c.post("/api/setup/verify-password", json={"password": pw})).json()["wizard_token"]
    await c.post(
        "/api/setup/initial-admin",
        json={"username": "admin", "password": "TestPass12"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    r = await c.get("/api/agents")
    # No JWT ⇒ 401 (auth, not lockdown).
    assert r.status_code == 401


async def test_finish_rejects_invalid_token_after_admin_exists(env: Any) -> None:
    c, _srv, home = env
    pw = read_password(Path.home())
    tok = (await c.post("/api/setup/verify-password", json={"password": pw})).json()["wizard_token"]
    await c.post(
        "/api/setup/initial-admin",
        json={"username": "admin", "password": "TestPass12"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    r = await c.post(
        "/api/setup/finish",
        json={"provider_draft": None},
        headers={"Authorization": "Bearer faketoken"},
    )
    assert r.status_code == 401


async def _create_admin(c):
    token = (
        await c.post("/api/setup/verify-password", json={"password": read_password(Path.home())})
    ).json()["wizard_token"]
    created = await c.post(
        "/api/setup/initial-admin",
        json={"username": "admin", "password": "TestPass12"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201
    return token, {"Authorization": f"Bearer {created.json()['access_token']}"}


async def test_completed_setup_rejects_every_wizard_capability_after_restart(tmp_octop_home):
    async with octop_client(tmp_octop_home) as (c, srv):
        token, admin = await _create_admin(c)
        resumed = await c.post("/api/setup/resume-wizard", headers=admin)
        extra = resumed.json()["wizard_token"]
        assert (await c.post("/api/setup/finish", json={}, headers=admin)).status_code == 200
        assert not srv.wizard_tokens.validate(token)
        assert not srv.wizard_tokens.validate(extra)
    async with octop_client(tmp_octop_home) as (c, srv):
        for headers in ({}, admin, {"Authorization": f"Bearer {extra}"}):
            assert (await c.post("/api/setup/resume-wizard", headers=headers)).status_code == 410
            assert (await c.post("/api/setup/finish", json={}, headers=headers)).status_code == 410
            assert (
                await c.post(
                    "/api/setup/test-provider",
                    json={
                        "name": "offline",
                        "type": "openai",
                        "api_key": "test",
                        "model_id": "test",
                    },
                    headers=headers,
                )
            ).status_code == 410
            assert (await c.get("/api/setup/validate-token", headers=headers)).status_code == 410
        assert srv.services.provider_repo.list_all() == []
        # Even disabling every account must not reopen the initial database gate.
        with srv.services.db.transaction() as conn:
            conn.execute("UPDATE users SET disabled=1")
        assert (await c.post("/api/setup/begin")).status_code == 410
        assert (await c.post("/api/setup/database", json={"driver": "sqlite"})).status_code == 410


async def test_unfinished_setup_restart_requires_initial_admin(tmp_octop_home):
    async with octop_client(tmp_octop_home) as (c, srv):
        _, admin = await _create_admin(c)
    async with octop_client(tmp_octop_home) as (c, srv):
        assert (await c.post("/api/setup/resume-wizard")).status_code == 401
        resumed = await c.post("/api/setup/resume-wizard", headers=admin)
        assert resumed.status_code == 200
        assert (await c.post("/api/setup/finish", json={}, headers=admin)).status_code == 200


async def test_other_pre_admin_tokens_revoked(env):
    c, srv, _ = env
    extra = (
        await c.post("/api/setup/verify-password", json={"password": read_password(Path.home())})
    ).json()["wizard_token"]
    token, _ = await _create_admin(c)
    assert srv.wizard_tokens.validate(token)
    assert not srv.wizard_tokens.validate(extra)
    assert (
        await c.post("/api/setup/finish", json={}, headers={"Authorization": f"Bearer {extra}"})
    ).status_code == 401


async def test_concurrent_finish_only_applies_once(env, monkeypatch):
    c, srv, _ = env
    _, admin = await _create_admin(c)
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def bootstrap(*args, **kwargs):
        calls.append(1)
        started.set()
        await release.wait()

    monkeypatch.setattr("octop.api.routers.setup._bootstrap_default_agent", bootstrap)
    first = asyncio.create_task(c.post("/api/setup/finish", json={}, headers=admin))
    await started.wait()
    assert (await c.post("/api/setup/finish", json={}, headers=admin)).status_code == 410
    assert (await c.post("/api/setup/resume-wizard", headers=admin)).status_code == 410
    release.set()
    assert (await first).status_code == 200
    assert calls == [1]


async def test_invalid_draft_retry_and_failed_side_effect_stays_closed(env, monkeypatch):
    c, srv, _ = env
    _, admin = await _create_admin(c)
    invalid = {"provider_draft": {"name": "x", "type": "openai", "models": []}}
    assert (await c.post("/api/setup/finish", json=invalid, headers=admin)).status_code == 400
    assert (await c.post("/api/setup/resume-wizard", headers=admin)).status_code == 200

    async def fail(*args, **kwargs):
        raise RuntimeError("interrupted side effect")

    monkeypatch.setattr("octop.api.routers.setup._apply_provider_draft", fail)
    valid = {
        "provider_draft": {
            "name": "x",
            "type": "openai",
            "api_key": "fixture",
            "base_url": "https://unused.invalid",
            "models": [{"id": "x", "name": "x"}],
        }
    }
    assert (await c.post("/api/setup/finish", json=valid, headers=admin)).status_code == 500
    assert (await c.post("/api/setup/finish", json=valid, headers=admin)).status_code == 410
    assert (await c.post("/api/setup/resume-wizard", headers=admin)).status_code == 410


@pytest.mark.parametrize("existing_user", [False, True])
def test_migration_37_closes_existing_installs(tmp_path, monkeypatch, existing_user):
    from octop.infra.db import migrate
    from octop.infra.db.pool import SqlitePool
    from octop.infra.db.repos.setup_lifecycle import SetupLifecycleRepo

    db = SqlitePool(tmp_path / "setup-upgrade.db")
    discover = migrate._discover
    monkeypatch.setattr(
        migrate,
        "_discover",
        lambda dialect="sqlite": [(v, p) for v, p in discover(dialect) if v <= 36],
    )
    migrate.run_migrations(db)
    if existing_user:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO users(username,password_hash,role,disabled,created_at) VALUES('old','unused','admin',1,1)"
            )
    monkeypatch.setattr(migrate, "_discover", discover)
    migrate.run_migrations(db)
    assert SetupLifecycleRepo(db).state() == (
        ("completed", None) if existing_user else ("pending", None)
    )
    assert {p.read_text() for v, p in discover("sqlite") if v == 37} == {
        p.read_text() for v, p in discover("postgresql") if v == 37
    }
    db.close()


async def test_disabled_initial_admin_cannot_resume_even_with_live_token(env):
    c, srv, _ = env
    token, admin = await _create_admin(c)
    with srv.services.db.transaction() as conn:
        conn.execute("UPDATE users SET disabled=1")
    for headers in (admin, {"Authorization": f"Bearer {token}"}):
        assert (await c.post("/api/setup/finish", json={}, headers=headers)).status_code == 401
        assert (await c.post("/api/setup/resume-wizard", headers=headers)).status_code == 401


async def test_concurrent_initial_admin_claim_creates_only_one(env):
    c, srv, _ = env
    token = (
        await c.post("/api/setup/verify-password", json={"password": read_password(Path.home())})
    ).json()["wizard_token"]
    responses = await asyncio.gather(
        *(
            c.post(
                "/api/setup/initial-admin",
                json={"username": name, "password": "TestPass12"},
                headers={"Authorization": f"Bearer {token}"},
            )
            for name in ("first", "second")
        )
    )
    assert sorted(r.status_code for r in responses) == [201, 410]
    assert srv.user_manager.count() == 1


async def test_initial_admin_insert_failure_rolls_back_setup_claim(env, monkeypatch):
    from octop.infra.db.repos import users
    from octop.infra.setup.lifecycle import lifecycle

    c, srv, _ = env
    original = users.insert_returning_id

    def fail(*args, **kwargs):
        raise RuntimeError("simulated insert failure")

    monkeypatch.setattr(users, "insert_returning_id", fail)
    with pytest.raises(RuntimeError):
        srv.services.user_repo.create(
            username="failed", password_hash="unused", role="admin", initial_setup=True
        )
    assert lifecycle(srv).state() == ("pending", None)
    assert srv.user_manager.count() == 0
    monkeypatch.setattr(users, "insert_returning_id", original)
    await _create_admin(c)
    assert lifecycle(srv).state()[0] == "active"


async def test_bootstrap_failure_does_not_report_completed_and_restart_stays_closed(
    tmp_octop_home, monkeypatch
):
    from octop.infra.setup.lifecycle import lifecycle

    async def fail(*args, **kwargs):
        raise RuntimeError("simulated bootstrap interruption")

    monkeypatch.setattr("octop.api.routers.setup._bootstrap_default_agent", fail)
    async with octop_client(tmp_octop_home) as (c, srv):
        _, admin = await _create_admin(c)
        assert (await c.post("/api/setup/finish", json={}, headers=admin)).status_code == 500
        assert lifecycle(srv).state()[0] == "finishing"
    async with octop_client(tmp_octop_home) as (c, srv):
        assert lifecycle(srv).state()[0] == "finishing"
        for headers in ({}, admin):
            assert (await c.post("/api/setup/resume-wizard", headers=headers)).status_code == 410
            assert (await c.post("/api/setup/finish", json={}, headers=headers)).status_code == 410


async def test_other_worker_pending_token_cannot_control_active_setup(env):
    from octop.infra.setup.wizard_tokens import WizardTokenStore

    c, srv, _ = env
    other_worker = WizardTokenStore(ttl_seconds=300)
    old, _ = other_worker.issue()
    _, admin = await _create_admin(c)
    # A second worker retains its own in-memory token store across the DB transition.
    srv.wizard_tokens = other_worker
    stale_headers = {"Authorization": f"Bearer {old}"}
    assert (await c.get("/api/setup/validate-token", headers=stale_headers)).json() == {
        "valid": False
    }
    assert (await c.post("/api/setup/finish", json={}, headers=stale_headers)).status_code == 401
    fresh = (await c.post("/api/setup/resume-wizard", headers=admin)).json()["wizard_token"]
    headers = {"Authorization": f"Bearer {fresh}"}
    assert (await c.get("/api/setup/validate-token", headers=headers)).json() == {"valid": True}
    assert (await c.post("/api/setup/finish", json={}, headers=headers)).status_code == 200
