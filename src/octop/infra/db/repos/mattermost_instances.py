"""Deployment-only metadata and live read-only verification predicates. No secrets."""

from __future__ import annotations

import json
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import now_ts

# Every observation checks current DB roles, not a cached User.is_admin claim.
_SCOPE_QUERY = """
SELECT c.* FROM mattermost_instances c
JOIN worker_mattermost_intents i ON i.organization_id=c.organization_id AND i.instance_key=c.instance_key
JOIN digital_workers w ON w.worker_id=i.worker_id AND w.organization_id=i.organization_id
JOIN agents a ON a.agent_id=w.agent_id
JOIN users u ON u.id=?
WHERE c.organization_id=? AND c.instance_key=? AND c.revision=? AND c.status='configured'
AND i.worker_id=? AND i.revision=? AND i.status='draft' AND i.bot_id=c.bot_id
AND i.worker_version=? AND w.version=i.worker_version AND w.status='active' AND a.enabled=1
AND u.disabled=0
AND EXISTS (SELECT 1 FROM organization_positions p JOIN organization_assignments x ON x.position_id=p.position_id
 WHERE p.position_id=w.position_id AND p.organization_id=w.organization_id
 AND x.subject_type='agent' AND x.subject_id=w.agent_id)
AND (u.role='admin' OR (
 EXISTS (SELECT 1 FROM organization_members m WHERE m.organization_id=w.organization_id AND m.user_id=u.id AND m.role IN ('owner','manager'))
 AND (a.user_id=u.id OR a.is_shared=1)))
"""


class MattermostInstanceRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def configure(
        self,
        *,
        actor_id: int,
        organization_id: str,
        instance_key: str,
        origin: str,
        bot_id: str,
        credential_ref: str,
        status: str,
        expected_revision: int,
    ) -> int | None:
        """Local deployment operator only; not exposed through HTTP. CAS + audit."""
        with self._db.transaction() as conn:
            result = conn.execute(
                "INSERT INTO mattermost_instances "
                "(organization_id,instance_key,origin,bot_id,credential_ref,status,revision,updated_by,updated_at) "
                "SELECT ?,?,?,?,?,?,1,?,? WHERE EXISTS (SELECT 1 FROM users WHERE id=? AND role='admin' AND disabled=0) "
                "AND EXISTS (SELECT 1 FROM organizations WHERE organization_id=?) "
                "AND (?=0 OR EXISTS (SELECT 1 FROM mattermost_instances WHERE organization_id=? AND instance_key=?)) "
                "ON CONFLICT(organization_id,instance_key) DO UPDATE SET origin=excluded.origin,bot_id=excluded.bot_id,"
                "credential_ref=excluded.credential_ref,status=excluded.status,revision=mattermost_instances.revision+1,"
                "updated_by=excluded.updated_by,updated_at=excluded.updated_at "
                "WHERE mattermost_instances.revision=? AND mattermost_instances.revision<9223372036854775807",
                (
                    organization_id,
                    instance_key,
                    origin,
                    bot_id,
                    credential_ref,
                    status,
                    actor_id,
                    now_ts(),
                    actor_id,
                    organization_id,
                    expected_revision,
                    organization_id,
                    instance_key,
                    expected_revision,
                ),
            )
            if result.rowcount != 1:
                return None
            row = conn.execute(
                "SELECT revision FROM mattermost_instances WHERE organization_id=? AND instance_key=?",
                (organization_id, instance_key),
            ).fetchone()
            assert row is not None
            revision = int(row["revision"])
            conn.execute(
                "INSERT INTO audit_log(ts,actor,action,target,payload) VALUES(?,?,?,?,?)",
                (
                    now_ts(),
                    str(actor_id),
                    "mattermost.instance_configuration",
                    instance_key,
                    json.dumps(
                        {
                            "organization_id": organization_id,
                            "revision": revision,
                            "status": status,
                            "execution_authorized": False,
                        }
                    ),
                ),
            )
            return revision

    def current_configuration(
        self,
        *,
        actor_id: int,
        organization_id: str,
        instance_key: str,
        configuration_revision: int,
        worker_id: str,
        intent_revision: int,
        worker_version: int,
    ) -> dict[str, Any] | None:
        with self._db.connect() as conn:
            row = conn.execute(
                _SCOPE_QUERY,
                (
                    actor_id,
                    organization_id,
                    instance_key,
                    configuration_revision,
                    worker_id,
                    intent_revision,
                    worker_version,
                ),
            ).fetchone()
        return dict(row) if row is not None else None

    def audit_observation(self, *, scope: dict[str, Any], result: str) -> str:
        """Recheck and audit together; never store a reusable verified grant.

        SQLite serializes this write. PostgreSQL uses a statement snapshot, not
        global permission locks. Neither can undo an already-started GET.
        """
        if result not in {
            "bot_identity_observed",
            "identity_rejected",
            "unavailable",
            "access_changed",
        }:
            raise ValueError("invalid observation")
        with self._db.transaction() as conn:
            row = conn.execute(
                _SCOPE_QUERY,
                (
                    scope["actor_id"],
                    scope["organization_id"],
                    scope["instance_key"],
                    scope["configuration_revision"],
                    scope["worker_id"],
                    scope["intent_revision"],
                    scope["worker_version"],
                ),
            ).fetchone()
            if row is None:
                result = "access_changed"
            conn.execute(
                "INSERT INTO audit_log(ts,actor,action,target,payload) VALUES(?,?,?,?,?)",
                (
                    now_ts(),
                    str(scope["actor_id"]),
                    "digital_worker.mattermost_identity_observation",
                    scope["worker_id"],
                    json.dumps(
                        {
                            "scope": scope,
                            "result": result,
                            "execution_authorized": False,
                            "consistency": "observation_not_atomic",
                        }
                    ),
                ),
            )
            return result
