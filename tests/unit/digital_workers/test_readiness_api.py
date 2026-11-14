from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.unit.digital_workers.test_readiness import setup as setup  # noqa: F401

from octop.api.deps import current_user, get_server
from octop.api.routers.worker_plan_readiness import router
from octop.infra.users.identity import User


@pytest.fixture
def api(setup):
    _, repos, _, goal_id, plan_id = setup
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[current_user] = lambda: User(1, "manager", "user", None)
    app.dependency_overrides[get_server] = lambda: SimpleNamespace(
        services=SimpleNamespace(repos=repos)
    )
    with TestClient(app) as client:
        yield (
            app,
            client,
            f"/organizations/o/digital-workers/w/goals/{goal_id}/plans/{plan_id}/readiness",
        )


def test_readiness_contract(api):
    _, client, path = api
    response = client.get(path, params={"expected_worker_version": 1, "expected_plan_version": 2})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["execution_authorized"] is False
    assert body["scope"] == "local_observation_not_atomic"
    assert {x["code"] for x in body["checks"] if x["status"] == "unknown"} == {
        "runtime_approval",
        "knowledge_user_acl",
        "model_budget",
    }


@pytest.mark.parametrize("version", [0, -1, "abc", "1.1"])
def test_invalid_version(api, version):
    _, client, path = api
    assert (
        client.get(
            path, params={"expected_worker_version": version, "expected_plan_version": 2}
        ).status_code
        == 422
    )


def test_requires_versions(api):
    _, client, path = api
    assert client.get(path).status_code == 422


@pytest.mark.parametrize("user_id", [2, 3])
def test_forbidden(api, user_id):
    app, client, path = api
    app.dependency_overrides[current_user] = lambda: User(user_id, "user", "user", None)
    response = client.get(path, params={"expected_worker_version": 1, "expected_plan_version": 2})
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert set(response.json()["detail"]) == {"code"}


@pytest.mark.parametrize("worker_version,plan_version", [(2, 2), (1, 1)])
def test_version_conflict(api, worker_version, plan_version):
    _, client, path = api
    response = client.get(
        path,
        params={"expected_worker_version": worker_version, "expected_plan_version": plan_version},
    )
    assert response.status_code == 409
    assert response.headers["cache-control"] == "no-store"


def test_cross_org(api):
    _, client, path = api
    assert (
        client.get(
            path.replace("/organizations/o/", "/organizations/other/"),
            params={"expected_worker_version": 1, "expected_plan_version": 2},
        ).status_code
        == 404
    )


def test_unavailable(api):
    app, client, path = api
    app.dependency_overrides[get_server] = lambda: SimpleNamespace()
    assert (
        client.get(
            path, params={"expected_worker_version": 1, "expected_plan_version": 2}
        ).status_code
        == 503
    )


@pytest.mark.parametrize("user_id", [2, 3])
def test_nonmanager_cannot_discover_organization_existence(api, user_id):
    app, client, path = api
    app.dependency_overrides[current_user] = lambda: User(user_id, "user", "user", None)
    params = {"expected_worker_version": 1, "expected_plan_version": 2}
    existing = client.get(path, params=params)
    missing = client.get(path.replace("/organizations/o/", "/organizations/absent/"), params=params)
    assert existing.status_code == missing.status_code == 403
    assert existing.json() == missing.json() == {"detail": {"code": "ORGANIZATION_FORBIDDEN"}}
