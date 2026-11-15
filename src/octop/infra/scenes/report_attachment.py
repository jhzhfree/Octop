"""Explicit sharing, not automatic execution, completion or outbound report approval."""

from __future__ import annotations

from typing import Any

from octop.i18n import tr
from octop.infra.db.repos.scene_acceptance import AcceptanceStoreError
from octop.infra.db.repos.scene_report_attachment import SceneReportAttachmentRepo
from octop.infra.enterprise_assistant.adapters import WorkflowError
from octop.infra.enterprise_assistant.service import AssistantService, digest
from octop.infra.scenes.service import EnterpriseSceneService, SceneError
from octop.infra.utils.locale import resolve_user_locale


class SceneReportAttachmentService:
    def __init__(self, scenes: EnterpriseSceneService) -> None:
        self.scenes = scenes
        self.repo = SceneReportAttachmentRepo(scenes.repos.db)
        self.assistant = AssistantService(scenes.repos, scenes.actor)

    def _error(self, reason: str, status: int) -> SceneError:
        locale = resolve_user_locale(
            user_repo=self.scenes.repos.user_repo, user_id=self.scenes.actor.id
        )
        return SceneError(
            "REPORT_ATTACHMENT_" + reason.upper(), tr("report_attachment." + reason, locale), status
        )

    def _access(self, organization_id: str, run_id: str, *, owner_only: bool = False) -> None:
        try:
            self.repo.check_access(
                organization_id, run_id, self.scenes.actor.id, owner_only=owner_only
            )
        except AcceptanceStoreError as exc:
            raise self._error("forbidden", 403) from exc

    def get(self, organization_id: str, run_id: str) -> dict[str, Any] | None:
        self._access(organization_id, run_id)
        result = self.repo.get(organization_id, run_id)
        self._access(organization_id, run_id)
        return result

    def _source(self, organization_id: str, run_id: str, report_run_id: str) -> dict[str, Any]:
        self._access(organization_id, run_id, owner_only=True)
        scene_run = self.scenes.repos.enterprise_scene_repo.get_run(run_id)
        if scene_run is None or scene_run["status"] != "succeeded":
            raise self._error("invalid", 409)
        try:
            self.assistant.require("report")
            self.assistant.require("inspection")
            report = self.assistant.run_owned(report_run_id)
            self.assistant.agent(str(scene_run["agent_id"]))
        except WorkflowError as exc:
            raise self._error("forbidden", 403) from exc
        job = report["snapshot"].get("job", {})
        sources = [item for item in report["evidence"] if item.get("type") == "source"]
        if (
            report["status"] != "awaiting_approval"
            or job.get("kind") != "report"
            or job.get("agent_id") != scene_run["agent_id"]
            or not sources
            or any(item.get("kind") != "inspection" for item in sources)
            or not report["content"].strip()
            or len(report["content"]) > 40000
            or digest(report["content"], report["snapshot"]) != report["content_hash"]
        ):
            raise self._error("invalid", 409)
        return report

    def preview(self, organization_id: str, run_id: str, report_run_id: str) -> dict[str, Any]:
        report = self._source(organization_id, run_id, report_run_id)
        return {
            "report_run_id": report["id"],
            "report_version": report["version"],
            "report_content_hash": report["content_hash"],
            "content": report["content"],
        }

    def attach(
        self,
        organization_id: str,
        run_id: str,
        *,
        report_run_id: str,
        expected_report_version: int,
        expected_content_hash: str,
        share_with_reviewer: bool,
    ) -> dict[str, Any]:
        if share_with_reviewer is not True:
            raise self._error("invalid", 422)
        report = self._source(organization_id, run_id, report_run_id)
        if (
            report["version"] != expected_report_version
            or report["content_hash"] != expected_content_hash
        ):
            raise self._error("conflict", 409)
        try:
            return self.repo.attach(
                organization_id=organization_id,
                run_id=run_id,
                actor_id=self.scenes.actor.id,
                report=report,
            )
        except AcceptanceStoreError as exc:
            if exc.reason in {"forbidden", "not_found"}:
                raise self._error("forbidden", 403) from exc
            raise self._error("conflict", 409) from exc
