"""JWT-backed HTTP contract tests. Domain transactions have separate real-DB tests."""

from __future__ import annotations

import socket

import pytest

from octop.api.routers import mattermost_publication as api
from octop.infra.scenes.service import SceneError

PATH = "/api/organizations/org/scene-runs/run/mattermost-publication"
PREVIEW = {
    "expected_report_sha256": "a" * 64,
    "expected_worker_version": 1,
    "expected_intent_revision": 2,
    "expected_configuration_revision": 3,
}
DECISION = {"expected_publication_sha256": "b" * 64}


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("publication HTTP tests must not access the network")

    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)


@pytest.fixture
def domain_stub(monkeypatch):
    calls = []
    record = {
        "organization_id": "org",
        "run_id": "run",
        "worker_id": "worker",
        "worker_version": 1,
        "intent_revision": 2,
        "configuration_revision": 3,
        "instance_key": "work",
        "team_id": "team",
        "channel_id": "channel",
        "bot_id": "bot",
        "report_sha256": "a" * 64,
        "publication_sha256": "b" * 64,
        "content": "Immutable report",
        "proposed_by": 1,
        "approved_by": None,
        "status": "pending_approval",
        "created_at": 1,
        "updated_at": 1,
        "connected": False,
        "dispatch_enabled": False,
        "execution_authorized": False,
        # Response schema must not leak internal details even if service adds them.
        "credential_ref": "SECRET_REF",
        "origin": "SECRET_ORIGIN",
    }

    class Service:
        def __init__(self, repos, actor_id):
            calls.append(("actor", actor_id))

        def candidate(self, org, run):
            calls.append(("candidate", org, run))
            return PREVIEW | {"credential_ref": "SECRET_REF", "origin": "SECRET_ORIGIN"}

        def get(self, org, run):
            calls.append(("get", org, run))
            return record

        def preview(self, org, run, **kwargs):
            calls.append(("preview", org, run, kwargs))
            return record

        def approve(self, org, run, **kwargs):
            calls.append(("approve", org, run, kwargs))
            return record | {"status": "blocked_not_connected", "approved_by": 2}

        def revoke(self, org, run, **kwargs):
            calls.append(("revoke", org, run, kwargs))
            return record | {"status": "revoked"}

    monkeypatch.setattr(api, "MattermostPublicationService", Service)
    return calls


async def test_authenticated_contract_uses_jwt_identity_and_filters_response(env, domain_stub):
    client, server, auth = env
    me = (await client.get("/api/auth/me", headers=auth)).json()
    for method, suffix, body, operation in (
        ("GET", "", None, "get"),
        ("POST", "", PREVIEW, "preview"),
        ("POST", "/approve", DECISION, "approve"),
        ("POST", "/revoke", DECISION, "revoke"),
    ):
        response = await client.request(method, PATH + suffix, headers=auth, json=body)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert "SECRET" not in response.text
        assert "credential_ref" not in response.json()
        assert response.json()["dispatch_enabled"] is False
        assert domain_stub[-2] == ("actor", me["id"])
        assert domain_stub[-1][0:3] == (operation, "org", "run")
        if body:
            assert domain_stub[-1][3] == body


async def test_no_jwt_cannot_reach_domain(env, domain_stub):
    client, _, _ = env
    for suffix, body in (("", PREVIEW), ("/approve", DECISION), ("/revoke", DECISION)):
        response = await client.post(PATH + suffix, json=body)
        assert response.status_code == 401
    assert (await client.get(PATH)).status_code == 401
    assert not domain_stub


@pytest.mark.parametrize(
    "suffix,body", [("", PREVIEW), ("/approve", DECISION), ("/revoke", DECISION)]
)
async def test_extra_identity_destination_content_or_secret_is_rejected_without_echo(
    env,
    domain_stub,
    suffix,
    body,
):
    client, _, auth = env
    for key in ("actor_id", "approved_by", "channel_id", "content", "SECRET_AS_FIELD_NAME"):
        response = await client.post(PATH + suffix, headers=auth, json=body | {key: "SECRET_VALUE"})
        assert response.status_code == 422
        assert "SECRET" not in response.text
        assert key not in response.text
    assert not domain_stub


async def test_malformed_input_never_echoed_and_no_dispatch_routes(env, domain_stub):
    client, _, auth = env
    for payload in ('{"SECRET_JSON":', '{"expected_report_sha256":"SECRET_HASH"}'):
        response = await client.post(PATH, headers=auth, content=payload)
        assert response.status_code == 422
        assert "SECRET" not in response.text
    for suffix in ("/send", "/dispatch", "/retry"):
        response = await client.post(PATH + suffix, headers=auth, json={})
        assert response.status_code in (404, 405)
    assert not domain_stub


@pytest.mark.parametrize("status", [403, 404, 409])
async def test_domain_denials_preserve_status_and_safe_code(env, monkeypatch, status):
    client, _, auth = env

    class Denied:
        def __init__(self, repos, actor_id):
            pass

        def approve(self, *args, **kwargs):
            raise SceneError("PUBLICATION_DENIED", "publication denied", status)

    monkeypatch.setattr(api, "MattermostPublicationService", Denied)
    response = await client.post(PATH + "/approve", headers=auth, json=DECISION)
    assert response.status_code == status
    assert response.json()["detail"]["code"] == "PUBLICATION_DENIED"
    assert response.headers["cache-control"] == "no-store"


async def test_openapi_describes_local_only_typed_operations(env):
    client, _, _ = env
    schema = client._octop_app.openapi()
    path = PATH.replace("/org/", "/{organization_id}/").replace("/run/", "/{run_id}/")
    for suffix in ("", "/approve", "/revoke"):
        operation = schema["paths"][path + suffix]["post"]
        assert operation["summary"]
        assert operation["responses"]["200"]["content"]["application/json"]["schema"][
            "$ref"
        ].endswith("/PublicationRecord")


async def test_candidate_jwt_identity_and_exact_whitelist(env, domain_stub):
    client, _, auth = env
    assert (await client.get(PATH + "/candidate")).status_code == 401
    assert not domain_stub
    me = (await client.get("/api/auth/me", headers=auth)).json()
    response = await client.get(PATH + "/candidate", headers=auth)
    assert response.status_code == 200
    assert response.json() == PREVIEW
    assert response.headers["cache-control"] == "no-store"
    assert domain_stub == [("actor", me["id"]), ("candidate", "org", "run")]
