"""Router boundary tests with ASGI, real acceptance DB and offline evidence adapter."""

import hashlib
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from tests.unit.scenes.test_acceptance import setup  # noqa: F401

from octop.api.deps import current_user, get_server
from octop.api.routers import scene_acceptance as api

BASE = "/organizations/o/scene-runs/r/acceptance"


@pytest.fixture
def api_env(request, monkeypatch):
    db, _, _, run = request.getfixturevalue("setup")
    user = SimpleNamespace(id=2)
    calls = []

    async def evidence(**kwargs):
        calls.append(kwargs)
        return "server controlled summary"

    monkeypatch.setattr(api, "scene_run_evidence", evidence)
    monkeypatch.setattr(
        api,
        "EnterpriseSceneService",
        lambda repos, actor: SimpleNamespace(
            actor=actor,
            _require_manager=lambda org: None,
            get_run_evidence_access=lambda org, rid, **kw: run.copy(),
        ),
    )
    server = SimpleNamespace(
        services=SimpleNamespace(db=db, repos=object(), thread_message_repo=object()),
        app_runtime=SimpleNamespace(
            cron_manager=object(),
            gateway=SimpleNamespace(thread_registry=object()),
            agent_registry=object(),
        ),
    )
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[current_user] = lambda: user
    app.dependency_overrides[get_server] = lambda: server
    return app, user, calls


async def test_api_review_decide_and_retry(api_env):
    app, _, calls = api_env
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        preview = await client.get(BASE + "/review")
        assert preview.status_code == 200
        assert preview.headers["cache-control"] == "no-store"
        data = preview.json()
        assert data["answer"] == "server controlled summary"
        assert data["acceptance"] is None
        assert data["evidence_sources"] == [
            {
                "kind": "assistant_summary",
                "source_run_id": "r",
                "content_sha256": hashlib.sha256(data["answer"].encode()).hexdigest(),
            }
        ]
        body = {
            "decision": "accepted",
            "expected_evidence_sha256": data["evidence_sha256"],
            "expected_snapshot_sha256": data["snapshot_sha256"],
        }
        decision = await client.post(BASE, json=body)
        assert decision.status_code == 200
        assert decision.json()["reviewer_id"] == 2
        assert "answer" not in decision.json()
        assert (await client.post(BASE, json=body)).json() == decision.json()
        changed = await client.post(BASE, json=body | {"decision": "changes_requested"})
        assert changed.status_code == 409
        assert all(call["run"]["run_id"] == "r" for call in calls)
        assert len(calls) == 4


@pytest.mark.parametrize(
    "extra",
    [
        {"answer": "forged"},
        {"reviewer_id": 4},
        {"is_admin": True},
        {"completion_percentage": 100},
        {"evidence_sources": []},
    ],
)
async def test_client_cannot_supply_evidence_or_authority(api_env, extra):
    app, _, calls = api_env
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            BASE,
            json={
                "decision": "accepted",
                "expected_evidence_sha256": "a" * 64,
                "expected_snapshot_sha256": "b" * 64,
            }
            | extra,
        )
        assert response.status_code == 422
        assert not calls


@pytest.mark.parametrize("reviewer", [1, 3])
async def test_api_forbidden_never_loads_evidence(api_env, reviewer):
    app, user, calls = api_env
    user.id = reviewer
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(BASE + "/review")
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "ACCEPTANCE_FORBIDDEN"
        assert not calls


async def test_forged_hash_and_cross_org_rejected(api_env):
    app, _, calls = api_env
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            BASE,
            json={
                "decision": "accepted",
                "expected_evidence_sha256": "a" * 64,
                "expected_snapshot_sha256": "b" * 64,
            },
        )
        assert response.status_code == 409
        calls.clear()
        response = await client.get(BASE.replace("/o/", "/other/") + "/review")
        assert response.status_code == 404
        assert not calls


def test_source_openapi_contract(api_env):
    app, _, _ = api_env
    schema = app.openapi()["components"]["schemas"]
    assert "evidence_sources" in schema["AcceptanceReviewRecord"]["required"]
    fields = schema["EvidenceSourceRecord"]["properties"]
    assert fields["kind"]["enum"] == [
        "same_run_http_report",
        "assistant_summary",
        "owner_shared_report",
    ]
    assert all(fields[key]["description"] for key in fields)


async def test_requester_reads_feedback_but_unrelated_reader_cannot(api_env):
    app, user, _ = api_env
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        review = (await client.get(BASE + "/review")).json()
        body = {
            "decision": "changes_requested",
            "review_note": "  Add retry evidence  ",
            "expected_evidence_sha256": review["evidence_sha256"],
            "expected_snapshot_sha256": review["snapshot_sha256"],
        }
        response = await client.post(BASE, json=body)
        assert response.status_code == 200
        assert response.json()["review_note"] == "Add retry evidence"
        user.id = 1
        feedback = await client.get(BASE)
        assert feedback.status_code == 200
        assert feedback.headers["cache-control"] == "no-store"
        assert feedback.json()["review_note"] == "Add retry evidence"
        user.id = 3
        assert (await client.get(BASE)).status_code == 403
        user.id = 2
        assert (
            await client.post(BASE, json=body | {"review_note": "different"})
        ).status_code == 409
