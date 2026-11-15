"""Local readiness and credential-free intents; neither authorizes external actions."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from octop.infra.digital_workers.service import DigitalWorkerError, DigitalWorkerService


class MattermostReadinessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_worker_version: int = Field(ge=1)


class MattermostReadinessReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    organization_id: str
    worker_id: str
    agent_id: str
    worker_version: int
    human_actor_id: int
    operation_mode: Literal["human_initiated_local_check"] = "human_initiated_local_check"
    identity_status: Literal["unbound"] = "unbound"
    mattermost_bot_id: None = None
    connected: Literal[False] = False
    human_initiated_send_allowed: Literal[False] = False
    automatic_send_allowed: Literal[False] = False
    execution_authorized: Literal[False] = False
    scope: Literal["local_observation_not_atomic"] = "local_observation_not_atomic"


class MattermostReadinessService(DigitalWorkerService):
    def _authorize(
        self, organization_id: str, worker_id: str, *, expected_worker_version: int
    ) -> dict[str, Any]:
        # Authorize before looking up resources. Never resolve identities by display name.
        if not self.actor.is_admin:
            member = self.repos.organization_repo.get_member(organization_id, self.actor.id)
            if member is None or member["role"] not in {"owner", "manager"}:
                raise DigitalWorkerError(
                    "ORGANIZATION_FORBIDDEN", "organization access denied", 403
                )
        self._require_manager(organization_id)
        worker = self._worker(organization_id, worker_id)
        agent = self.repos.agent_repo.get(str(worker["agent_id"]))
        if agent is None or (
            not self.actor.is_admin and agent.user_id != self.actor.id and not agent.is_shared
        ):
            raise DigitalWorkerError("ORGANIZATION_FORBIDDEN", "Agent access is not granted", 403)
        if worker["version"] != expected_worker_version:
            raise DigitalWorkerError(
                "DIGITAL_WORKER_VERSION_CONFLICT", "worker profile was updated", 409
            )
        return worker

    def inspect(
        self, organization_id: str, worker_id: str, *, expected_worker_version: int
    ) -> MattermostReadinessReport:
        worker = self._authorize(
            organization_id, worker_id, expected_worker_version=expected_worker_version
        )
        report = MattermostReadinessReport(
            organization_id=organization_id,
            worker_id=str(worker["worker_id"]),
            agent_id=str(worker["agent_id"]),
            worker_version=int(worker["version"]),
            human_actor_id=self.actor.id,
        )
        # Record the authenticated local check, not an imaginary bot action or binding.
        self.repos.audit_repo.write(
            actor=str(self.actor.id),
            action="digital_worker.mattermost_readiness",
            target=str(worker["worker_id"]),
            payload=report.model_dump_json(),
        )
        return report


class MattermostIntentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_worker_version: int = Field(ge=1)
    expected_revision: int = Field(ge=0)
    # An operator-managed alias, NOT a URL or a credential reference.
    instance_key: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    team_id: str = Field(pattern=r"^[a-z0-9]{26}$")
    channel_id: str = Field(pattern=r"^[a-z0-9]{26}$")
    bot_id: str = Field(pattern=r"^[a-z0-9]{26}$")
    status: Literal["draft", "revoked"] = "draft"


class MattermostIntentReport(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    organization_id: str
    worker_id: str
    instance_key: str
    team_id: str
    channel_id: str
    bot_id: str
    status: Literal["draft", "revoked"]
    revision: int
    worker_version: int
    updated_by: int
    updated_at: int
    identity_status: Literal["unverified"] = "unverified"
    operation_mode: Literal["human_initiated_configuration"] = "human_initiated_configuration"
    connected: Literal[False] = False
    execution_authorized: Literal[False] = False
    human_initiated_send_allowed: Literal[False] = False
    automatic_send_allowed: Literal[False] = False


class MattermostIntentService(MattermostReadinessService):
    def get_intent(
        self, organization_id: str, worker_id: str, *, expected_worker_version: int
    ) -> MattermostIntentReport | None:
        self._authorize(organization_id, worker_id, expected_worker_version=expected_worker_version)
        row = self.repos.mattermost_intent_repo.get(organization_id, worker_id)
        return MattermostIntentReport.model_validate(row) if row else None

    def save_intent(
        self, organization_id: str, worker_id: str, body: MattermostIntentRequest
    ) -> MattermostIntentReport:
        self._authorize(
            organization_id, worker_id, expected_worker_version=body.expected_worker_version
        )
        row = self.repos.mattermost_intent_repo.save(
            organization_id, worker_id, actor_id=self.actor.id, **body.model_dump()
        )
        if row is None:
            raise DigitalWorkerError(
                "DIGITAL_WORKER_VERSION_CONFLICT", "configuration changed or access revoked", 409
            )
        return MattermostIntentReport.model_validate(row)
