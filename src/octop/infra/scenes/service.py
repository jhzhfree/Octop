"""Approval-gated, one-shot enterprise scenes backed by existing Cron execution."""

from __future__ import annotations

from typing import Any

from octop.infra.db.services import RepoBundle
from octop.infra.gateway.threads import ThreadRegistry
from octop.infra.users.identity import User
from octop.infra.utils.ulid import new_ulid


class SceneError(Exception):
    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class EnterpriseSceneService:
    def __init__(self, repos: RepoBundle, actor: User) -> None:
        self.repos = repos
        self.actor = actor

    def _member_role(self, organization_id: str) -> str:
        if self.actor.is_admin:
            return "owner"
        member = self.repos.organization_repo.get_member(organization_id, self.actor.id)
        if member is None:
            raise SceneError("SCENE_FORBIDDEN", "organization access denied", 403)
        return str(member["role"])

    def _require_manager(self, organization_id: str) -> None:
        if self._member_role(organization_id) not in {"owner", "manager"}:
            raise SceneError("SCENE_FORBIDDEN", "organization manager required", 403)

    def _scene(self, organization_id: str, scene_id: str) -> dict[str, Any]:
        self._member_role(organization_id)
        row = self.repos.enterprise_scene_repo.get(scene_id)
        if row is None or row["organization_id"] != organization_id:
            raise SceneError("SCENE_NOT_FOUND", "scene not found", 404)
        return row

    def _assigned(self, organization_id: str, position_id: str, agent_id: str) -> bool:
        position = self.repos.organization_repo.get_position(position_id)
        if position is None or position["organization_id"] != organization_id:
            return False
        return any(
            item["position_id"] == position_id
            and item["subject_type"] == "agent"
            and item["subject_id"] == agent_id
            for item in self.repos.organization_repo.list_assignments(organization_id)
        )

    @staticmethod
    def _public(row: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in row.items() if key not in {"id", "mcp_servers_json"}}

    def list_scenes(self, organization_id: str) -> list[dict[str, Any]]:
        self._member_role(organization_id)
        return [
            self._public(row)
            for row in self.repos.enterprise_scene_repo.list_for_org(organization_id)
        ]

    def get_scene(self, organization_id: str, scene_id: str) -> dict[str, Any]:
        return self._public(self._scene(organization_id, scene_id))

    def create_scene(
        self,
        organization_id: str,
        *,
        position_id: str,
        agent_id: str,
        name: str,
        prompt: str,
        mcp_servers: list[str],
    ) -> dict[str, Any]:
        self._require_manager(organization_id)
        if not self._assigned(organization_id, position_id, agent_id):
            raise SceneError(
                "SCENE_AGENT_NOT_ASSIGNED", "Agent must be appointed to this position", 409
            )
        agent = self.repos.agent_repo.get(agent_id)
        if agent is None or not agent.enabled:
            raise SceneError("SCENE_AGENT_UNAVAILABLE", "Agent is unavailable", 409)
        row = self.repos.enterprise_scene_repo.create(
            scene_id=new_ulid(),
            organization_id=organization_id,
            position_id=position_id,
            agent_id=agent_id,
            owner_user_id=self.actor.id,
            name=name.strip(),
            prompt=prompt.strip(),
            mcp_servers=mcp_servers,
        )
        self.repos.audit_repo.write(
            actor=str(self.actor.id),
            action="scene.create",
            target=row["scene_id"],
            payload=row["name"],
        )
        return self._public(row)

    def update_scene(
        self, organization_id: str, scene_id: str, updates: dict[str, Any], *, expected_version: int
    ) -> dict[str, Any]:
        self._require_manager(organization_id)
        self._scene(organization_id, scene_id)
        if "enabled" in updates and updates["enabled"] is None:
            raise SceneError("SCENE_INVALID", "enabled cannot be null", 422)
        for key in ("name", "prompt"):
            if key in updates:
                if updates[key] is None or not str(updates[key]).strip():
                    raise SceneError("SCENE_INVALID", f"{key} cannot be empty", 422)
                updates[key] = str(updates[key]).strip()
        row = self.repos.enterprise_scene_repo.update(
            scene_id, updates, expected_version=expected_version, actor_id=self.actor.id
        )
        if row is None:
            raise SceneError("SCENE_VERSION_CONFLICT", "scene changed since it was loaded", 409)
        self.repos.audit_repo.write(
            actor=str(self.actor.id), action="scene.update", target=scene_id
        )
        return self._public(row)

    def list_scene_versions(self, organization_id: str, scene_id: str) -> list[dict[str, Any]]:
        self._scene(organization_id, scene_id)
        return self.repos.enterprise_scene_repo.list_versions(scene_id)

    def request_run(self, organization_id: str, scene_id: str) -> dict[str, Any]:
        self._member_role(organization_id)
        scene = self._scene(organization_id, scene_id)
        if not scene["enabled"]:
            raise SceneError("SCENE_DISABLED", "scene is disabled", 409)
        agent = self.repos.agent_repo.get(str(scene["agent_id"]))
        if agent is None or not agent.enabled:
            raise SceneError("SCENE_AGENT_UNAVAILABLE", "Agent is unavailable", 409)
        if not self.actor.is_admin and agent.user_id != self.actor.id and not agent.is_shared:
            raise SceneError("SCENE_AGENT_FORBIDDEN", "Agent access is not granted", 403)
        if not self._assigned(organization_id, scene["position_id"], scene["agent_id"]):
            raise SceneError(
                "SCENE_AGENT_NOT_ASSIGNED", "Agent is no longer appointed to this position", 409
            )
        run = self.repos.enterprise_scene_repo.create_run(
            run_id=new_ulid(), scene=scene, requested_by=self.actor.id
        )
        self.repos.audit_repo.write(
            actor=str(self.actor.id), action="scene.run_requested", target=run["run_id"]
        )
        return self._public(run)

    def _run(self, organization_id: str, run_id: str) -> dict[str, Any]:
        self._member_role(organization_id)
        row = self.repos.enterprise_scene_repo.get_run(run_id)
        if row is None or row["organization_id"] != organization_id:
            raise SceneError("SCENE_RUN_NOT_FOUND", "scene run not found", 404)
        return row

    def list_runs(
        self, organization_id: str, *, scene_id: str | None = None
    ) -> list[dict[str, Any]]:
        self._member_role(organization_id)
        rows = self.repos.enterprise_scene_repo.list_runs(organization_id, scene_id=scene_id)
        for row in rows:
            self._sync_status(row)
        return [
            self._public(self.repos.enterprise_scene_repo.get_run(str(row["run_id"])) or row)
            for row in rows
        ]

    def get_run(self, organization_id: str, run_id: str) -> dict[str, Any]:
        row = self._run(organization_id, run_id)
        self._sync_status(row)
        return self._public(self.repos.enterprise_scene_repo.get_run(run_id) or row)

    def get_run_evidence_access(self, organization_id: str, run_id: str) -> dict[str, Any]:
        row = self._run(organization_id, run_id)
        if not self.actor.is_admin and self.actor.id not in {
            int(row["requested_by"]),
            int(row["approved_by"]) if row["approved_by"] is not None else None,
        }:
            raise SceneError("SCENE_EVIDENCE_FORBIDDEN", "scene run evidence access denied", 403)
        self._sync_status(row)
        return self._public(self.repos.enterprise_scene_repo.get_run(run_id) or row)

    def _sync_status(self, row: dict[str, Any]) -> None:
        if row["status"] != "queued" or not row["cron_id"]:
            return
        cron = self.repos.cron_repo.get(str(row["cron_id"]))
        if cron is None or cron.last_status is None:
            return
        self.repos.enterprise_scene_repo.sync_cron_status(
            str(row["run_id"]), status="succeeded" if cron.last_status == "ok" else "failed"
        )

    async def decide_run(
        self, organization_id: str, run_id: str, *, approve: bool, note: str, server: Any
    ) -> dict[str, Any]:
        self._require_manager(organization_id)
        run = self._run(organization_id, run_id)
        if run["status"] != "awaiting_approval":
            raise SceneError("SCENE_RUN_ALREADY_DECIDED", "run is no longer awaiting approval", 409)
        if int(run["requested_by"]) == self.actor.id:
            raise SceneError("SCENE_SELF_APPROVAL", "requester cannot approve their own run", 403)
        if not approve:
            if not self.repos.enterprise_scene_repo.decide_run(
                run_id,
                approved_by=self.actor.id,
                cron_id=None,
                status="rejected",
                note=note.strip(),
            ):
                raise SceneError("SCENE_RUN_ALREADY_DECIDED", "run was already decided", 409)
            self.repos.audit_repo.write(
                actor=str(self.actor.id), action="scene.run_rejected", target=run_id
            )
            return self.get_run(organization_id, run_id)

        agent = self.repos.agent_repo.get(str(run["agent_id"]))
        if agent is None or not agent.enabled:
            self.repos.enterprise_scene_repo.mark_failed(run_id, summary="Agent is unavailable")
            raise SceneError("SCENE_AGENT_UNAVAILABLE", "Agent is unavailable", 409)
        if not self._assigned(organization_id, str(run["position_id"]), str(run["agent_id"])):
            self.repos.enterprise_scene_repo.mark_failed(
                run_id, summary="Agent appointment changed"
            )
            raise SceneError(
                "SCENE_AGENT_NOT_ASSIGNED", "Agent appointment changed before approval", 409
            )

        from octop.infra.cron.manager import CronCreateSpec  # noqa: PLC0415

        cron_id = new_ulid()
        manager = server.app_runtime.cron_manager
        try:
            await manager.create(
                CronCreateSpec(
                    cron_id=cron_id,
                    agent_id=str(run["agent_id"]),
                    user_id=int(run["requested_by"]),
                    session_key=ThreadRegistry.make_key(
                        agent_id=str(run["agent_id"]),
                        channel_type="cron",
                        channel_subject_id=cron_id,
                        channel_chat_type=ThreadRegistry.CHAT_TYPE_DM,
                    ),
                    name=f"Scene: {run['scene_name']}",
                    trigger="date:2099-01-01T00:00:00",
                    prompt=str(run["prompt_snapshot"]),
                    task_type="agent",
                    mcp_servers=list(run["mcp_servers"]),
                    mcp_servers_exact=True,
                    enabled=False,
                    username=str(self.actor.id),
                )
            )
            from octop.api.common.validators import validate_chat_mcp_servers  # noqa: PLC0415

            authorized = await validate_chat_mcp_servers(
                server, user_id=int(run["requested_by"]), names=list(run["mcp_servers"])
            )
            if sorted(authorized or []) != sorted(run["mcp_servers"]):
                raise SceneError(
                    "SCENE_AUTHORIZATION_CHANGED", "Connector authorization changed", 409
                )
            if not self.repos.enterprise_scene_repo.decide_run(
                run_id,
                approved_by=self.actor.id,
                cron_id=cron_id,
                status="queued",
                note=note.strip(),
            ):
                await manager.delete(cron_id)
                raise SceneError("SCENE_RUN_ALREADY_DECIDED", "run was already decided", 409)
            await manager.run_now(cron_id)
        except Exception:
            self.repos.enterprise_scene_repo.mark_failed(
                run_id, summary="Approval execution could not be queued", cron_id=cron_id
            )
            if manager.get(cron_id) is not None:
                await manager.delete(cron_id)
            raise
        self.repos.audit_repo.write(
            actor=str(self.actor.id), action="scene.run_approved", target=run_id
        )
        return self.get_run(organization_id, run_id)
