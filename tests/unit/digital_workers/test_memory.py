from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from octop.infra.cron.delivery import CronDeliveryCommand, CronDeliveryService
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
        "mcp_servers": ["akb__1"],
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


@pytest.mark.asyncio
async def test_successful_run_candidate_review_writes_scoped_memory_to_akb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run()
    queued_run = {**run, "status": "queued"}
    candidate_state: dict[str, object] = {}

    def create_candidate(**values: object) -> dict[str, object]:
        candidate_state.update(values)
        candidate_state.update(status="pending", created_by=11)
        return candidate_state

    def claim_candidate(
        _candidate_id: str, *, reviewer_id: int, review_note: str, content: str
    ) -> bool:
        candidate_state.update(
            status="writing", reviewed_by=reviewer_id, review_note=review_note, content=content
        )
        return True

    def finish_candidate(
        _candidate_id: str,
        *,
        status: str,
        memory_code: str | None = None,
        error_summary: str | None = None,
    ) -> bool:
        candidate_state.update(status=status, memory_code=memory_code, error_summary=error_summary)
        return True

    repos = MagicMock()
    repos.enterprise_scene_repo.get_run_by_cron_id.return_value = queued_run
    repos.enterprise_scene_repo.get_run.return_value = run
    repos.organization_repo.get_member.return_value = {"role": "manager"}
    repos.digital_worker_repo.create_memory_candidate.side_effect = create_candidate
    repos.digital_worker_repo.get_memory_candidate.side_effect = lambda _candidate_id: (
        candidate_state or None
    )
    repos.digital_worker_repo.get_worker.return_value = {
        "capabilities": run["plan_snapshot"]["capabilities"]  # type: ignore[index]
    }
    repos.digital_worker_repo.claim_memory_candidate.side_effect = claim_candidate
    repos.digital_worker_repo.finish_memory_candidate.side_effect = finish_candidate
    repos.user_repo.get.return_value = SimpleNamespace(disabled=False)
    connector = MagicMock()
    connector.custom_harness_config_for_user.return_value = {
        "transport": "streamable_http",
        "url": "https://akb.example.com/api/mcp",
        "headers": {"X-API-Key": "secret"},
    }

    delivery = CronDeliveryService(gateway=MagicMock(), agent_manager=MagicMock(), repos=repos)
    command = CronDeliveryCommand(
        cron_id="cron-1",
        cron_name="Weekly report",
        agent_id="agent-1",
        user_id=11,
        session_key="agent-1:dashboard:11:dm",
        prompt="Prepare the weekly report",
        fresh_thread=False,
        task_type="agent",
        model=None,
        mcp_servers=("akb__1",),
        mcp_servers_exact=True,
    )
    delivery._create_scene_memory_candidates(command, "  Reviewed weekly result  ")

    assert candidate_state["content"] == "Reviewed weekly result"
    assert candidate_state["source_run_id"] == run["run_id"]
    assert candidate_state["scope_code"] == "weekly-report"

    client = AkbMemoryClient(AkbMemoryConfig("https://akb.example.com", "secret"))
    captured_payloads: list[dict[str, object]] = []

    def fake_capture(payload: dict[str, object]) -> dict[str, object]:
        captured_payloads.append(payload)
        return {"memory_code": "MEM-APPROVED"}

    monkeypatch.setattr(client, "_capture", fake_capture)
    monkeypatch.setattr(
        "octop.infra.digital_workers.memory._akb_memory_client", lambda _config: client
    )
    memory_service = WorkerMemoryService(repos, SimpleNamespace(id=22, is_admin=False), connector)

    reviewed = await memory_service.review_candidate(
        "org1",
        str(candidate_state["candidate_id"]),
        approve=True,
        review_note="Reviewed against the run output",
        content="Approved durable result",
    )

    assert reviewed["status"] == "written"
    assert reviewed["memory_code"] == "MEM-APPROVED"
    assert captured_payloads[0]["content"] == "Approved durable result"
    assert captured_payloads[0]["scopeType"] == "TASK"
    assert captured_payloads[0]["scopeCode"] == "weekly-report"
    assert captured_payloads[0]["sourceRef"] == run["run_id"]
    assert captured_payloads[0]["idempotencyKey"] == f"octop:{candidate_state['candidate_id']}"


def test_run_level_capture_opt_out_does_not_stage_memory_candidate() -> None:
    run = _run()
    queued_run = {**run, "status": "queued"}
    run["plan_snapshot"]["capabilities"]["memory_sources"][0][  # type: ignore[index]
        "capture_enabled"
    ] = False
    worker_repo = MagicMock()
    repos = SimpleNamespace(
        enterprise_scene_repo=SimpleNamespace(get_run_by_cron_id=lambda _cron_id: queued_run),
        digital_worker_repo=worker_repo,
    )
    delivery = CronDeliveryService(gateway=MagicMock(), agent_manager=MagicMock(), repos=repos)
    command = CronDeliveryCommand(
        cron_id="cron-1",
        cron_name="Weekly report",
        agent_id="agent-1",
        user_id=11,
        session_key="agent-1:dashboard:11:dm",
        prompt="Prepare the weekly report",
        fresh_thread=False,
        task_type="agent",
        model=None,
        mcp_servers=("akb__1",),
        mcp_servers_exact=True,
    )

    delivery._create_scene_memory_candidates(command, "A successful result")

    worker_repo.create_memory_candidate.assert_not_called()
