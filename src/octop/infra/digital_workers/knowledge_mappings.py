"""Explicit local draft mappings, not AKB authorization or a retrieval path.

The caller must use this service rather than the SQL repository. The absence of
verified delegated-user ACL support means these records cannot be activated.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from octop.infra.digital_workers.service import DigitalWorkerError, DigitalWorkerService
from octop.infra.knowledge.sources import canonical_knowledge_sources


def capability_json(source: dict[str, Any]) -> str:
    value = dict(source)
    for key in ("read_tools", "forbidden_arguments"):
        value[key] = sorted(set(value.get(key, [])))
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class WorkerKnowledgeMappingService(DigitalWorkerService):
    def _context(self, organization_id: str, worker_id: str) -> dict[str, Any]:
        self._require_manager(organization_id)
        worker = self._worker(organization_id, worker_id)
        agent = self.repos.agent_repo.get(worker["agent_id"])
        if (
            worker["status"] != "active"
            or agent is None
            or not agent.enabled
            or not self._is_appointed(organization_id, worker["position_id"], worker["agent_id"])
            or (not self.actor.is_admin and agent.user_id != self.actor.id and not agent.is_shared)
        ):
            raise DigitalWorkerError("ORGANIZATION_FORBIDDEN", "mapping context unavailable", 403)
        return worker

    def _snapshot(
        self,
        organization_id: str,
        worker_id: str,
        *,
        mcp_server: str,
        knowledge_base_ref: str,
        external_source_id: str,
    ) -> dict[str, Any]:
        worker = self._context(organization_id, worker_id)
        candidates = [
            s
            for s in canonical_knowledge_sources(worker["capabilities"])
            if s["provider"] == "akb"
            and s["mcp_server"] == mcp_server
            and s["knowledge_base_ref"] == knowledge_base_ref
        ]
        source = self.repos.external_knowledge_repo.get(external_source_id)
        if (
            len(candidates) != 1
            or source is None
            or source.kind not in ("akb-mcp", "akb-rest")
            or source.auth_type != "agent"
            or not source.enabled
            or not source.connection_enabled
            or source.ref != knowledge_base_ref
            or (
                not self.actor.is_admin
                and not (source.owner_user_id == self.actor.id or source.shared)
            )
            or (
                not self.actor.is_admin
                and not (
                    source.connection_owner_user_id == self.actor.id or source.connection_shared
                )
            )
            or external_source_id
            not in self.repos.external_knowledge_repo.list_source_ids_for_target(
                "agent", worker["agent_id"]
            )
        ):
            raise DigitalWorkerError("ORGANIZATION_FORBIDDEN", "mapping source unavailable", 403)
        scenes = self.repos.enterprise_scene_repo.list_for_org(organization_id)
        if not any(
            s["position_id"] == worker["position_id"]
            and s["agent_id"] == worker["agent_id"]
            and s.get("enabled")
            and mcp_server in s.get("mcp_servers", [])
            for s in scenes
        ):
            raise DigitalWorkerError("ORGANIZATION_FORBIDDEN", "mapping scene unavailable", 403)
        encoded = capability_json(candidates[0])
        return {
            "organization_id": organization_id,
            "worker_id": worker_id,
            "capability_key": "v1:" + hashlib.sha256(encoded.encode()).hexdigest(),
            "capability_json": encoded,
            "capability_version": worker["capability_version"],
            "worker_version": worker["version"],
            "external_source_id": external_source_id,
            "expected_connection_id": source.connection_id,
            "connection_revision": source.revision,
            "resource_revision": source.resource_revision,
        }

    def create_draft(
        self,
        organization_id: str,
        worker_id: str,
        *,
        mcp_server: str,
        knowledge_base_ref: str,
        external_source_id: str,
        expected_capability_version: int,
        expected_connection_revision: int,
        expected_resource_revision: int,
    ) -> dict[str, Any]:
        values = self._snapshot(
            organization_id,
            worker_id,
            mcp_server=mcp_server,
            knowledge_base_ref=knowledge_base_ref,
            external_source_id=external_source_id,
        )
        if (
            values["capability_version"],
            values["connection_revision"],
            values["resource_revision"],
        ) != (
            expected_capability_version,
            expected_connection_revision,
            expected_resource_revision,
        ):
            raise DigitalWorkerError(
                "DIGITAL_WORKER_CAPABILITY_VERSION_CONFLICT", "mapping snapshot changed", 409
            )
        mapping_id = self.repos.worker_knowledge_mapping_repo.create_draft(values, self.actor.id)
        if mapping_id is None:
            raise DigitalWorkerError(
                "DIGITAL_WORKER_CAPABILITY_VERSION_CONFLICT",
                "mapping exists or snapshot changed",
                409,
            )
        self._audit("digital_worker.knowledge_mapping_draft", mapping_id)
        return self.inspect(organization_id, worker_id, mapping_id)

    def inspect(self, organization_id: str, worker_id: str, mapping_id: str) -> dict[str, Any]:
        self._require_manager(organization_id)
        self._worker(organization_id, worker_id)
        row = self.repos.worker_knowledge_mapping_repo.get(organization_id, worker_id, mapping_id)
        if row is None:
            raise DigitalWorkerError("DIGITAL_WORKER_NOT_FOUND", "mapping not found", 404)
        current = False
        if row["status"] == "draft":
            cap = json.loads(row["capability_json"])
            try:
                snapshot = self._snapshot(
                    organization_id,
                    worker_id,
                    mcp_server=cap["mcp_server"],
                    knowledge_base_ref=cap["knowledge_base_ref"],
                    external_source_id=row["external_source_id"],
                )
                current = all(row[key] == value for key, value in snapshot.items())
            except DigitalWorkerError:
                pass
        return {
            **row,
            "local_snapshot_current": current,
            "retrieval_enabled": False,
            "authorization_status": "delegated_user_acl_unverified",
        }

    def revoke(
        self, organization_id: str, worker_id: str, mapping_id: str, *, expected_revision: int
    ) -> dict[str, Any]:
        self._require_manager(organization_id)
        self._worker(organization_id, worker_id)
        if not self.repos.worker_knowledge_mapping_repo.revoke(
            organization_id, worker_id, mapping_id, expected_revision, self.actor.id
        ):
            raise DigitalWorkerError(
                "DIGITAL_WORKER_CAPABILITY_VERSION_CONFLICT", "mapping changed or missing", 409
            )
        self._audit("digital_worker.knowledge_mapping_revoke", mapping_id)
        return self.inspect(organization_id, worker_id, mapping_id)
