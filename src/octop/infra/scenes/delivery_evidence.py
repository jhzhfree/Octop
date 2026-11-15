"""Authorized delivery reads; discard content when authority changes during I/O."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from typing import Any

from octop.infra.db.repos.scene_acceptance import snapshot_hash
from octop.infra.db.repos.scene_inspection import SceneInspectionRepo
from octop.infra.scenes.service import EnterpriseSceneService, SceneError
from octop.infra.users.identity import User


class SceneDeliveryEvidenceService:
    def __init__(
        self,
        scenes: EnterpriseSceneService,
        loader: Callable[[dict[str, Any]], Awaitable[str | None]],
    ) -> None:
        self.scenes = scenes
        self.loader = loader

    def _authorized_run(self, organization_id: str, run_id: str) -> dict[str, Any]:
        # The request's User may predate a disable, role change or permission revoke.
        row = self.scenes.repos.user_repo.get(self.scenes.actor.id)
        if row is None or row.disabled or (row.role != "admin" and "tasks" not in row.permissions):
            raise SceneError("SCENE_EVIDENCE_FORBIDDEN", "scene run evidence access denied", 403)
        actor = User(
            id=row.id,
            username=row.username,
            role=row.role,
            display_name=row.display_name,
            permissions=row.permissions,
        )
        return EnterpriseSceneService(self.scenes.repos, actor).get_run_evidence_access(
            organization_id, run_id
        )

    async def read(self, organization_id: str, run_id: str) -> dict[str, Any]:
        run = self._authorized_run(organization_id, run_id)
        expected = snapshot_hash(run)
        answer = await self.loader(run) if run["status"] == "succeeded" else None
        current = self._authorized_run(organization_id, run_id)
        if snapshot_hash(current) != expected:
            raise SceneError("SCENE_EVIDENCE_STALE", "run changed during evidence retrieval", 409)
        if answer is not None and run.get("inspection_report_sha256"):
            report = SceneInspectionRepo(self.scenes.repos.db).get(run_id)
            if (
                report is None
                or report["status"] != "reported"
                or report["content"] != answer
                or report["content_sha256"] != run["inspection_report_sha256"]
                or hashlib.sha256(answer.encode()).hexdigest() != run["inspection_report_sha256"]
            ):
                raise SceneError("SCENE_EVIDENCE_STALE", "inspection report integrity changed", 409)
        return {"run_id": run_id, "available": answer is not None, "answer": answer}
