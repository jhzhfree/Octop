"""Owner-isolated workflow orchestration; explicit human decisions guard every write."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from apscheduler.triggers.cron import CronTrigger

from octop.infra.connectors.crypto import decrypt_credentials, encrypt_credentials
from octop.infra.db.repos.enterprise_assistant import EnterpriseAssistantRepo
from octop.infra.db.services import RepoBundle
from octop.infra.users.identity import User
from octop.infra.users.permissions import user_has_permission

from . import adapters
from .adapters import WorkflowError
from .models import (
    MODULE_FOR,
    ConnectionConfig,
    ConnectionInput,
    Decision,
    Features,
    JobConfig,
    JobInput,
)
from .reports import format_report

logger = logging.getLogger(__name__)
FLAGS_KEY = "enterprise_assistant.features.v1"


def next_due(config: JobConfig, now: int | None = None) -> int | None:
    if not config.enabled or not config.cron:
        return None
    moment = datetime.fromtimestamp(now if now is not None else time.time(), tz=UTC)
    fire = CronTrigger.from_crontab(config.cron, timezone=config.timezone).get_next_fire_time(
        None, moment
    )
    return int(fire.timestamp()) if fire else None


def digest(content: str, snapshot: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            {"content": content, "snapshot": snapshot}, sort_keys=True, ensure_ascii=False
        ).encode()
    ).hexdigest()


class AssistantService:
    def __init__(self, repos: RepoBundle, user: User) -> None:
        self.repos = repos
        self.user = user
        self.repo = EnterpriseAssistantRepo(repos.db)

    def flags(self) -> Features:
        raw = self.repos.settings_repo.get(FLAGS_KEY)
        return Features.model_validate_json(raw) if raw else Features()

    def set_flags(self, flags: Features) -> Features:
        if not self.user.is_admin:
            raise WorkflowError("ADMIN_REQUIRED")
        flags.allowed_origins = list(
            dict.fromkeys(adapters.origin(u) for u in flags.allowed_origins)
        )
        self.repos.settings_repo.set(FLAGS_KEY, flags.model_dump_json())
        self.repos.audit_repo.write(
            actor=str(self.user.id),
            action="enterprise_assistant.features.update",
            payload=flags.model_dump_json(),
        )
        return flags

    def require(self, kind: str | None = None) -> Features:
        row = self.repos.user_repo.get(self.user.id)
        if row is None or row.disabled:
            raise WorkflowError("ACCESS_DENIED")
        current = User(
            row.id, row.username, row.role, row.display_name, permissions=row.permissions
        )
        if not user_has_permission(current, "tasks"):
            raise WorkflowError("ACCESS_DENIED")
        flags = self.flags()
        if not flags.enabled or (kind and not getattr(flags, MODULE_FOR[kind])):
            raise WorkflowError("MODULE_DISABLED")
        return flags

    def owned(self, resource_id: str, category: str) -> dict[str, Any]:
        row = self.repo.resource(resource_id)
        if not row or row["owner_id"] != self.user.id or row["category"] != category:
            raise WorkflowError("RESOURCE_NOT_FOUND")
        return row

    def run_owned(self, run_id: str) -> dict[str, Any]:
        row = self.repo.run(run_id)
        if not row or row["owner_id"] != self.user.id:
            raise WorkflowError("RESOURCE_NOT_FOUND")
        return row

    def agent(self, agent_id: str) -> None:
        row = self.repos.agent_repo.get(agent_id)
        if not row or row.user_id != self.user.id or not row.enabled:
            raise WorkflowError("AGENT_NOT_AVAILABLE")

    def list_resources(self, category: str) -> list[dict[str, Any]]:
        self.require()
        return [self.public(r) for r in self.repo.resources(self.user.id, category)]

    @staticmethod
    def public(row: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in row.items() if k != "secret_blob"} | {
            "has_secret": bool(row.get("secret_blob")),
        }

    def save_connection(
        self, body: ConnectionInput, resource_id: str | None = None
    ) -> dict[str, Any]:
        flags = self.require(body.config.kind)
        adapters.validate_connection(body.config, flags)
        old = self.owned(resource_id, "connection") if resource_id else None
        if old and body.config.kind != old["config"]["kind"]:
            raise WorkflowError("CONNECTION_KIND_IMMUTABLE")
        if bool(old) != bool(body.expected_version):
            raise WorkflowError("VERSION_REQUIRED")
        if (
            old
            and old["config"].get("inspection_transport", "http")
            != body.config.inspection_transport
            and not body.secret
        ):
            raise WorkflowError("SECRET_REQUIRED")
        encrypted = (old or {}).get("secret_blob", "")
        if body.secret:
            encrypted = encrypt_credentials(self.repos.secret_repo, {"secret": body.secret}).decode(
                "ascii"
            )
        if (
            body.config.auth != "none"
            or body.config.inspection_transport == "ssh"
            or body.config.kind in {"oa", "wecom", "vmware"}
        ) and not encrypted:
            raise WorkflowError("SECRET_REQUIRED")
        resource_id = resource_id or str(uuid4())
        if not self.repo.save(
            resource_id,
            self.user.id,
            "connection",
            body.config.model_dump(),
            encrypted,
            body.expected_version,
            None,
        ):
            raise WorkflowError("VERSION_CONFLICT")
        return self.public(self.owned(resource_id, "connection"))

    def save_job(self, body: JobInput, resource_id: str | None = None) -> dict[str, Any]:
        self.require(body.config.kind)
        config = body.config
        self.agent(config.agent_id)
        connection = self.owned(config.connection_id, "connection")
        required_kind = "wecom" if config.kind == "report" else config.kind
        if connection["config"]["kind"] != required_kind:
            raise WorkflowError("CONNECTION_KIND_MISMATCH")
        for source_id in config.source_job_ids:
            source = self.owned(source_id, "job")["config"]
            if (
                source["kind"] not in {"inspection", "jira", "oa"}
                or source["agent_id"] != config.agent_id
            ):
                raise WorkflowError("REPORT_SOURCE_INVALID")
        if resource_id:
            self.owned(resource_id, "job")
        if bool(resource_id) != bool(body.expected_version):
            raise WorkflowError("VERSION_REQUIRED")
        resource_id = resource_id or str(uuid4())
        if not self.repo.save(
            resource_id,
            self.user.id,
            "job",
            config.model_dump(),
            "",
            body.expected_version,
            next_due(config),
        ):
            raise WorkflowError("VERSION_CONFLICT")
        return self.public(self.owned(resource_id, "job"))

    def enqueue(
        self, job_id: str, trigger: str | None = None, agent_id: str | None = None
    ) -> dict[str, Any]:
        job = self.owned(job_id, "job")
        config = JobConfig.model_validate(job["config"])
        self.require(config.kind)
        self.agent(config.agent_id)
        if agent_id is not None and config.agent_id != agent_id:
            raise WorkflowError("AGENT_NOT_AVAILABLE")
        connection = self.owned(config.connection_id, "connection")
        snapshot = {
            "job": config.model_dump(),
            "job_version": job["version"],
            "connection": connection["config"],
            "connection_version": connection["version"],
        }
        run_id = str(uuid4())
        if not self.repo.enqueue(run_id, self.user.id, job_id, trigger or str(uuid4()), snapshot):
            raise WorkflowError("RUN_ALREADY_ACTIVE")
        return self.run_owned(run_id)

    def list_runs(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        self.require()
        return [
            r
            for r in self.repo.runs(self.user.id)
            if not agent_id or r["snapshot"]["job"]["agent_id"] == agent_id
        ]

    def edit_draft(self, run_id: str, expected: int, content: str) -> dict[str, Any]:
        self.require("report")
        run = self.run_owned(run_id)
        if run["snapshot"]["job"]["kind"] != "report" or run["status"] != "awaiting_approval":
            raise WorkflowError("DRAFT_NOT_EDITABLE")
        if not self.repo.transition(
            run_id,
            expected,
            "awaiting_approval",
            "awaiting_approval",
            content=content,
            content_hash=digest(content, run["snapshot"]),
        ):
            raise WorkflowError("VERSION_CONFLICT")
        return self.run_owned(run_id)

    def decide(self, run_id: str, body: Decision) -> dict[str, Any]:
        run = self.run_owned(run_id)
        self.require(run["snapshot"]["job"]["kind"])
        if run["content_hash"] != body.content_hash:
            raise WorkflowError("VERSION_CONFLICT")
        # An uncertain side effect can be closed only after checking the remote receipt.
        if run["status"] == "unknown" and body.approve:
            raise WorkflowError("REMOTE_RECONCILIATION_REQUIRED")
        if run["status"] not in {"awaiting_approval", "unknown"}:
            raise WorkflowError("DECISION_NOT_AVAILABLE")
        if body.approve:
            self.live_snapshot(run)
            if (
                run["snapshot"]["job"]["kind"] == "report"
                and len(adapters.report_chunks(run["content"])) > 10
            ):
                raise WorkflowError("REPORT_TOO_LARGE")
        if not self.repo.transition(
            run_id,
            body.expected_version,
            run["status"],
            "approved" if body.approve else "rejected",
            evidence=[
                *run["evidence"],
                {
                    "type": "human_decision",
                    "user_id": self.user.id,
                    "approve": body.approve,
                    "at": int(time.time()),
                    "content_hash": body.content_hash,
                },
            ],
        ):
            raise WorkflowError("VERSION_CONFLICT")
        return self.run_owned(run_id)

    def live_snapshot(
        self, run: dict[str, Any]
    ) -> tuple[JobConfig, ConnectionConfig, str, Features]:
        snapshot = run["snapshot"]
        job = JobConfig.model_validate(snapshot["job"])
        flags = self.require(job.kind)
        self.agent(job.agent_id)
        current_job = self.owned(run["job_id"], "job")
        connection = self.owned(job.connection_id, "connection")
        if (
            current_job["version"] != snapshot["job_version"]
            or connection["version"] != snapshot["connection_version"]
        ):
            raise WorkflowError("CONFIGURATION_CHANGED")
        config = ConnectionConfig.model_validate(snapshot["connection"])
        adapters.validate_connection(config, flags)
        secret = ""
        if connection["secret_blob"]:
            secret = str(
                decrypt_credentials(self.repos.secret_repo, connection["secret_blob"].encode())[
                    "secret"
                ]
            )
        return job, config, secret, flags

    async def process(self, run_id: str) -> None:
        run = self.run_owned(run_id)
        old = run["status"]
        if old not in {"queued", "approved"}:
            return
        state = "executing" if old == "approved" else "running"
        if not self.repo.transition(run_id, run["version"], old, state):
            return
        run = self.run_owned(run_id)
        evidence = list(run["evidence"])
        content = run["content"]
        side_effect_started = False
        try:
            job, config, secret, flags = self.live_snapshot(run)
            evidence.append(
                {
                    "type": "stage",
                    "stage": "access_and_connection_validated",
                    "at": int(time.time()),
                }
            )
            if old == "approved":
                if run["content_hash"] != digest(content, run["snapshot"]):
                    raise WorkflowError("CONTENT_HASH_MISMATCH")
                if job.kind == "report":

                    def receipt(value: dict[str, Any]) -> None:
                        nonlocal run, side_effect_started
                        if value.get("type") == "wecom_attempt":
                            self.live_snapshot(run)  # Kill switch before each chunk.
                        evidence.append(value)
                        if not self.repo.transition(
                            run_id, run["version"], state, state, evidence=evidence
                        ):
                            raise WorkflowError("VERSION_CONFLICT")
                        run = self.run_owned(run_id)
                        side_effect_started = True

                    await adapters.send_wecom(config, secret, content, flags, receipt)
                    final = "sent"
                elif job.kind == "vmware":

                    def before_deploy() -> None:
                        nonlocal side_effect_started
                        self.live_snapshot(run)
                        side_effect_started = True

                    evidence.append(
                        await adapters.deploy_vm(
                            config, secret, job.vm_name, flags, before_deploy=before_deploy
                        )
                    )
                    final = "succeeded"
                else:
                    raise WorkflowError("APPROVAL_KIND_INVALID")
            elif job.kind == "report":
                cutoff = int(time.time()) - 7 * 86400
                facts: list[dict[str, Any]] = []
                for source_id in job.source_job_ids:
                    source = self.owned(source_id, "job")
                    source_config = source["config"]
                    self.require(source_config["kind"])
                    latest = self.repo.latest_for_job(self.user.id, source_id)
                    if (
                        not latest
                        or latest["status"] != "succeeded"
                        or latest["created_at"] < cutoff
                        or latest["snapshot"]["job_version"] != source["version"]
                    ):
                        raise WorkflowError("REPORT_SOURCE_MISSING_OR_STALE")
                    facts.append(
                        {
                            "type": "source",
                            "job_id": source_id,
                            "run_id": latest["id"],
                            "collected_at": latest["created_at"],
                            "name": source_config["name"],
                            "facts": latest["evidence"],
                        }
                    )
                evidence.extend(facts)
                content = format_report(facts, self.user.locale)
                if len(content) > 40000:
                    raise WorkflowError("REPORT_TOO_LARGE")
                final = "awaiting_approval"
            elif job.kind == "vmware":
                content = json.dumps(
                    {
                        "name": job.vm_name,
                        "os": config.os_label,
                        "template_id": config.template_id,
                        "folder": config.folder,
                        "resource_pool": config.resource_pool,
                        "datastore": config.datastore,
                        "customization_spec": config.customization_spec,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                final = "awaiting_approval"
            else:
                evidence.extend(await adapters.collect(config, secret, flags))
                final = "succeeded"
            if len(json.dumps(evidence, ensure_ascii=False).encode()) > 1024 * 1024:
                raise WorkflowError("EVIDENCE_TOO_LARGE")
            self.repo.transition(
                run_id,
                run["version"],
                state,
                final,
                evidence=evidence,
                content=content,
                content_hash=digest(content, run["snapshot"]),
            )
        except asyncio.CancelledError:
            self.repo.transition(
                run_id,
                run["version"],
                state,
                "unknown" if side_effect_started else "failed",
                error="PROCESS_INTERRUPTED",
                evidence=evidence,
                content_hash=digest(content, run["snapshot"]),
            )
            raise
        except Exception as exc:
            code = str(exc) if isinstance(exc, WorkflowError) else "CONNECTOR_FAILED"
            self.repo.transition(
                run_id,
                run["version"],
                state,
                "unknown" if side_effect_started else "failed",
                error=code,
                evidence=evidence,
                content_hash=digest(content, run["snapshot"]),
            )


def owner_service(repos: RepoBundle, owner_id: int) -> AssistantService | None:
    row = repos.user_repo.get(owner_id)
    if not row or row.disabled:
        return None
    return AssistantService(
        repos,
        User(
            row.id,
            row.username,
            row.role,
            row.display_name,
            locale=row.locale,
            permissions=row.permissions,
        ),
    )


async def worker(get_repos: Callable[[], RepoBundle | None]) -> None:
    """Single lightweight DB worker, no external queue. Missed schedules >5 minutes are skipped."""
    while True:
        try:
            repos = get_repos()
            if repos is None:
                await asyncio.sleep(2)
                continue
            repo = EnterpriseAssistantRepo(repos.db)
            repo.recover()
            now = int(time.time())
            for row in repo.due(now):
                service = owner_service(repos, row["owner_id"])
                config = JobConfig.model_validate(row["config"])
                if not repo.advance(
                    row["id"], row["version"], row["next_due"], next_due(config, now + 1)
                ):
                    continue
                if service and now - row["next_due"] <= 300:
                    with contextlib.suppress(WorkflowError):
                        service.enqueue(row["id"], f"schedule:{row['next_due']}")
            for run in repo.pending():
                service = owner_service(repos, run["owner_id"])
                if service:
                    await asyncio.wait_for(service.process(run["id"]), timeout=240)
                else:
                    repo.transition(
                        run["id"], run["version"], run["status"], "blocked", error="ACCESS_DENIED"
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("enterprise assistant worker iteration failed; details suppressed")
        await asyncio.sleep(2)
