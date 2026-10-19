"""Business rules for virtual worker profiles and measurable stage goals."""

from __future__ import annotations

import json
from typing import Any

from octop.infra.db.services import RepoBundle
from octop.infra.users.identity import User
from octop.infra.utils.ulid import new_ulid


class DigitalWorkerError(Exception):
    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class DigitalWorkerService:
    def __init__(self, repos: RepoBundle, actor: User) -> None:
        self.repos = repos
        self.actor = actor

    def _audit(self, action: str, target: str) -> None:
        self.repos.audit_repo.write(actor=str(self.actor.id), action=action, target=target)

    @staticmethod
    def _required_text(value: str, field: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise DigitalWorkerError(
                "DIGITAL_WORKER_FIELD_REQUIRED", f"{field} cannot be blank", 422
            )
        return normalized

    def _organization_role(self, organization_id: str) -> str:
        organization = self.repos.organization_repo.get(organization_id)
        if organization is None:
            raise DigitalWorkerError("ORGANIZATION_NOT_FOUND", "organization not found", 404)
        if self.actor.is_admin:
            return "owner"
        member = self.repos.organization_repo.get_member(organization_id, self.actor.id)
        if member is None:
            raise DigitalWorkerError("ORGANIZATION_FORBIDDEN", "organization access denied", 403)
        return str(member["role"])

    def _require_manager(self, organization_id: str) -> None:
        if self._organization_role(organization_id) not in {"owner", "manager"}:
            raise DigitalWorkerError("ORGANIZATION_FORBIDDEN", "organization manager required", 403)

    def _worker(self, organization_id: str, worker_id: str) -> dict[str, Any]:
        self._organization_role(organization_id)
        row = self.repos.digital_worker_repo.get_worker(worker_id)
        if row is None or row["organization_id"] != organization_id:
            raise DigitalWorkerError("DIGITAL_WORKER_NOT_FOUND", "digital worker not found", 404)
        return row

    def _is_appointed(self, organization_id: str, position_id: str, agent_id: str) -> bool:
        return any(
            item["position_id"] == position_id
            and item["subject_type"] == "agent"
            and item["subject_id"] == agent_id
            for item in self.repos.organization_repo.list_assignments(organization_id)
        )

    def list_workers(self, organization_id: str) -> list[dict[str, Any]]:
        self._organization_role(organization_id)
        return self.repos.digital_worker_repo.list_workers(organization_id)

    def create_worker(
        self,
        organization_id: str,
        *,
        position_id: str,
        agent_id: str,
        name: str,
        responsibilities: str | None = None,
    ) -> dict[str, Any]:
        self._require_manager(organization_id)
        position = self.repos.organization_repo.get_position(position_id)
        if position is None or position["organization_id"] != organization_id:
            raise DigitalWorkerError("ORGANIZATION_POSITION_NOT_FOUND", "position not found", 404)
        if not self._is_appointed(organization_id, position_id, agent_id):
            raise DigitalWorkerError(
                "DIGITAL_WORKER_AGENT_NOT_APPOINTED",
                "Agent must be appointed to the selected position",
                409,
            )
        agent = self.repos.agent_repo.get(agent_id)
        if agent is None or not agent.enabled:
            raise DigitalWorkerError("DIGITAL_WORKER_AGENT_INVALID", "enabled Agent not found", 404)
        if not self.actor.is_admin and agent.user_id != self.actor.id and not agent.is_shared:
            raise DigitalWorkerError("ORGANIZATION_FORBIDDEN", "Agent access is not granted", 403)
        created = self.repos.digital_worker_repo.create_worker(
            organization_id=organization_id,
            position_id=position_id,
            agent_id=agent_id,
            owner_user_id=self.actor.id,
            name=self._required_text(name, "name"),
            responsibilities=(
                responsibilities if responsibilities is not None else position["duties"]
            ).strip(),
        )
        if created is None:
            raise DigitalWorkerError(
                "DIGITAL_WORKER_EXISTS",
                "a digital worker already uses this Agent in the position",
                409,
            )
        self._audit("digital_worker.create", str(created["worker_id"]))
        return created

    def update_worker(
        self,
        organization_id: str,
        worker_id: str,
        *,
        expected_version: int,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_manager(organization_id)
        current = self._worker(organization_id, worker_id)
        if current["version"] != expected_version:
            raise DigitalWorkerError(
                "DIGITAL_WORKER_VERSION_CONFLICT", "worker profile was updated", 409
            )
        normalized = dict(updates)
        if "name" in normalized:
            normalized["name"] = self._required_text(normalized["name"], "name")
        if "responsibilities" in normalized:
            normalized["responsibilities"] = normalized["responsibilities"].strip()
        next_status = normalized.get("status")
        allowed_transitions = {
            "draft": {"active", "archived"},
            "active": {"paused", "archived"},
            "paused": {"active", "archived"},
            "archived": set(),
        }
        if (
            next_status
            and next_status != current["status"]
            and next_status not in allowed_transitions[current["status"]]
        ):
            raise DigitalWorkerError(
                "DIGITAL_WORKER_STATUS_INVALID", "invalid digital worker status transition", 409
            )
        if next_status == "active":
            agent = self.repos.agent_repo.get(str(current["agent_id"]))
            if (
                agent is None
                or not agent.enabled
                or not self._is_appointed(
                    organization_id, str(current["position_id"]), str(current["agent_id"])
                )
            ):
                raise DigitalWorkerError(
                    "DIGITAL_WORKER_AGENT_INVALID",
                    "Agent must be enabled and appointed to activate this profile",
                    409,
                )
        updated = self.repos.digital_worker_repo.update_worker(
            worker_id, normalized, expected_version=expected_version
        )
        if updated is None:
            raise DigitalWorkerError(
                "DIGITAL_WORKER_VERSION_CONFLICT", "worker profile was updated", 409
            )
        self._audit("digital_worker.update", worker_id)
        return updated

    def list_goals(self, organization_id: str, worker_id: str) -> list[dict[str, Any]]:
        self._worker(organization_id, worker_id)
        return self.repos.digital_worker_repo.list_goals(worker_id)

    def create_goal(
        self,
        organization_id: str,
        worker_id: str,
        *,
        title: str,
        description: str,
        success_criteria: str,
        period_start: str | None,
        period_end: str | None,
    ) -> dict[str, Any]:
        self._require_manager(organization_id)
        self._worker(organization_id, worker_id)
        if period_start and period_end and period_start > period_end:
            raise DigitalWorkerError(
                "DIGITAL_WORKER_GOAL_PERIOD_INVALID", "goal period is invalid", 422
            )
        goal = self.repos.digital_worker_repo.create_goal(
            worker_id=worker_id,
            title=self._required_text(title, "title"),
            description=description.strip(),
            success_criteria=self._required_text(success_criteria, "success_criteria"),
            period_start=period_start,
            period_end=period_end,
            created_by=self.actor.id,
        )
        self._audit("digital_worker.goal_create", str(goal["goal_id"]))
        return goal

    def update_goal(
        self, organization_id: str, worker_id: str, goal_id: str, updates: dict[str, Any]
    ) -> dict[str, Any]:
        self._require_manager(organization_id)
        self._worker(organization_id, worker_id)
        current = self.repos.digital_worker_repo.get_goal(goal_id)
        if current is None or current["worker_id"] != worker_id:
            raise DigitalWorkerError("DIGITAL_WORKER_GOAL_NOT_FOUND", "worker goal not found", 404)
        normalized = dict(updates)
        next_status = normalized.get("status")
        allowed_transitions = {
            "draft": {"active", "cancelled"},
            "active": {"completed", "cancelled"},
            "completed": set(),
            "cancelled": set(),
        }
        if (
            next_status
            and next_status != current["status"]
            and next_status not in allowed_transitions[current["status"]]
        ):
            raise DigitalWorkerError(
                "DIGITAL_WORKER_GOAL_STATUS_INVALID", "invalid goal status transition", 409
            )
        if current["status"] != "draft" and any(
            field in normalized
            for field in ("title", "description", "success_criteria", "period_start", "period_end")
        ):
            raise DigitalWorkerError(
                "DIGITAL_WORKER_GOAL_LOCKED", "only draft goals can be edited", 409
            )
        for field in ("title", "description", "success_criteria"):
            if field in normalized:
                normalized[field] = (
                    self._required_text(normalized[field], field)
                    if field in {"title", "success_criteria"}
                    else normalized[field].strip()
                )
        start = normalized.get("period_start", current["period_start"])
        end = normalized.get("period_end", current["period_end"])
        if start and end and start > end:
            raise DigitalWorkerError(
                "DIGITAL_WORKER_GOAL_PERIOD_INVALID", "goal period is invalid", 422
            )
        updated = self.repos.digital_worker_repo.update_goal(goal_id, normalized)
        if updated is None:
            raise DigitalWorkerError("DIGITAL_WORKER_GOAL_NOT_FOUND", "worker goal not found", 404)
        self._audit("digital_worker.goal_update", goal_id)
        return updated

    def delete_goal(self, organization_id: str, worker_id: str, goal_id: str) -> None:
        self._require_manager(organization_id)
        self._worker(organization_id, worker_id)
        goal = self.repos.digital_worker_repo.get_goal(goal_id)
        if goal is None or goal["worker_id"] != worker_id:
            raise DigitalWorkerError("DIGITAL_WORKER_GOAL_NOT_FOUND", "worker goal not found", 404)
        if goal["status"] != "draft":
            raise DigitalWorkerError(
                "DIGITAL_WORKER_GOAL_LOCKED", "only draft goals can be deleted", 409
            )
        self.repos.digital_worker_repo.delete_goal(goal_id)
        self._audit("digital_worker.goal_delete", goal_id)

    def _goal(self, organization_id: str, worker_id: str, goal_id: str) -> dict[str, Any]:
        self._worker(organization_id, worker_id)
        goal = self.repos.digital_worker_repo.get_goal(goal_id)
        if goal is None or goal["worker_id"] != worker_id:
            raise DigitalWorkerError("DIGITAL_WORKER_GOAL_NOT_FOUND", "worker goal not found", 404)
        return goal

    def _plan(
        self, organization_id: str, worker_id: str, goal_id: str, plan_id: str
    ) -> dict[str, Any]:
        self._goal(organization_id, worker_id, goal_id)
        plan = self.repos.digital_worker_repo.get_plan(plan_id)
        if plan is None or plan["goal_id"] != goal_id:
            raise DigitalWorkerError("DIGITAL_WORKER_PLAN_NOT_FOUND", "worker plan not found", 404)
        return plan

    def list_plans(
        self, organization_id: str, worker_id: str, goal_id: str
    ) -> list[dict[str, Any]]:
        self._goal(organization_id, worker_id, goal_id)
        return self.repos.digital_worker_repo.list_plans(goal_id)

    def create_plan(
        self,
        organization_id: str,
        worker_id: str,
        goal_id: str,
        *,
        title: str,
        approach: str,
        steps: list[dict[str, str]],
    ) -> dict[str, Any]:
        self._require_manager(organization_id)
        self._goal(organization_id, worker_id, goal_id)
        normalized_steps = [
            {
                "action": self._required_text(step["action"], "step action"),
                "expected_output": self._required_text(
                    step["expected_output"], "step expected_output"
                ),
            }
            for step in steps
        ]
        plan = self.repos.digital_worker_repo.create_plan(
            goal_id=goal_id,
            title=self._required_text(title, "title"),
            approach=approach.strip(),
            steps=normalized_steps,
            created_by=self.actor.id,
        )
        self._audit("digital_worker.plan_create", str(plan["plan_id"]))
        return plan

    def update_plan(
        self,
        organization_id: str,
        worker_id: str,
        goal_id: str,
        plan_id: str,
        *,
        expected_version: int,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_manager(organization_id)
        current = self._plan(organization_id, worker_id, goal_id, plan_id)
        if current["version"] != expected_version:
            raise DigitalWorkerError(
                "DIGITAL_WORKER_PLAN_VERSION_CONFLICT", "worker plan was updated", 409
            )
        normalized = dict(updates)
        if "title" in normalized:
            normalized["title"] = self._required_text(normalized["title"], "title")
        if "approach" in normalized:
            normalized["approach"] = normalized["approach"].strip()
        if "steps" in normalized:
            normalized["steps"] = [
                {
                    "action": self._required_text(step["action"], "step action"),
                    "expected_output": self._required_text(
                        step["expected_output"], "step expected_output"
                    ),
                }
                for step in normalized["steps"]
            ]
        next_status = normalized.get("status")
        transitions = {
            "draft": {"ready", "cancelled"},
            "ready": {"draft", "cancelled"},
            "cancelled": set(),
        }
        if (
            next_status
            and next_status != current["status"]
            and next_status not in transitions[current["status"]]
        ):
            raise DigitalWorkerError(
                "DIGITAL_WORKER_PLAN_STATUS_INVALID", "invalid worker plan status transition", 409
            )
        if current["status"] != "draft" and any(
            key in normalized for key in ("title", "approach", "steps")
        ):
            raise DigitalWorkerError(
                "DIGITAL_WORKER_PLAN_LOCKED", "only draft plans can be edited", 409
            )
        if next_status == "ready":
            goal = self._goal(organization_id, worker_id, goal_id)
            worker = self._worker(organization_id, worker_id)
            steps = normalized.get("steps", current["steps"])
            approach = normalized.get("approach", current["approach"])
            if goal["status"] != "active" or worker["status"] != "active":
                raise DigitalWorkerError(
                    "DIGITAL_WORKER_PLAN_PREREQUISITE",
                    "worker and goal must be active before preparing a plan",
                    409,
                )
            if (
                not approach
                or not steps
                or any(not item["action"] or not item["expected_output"] for item in steps)
            ):
                raise DigitalWorkerError(
                    "DIGITAL_WORKER_PLAN_INCOMPLETE",
                    "plan requires an approach and steps with expected outputs",
                    422,
                )
        updated = self.repos.digital_worker_repo.update_plan(
            plan_id, normalized, expected_version=expected_version
        )
        if updated is None:
            raise DigitalWorkerError(
                "DIGITAL_WORKER_PLAN_VERSION_CONFLICT", "worker plan was updated", 409
            )
        self._audit("digital_worker.plan_update", plan_id)
        return updated

    def delete_plan(self, organization_id: str, worker_id: str, goal_id: str, plan_id: str) -> None:
        self._require_manager(organization_id)
        plan = self._plan(organization_id, worker_id, goal_id, plan_id)
        if plan["status"] != "draft":
            raise DigitalWorkerError(
                "DIGITAL_WORKER_PLAN_LOCKED", "only draft plans can be deleted", 409
            )
        self.repos.digital_worker_repo.delete_plan(plan_id)
        self._audit("digital_worker.plan_delete", plan_id)

    def list_capability_proposals(
        self, organization_id: str, worker_id: str
    ) -> list[dict[str, Any]]:
        self._worker(organization_id, worker_id)
        return self.repos.digital_worker_repo.list_capability_proposals(worker_id)

    @staticmethod
    def _configured_ids(raw: str | None) -> list[str]:
        try:
            values = json.loads(raw or "[]")
        except (TypeError, json.JSONDecodeError):
            return []
        if not isinstance(values, list):
            return []
        return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))

    def _authorized_resource_ids(self, worker: dict[str, Any]) -> dict[str, list[str]]:
        agent = self.repos.agent_repo.get(str(worker["agent_id"]))
        if agent is None:
            return {"skill_package_ids": [], "knowledge_base_ids": []}
        skill_ids = self._configured_ids(agent.skill_package_ids)
        knowledge_ids = self._configured_ids(agent.knowledge_base_ids)
        return {
            "skill_package_ids": [
                package_id
                for package_id in skill_ids
                if self.repos.skill_package_repo.get(package_id) is not None
            ],
            "knowledge_base_ids": [
                kb_id
                for kb_id in knowledge_ids
                if self.repos.knowledge_repo.get_base(kb_id) is not None
            ],
        }

    def list_authorized_resources(
        self, organization_id: str, worker_id: str
    ) -> dict[str, list[dict[str, str]]]:
        self._require_manager(organization_id)
        worker = self._worker(organization_id, worker_id)
        agent = self.repos.agent_repo.get(str(worker["agent_id"]))
        if agent is None or (
            not self.actor.is_admin and agent.user_id != self.actor.id and not agent.is_shared
        ):
            raise DigitalWorkerError("ORGANIZATION_FORBIDDEN", "Agent access is not granted", 403)
        configured = self._authorized_resource_ids(worker)
        skills = []
        for package_id in configured["skill_package_ids"]:
            package = self.repos.skill_package_repo.get(package_id)
            if package is not None:
                skills.append({"id": package.id, "name": package.name})
        knowledge_bases = []
        for kb_id in configured["knowledge_base_ids"]:
            base = self.repos.knowledge_repo.get_base(kb_id)
            if base is not None:
                knowledge_bases.append({"id": base.id, "name": base.name})
        return {"skills": skills, "knowledge_bases": knowledge_bases}

    def create_capability_proposal(
        self,
        organization_id: str,
        worker_id: str,
        *,
        source_run_id: str,
        source_summary: str,
        observed_gaps: str,
        proposed_changes: dict[str, list[str]],
        expected_benefit: str,
        risk_notes: str,
    ) -> dict[str, Any]:
        self._require_manager(organization_id)
        worker = self._worker(organization_id, worker_id)
        if worker["status"] != "active":
            raise DigitalWorkerError(
                "DIGITAL_WORKER_CAPABILITY_WORKER_INACTIVE",
                "only active virtual workers can receive improvement proposals",
                409,
            )
        run = self.repos.enterprise_scene_repo.get_run(source_run_id)
        if run is None or run["organization_id"] != organization_id:
            raise DigitalWorkerError("SCENE_RUN_NOT_FOUND", "source scene run not found", 404)
        snapshot = run.get("plan_snapshot") or {}
        if (
            run["status"] != "succeeded"
            or (snapshot.get("worker") or {}).get("worker_id") != worker_id
        ):
            raise DigitalWorkerError(
                "DIGITAL_WORKER_CAPABILITY_EVIDENCE_INVALID",
                "proposal requires a successful scene run for this virtual worker",
                409,
            )
        normalized = {
            field: list(dict.fromkeys(value.strip() for value in proposed_changes[field]))
            for field in (
                "skills",
                "knowledge",
                "operating_guidance",
                "skill_package_ids",
                "knowledge_base_ids",
            )
        }
        if not any(normalized.values()):
            raise DigitalWorkerError(
                "DIGITAL_WORKER_CAPABILITY_CHANGE_EMPTY",
                "proposal must include at least one descriptive capability change",
                422,
            )
        if any(not value for values in normalized.values() for value in values):
            raise DigitalWorkerError(
                "DIGITAL_WORKER_CAPABILITY_CHANGE_EMPTY",
                "capability change entries cannot be empty",
                422,
            )
        configured = self._authorized_resource_ids(worker)
        for field in ("skill_package_ids", "knowledge_base_ids"):
            if not set(normalized[field]).issubset(configured[field]):
                raise DigitalWorkerError(
                    "DIGITAL_WORKER_RESOURCE_NOT_AUTHORIZED",
                    "capability proposals may reference only resources already authorized for the Agent",
                    409,
                )
        proposal = self.repos.digital_worker_repo.create_capability_proposal(
            proposal_id=new_ulid(),
            worker_id=worker_id,
            source_run_id=source_run_id,
            source_summary=self._required_text(source_summary, "source_summary"),
            observed_gaps=self._required_text(observed_gaps, "observed_gaps"),
            proposed_changes=normalized,
            expected_benefit=self._required_text(expected_benefit, "expected_benefit"),
            risk_notes=risk_notes.strip(),
            base_capability_version=int(worker["capability_version"]),
            created_by=self.actor.id,
        )
        if proposal is None:
            raise DigitalWorkerError(
                "DIGITAL_WORKER_CAPABILITY_PROPOSAL_EXISTS",
                "a capability proposal already exists for this scene run",
                409,
            )
        self._audit("digital_worker.capability_proposal_create", str(proposal["proposal_id"]))
        return proposal

    def review_capability_proposal(
        self,
        organization_id: str,
        worker_id: str,
        proposal_id: str,
        *,
        approve: bool,
        review_note: str,
    ) -> dict[str, Any]:
        self._require_manager(organization_id)
        worker = self._worker(organization_id, worker_id)
        proposal = self.repos.digital_worker_repo.get_capability_proposal(proposal_id)
        if proposal is None or proposal["worker_id"] != worker_id:
            raise DigitalWorkerError(
                "DIGITAL_WORKER_CAPABILITY_PROPOSAL_NOT_FOUND",
                "capability proposal not found",
                404,
            )
        if int(proposal["created_by"]) == self.actor.id:
            raise DigitalWorkerError(
                "DIGITAL_WORKER_CAPABILITY_SELF_REVIEW",
                "proposal author cannot review their own capability proposal",
                403,
            )
        if approve:
            configured = self._authorized_resource_ids(worker)
            for field in ("skill_package_ids", "knowledge_base_ids"):
                proposed_ids = set(proposal["proposed_changes"].get(field, []))
                if not proposed_ids.issubset(configured[field]):
                    raise DigitalWorkerError(
                        "DIGITAL_WORKER_RESOURCE_NOT_AUTHORIZED",
                        "referenced Agent resources changed before proposal review",
                        409,
                    )
        reviewed = self.repos.digital_worker_repo.review_capability_proposal(
            proposal_id,
            reviewer_id=self.actor.id,
            approve=approve,
            review_note=review_note.strip(),
        )
        if reviewed is None:
            raise DigitalWorkerError(
                "DIGITAL_WORKER_CAPABILITY_REVIEW_CONFLICT",
                "proposal was already reviewed or its capability base version changed",
                409,
            )
        self._audit(
            "digital_worker.capability_proposal_publish"
            if approve
            else "digital_worker.capability_proposal_reject",
            proposal_id,
        )
        return reviewed
