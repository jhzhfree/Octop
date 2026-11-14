"""Provider secrets stay server-side, including legacy stored rows."""

import json
from unittest.mock import AsyncMock, patch

import pytest


async def test_secret_boundary_and_edit_semantics(env):
    c, srv, auth = env
    secret = "synthetic-provider-secret"
    extra = json.dumps(
        {
            "headers": {"Authorization": "synthetic-header"},
            "nested": {"headers": {"X-Key": "synthetic-nested"}},
        }
    )
    response = await c.post(
        "/api/admin/providers",
        headers=auth,
        json={"name": "legacy", "kind": "openai", "api_key": secret, "extra_json": extra},
    )
    pid = response.json()["id"]
    assert response.json()["has_api_key"] is True
    assert "api_key" not in response.json()
    await c.post(
        "/api/users",
        headers=auth,
        json={"username": "regular", "password": "TestPass12", "role": "user"},
    )
    token = (
        await c.post("/api/auth/login", json={"username": "regular", "password": "TestPass12"})
    ).json()["access_token"]
    user_auth = {"Authorization": f"Bearer {token}"}
    for headers, path in [
        (auth, "/api/admin/providers"),
        (auth, "/api/providers"),
        (user_auth, "/api/providers"),
        (user_auth, "/api/providers/resolved"),
    ]:
        result = await c.get(path, headers=headers)
        assert result.status_code == 200
        assert (
            secret not in result.text
            and "synthetic-header" not in result.text
            and "synthetic-nested" not in result.text
        )
    for endpoint in ["fetch-models", "test-draft"]:
        result = await c.post(
            f"/api/admin/providers/{endpoint}",
            headers=user_auth,
            json={"provider_id": pid, "name": "legacy", "kind": "openai", "model_id": "m"},
        )
        assert result.status_code == 403
    result = await c.patch(f"/api/admin/providers/{pid}", headers=auth, json={"note": "updated"})
    assert result.json()["has_api_key"] is True
    assert srv.services.provider_repo.get(pid).api_key == secret
    assert srv.services.provider_repo.get(pid).extra_json == extra
    for value in [None, "", "  "]:
        await c.patch(f"/api/admin/providers/{pid}", headers=auth, json={"api_key": "replacement"})
        result = await c.patch(f"/api/admin/providers/{pid}", headers=auth, json={"api_key": value})
        assert result.json()["has_api_key"] is False
        assert not srv.services.provider_repo.get(pid).api_key


@pytest.mark.parametrize("endpoint", ["fetch-models", "test-draft"])
async def test_stored_credentials_reuse_same_destination_only(env, endpoint):
    c, srv, auth = env
    pid = srv.services.provider_repo.create(
        name="legacy",
        kind="openai",
        base_url="https://gateway.example/v1",
        api_key="synthetic-key",
        extra_json='{"headers":{"X-Key":"synthetic-header"}}',
    )
    body = {
        "provider_id": pid,
        "kind": "openai",
        "name": "legacy",
        "model_id": "m",
        "base_url": "https://gateway.example/v1",
    }
    target = (
        "fetch_openai_compatible_models" if endpoint == "fetch-models" else "probe_provider_row"
    )
    with patch(
        f"octop.api.routers.providers.{target}", new=AsyncMock(return_value={"ok": True})
    ) as probe:
        result = await c.post(f"/api/admin/providers/{endpoint}", headers=auth, json=body)
        assert result.status_code == 200 and result.json()["ok"]
        if endpoint == "fetch-models":
            assert probe.await_args.kwargs["api_key"] == "synthetic-key"
            assert probe.await_args.kwargs["extra_headers"] == {"X-Key": "synthetic-header"}
        else:
            assert probe.await_args.args[0].api_key == "synthetic-key"
        probe.reset_mock()
        body["base_url"] = "https://other.example/v1"
        result = await c.post(f"/api/admin/providers/{endpoint}", headers=auth, json=body)
        assert result.status_code == 403
        probe.assert_not_awaited()


async def test_legacy_oauth_failure_never_returns_stored_exception(env):
    c, srv, auth = env
    srv.services.settings_repo.set(
        "codex_oauth.pending.legacy-error",
        json.dumps({"status": "error", "error": "synthetic-legacy-secret"}),
    )
    response = await c.get("/api/admin/providers/codex-oauth/pending/legacy-error", headers=auth)
    assert response.status_code == 200
    assert response.json()["status"] == "error"
    assert "synthetic-legacy-secret" not in response.text
