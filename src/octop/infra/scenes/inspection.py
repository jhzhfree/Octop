"""Approved same-run HTTP inspections and deterministic local reporting, never outbound sending."""

from __future__ import annotations

from typing import Any

from octop.infra.db.repos.scene_inspection import SceneInspectionRepo
from octop.infra.db.services import RepoBundle
from octop.infra.enterprise_assistant.adapters import WorkflowError
from octop.infra.enterprise_assistant.reports import format_report, health_status
from octop.infra.enterprise_assistant.service import (
    FLAGS_KEY,
    AssistantService,
    digest,
    owner_service,
)
from octop.infra.users.identity import User


def inspection_scope(
    repos: RepoBundle, actor: User, agent_id: str, job_ids: list[str]
) -> dict[str, Any]:
    service = AssistantService(repos, actor)
    service.require("inspection")
    service.require("report")
    service.agent(agent_id)
    if not 1 <= len(job_ids) <= 10 or len(set(job_ids)) != len(job_ids):
        raise WorkflowError("INSPECTION_SCOPE_INVALID")
    sources = []
    for job_id in job_ids:
        job = service.owned(job_id, "job")
        config = job["config"]
        connection = service.owned(config["connection_id"], "connection")
        if (
            config["kind"] != "inspection"
            or config["agent_id"] != agent_id
            or connection["config"]["kind"] != "inspection"
            or connection["config"].get("inspection_transport", "http") != "http"
        ):
            raise WorkflowError("INSPECTION_SCOPE_INVALID")
        sources.append(
            {
                "job_id": job_id,
                "name": config["name"],
                "job_version": job["version"],
                "connection_id": connection["id"],
                "connection_version": connection["version"],
            }
        )
    return {"mode": "http_inspection", "share_results": True, "sources": sources}


def candidates(repos: RepoBundle, actor: User, agent_id: str) -> list[dict[str, Any]]:
    service = AssistantService(repos, actor)
    service.require("inspection")
    service.require("report")
    service.agent(agent_id)
    result = []
    for job in service.repo.resources(actor.id, "job"):
        if job["config"].get("kind") != "inspection" or job["config"].get("agent_id") != agent_id:
            continue
        connection = service.repo.resource(job["config"]["connection_id"])
        if (
            not connection
            or connection["owner_id"] != actor.id
            or connection["config"].get("kind") != "inspection"
            or connection["config"].get("inspection_transport", "http") != "http"
        ):
            continue
        result.append(
            {
                "job_id": job["id"],
                "name": job["config"]["name"],
                "job_version": job["version"],
                "connection_version": connection["version"],
            }
        )
        if len(result) == 100:
            break
    return result


async def execute_inspection(
    repos: RepoBundle, run: dict[str, Any], *, cron_id: str, user_id: int, agent_id: str
) -> str:
    repo = SceneInspectionRepo(repos.db)
    service = owner_service(repos, user_id)
    if service is None:
        raise RuntimeError("inspection requester unavailable")
    batch = repo.claim(str(run["run_id"]), cron_id, user_id, agent_id)
    try:
        facts = []
        source_versions: dict[str, int] = {}
        counts = {
            "sources": len(batch["sources"]),
            "attempted": 0,
            "succeeded": 0,
            "failed": 0,
            "unknown": 0,
            "health_unknown": 0,
            "reports_persisted": 1,
            "scene_bound": 1,
            "not_started": 0,
        }
        for source in batch["sources"]:
            # Revoke stops the whole batch; it is not a healthy or missing-data result.
            current = repos.user_repo.get(user_id)
            if (
                not current
                or current.disabled
                or (
                    current.role != "admin"
                    and repos.organization_repo.get_member(run["organization_id"], user_id) is None
                )
            ):
                raise RuntimeError("inspection requester authority changed")
            service.require("inspection")
            service.require("report")
            service.agent(agent_id)
            child = service.run_owned(source["run_id"])
            service.live_snapshot(
                child
            )  # Versions, current allowlist and credentials checked before I/O.
            await service.process(child["id"])
            child = service.run_owned(child["id"])
            if child["status"] not in {"succeeded", "failed"}:
                raise RuntimeError("inspection child did not persist a terminal result")
            if child["content_hash"] != digest(child["content"], child["snapshot"]):
                raise RuntimeError("inspection child evidence persistence incomplete")
            source_versions[child["id"]] = child["version"]
            counts["attempted"] += int(
                any(e.get("stage") == "collection_started" for e in child["evidence"])
            )
            counts[child["status"]] += 1
            statuses = [
                health_status(e.get("status"))
                for e in child["evidence"]
                if e.get("type") == "health"
            ]
            if child["status"] == "succeeded" and (not statuses or "unknown" in statuses):
                counts["health_unknown"] += 1
            facts.append(
                {
                    "type": "source",
                    "job_id": child["job_id"],
                    "run_id": child["id"],
                    "collected_at": child["updated_at"],
                    "name": source["name"],
                    "kind": "inspection",
                    "collection_status": child["status"],
                    "facts": child["evidence"]
                    if child["status"] == "succeeded"
                    else [{"type": "health", "status": "unavailable"}],
                }
            )
        # Final current permission check before exposing the local report.
        service.require("inspection")
        service.require("report")
        content = f"Scene inspection run: {run['run_id']}\n" + format_report(
            facts, service.user.locale
        )
        if len(content) > 20000:
            raise RuntimeError("inspection report too large")
        flags_snapshot = repos.settings_repo.get(FLAGS_KEY) or ""
        for source in batch["sources"]:
            service.live_snapshot(service.run_owned(source["run_id"]))
        repo.finish(
            run["run_id"],
            content,
            counts,
            expected_flags=flags_snapshot,
            expected_sources=source_versions,
        )
        return content
    except BaseException:
        repo.fail(run["run_id"])
        raise


def inspection_result(
    repos: RepoBundle, actor: User, organization_id: str, run_id: str
) -> dict[str, Any] | None:
    from octop.infra.db.repos.scene_report_attachment import SceneReportAttachmentRepo

    access = SceneReportAttachmentRepo(repos.db)
    access.check_access(organization_id, run_id, actor.id)
    result = SceneInspectionRepo(repos.db).get(run_id)
    access.check_access(organization_id, run_id, actor.id)
    if result is None:
        return None
    # A report is available through the independently authorized evidence path.
    return {key: value for key, value in result.items() if key != "content"}
