"""Credential-free integration intent persistence; never an authorization source."""

from __future__ import annotations

import json
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import now_ts


class MattermostIntentRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def get(self, organization_id: str, worker_id: str) -> dict[str, Any] | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM worker_mattermost_intents WHERE organization_id=? AND worker_id=?",
                (organization_id, worker_id),
            ).fetchone()
        return dict(row) if row else None

    def save(
        self,
        organization_id: str,
        worker_id: str,
        *,
        actor_id: int,
        expected_worker_version: int,
        expected_revision: int,
        instance_key: str,
        team_id: str,
        channel_id: str,
        bot_id: str,
        status: str,
    ) -> dict[str, Any] | None:
        # The write predicate rechecks live membership, Agent access, and both revisions.
        # Audit and mutation commit together. No speculative success audit on conflict.
        with self._db.transaction() as conn:
            result = conn.execute(
                "INSERT INTO worker_mattermost_intents "
                "(worker_id,organization_id,instance_key,team_id,channel_id,bot_id,status,revision,worker_version,updated_by,updated_at) "
                "SELECT ?,?,?,?,?,?,?,1,?,?,? WHERE EXISTS ("
                "SELECT 1 FROM digital_workers w JOIN agents a ON a.agent_id=w.agent_id "
                "JOIN users u ON u.id=? WHERE u.disabled=0 AND w.worker_id=? AND w.organization_id=? AND w.version=? "
                "AND (u.role='admin' OR ("
                "EXISTS (SELECT 1 FROM organization_members m WHERE m.organization_id=w.organization_id AND m.user_id=u.id AND m.role IN ('owner','manager')) "
                "AND (a.user_id=u.id OR a.is_shared=1)))) "
                "AND (?=0 OR EXISTS (SELECT 1 FROM worker_mattermost_intents WHERE worker_id=?)) "
                "ON CONFLICT(worker_id) DO UPDATE SET "
                "instance_key=excluded.instance_key,team_id=excluded.team_id,channel_id=excluded.channel_id,bot_id=excluded.bot_id,"
                "status=excluded.status,revision=worker_mattermost_intents.revision+1,"
                "worker_version=excluded.worker_version,updated_by=excluded.updated_by,updated_at=excluded.updated_at "
                "WHERE worker_mattermost_intents.revision=? AND worker_mattermost_intents.organization_id=excluded.organization_id",
                (
                    worker_id,
                    organization_id,
                    instance_key,
                    team_id,
                    channel_id,
                    bot_id,
                    status,
                    expected_worker_version,
                    actor_id,
                    now_ts(),
                    actor_id,
                    worker_id,
                    organization_id,
                    expected_worker_version,
                    expected_revision,
                    worker_id,
                    expected_revision,
                ),
            )
            if result.rowcount != 1:
                return None
            row = conn.execute(
                "SELECT * FROM worker_mattermost_intents WHERE worker_id=?", (worker_id,)
            ).fetchone()
            assert row is not None
            conn.execute(
                "INSERT INTO audit_log(ts,actor,action,target,payload) VALUES(?,?,?,?,?)",
                (
                    now_ts(),
                    str(actor_id),
                    "digital_worker.mattermost_intent",
                    worker_id,
                    json.dumps(
                        {
                            "organization_id": organization_id,
                            "revision": row["revision"],
                            "status": status,
                            "operation_mode": "human_initiated_configuration",
                            "identity_status": "unverified",
                            "execution_authorized": False,
                        }
                    ),
                ),
            )
            return dict(row)
