from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from octop.infra.digital_workers.memory import WorkerMemoryService
from octop.infra.knowledge.akb_client import AkbMemoryClient, AkbMemoryConfig


def _candidate() -> dict[str, object]:
    return {
        "candidate_id": "01JABCDEF0123456789ABCDEFG",
        "organization_id": "org1",
        "worker_id": "worker1",
        "source_run_id": "01JABCDEF0123456789ABCDEFGH",
        "provider": "akb",
        "mcp_server": "akb__1",
        "scope_type": "TASK",
        "scope_code": "weekly-report",
        "content": "Reviewed result",
        "status": "pending",
        "created_by": 11,
    }


def _run() -> dict[str, object]:
    return {
        "run_id": "01JABCDEF0123456789ABCDEFGH",
        "organization_id": "org1",
        "requested_by": 11,
        "status": "succeeded",
        "plan_snapshot": {
            "worker": {"worker_id": "worker1"},
            "capabilities": {
                "memory_sources": [
                    {
                        "provider": "akb",
                        "mcp_server": "akb__1",
                        "scope_type": "TASK",
                        "scope_code": "weekly-report",
                    }
                ]
            },
        },
    }


def _service() -> tuple[WorkerMemoryService, MagicMock, MagicMock]:
    repos = MagicMock()
    repos.organization_repo.get_member.return_value = {"role": "manager"}
    repos.digital_worker_repo.get_memory_candidate.side_effect = [_candidate(), _candidate()]
    repos.enterprise_scene_repo.get_run.return_value = _run()
    repos.digital_worker_repo.get_worker.return_value = {
        "capabilities": {
            "memory_sources": [
                {
                    "provider": "akb",
                    "mcp_server": "akb__1",
                    "scope_type": "TASK",
                    "scope_code": "weekly-report",
                }
            ]
        }
    }
    repos.user_repo.get.return_value = SimpleNamespace(disabled=False)
    repos.digital_worker_repo.claim_memory_candidate.return_value = True
    repos.digital_worker_repo.finish_memory_candidate.return_value = True
    repos.digital_worker_repo.get_memory_candidate.side_effect = [
        _candidate(),
        {**_candidate(), "status": "writing"},
        {**_candidate(), "status": "written", "memory_code": "MEM-123"},
    ]
    connector = MagicMock()
    connector.custom_harness_config_for_user.return_value = {
        "transport": "streamable_http",
        "url": "https://akb.example.com/api/mcp",
        "headers": {"X-API-Key": "secret"},
    }
    return (
        WorkerMemoryService(
            repos,
            SimpleNamespace(id=22, is_admin=False),
            connector,
        ),
        repos,
        connector,
    )


@pytest.mark.asyncio
async def test_approval_captures_only_reviewed_content_with_stable_idempotency_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repos, connector = _service()
    capture = AsyncMock(return_value={"memory_code": "MEM-123"})
    monkeypatch.setattr(
        "octop.infra.digital_workers.memory._akb_memory_client",
        lambda _config: SimpleNamespace(capture=capture),
    )

    result = await service.review_candidate(
        "org1",
        "01JABCDEF0123456789ABCDEFG",
        approve=True,
        review_note="Approved after scope check",
        content="Edited and verified result",
    )

    assert result["status"] == "written"
    assert result["memory_code"] == "MEM-123"
    connector.custom_harness_config_for_user.assert_called_once_with(11, "akb__1")
    capture.assert_awaited_once_with(
        content="Edited and verified result",
        scope_type="TASK",
        scope_code="weekly-report",
        source_ref="01JABCDEF0123456789ABCDEFGH",
        idempotency_key="octop:01JABCDEF0123456789ABCDEFG",
        tags=["octop", "approved-task-result", "worker:worker1"],
    )
    repos.audit_repo.write.assert_called_once()


@pytest.mark.asyncio
async def test_approval_rejects_candidate_not_present_in_run_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repos, _connector = _service()
    run = _run()
    run["plan_snapshot"]["capabilities"]["memory_sources"] = []  # type: ignore[index]
    repos.enterprise_scene_repo.get_run.return_value = run
    capture = AsyncMock()
    monkeypatch.setattr(
        "octop.infra.digital_workers.memory._akb_memory_client",
        lambda _config: SimpleNamespace(capture=capture),
    )

    with pytest.raises(Exception, match="approved memory binding"):
        await service.review_candidate(
            "org1", "01JABCDEF0123456789ABCDEFG", approve=True, review_note=""
        )

    repos.digital_worker_repo.claim_memory_candidate.assert_not_called()
    capture.assert_not_awaited()


@pytest.mark.asyncio
async def test_akb_capture_payload_contains_source_scope_and_retry_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AkbMemoryClient(AkbMemoryConfig("https://akb.example.com", "secret"))
    captured: dict[str, object] = {}

    def fake_capture(payload: dict[str, object]) -> dict[str, object]:
        captured.update(payload)
        return {"memory_code": "MEM-123"}

    monkeypatch.setattr(client, "_capture", fake_capture)

    result = await client.capture(
        content="A verified result",
        scope_type="TASK",
        scope_code="weekly-report",
        source_ref="01JABCDEF0123456789ABCDEFGH",
        idempotency_key="octop:01JABCDEF0123456789ABCDEFG",
        tags=["octop", "approved-task-result"],
    )

    assert result == {"memory_code": "MEM-123"}
    assert captured == {
        "memoryLevel": "L2",
        "scopeType": "TASK",
        "scopeCode": "weekly-report",
        "content": "A verified result",
        "tags": ["octop", "approved-task-result"],
        "sourceType": "TASK_RESULT",
        "sourceRef": "01JABCDEF0123456789ABCDEFGH",
        "idempotencyKey": "octop:01JABCDEF0123456789ABCDEFG",
        "confidence": 0.8,
        "importance": 3,
        "expiresInDays": 180,
    }
