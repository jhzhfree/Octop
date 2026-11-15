"""Human review of server-resolved delivery summaries; never model self-acceptance."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from typing import Any

from octop.infra.db.repos.scene_acceptance import (
    AcceptanceStoreError,
    SceneAcceptanceRepo,
    snapshot_hash,
)
from octop.infra.db.repos.scene_report_attachment import SceneReportAttachmentRepo, attachment_hash
from octop.infra.scenes.service import EnterpriseSceneService, SceneError

EvidenceLoader = Callable[[dict[str, Any]], Awaitable[str | None]]


class SceneAcceptanceService:
    def __init__(
        self,
        scenes: EnterpriseSceneService,
        repo: SceneAcceptanceRepo,
        evidence_loader: EvidenceLoader,
    ) -> None:
        self.scenes = scenes
        self.repo = repo
        # Server-owned adapter around scene_run_evidence; never request-supplied text.
        self.evidence_loader = evidence_loader

    def _authorized_run(self, organization_id: str, run_id: str) -> dict[str, Any]:
        try:
            self.repo.check_reviewer(organization_id, run_id, self.scenes.actor.id)
        except AcceptanceStoreError as exc:
            status = 404 if exc.reason == "not_found" else 403
            code = "SCENE_NOT_FOUND" if status == 404 else "ACCEPTANCE_FORBIDDEN"
            raise SceneError(code, str(exc), status) from exc
        self.scenes._require_manager(organization_id)
        run = self.scenes.get_run_evidence_access(organization_id, run_id)
        if self.scenes.actor.id == run["requested_by"]:
            raise SceneError("ACCEPTANCE_FORBIDDEN", "requester cannot accept own delivery", 403)
        if run["status"] != "succeeded":
            raise SceneError("ACCEPTANCE_NOT_READY", "execution must succeed before review", 409)
        return run

    async def review(self, organization_id: str, run_id: str) -> dict[str, Any]:
        run = self._authorized_run(organization_id, run_id)
        answer = await self.evidence_loader(run)
        if not answer or not answer.strip():
            raise SceneError("ACCEPTANCE_NO_EVIDENCE", "delivery summary unavailable", 409)
        attachment_digest = run.get("report_attachment_sha256")
        if attachment_digest:
            attachment = SceneReportAttachmentRepo(self.repo.db).get(organization_id, run_id)
            if (
                attachment is None
                or attachment["attachment_sha256"] != attachment_digest
                or attachment_hash(attachment) != attachment_digest
            ):
                raise SceneError("ACCEPTANCE_STALE", "report attachment integrity changed", 409)
            # Stable protocol marker: sharing is manual, not proof the Agent generated this report.
            answer += (
                "\n\n[owner_shared_report_snapshot "
                + attachment_digest
                + "]\n"
                + attachment["content"]
            )
        # Permission and snapshot must still hold after asynchronous evidence I/O.
        current = self._authorized_run(organization_id, run_id)
        if snapshot_hash(current) != snapshot_hash(run):
            raise SceneError("ACCEPTANCE_STALE", "run changed during evidence retrieval", 409)
        return {
            "run_id": run_id,
            "answer": answer,
            "evidence_sha256": hashlib.sha256(answer.encode()).hexdigest(),
            "evidence_length": len(answer),
            "snapshot_sha256": snapshot_hash(run),
            "acceptance": self.repo.get(organization_id, run_id),
        }

    async def decide(
        self,
        organization_id: str,
        run_id: str,
        *,
        decision: str,
        expected_evidence_sha256: str,
        expected_snapshot_sha256: str,
    ) -> dict[str, Any]:
        if decision not in {"accepted", "changes_requested"}:
            raise SceneError("ACCEPTANCE_INVALID", "invalid acceptance decision", 422)
        review = await self.review(organization_id, run_id)
        if (
            review["evidence_sha256"] != expected_evidence_sha256
            or review["snapshot_sha256"] != expected_snapshot_sha256
        ):
            raise SceneError("ACCEPTANCE_STALE", "reviewed evidence or run changed", 409)
        try:
            return self.repo.decide(
                organization_id=organization_id,
                run_id=run_id,
                reviewer_id=self.scenes.actor.id,
                decision=decision,
                evidence_sha256=review["evidence_sha256"],
                evidence_length=review["evidence_length"],
                expected_snapshot_hash=review["snapshot_sha256"],
            )
        except AcceptanceStoreError as exc:
            code, status = {
                "invalid": ("ACCEPTANCE_INVALID", 422),
                "not_found": ("SCENE_NOT_FOUND", 404),
                "forbidden": ("ACCEPTANCE_FORBIDDEN", 403),
                "stale": ("ACCEPTANCE_STALE", 409),
                "conflict": ("ACCEPTANCE_CONFLICT", 409),
            }[exc.reason]
            raise SceneError(code, str(exc), status) from exc
