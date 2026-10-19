"""Approval-gated, one-shot enterprise scenes backed by existing Cron execution."""

from __future__ import annotations

import json
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
    def _configured_ids(raw: str | None) -> set[str]:
        try:
            values = json.loads(raw or "[]")
        except (TypeError, json.JSONDecodeError):
            return set()
        return (
            {str(value).strip() for value in values if str(value).strip()}
            if isinstance(values, list)
            else set()
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

    def request_run(
        self, organization_id: str, scene_id: str, *, plan_id: str | None = None
    ) -> dict[str, Any]:
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
        plan_snapshot = None
        prompt_snapshot = None
        if plan_id is not None:
            plan = self.repos.digital_worker_repo.get_plan(plan_id)
            if plan is None:
                raise SceneError("SCENE_PLAN_NOT_FOUND", "virtual worker plan not found", 404)
            goal = self.repos.digital_worker_repo.get_goal(str(plan["goal_id"]))
            worker = (
                self.repos.digital_worker_repo.get_worker(str(goal["worker_id"]))
                if goal is not None
                else None
            )
            if goal is None or worker is None or worker["organization_id"] != organization_id:
                raise SceneError("SCENE_PLAN_NOT_FOUND", "virtual worker plan not found", 404)
            if (
                worker["position_id"] != scene["position_id"]
                or worker["agent_id"] != scene["agent_id"]
            ):
                raise SceneError(
                    "SCENE_PLAN_SCENE_MISMATCH",
                    "plan worker must match the scene organization, position, and Agent",
                    409,
                )
            if (
                plan["status"] != "ready"
                or goal["status"] != "active"
                or worker["status"] != "active"
            ):
                raise SceneError(
                    "SCENE_PLAN_NOT_READY",
                    "worker, goal, and plan must be active and ready before execution request",
                    409,
                )
            worker_capabilities = worker.get("capabilities") or {}
            from octop.infra.knowledge.sources import (  # noqa: PLC0415
                canonical_knowledge_sources,
                legacy_akb_sources,
            )

            knowledge_sources = canonical_knowledge_sources(worker_capabilities)
            akb_sources = legacy_akb_sources(knowledge_sources)
            scene_mcp_servers = set(str(name) for name in scene.get("mcp_servers", []))
            for source in knowledge_sources:
                if (
                    not isinstance(source, dict)
                    or source.get("mcp_server") not in scene_mcp_servers
                    or source.get("access") != "read"
                    or not isinstance(source.get("provider"), str)
                    or not isinstance(source.get("knowledge_base_ref"), str)
                    or not isinstance(source.get("read_tools"), list)
                    or not source.get("read_tools")
                ):
                    raise SceneError(
                        "SCENE_KNOWLEDGE_SOURCE_NOT_AUTHORIZED",
                        "a knowledge source is invalid or not enabled for the scene",
                        409,
                    )
            approved_skill_ids = set(worker_capabilities.get("skill_package_ids", []))
            approved_knowledge_ids = set(worker_capabilities.get("knowledge_base_ids", []))
            enabled_skill_ids = self._configured_ids(agent.skill_package_ids)
            enabled_knowledge_ids = self._configured_ids(agent.knowledge_base_ids)
            effective_capabilities = {
                "skills": worker_capabilities.get("skills", []),
                "knowledge": worker_capabilities.get("knowledge", []),
                "operating_guidance": worker_capabilities.get("operating_guidance", []),
                "skill_package_ids": sorted(approved_skill_ids & enabled_skill_ids),
                "knowledge_base_ids": sorted(approved_knowledge_ids & enabled_knowledge_ids),
                "knowledge_sources": knowledge_sources,
                "akb_knowledge_sources": akb_sources,
            }
            plan_snapshot = {
                "worker": {
                    "worker_id": worker["worker_id"],
                    "name": worker["name"],
                    "responsibilities": worker["responsibilities"],
                    "capability_version": worker["capability_version"],
                },
                "capabilities": effective_capabilities,
                "goal": {
                    "goal_id": goal["goal_id"],
                    "title": goal["title"],
                    "description": goal["description"],
                    "success_criteria": goal["success_criteria"],
                    "period_start": goal["period_start"],
                    "period_end": goal["period_end"],
                },
                "plan": {
                    "plan_id": plan["plan_id"],
                    "title": plan["title"],
                    "approach": plan["approach"],
                    "steps": plan["steps"],
                    "version": plan["version"],
                },
            }
            step_text = "\n".join(
                f"{index}. {step['action']}\n   Expected output: {step['expected_output']}"
                for index, step in enumerate(plan["steps"], start=1)
            )
            capability_text = "\n".join(
                (
                    f"Skills: {', '.join(effective_capabilities['skills']) or 'none'}",
                    f"Knowledge topics: {', '.join(effective_capabilities['knowledge']) or 'none'}",
                    "Operating guidance: "
                    f"{'; '.join(effective_capabilities['operating_guidance']) or 'none'}",
                    "Pre-authorized skill package IDs: "
                    f"{', '.join(effective_capabilities['skill_package_ids']) or 'none'}",
                    "Pre-authorized knowledge base IDs: "
                    f"{', '.join(effective_capabilities['knowledge_base_ids']) or 'none'}",
                    "Pre-authorized product knowledge sources: "
                    + (
                        "; ".join(
                            f"{source.get('provider', '')}: {source.get('mcp_server', '')} / "
                            f"{source.get('knowledge_base_ref', '')}; "
                            f"tools={','.join(source.get('read_tools', []))}"
                            for source in knowledge_sources
                        )
                        or "none"
                    ),
                )
            )
            prompt_snapshot = (
                f"{scene['prompt'].rstrip()}\n\n"
                "Virtual worker plan submitted for human approval:\n"
                f"Worker: {worker['name']}\n"
                f"Responsibilities: {worker['responsibilities']}\n"
                f"Reviewed capability profile version: {worker['capability_version']}\n"
                f"Reviewed capabilities:\n{capability_text}\n"
                f"Goal: {goal['title']}\n"
                f"Goal success criteria: {goal['success_criteria']}\n"
                f"Plan: {plan['title']}\n"
                f"Approach: {plan['approach']}\n"
                f"Steps:\n{step_text}\n\n"
                "Return a concise result mapped to the goal success criteria and expected outputs. "
                "Use only the Agent, skill packages, knowledge bases, and connectors already "
                "authorized for this scene. For a product knowledge source, use only its listed "
                "MCP server and approved read tools and knowledge-base reference. "
                "Capability references do not expand access."
            )
        run = self.repos.enterprise_scene_repo.create_run(
            run_id=new_ulid(),
            scene=scene,
            requested_by=self.actor.id,
            plan_id=plan_id,
            plan_snapshot=plan_snapshot,
            prompt_snapshot=prompt_snapshot,
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
        if cron is None:
            # A missing job cannot produce evidence of success. Record a terminal
            # failure rather than leaving the run queued forever after deletion.
            self.repos.enterprise_scene_repo.mark_failed(
                str(row["run_id"]),
                summary="Cron job disappeared before completion",
                cron_id=str(row["cron_id"]),
            )
            return
        if cron.last_status not in {"ok", "error"}:
            return
        self.repos.enterprise_scene_repo.sync_cron_status(
            str(row["run_id"]),
            cron_id=str(row["cron_id"]),
            status="succeeded" if cron.last_status == "ok" else "failed",
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

        requester_id = int(run["requested_by"])
        requester = self.repos.user_repo.get(requester_id)
        if requester is None or requester.disabled:
            raise SceneError("SCENE_REQUESTER_UNAVAILABLE", "requester is inactive", 409)
        if not requester.is_admin and self.repos.organization_repo.get_member(
            organization_id, requester_id
        ) is None:
            raise SceneError(
                "SCENE_REQUESTER_FORBIDDEN", "requester left the organization", 409
            )
        agent = self.repos.agent_repo.get(str(run["agent_id"]))
        if agent is not None and not requester.is_admin and (
            agent.user_id != requester_id and not agent.is_shared
        ):
            raise SceneError(
                "SCENE_REQUESTER_AGENT_FORBIDDEN",
                "requester no longer has Agent access",
                409,
            )
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

        plan_snapshot = run.get("plan_snapshot") or {}
        capabilities = plan_snapshot.get("capabilities") or {}
        if capabilities:
            configured_skills = self._configured_ids(agent.skill_package_ids)
            configured_knowledge = self._configured_ids(agent.knowledge_base_ids)
            skill_refs = set(capabilities.get("skill_package_ids", []))
            knowledge_refs = set(capabilities.get("knowledge_base_ids", []))
            if (
                not skill_refs.issubset(configured_skills)
                or not knowledge_refs.issubset(configured_knowledge)
                or any(self.repos.skill_package_repo.get(item) is None for item in skill_refs)
                or any(
                    (base := self.repos.knowledge_repo.get_base(item)) is None
                    or (
                        not requester.is_admin
                        and base.owner_user_id != requester_id
                        and not base.shared
                    )
                    for item in knowledge_refs
                )
            ):
                self.repos.enterprise_scene_repo.mark_failed(
                    run_id,
                    summary="Virtual worker capability resource authorization changed",
                )
                self.repos.audit_repo.write(
                    actor=str(self.actor.id),
                    action="scene.run_failed",
                    target=run_id,
                    payload="capability resource authorization changed",
                )
                raise SceneError(
                    "SCENE_CAPABILITY_RESOURCE_CHANGED",
                    "Agent skill or knowledge authorization changed; submit a new run request",
                    409,
                )
            from octop.infra.knowledge.sources import canonical_knowledge_sources  # noqa: PLC0415

            knowledge_sources = canonical_knowledge_sources(capabilities)
            worker_id = str((plan_snapshot.get("worker") or {}).get("worker_id") or "")
            current_worker = self.repos.digital_worker_repo.get_worker(worker_id)
            current_sources = canonical_knowledge_sources(
                (current_worker or {}).get("capabilities", {})
            )
            current_source_keys = {
                json.dumps(source, sort_keys=True)
                for source in current_sources
                if isinstance(source, dict)
            } if isinstance(current_sources, list) else set()
            source_invalid = (
                not isinstance(knowledge_sources, list)
                or any(
                    not isinstance(source, dict)
                    or source.get("access") != "read"
                    or not isinstance(source.get("provider"), str)
                    or not isinstance(source.get("knowledge_base_ref"), str)
                    or not isinstance(source.get("read_tools"), list)
                    or not source.get("read_tools")
                    or source.get("mcp_server") not in set(run.get("mcp_servers", []))
                    or json.dumps(source, sort_keys=True) not in current_source_keys
                    for source in knowledge_sources
                )
            )
            if source_invalid:
                self.repos.enterprise_scene_repo.mark_failed(
                    run_id, summary="Virtual worker knowledge-source authorization changed"
                )
                self.repos.audit_repo.write(
                    actor=str(self.actor.id),
                    action="scene.run_failed",
                    target=run_id,
                    payload="knowledge-source authorization changed",
                )
                raise SceneError(
                    "SCENE_KNOWLEDGE_SOURCE_AUTHORIZATION_CHANGED",
                    "knowledge-source authorization changed; submit a new run request",
                    409,
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
            try:
                await manager.run_now(cron_id)
            except Exception:
                # The DB transition is already committed. A dispatch error
                # cannot prove that execution did not start, so retain the
                # job and return its durable queued state for reconciliation.
                self.repos.audit_repo.write(
                    actor=str(self.actor.id),
                    action="scene.run_dispatch_uncertain",
                    target=run_id,
                )
                return self.get_run(organization_id, run_id)
        except Exception:
            # The task may already have started when dispatch reports an error.
            # Do not delete a queued job here: cancellation is not guaranteed
            # and could race with the running task. Preserve the cron identity
            # for reconciliation and operator investigation.
            current = self.repos.enterprise_scene_repo.get_run(run_id)
            if current is None or current["status"] == "awaiting_approval":
                self.repos.enterprise_scene_repo.mark_failed(
                    run_id, summary="Approval execution could not be queued"
                )
                if manager.get(cron_id) is not None:
                    await manager.delete(cron_id)
            raise
        self.repos.audit_repo.write(
            actor=str(self.actor.id), action="scene.run_approved", target=run_id
        )
        return self.get_run(organization_id, run_id)
