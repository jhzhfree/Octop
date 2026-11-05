from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.services import RepoBundle
from octop.infra.enterprise_assistant import adapters
from octop.infra.enterprise_assistant.adapters import WorkflowError
from octop.infra.enterprise_assistant.models import (
    ConnectionConfig,
    ConnectionInput,
    Decision,
    Features,
    JobConfig,
    JobInput,
)
from octop.infra.enterprise_assistant.service import AssistantService, next_due
from octop.infra.users.identity import User


@pytest.fixture
def service(tmp_path):
    pool = SqlitePool(tmp_path / "workflows.db")
    run_migrations(pool)
    repos = RepoBundle.from_pool(pool)
    uid = repos.user_repo.create(username="owner", password_hash="unused", role="admin")
    repos.agent_repo.create(agent_id="assistant", user_id=uid, name="Assistant")
    svc = AssistantService(repos, User(uid, "owner", "admin", None))
    svc.set_flags(
        Features(
            enabled=True,
            inspection=True,
            work_items=True,
            reports=True,
            vmware=True,
            allowed_origins=["https://services.example", "https://qyapi.weixin.qq.com"],
        )
    )
    yield svc
    pool.close()


def connection(service, kind="inspection", **kwargs):
    config = {"name": kind, "kind": kind, "url": "https://services.example", **kwargs}
    if kind == "wecom":
        config["url"] = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send"
    if kind == "vmware":
        config.update(
            template_id="template-1",
            os_label="Ubuntu",
            folder="group-v1",
            resource_pool="resgroup-1",
            datastore="datastore-1",
        )
    return service.save_connection(
        ConnectionInput(config=ConnectionConfig(**config), secret="sensitive")
    )


def job(service, conn, kind="inspection", **kwargs):
    return service.save_job(
        JobInput(
            config=JobConfig(
                name=kind, kind=kind, agent_id="assistant", connection_id=conn["id"], **kwargs
            )
        )
    )


def decide(service, run, approve=True):
    return service.decide(
        run["id"],
        Decision(
            expected_version=run["version"], content_hash=run["content_hash"], approve=approve
        ),
    )


async def successful_read(service, monkeypatch):
    monkeypatch.setattr(
        adapters, "collect", AsyncMock(return_value=[{"type": "fact", "value": "done"}])
    )
    source = job(service, connection(service))
    run = service.enqueue(source["id"])
    await service.process(run["id"])
    return source


def test_default_switches_and_both_gates(service):
    assert not Features().enabled and not Features().reports
    service.set_flags(Features())
    with pytest.raises(WorkflowError, match="MODULE_DISABLED"):
        service.list_resources("job")
    flags = Features(enabled=True)
    service.set_flags(flags)
    with pytest.raises(WorkflowError, match="MODULE_DISABLED"):
        connection(service)


def test_encrypts_secret_and_owner_isolation(service):
    conn = connection(service, auth="bearer")
    assert conn["has_secret"] and "secret_blob" not in conn
    stored = service.repo.resource(conn["id"])
    assert "sensitive" not in json.dumps(stored)
    uid = service.repos.user_repo.create(
        username="other", password_hash="unused", role="user", permissions=["tasks"]
    )
    other = AssistantService(service.repos, User(uid, "other", "user", None, permissions=["tasks"]))
    assert not other.list_resources("connection")
    with pytest.raises(WorkflowError, match="RESOURCE_NOT_FOUND"):
        other.owned(conn["id"], "connection")
    with pytest.raises(WorkflowError, match="ADMIN_REQUIRED"):
        other.set_flags(Features())


def test_origin_policy_and_secret_url_blocking(service):
    for url in [
        "http://services.example",
        "https://evil.example",
        "https://services.example@evil.example",
    ]:
        with pytest.raises(WorkflowError):
            connection(service, url=url)
    with pytest.raises(WorkflowError, match="URL_QUERY_NOT_ALLOWED"):
        connection(service, url="https://services.example/?token=secret")
    with pytest.raises(WorkflowError):
        adapters.origin("https://169.254.169.254")


