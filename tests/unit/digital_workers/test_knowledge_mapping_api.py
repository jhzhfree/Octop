from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from tests.unit.digital_workers.test_knowledge_mappings import setup as setup  # noqa: F401

from octop.api.deps import current_user, get_server
from octop.api.routers.worker_knowledge_mappings import router
from octop.infra.users.identity import User

URL = "/api/organizations/o/digital-workers/w/knowledge-mappings"
BODY = {
    "mcp_server": "akb",
    "knowledge_base_ref": "kb",
    "external_source_id": "e",
    "expected_capability_version": 1,
    "expected_connection_revision": 1,
    "expected_resource_revision": 1,
}


def app_for(repos, actor):
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[current_user] = lambda: actor
    app.dependency_overrides[get_server] = lambda: SimpleNamespace(
        services=SimpleNamespace(repos=repos)
    )
    return app


async def test_http_lifecycle_and_openapi(setup):
    db, repos, service = setup
    app = app_for(repos, service.actor)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        made = await c.post(URL, json=BODY)
        assert made.status_code == 201, made.text
        row = made.json()
        assert row["status"] == "draft" and row["retrieval_enabled"] is False
        assert "id" not in row
        mid = row["mapping_id"]
        assert (await c.get(f"{URL}/{mid}")).json()["local_snapshot_current"]
        assert (await c.post(URL, json=BODY)).status_code == 409
        assert (
            await c.post(f"{URL}/{mid}/revoke", json={"expected_revision": 9})
        ).status_code == 409
        revoked = await c.post(f"{URL}/{mid}/revoke", json={"expected_revision": 1})
        assert revoked.status_code == 200 and revoked.json()["revision"] == 2
        assert not revoked.json()["local_snapshot_current"]
        assert (
            await c.post(f"{URL}/{mid}/revoke", json={"expected_revision": 1})
        ).status_code == 409
        remade = (await c.post(URL, json=BODY)).json()
        assert remade["mapping_id"] != mid
        first = (await c.get(URL, params={"limit": 1})).json()
        second = (await c.get(URL, params={"limit": 1, "offset": 1})).json()
        assert [r["mapping_id"] for r in first["items"]] == [remade["mapping_id"]]
        assert [r["mapping_id"] for r in second["items"]] == [mid]
        assert (await c.get(URL, params={"offset": 2})).json()["items"] == []
    schema = app.openapi()
    for operations in schema["paths"].values():
        for operation in operations.values():
            assert operation["summary"] and operation["responses"]
    assert "MappingResponse" in schema["components"]["schemas"]


@pytest.mark.parametrize(
    "extra",
    [
        {"enabled": True},
        {"retrieval_enabled": True},
        {"credentials": "secret"},
        {"expected_capability_version": True},
        {"mcp_server": "   "},
    ],
)
async def test_rejects_privilege_fields_and_invalid_bodies(setup, extra):
    _, repos, service = setup
    async with AsyncClient(
        transport=ASGITransport(app=app_for(repos, service.actor)), base_url="http://test"
    ) as c:
        assert (await c.post(URL, json={**BODY, **extra})).status_code == 422
        assert (await c.get(URL)).json()["items"] == []


async def test_member_cannot_observe_or_mutate_existence(setup):
    _, repos, service = setup
    app = app_for(repos, service.actor)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        mid = (await c.post(URL, json=BODY)).json()["mapping_id"]
        app.dependency_overrides[current_user] = lambda: User(2, "other", "user", None)
        for suffix in (mid, "missing"):
            assert (await c.get(f"{URL}/{suffix}")).status_code == 403
            assert (
                await c.post(f"{URL}/{suffix}/revoke", json={"expected_revision": 1})
            ).status_code == 403
        assert (await c.get(URL)).status_code == 403
        assert (await c.post(URL, json=BODY)).status_code == 403


