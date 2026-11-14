"""Read-only, local plan observations. Never substitutes for run authorization."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from octop.infra.digital_workers.service import DigitalWorkerError, DigitalWorkerService


class ReadinessCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    status: Literal["satisfied", "unmet", "unknown"]


class PlanReadinessReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    organization_id: str
    worker_id: str
    goal_id: str
    plan_id: str
    worker_version: int
    capability_version: int
    plan_version: int
    goal_updated_at: float
    observed_at: str
    checks: list[ReadinessCheck]
    execution_authorized: Literal[False] = False
    scope: Literal["local_observation_not_atomic"] = "local_observation_not_atomic"


class WorkerPlanReadinessService(DigitalWorkerService):
    def _require_manager(self, organization_id: str) -> None:
        # Do not disclose organization existence to non-managers/non-members.
        if not self.actor.is_admin:
            member = self.repos.organization_repo.get_member(organization_id, self.actor.id)
            if member is None or member["role"] not in {"owner", "manager"}:
                raise DigitalWorkerError(
                    "ORGANIZATION_FORBIDDEN", "organization manager required", 403
                )
        super()._require_manager(organization_id)

    def _is_appointed(self, organization_id: str, position_id: str, agent_id: str) -> bool:
        return self.repos.organization_repo.has_agent_assignment(
            organization_id, position_id, agent_id
        )

    def _snapshot(
        self, organization_id: str, worker_id: str, goal_id: str, plan_id: str
    ) -> dict[str, Any]:
        # Do not expose plan existence or agent state before organization authorization.
        self._require_manager(organization_id)
        worker = self._worker(organization_id, worker_id)
        goal = self._goal(organization_id, worker_id, goal_id)
        plan = self._plan(organization_id, worker_id, goal_id, plan_id)
        agent = self.repos.agent_repo.get(str(worker["agent_id"]))
        agent_access = agent is not None and (
            self.actor.is_admin or agent.user_id == self.actor.id or bool(agent.is_shared)
        )
        # Agent details are not returned; inaccessible agents cannot produce a report.
        if not agent_access:
            raise DigitalWorkerError("ORGANIZATION_FORBIDDEN", "Agent access is not granted", 403)
        return {
            "worker": worker,
            "goal": goal,
            "plan": plan,
            "agent_enabled": bool(agent and agent.enabled),
            "appointed": self._is_appointed(
                organization_id, str(worker["position_id"]), str(worker["agent_id"])
            ),
            "scene": self.repos.enterprise_scene_repo.has_enabled_for_worker(
                organization_id, str(worker["position_id"]), str(worker["agent_id"])
            ),
        }

    def inspect(
        self,
        organization_id: str,
        worker_id: str,
        goal_id: str,
        plan_id: str,
        *,
        expected_worker_version: int,
        expected_plan_version: int,
    ) -> PlanReadinessReport:
        snapshot = self._snapshot(organization_id, worker_id, goal_id, plan_id)
        worker, goal, plan = (snapshot[name] for name in ("worker", "goal", "plan"))
        if worker["version"] != expected_worker_version:
            raise DigitalWorkerError(
                "DIGITAL_WORKER_VERSION_CONFLICT", "worker profile was updated", 409
            )
        if plan["version"] != expected_plan_version:
            raise DigitalWorkerError(
                "DIGITAL_WORKER_PLAN_VERSION_CONFLICT", "worker plan was updated", 409
            )
        steps = plan["steps"]
        content = bool(
            str(plan["title"]).strip()
            and str(plan["approach"]).strip()
            and str(goal["success_criteria"]).strip()
            and steps
            and all(
                isinstance(step, dict)
                and str(step.get("action") or "").strip()
                and str(step.get("expected_output") or "").strip()
                for step in steps
            )
        )
        observed = {
            "worker_active": worker["status"] == "active",
            "goal_active": goal["status"] == "active",
            "plan_ready": plan["status"] == "ready",
            "plan_content_complete": content,
            "agent_enabled": snapshot["agent_enabled"],
            "agent_appointed": snapshot["appointed"],
            "matching_scene_enabled": snapshot["scene"],
        }
        checks = [
            ReadinessCheck(code=code, status="satisfied" if value else "unmet")
            for code, value in observed.items()
        ]
        checks.extend(
            ReadinessCheck(code=code, status="unknown")
            for code in ("runtime_approval", "knowledge_user_acl", "model_budget")
        )
        # Detect observed revocation/change during the read, but never promise an atomic
        # authorization snapshot or a lease. Execution must revalidate independently.
        if snapshot != self._snapshot(organization_id, worker_id, goal_id, plan_id):
            raise DigitalWorkerError(
                "DIGITAL_WORKER_PLAN_VERSION_CONFLICT", "worker plan was updated", 409
            )
        return PlanReadinessReport(
            organization_id=organization_id,
            worker_id=worker_id,
            goal_id=goal_id,
            plan_id=plan_id,
            worker_version=worker["version"],
            capability_version=worker["capability_version"],
            plan_version=plan["version"],
            goal_updated_at=goal["updated_at"],
            observed_at=datetime.now(UTC).isoformat(),
            checks=checks,
        )