def test_config_version_and_schedule_validation(service):
    c = connection(service)
    body = ConnectionInput(config=ConnectionConfig.model_validate(c["config"]), expected_version=1)
    assert service.save_connection(body, c["id"])["version"] == 2
    with pytest.raises(WorkflowError, match="VERSION_CONFLICT"):
        service.save_connection(body, c["id"])
    with pytest.raises(ValidationError):
        JobConfig(
            name="bad",
            kind="vmware",
            agent_id="assistant",
            connection_id=c["id"],
            vm_name="vm",
            cron="0 0 * * *",
        )
    config = JobConfig(
        name="daily",
        kind="inspection",
        agent_id="assistant",
        connection_id=c["id"],
        enabled=True,
        cron="0 9 * * *",
        timezone="Asia/Shanghai",
    )
    assert next_due(config, 0) == 3600


async def test_duplicate_claims_and_agent_binding(service, monkeypatch):
    collect = AsyncMock(return_value=[])
    monkeypatch.setattr(adapters, "collect", collect)
    j = job(service, connection(service))
    with pytest.raises(WorkflowError, match="AGENT_NOT_AVAILABLE"):
        service.enqueue(j["id"], agent_id="another-agent")
    run = service.enqueue(j["id"], "same-trigger")
    with pytest.raises(WorkflowError, match="RUN_ALREADY_ACTIVE"):
        service.enqueue(j["id"])
    await asyncio.gather(service.process(run["id"]), service.process(run["id"]))
    assert collect.await_count == 1
    with pytest.raises(WorkflowError, match="RUN_ALREADY_ACTIVE"):
        service.enqueue(j["id"], "same-trigger")


async def test_report_confirmed_exactly_once_and_draft_invalidates_decision(service, monkeypatch):
    source = await successful_read(service, monkeypatch)
    report = job(service, connection(service, "wecom"), "report", source_job_ids=[source["id"]])
    send = AsyncMock()
    monkeypatch.setattr(adapters, "send_wecom", send)
    run = service.enqueue(report["id"])
    await service.process(run["id"])
    pending = service.run_owned(run["id"])
    assert pending["status"] == "awaiting_approval" and send.await_count == 0
    edited = service.edit_draft(
        run["id"], pending["version"], "Final report with source run citation"
    )
    with pytest.raises(WorkflowError, match="VERSION_CONFLICT"):
        decide(service, pending)
    approved = decide(service, edited)
    with pytest.raises(WorkflowError, match="DECISION_NOT_AVAILABLE"):
        decide(service, edited)
    await asyncio.gather(service.process(approved["id"]), service.process(approved["id"]))
    assert send.await_count == 1
    assert service.run_owned(run["id"])["status"] == "sent"


async def test_report_requires_recent_success_not_previous_success(service, monkeypatch):
    source = await successful_read(service, monkeypatch)
    monkeypatch.setattr(
        adapters, "collect", AsyncMock(side_effect=WorkflowError("UPSTREAM_UNAVAILABLE"))
    )
    failed = service.enqueue(source["id"])
    await service.process(failed["id"])
    report = job(service, connection(service, "wecom"), "report", source_job_ids=[source["id"]])
    run = service.enqueue(report["id"])
    await service.process(run["id"])
    assert service.run_owned(run["id"])["error_code"] == "REPORT_SOURCE_MISSING_OR_STALE"


async def test_kill_switch_and_changed_destination_block_approved_vm(service, monkeypatch):
    deploy = AsyncMock(return_value={"vm_id": "vm-1"})
    monkeypatch.setattr(adapters, "deploy_vm", deploy)
    c = connection(service, "vmware")
    j = job(service, c, "vmware", vm_name="test-vm")
    run = service.enqueue(j["id"])
    await service.process(run["id"])
    pending = service.run_owned(run["id"])
    assert pending["status"] == "awaiting_approval" and deploy.await_count == 0
    decide(service, pending)
    service.set_flags(service.flags().model_copy(update={"vmware": False}))
    await service.process(run["id"])
    assert deploy.await_count == 0
    assert service.run_owned(run["id"])["error_code"] == "MODULE_DISABLED"


async def test_unknown_write_persists_partial_receipts_never_retries(service, monkeypatch):
    source = await successful_read(service, monkeypatch)
    report = job(service, connection(service, "wecom"), "report", source_job_ids=[source["id"]])

    async def partial(config, secret, content, flags, receipt):
        receipt({"type": "wecom_ack", "chunk": 1, "accepted": True})
        raise WorkflowError("UPSTREAM_UNAVAILABLE")

    send = AsyncMock(side_effect=partial)
    monkeypatch.setattr(adapters, "send_wecom", send)
    run = service.enqueue(report["id"])
    await service.process(run["id"])
    decide(service, service.run_owned(run["id"]))
    await service.process(run["id"])
    unknown = service.run_owned(run["id"])
    assert unknown["status"] == "unknown"
    assert any(e.get("chunk") == 1 for e in unknown["evidence"])
    await service.process(run["id"])
    assert send.await_count == 1
    with pytest.raises(WorkflowError, match="REMOTE_RECONCILIATION_REQUIRED"):
        decide(service, unknown)
    assert decide(service, unknown, False)["status"] == "rejected"


