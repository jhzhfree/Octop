"""Local SQLite maintenance diagnostics and suspension; resumption is blocked."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos.mattermost_ingress import IntakeError


class MattermostMaintenanceRepo:
    def __init__(self, db: DatabasePool):
        self.db = db
        if db.dialect != "sqlite":
            raise IntakeError(409, "MAINTENANCE_SQLITE_ONLY")

    @staticmethod
    def _snapshot(conn: Any, broker: str) -> tuple[dict[str, Any], dict[str, Any]]:
        if conn.execute("SELECT version FROM _schema_version").fetchone()[0] != 49:
            raise IntakeError(409, "MAINTENANCE_SCHEMA_REQUIRED")
        row = conn.execute(
            "SELECT * FROM mattermost_brokers WHERE broker_id=?", (broker,)
        ).fetchone()
        if not row or row["enabled"]:
            raise IntakeError(409, "MAINTENANCE_DISABLED_BROKER_REQUIRED")
        mapping = dict(row)
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM mattermost_intakes WHERE broker_id=? ORDER BY delivery_key",
                (broker,),
            )
        ]
        if not rows:
            raise IntakeError(409, "MAINTENANCE_RETAINED_HISTORY_REQUIRED")
        retired = [
            r[0]
            for r in conn.execute(
                "SELECT r.delivery_key FROM mattermost_retired_intakes r "
                "JOIN mattermost_intakes i ON i.delivery_key=r.delivery_key "
                "WHERE i.broker_id=? ORDER BY r.delivery_key",
                (broker,),
            )
        ]
        nonce_count = conn.execute(
            "SELECT COUNT(*) FROM mattermost_broker_nonces WHERE broker_id=?", (broker,)
        ).fetchone()[0]
        material = json.dumps(
            [mapping, rows, retired, nonce_count], sort_keys=True, separators=(",", ":")
        )
        plan = {
            "broker_id": broker,
            "revision": mapping["revision"],
            "organization_id": mapping["organization_id"],
            "worker_id": mapping["worker_id"],
            "delivery_keys": [r["delivery_key"] for r in rows],
            "already_retired": retired,
            "retained_nonces": nonce_count,
            "plan_digest": hashlib.sha256(material.encode()).hexdigest(),
            "policy": "resume_blocked_process_isolation_unproven",
            "execution_authorized": False,
        }
        return mapping, plan

    def plan(self, broker: str) -> dict[str, Any]:
        with self.db.transaction() as conn:
            return self._snapshot(conn, broker)[1]

    def status(self, broker: str) -> dict[str, Any]:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT broker_id,enabled,revision FROM mattermost_brokers WHERE broker_id=?",
                (broker,),
            ).fetchone()
            if not row:
                raise IntakeError(404, "MAINTENANCE_BROKER_NOT_FOUND")
            events = [
                {"ts": r[0], "action": r[1], "payload": json.loads(r[2])}
                for r in conn.execute(
                    "SELECT ts,action,payload FROM audit_log WHERE target=? "
                    "AND actor IN ('local-maintenance','schema-migration') "
                    "AND action LIKE 'mattermost.maintenance.%' "
                    "ORDER BY id DESC LIMIT 20",
                    (broker,),
                )
            ]
            return dict(row) | {"maintenance_events": events, "execution_authorized": False}

    @staticmethod
    def _audit(conn: Any, broker: str, action: str, payload: dict[str, Any], now: int) -> None:
        conn.execute(
            "INSERT INTO audit_log(ts,actor,action,target,payload) VALUES(?,?,?,?,?)",
            (
                now,
                "local-maintenance",
                "mattermost.maintenance." + action,
                broker,
                json.dumps(payload),
            ),
        )

    def retire_and_resume(self, broker: str, digest: str, reason: str, now: int) -> dict[str, Any]:
        """No cooperative revision or local flag proves old readers are isolated.

        Keep this legacy entry point fail-closed, including direct callers. Do
        not retire records, enable the broker, or claim any plan was applied.
        """
        raise IntakeError(409, "MAINTENANCE_PROCESS_ISOLATION_UNPROVEN")

    def suspend(self, broker: str, revision: int, reason: str, now: int) -> dict[str, Any]:
        """Compensate by disabling admission; never erase permanent identity fences."""
        if not 8 <= len(reason.strip()) <= 512:
            raise IntakeError(422, "MAINTENANCE_REASON_REQUIRED")
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM mattermost_brokers WHERE broker_id=?", (broker,)
            ).fetchone()
            if not row or not row["enabled"] or row["revision"] != revision:
                raise IntakeError(409, "MAINTENANCE_REVISION_CHANGED")
            conn.execute(
                "UPDATE mattermost_brokers SET enabled=0,revision=revision+1 WHERE broker_id=?",
                (broker,),
            )
            result = {"revision": revision + 1, "enabled": False, "reason": reason.strip()}
            self._audit(conn, broker, "suspended", result, now)
            return result
