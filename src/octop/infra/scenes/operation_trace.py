"""A bounded view of existing server audit facts, never reconstructed human intent."""

from __future__ import annotations

import json
import time
from typing import Any

from octop.infra.db.repos.scene_acceptance import snapshot_hash
from octop.infra.gateway.threads import ThreadRegistry
from octop.infra.scenes.delivery_evidence import authorized_delivery_run
from octop.infra.scenes.service import EnterpriseSceneService, SceneError

SCENE_ACTIONS = (
    "scene.run_requested",
    "scene.run_approved",
    "scene.run_rejected",
    "scene.run.delivery_reviewed",
    "scene.mattermost_publication.proposed",
    "scene.mattermost_publication.approved_locally",
    "scene.mattermost_publication.revoked",
)
CRON_ACTIONS = ("cron.run_ok", "cron.run_failed")


class SceneOperationTraceService:
    LIMIT = 100

    def __init__(self, scenes: EnterpriseSceneService) -> None:
        self.scenes = scenes

    def read(self, organization_id: str, run_id: str) -> dict[str, Any]:
        run = authorized_delivery_run(self.scenes, organization_id, run_id, reconcile=False)
        expected = snapshot_hash(run)
        cron = self.scenes.repos.cron_repo.get(run["cron_id"]) if run.get("cron_id") else None
        if cron and (
            cron.agent_id != run["agent_id"]
            or cron.user_id != run["requested_by"]
            or cron.session_key
            != ThreadRegistry.make_key(
                agent_id=run["agent_id"],
                channel_type="cron",
                channel_subject_id=cron.cron_id,
                channel_chat_type=ThreadRegistry.CHAT_TYPE_DM,
            )
        ):
            cron = None
        rows = self.scenes.repos.audit_repo.for_target(run_id, SCENE_ACTIONS, limit=self.LIMIT + 1)
        if cron:
            rows += self.scenes.repos.audit_repo.for_target(
                cron.cron_id, CRON_ACTIONS, limit=self.LIMIT + 1
            )
        rows.sort(key=lambda row: row.id, reverse=True)
        events = []
        for row in rows[: self.LIMIT]:
            # These actions persist organization scope inside their audit payload.
            # Never return that raw payload (it may contain unrelated private data).
            if (
                row.action.startswith("scene.mattermost_publication.")
                or row.action == "scene.run.delivery_reviewed"
            ):
                try:
                    payload = json.loads(row.payload or "null")
                except ValueError:
                    continue
                if (
                    not isinstance(payload, dict)
                    or payload.get("organization_id") != organization_id
                ):
                    continue
            actor_id = (
                int(row.actor)
                if row.actor
                and len(row.actor) <= 16
                and row.actor.isascii()
                and row.actor.isdigit()
                else None
            )
            if actor_id is not None and actor_id <= 0:
                actor_id = None
            system = row.action in CRON_ACTIONS and row.actor == "_system"
            events.append(
                {
                    "record_id": row.id,
                    "recorded_at": row.ts,
                    "action": row.action,
                    "actor_kind": "system" if system else "user_account" if actor_id else "unknown",
                    "actor_user_id": actor_id,
                    "interaction_mode": "service_execution" if system else "not_recorded",
                }
            )
        current = authorized_delivery_run(self.scenes, organization_id, run_id, reconcile=False)
        if (
            snapshot_hash(current) != expected
            or current["agent_id"] != run["agent_id"]
            or (cron and self.scenes.repos.cron_repo.get(cron.cron_id) != cron)
        ):
            raise SceneError("SCENE_EVIDENCE_STALE", "run changed during trace retrieval", 409)
        snapshot = run.get("plan_snapshot")
        if not isinstance(snapshot, dict):
            snapshot = {}
        return {
            "organization_id": organization_id,
            "run_id": run_id,
            "as_of": int(time.time()),
            "events": events,
            "truncated": len(rows) > self.LIMIT,
            "history_complete": False,
            "execution_authorized": False,
            "execution_context": {
                "agent_id": run["agent_id"],
                "on_behalf_of_user_id": run["requested_by"],
                "bound_cron_id": cron.cron_id if cron else None,
                "configured_executor": (
                    "http_inspection"
                    if cron and isinstance(snapshot.get("inspection"), dict)
                    else "agent_task"
                    if cron and cron.task_type == "agent"
                    else "unknown"
                ),
                "schedule_kind": "one_shot"
                if cron and cron.trigger.startswith("date:")
                else "unknown",
            },
        }