async def test_changed_connection_rejects_approval_but_can_cancel(service):
    c = connection(service, "vmware")
    j = job(service, c, "vmware", vm_name="new-vm")
    r = service.enqueue(j["id"])
    await service.process(r["id"])
    pending = service.run_owned(r["id"])
    service.save_connection(
        ConnectionInput(
            config=ConnectionConfig.model_validate(c["config"]),
            secret="rotated",
            expected_version=c["version"],
        ),
        c["id"],
    )
    with pytest.raises(WorkflowError, match="CONFIGURATION_CHANGED"):
        decide(service, pending)
    assert decide(service, pending, False)["status"] == "rejected"


async def test_revoked_permission_checked_at_execution(service, monkeypatch):
    collect = AsyncMock()
    monkeypatch.setattr(adapters, "collect", collect)
    j = job(service, connection(service))
    r = service.enqueue(j["id"])
    with service.repos.db.transaction() as conn:
        conn.execute("UPDATE users SET disabled=1 WHERE id=?", (service.user.id,))
    await service.process(r["id"])
    assert service.run_owned(r["id"])["error_code"] == "ACCESS_DENIED"
    collect.assert_not_awaited()


async def test_recovery_does_not_interrupt_live_request_or_repeat_write(service):
    j = job(service, connection(service))
    r = service.enqueue(j["id"])
    service.repo.transition(r["id"], r["version"], "queued", "running")
    service.repo.recover()
    assert service.run_owned(r["id"])["status"] == "running"
    with service.repos.db.transaction() as conn:
        conn.execute(
            "UPDATE enterprise_assistant_runs SET updated_at=? WHERE id=?",
            (int(time.time()) - 601, r["id"]),
        )
    service.repo.recover()
    assert service.run_owned(r["id"])["status"] == "failed"


async def test_bounded_transport_does_not_leak_secrets():
    async def handler(request):
        return httpx.Response(302, headers={"location": "https://unapproved.example?key=secret"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(WorkflowError, match="UPSTREAM_HTTP_302") as exc:
            await adapters.json_request(client, "GET", "https://approved.example")
        assert "secret" not in str(exc.value)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, content=b"x" * (adapters.MAX_BYTES + 1))
        )
    ) as client:
        with pytest.raises(WorkflowError, match="RESPONSE_TOO_LARGE"):
            await adapters.json_request(client, "GET", "https://approved.example")


async def test_jira_v3_pagination_and_authorization(monkeypatch):
    requests = []

    def handler(req):
        requests.append(req)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "issues": [
                        {"key": "OPS-1", "fields": {"summary": "Task", "status": {"name": "Open"}}}
                    ],
                    "nextPageToken": "next",
                },
            )
        return httpx.Response(200, json={"issues": [], "isLast": True})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        adapters,
        "_client",
        lambda cfg, secret: original(
            transport=httpx.MockTransport(handler), headers={"Authorization": "Bearer " + secret}
        ),
    )
    config = ConnectionConfig(name="Jira", kind="jira", url="https://jira.example", jira_api="3")
    result = await adapters.collect(
        config, "secret", Features(allowed_origins=["https://jira.example"])
    )
    assert result[0]["key"] == "OPS-1"
    assert str(requests[0].url).endswith("/rest/api/3/search/jql")
    assert json.loads(requests[1].content)["nextPageToken"] == "next"
    assert requests[0].headers["authorization"] == "Bearer secret"


def test_wecom_chunks_respect_utf8_limit():
    text = "周报" * 10000
    chunks = adapters.report_chunks(text)
    assert "".join(chunks) == text
    assert all(len(c.encode()) <= 2000 for c in chunks)