async def test_cross_org_and_stale_bindings(setup):
    db, repos, service = setup
    async with AsyncClient(
        transport=ASGITransport(app=app_for(repos, service.actor)), base_url="http://test"
    ) as c:
        row = (await c.post(URL, json=BODY)).json()
        other = URL.replace("/o/", "/other/")
        assert (await c.get(other)).status_code == 404
        assert (await c.get(f"{other}/{row['mapping_id']}")).status_code == 404
        assert (
            await c.post(f"{other}/{row['mapping_id']}/revoke", json={"expected_revision": 1})
        ).status_code == 404
        with db.transaction() as conn:
            conn.execute("UPDATE external_knowledge_connections SET enabled=0")
        inspected = (await c.get(f"{URL}/{row['mapping_id']}")).json()
        assert not inspected["local_snapshot_current"] and not inspected["retrieval_enabled"]
        assert (await c.post(URL, json=BODY)).status_code == 403


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
async def test_pagination_bounds(setup, params):
    _, repos, service = setup
    async with AsyncClient(
        transport=ASGITransport(app=app_for(repos, service.actor)), base_url="http://test"
    ) as c:
        assert (await c.get(URL, params=params)).status_code == 422


async def test_concurrent_revoke_only_one_wins(setup):
    import asyncio

    _, repos, service = setup
    async with AsyncClient(
        transport=ASGITransport(app=app_for(repos, service.actor)), base_url="http://test"
    ) as c:
        mid = (await c.post(URL, json=BODY)).json()["mapping_id"]
        results = await asyncio.gather(
            *[c.post(f"{URL}/{mid}/revoke", json={"expected_revision": 1}) for _ in range(2)]
        )
        assert sorted(r.status_code for r in results) == [200, 409]
        assert (await c.get(f"{URL}/{mid}")).json()["revision"] == 2


async def test_database_unavailable_localized(setup):
    _, repos, service = setup
    app = app_for(repos, service.actor)
    app.dependency_overrides[get_server] = lambda: SimpleNamespace(services=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get(URL, headers={"Accept-Language": "zh"})
        assert response.status_code == 503
        assert "知识映射" in response.json()["detail"]


async def test_list_is_persisted_state_without_per_item_inspection(setup, monkeypatch):
    from octop.infra.digital_workers.knowledge_mappings import WorkerKnowledgeMappingService

    _, repos, service = setup
    async with AsyncClient(
        transport=ASGITransport(app=app_for(repos, service.actor)), base_url="http://test"
    ) as c:
        row = (await c.post(URL, json=BODY)).json()

        def unexpected(*args, **kwargs):
            raise AssertionError("list must not inspect mappings or scan scenes")

        monkeypatch.setattr(WorkerKnowledgeMappingService, "inspect", unexpected)
        monkeypatch.setattr(repos.enterprise_scene_repo, "list_for_org", unexpected)
        monkeypatch.setattr(repos.worker_knowledge_mapping_repo, "get", unexpected)
        response = await c.get(URL)
        assert response.status_code == 200
        item = response.json()["items"][0]
        assert item["mapping_id"] == row["mapping_id"]
        assert item["local_snapshot_current"] is None
        assert item["retrieval_enabled"] is False
        assert item["authorization_status"] == "delegated_user_acl_unverified"


async def test_unauthorized_organization_existence_is_not_disclosed(setup):
    _, repos, _ = setup
    async with AsyncClient(
        transport=ASGITransport(app=app_for(repos, User(2, "other", "user", None))),
        base_url="http://test",
    ) as c:
        for method, suffix, body in (
            ("GET", "", None),
            ("GET", "/missing", None),
            ("POST", "", BODY),
            ("POST", "/missing/revoke", {"expected_revision": 1}),
        ):
            results = [
                await c.request(method, URL.replace("/o/", f"/{org}/") + suffix, json=body)
                for org in ("o", "other", "nonexistent")
            ]
            assert all(r.status_code == 403 for r in results)
            assert results[0].json() == results[1].json() == results[2].json()