async def test_vmware_session_inventory_template_and_receipt(monkeypatch):
    calls = []

    def handler(req):
        calls.append(req)
        if req.url.path == "/api/session":
            return httpx.Response(200, json="session")
        if req.url.path == "/api/vcenter/vm":
            return httpx.Response(200, json=[])
        assert req.headers["vmware-api-session-id"] == "session"
        body = json.loads(req.content)
        assert body["name"] == "test-vm" and body["placement"]["folder"] == "folder"
        assert body["guest_customization"] == {"name": "Linux-customization"}
        assert "datastore" not in body["placement"]
        assert body["vm_home_storage"] == {"datastore": "ds"}
        assert body["disk_storage"] == {"datastore": "ds"}
        return httpx.Response(200, json="vm-42")

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(**kw, transport=httpx.MockTransport(handler))
    )
    config = ConnectionConfig(
        name="vCenter",
        kind="vmware",
        url="https://vcenter.example",
        username="svc",
        template_id="template",
        os_label="Linux",
        folder="folder",
        resource_pool="pool",
        datastore="ds",
        customization_spec="Linux-customization",
    )
    receipt = await adapters.deploy_vm(
        config, "password", "test-vm", Features(allowed_origins=["https://vcenter.example"])
    )
    assert receipt["vm_id"] == "vm-42"
    assert calls[0].headers["authorization"].startswith("Basic ")
    assert calls[2].url.params["action"] == "deploy"
    assert calls[-1].method == "DELETE"


async def test_wecom_records_each_attempt_and_ack_without_exposing_key(monkeypatch):
    requests = []

    def handler(req):
        requests.append(req)
        return httpx.Response(200, json={"errcode": 0})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        adapters, "_client", lambda cfg, secret: original(transport=httpx.MockTransport(handler))
    )
    config = ConnectionConfig(
        name="Team", kind="wecom", url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send"
    )
    evidence = []
    await adapters.send_wecom(
        config,
        "key-secret",
        "周报" * 1000,
        Features(allowed_origins=["https://qyapi.weixin.qq.com"]),
        evidence.append,
    )
    assert len(requests) == 4
    assert [e["type"] for e in evidence] == ["wecom_attempt", "wecom_ack"] * 4
    assert "key-secret" not in json.dumps(evidence)


def test_ssh_pins_host_key_and_only_uses_fixed_commands(monkeypatch):
    import io
    from types import SimpleNamespace
    from unittest.mock import Mock

    ssh = pytest.importorskip("paramiko")
    key = ssh.RSAKey.generate(2048)
    pem = io.StringIO()
    key.write_private_key(pem)
    client = Mock()
    commands = []

    def execute(command, timeout):
        commands.append(command)
        out = io.BytesIO(b"read-only facts")
        out.channel = SimpleNamespace(recv_exit_status=lambda: 0)
        return Mock(), out, io.BytesIO(b"")

    client.exec_command.side_effect = execute
    monkeypatch.setattr(ssh, "SSHClient", lambda: client)
    config = ConnectionConfig(
        name="Linux",
        kind="inspection",
        url="ssh://host.example:2222",
        username="svc",
        inspection_transport="ssh",
        ssh_host_key=f"ssh-rsa {key.get_base64()}",
    )
    flags = Features(allowed_origins=["ssh://host.example:2222"])
    adapters.validate_connection(config, flags)
    facts = adapters.collect_ssh(config, pem.getvalue())
    assert len(facts) == 4 and tuple(commands) == adapters.SSH_COMMANDS
    assert client.connect.call_args.kwargs["look_for_keys"] is False
    assert client.connect.call_args.kwargs["allow_agent"] is False
    host, kind, pinned = client.get_host_keys().add.call_args.args
    assert host == "[host.example]:2222" and pinned == key
    assert isinstance(client.set_missing_host_key_policy.call_args.args[0], ssh.RejectPolicy)
    client.close.assert_called_once()


def test_report_is_readable_and_does_not_invent_completion():
    from octop.infra.enterprise_assistant.reports import format_report

    text = format_report(
        [
            {
                "name": "Jira tasks",
                "run_id": "run-1",
                "collected_at": 0,
                "facts": [
                    {"type": "stage", "stage": "validated"},
                    {
                        "type": "jira",
                        "key": "OPS-1",
                        "summary": "Investigate incident",
                        "status": "Open",
                    },
                ],
            }
        ],
        "en",
    )
    assert "OPS-1 · Investigate incident · Open" in text
    assert "Source run: run-1" in text and "not evidence of uncollected activity" in text
    assert "validated" not in text


def test_invalid_time_zone_is_a_validation_error():
    with pytest.raises(ValidationError, match="Unknown IANA time zone"):
        JobConfig(
            name="bad", kind="inspection", agent_id="a", connection_id="c", timezone="invalid/zone"
        )


async def test_report_rejects_evidence_from_previous_source_configuration(service, monkeypatch):
    source = await successful_read(service, monkeypatch)
    service.save_job(
        JobInput(
            config=JobConfig.model_validate(source["config"]), expected_version=source["version"]
        ),
        source["id"],
    )
    report = job(service, connection(service, "wecom"), "report", source_job_ids=[source["id"]])
    run = service.enqueue(report["id"])
    await service.process(run["id"])
    assert service.run_owned(run["id"])["error_code"] == "REPORT_SOURCE_MISSING_OR_STALE"


async def test_vm_gate_is_rechecked_after_preflight_before_remote_write(service, monkeypatch):
    writes = []

    async def deployment(config, secret, name, flags, before_deploy):
        service.set_flags(service.flags().model_copy(update={"vmware": False}))
        before_deploy()
        writes.append(name)
        return {"vm_id": "not-created"}

    monkeypatch.setattr(adapters, "deploy_vm", deployment)
    j = job(service, connection(service, "vmware"), "vmware", vm_name="test-vm")
    run = service.enqueue(j["id"])
    await service.process(run["id"])
    decide(service, service.run_owned(run["id"]))
    await service.process(run["id"])
    final = service.run_owned(run["id"])
    assert final["status"] == "failed" and final["error_code"] == "MODULE_DISABLED"
    assert not writes


async def test_report_gate_stops_next_chunk_and_keeps_first_receipt(service, monkeypatch):
    source = await successful_read(service, monkeypatch)
    report = job(service, connection(service, "wecom"), "report", source_job_ids=[source["id"]])
    sent = []

    async def sending(config, secret, content, flags, receipt):
        receipt({"type": "wecom_attempt", "chunk": 1})
        sent.append(1)
        receipt({"type": "wecom_ack", "chunk": 1, "accepted": True})
        service.set_flags(service.flags().model_copy(update={"reports": False}))
        receipt({"type": "wecom_attempt", "chunk": 2})
        sent.append(2)

    monkeypatch.setattr(adapters, "send_wecom", sending)
    run = service.enqueue(report["id"])
    await service.process(run["id"])
    decide(service, service.run_owned(run["id"]))
    await service.process(run["id"])
    final = service.run_owned(run["id"])
    assert final["status"] == "unknown" and final["error_code"] == "MODULE_DISABLED"
    assert sent == [1]
    assert any(e.get("type") == "wecom_ack" for e in final["evidence"])


def test_switching_inspection_transport_requires_new_credential(service):
    c = connection(service, auth="bearer")
    service.set_flags(
        service.flags().model_copy(
            update={"allowed_origins": ["https://services.example", "ssh://services.example"]}
        )
    )
    config = ConnectionConfig(
        name="SSH",
        kind="inspection",
        url="ssh://services.example",
        username="svc",
        inspection_transport="ssh",
        ssh_host_key="ssh-ed25519 public-key",
    )
    with pytest.raises(WorkflowError, match="SECRET_REQUIRED"):
        service.save_connection(
            ConnectionInput(config=config, expected_version=c["version"]), c["id"]
        )
    assert service.owned(c["id"], "connection")["version"] == 1


def test_friday_report_example_fires_on_friday_in_owner_time_zone():
    from datetime import UTC, datetime

    config = JobConfig(
        name="Friday report",
        kind="report",
        agent_id="a",
        connection_id="c",
        source_job_ids=["s"],
        enabled=True,
        cron="0 17 * * fri",
        timezone="Asia/Shanghai",
    )
    thursday = int(datetime(2026, 11, 5, 12, tzinfo=UTC).timestamp())
    assert next_due(config, thursday) == int(datetime(2026, 11, 6, 9, tzinfo=UTC).timestamp())


async def test_report_too_many_wecom_chunks_remains_editable(service, monkeypatch):
    source = await successful_read(service, monkeypatch)
    report = job(service, connection(service, "wecom"), "report", source_job_ids=[source["id"]])
    run = service.enqueue(report["id"])
    await service.process(run["id"])
    current = service.run_owned(run["id"])
    draft = service.edit_draft(run["id"], current["version"], "周报" * 4000)
    with pytest.raises(WorkflowError, match="REPORT_TOO_LARGE"):
        decide(service, draft)
    assert service.run_owned(run["id"])["status"] == "awaiting_approval"
