"""Atomic account-authenticated handoff to an existing worker's ordinary goal."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from psycopg.errors import DeadlockDetected, LockNotAvailable, QueryCanceled, SerializationFailure

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import now_ts
from octop.infra.utils.ulid import new_ulid


class HandoffError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class GoalHandoffRepo:
    def __init__(self, db: DatabasePool):
        self.db = db

    @staticmethod
    def authorize(conn: Any, org: str, actor: int, *, lock: bool = False) -> None:
        if lock:
            user = conn.execute(
                "SELECT role FROM users WHERE id=? AND disabled=0 FOR SHARE", (actor,)
            ).fetchone()
            if user is None:
                raise HandoffError("forbidden")
            if (
                user["role"] != "admin"
                and conn.execute(
                    "SELECT 1 FROM organization_members WHERE organization_id=? AND user_id=? "
                    "AND role IN ('owner','manager') FOR SHARE",
                    (org, actor),
                ).fetchone()
                is None
            ):
                raise HandoffError("forbidden")
            return
        row = conn.execute(
            "SELECT 1 FROM users u LEFT JOIN organization_members m "
            "ON m.user_id=u.id AND m.organization_id=? WHERE u.id=? AND u.disabled=0 "
            "AND (u.role='admin' OR m.role IN ('owner','manager'))",
            (org, actor),
        ).fetchone()
        if row is None:
            raise HandoffError("forbidden")

    @staticmethod
    def decode(row: Any) -> dict[str, Any]:
        result = dict(row)
        result.pop("request_sha256", None)
        result.pop("request_id", None)
        for key in ("source_snapshot", "target_snapshot"):
            result[key] = json.loads(result.pop(key + "_json"))
        return result

    def create(
        self, org: str, source_worker: str, source_goal: str, actor: int, request: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            return self._create(org, source_worker, source_goal, actor, request)
        except (DeadlockDetected, LockNotAvailable, QueryCanceled, SerializationFailure) as exc:
            # No partial goal or audit survives a failed transaction. Retry the same key.
            raise HandoffError("busy") from exc

    def _create(
        self, org: str, source_worker: str, source_goal: str, actor: int, request: dict[str, Any]
    ) -> dict[str, Any]:
        pg = self.db.dialect == "postgresql"
        if self.db.dialect not in ("sqlite", "postgresql"):
            raise HandoffError("unsupported_database")
        fingerprint = hashlib.sha256(
            json.dumps(
                {"source_worker": source_worker, "source_goal": source_goal, **request},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        with self.db.transaction() as conn:
            if pg:
                # Do not silently downgrade an administrator's stronger isolation.
                if conn.execute("SHOW transaction_isolation").fetchone()[0] != "read committed":
                    raise HandoffError("isolation_required")
                for setting, ceiling in (("lock_timeout", 2000), ("statement_timeout", 5000)):
                    current = int(
                        conn.execute(
                            "SELECT setting FROM pg_settings WHERE name=?", (setting,)
                        ).fetchone()[0]
                    )
                    bounded = min(current, ceiling) if current else ceiling
                    conn.execute("SELECT set_config(?, ?, true)", (setting, f"{bounded}ms"))
                # Serialize only identical request keys; row locks below protect ordinary
                # non-handoff mutations too. A hash collision merely adds contention.
                key = json.dumps(["goal_handoff", org, actor, request["request_id"]]).encode()
                lock_id = int.from_bytes(hashlib.sha256(key).digest()[:8], "big", signed=True)
                conn.execute("SELECT pg_advisory_xact_lock(?)", (lock_id,))
            self.authorize(conn, org, actor, lock=pg)
            old = conn.execute(
                "SELECT h.*,g.status AS target_goal_status FROM goal_handoffs h "
                "LEFT JOIN digital_worker_goals g ON g.goal_id=h.target_goal_id AND g.worker_id=h.target_worker_id "
                "WHERE h.organization_id=? AND h.created_by=? AND h.request_id=?",
                (org, actor, request["request_id"]),
            ).fetchone()
            if old is not None:
                if old["request_sha256"] != fingerprint:
                    raise HandoffError("conflict")
                return self.decode(old)
            if pg:
                conn.execute(
                    "SELECT worker_id FROM digital_workers WHERE organization_id=? "
                    "AND worker_id IN (?,?) ORDER BY worker_id FOR SHARE",
                    (org, source_worker, request["target_worker_id"]),
                ).fetchall()
            source = conn.execute(
                "SELECT g.*,w.name AS worker_name,w.position_id,w.agent_id,w.status AS worker_status "
                "FROM digital_worker_goals g JOIN digital_workers w ON w.worker_id=g.worker_id "
                "WHERE g.goal_id=? AND w.worker_id=? AND w.organization_id=?"
                + (" FOR UPDATE OF g" if pg else ""),
                (source_goal, source_worker, org),
            ).fetchone()
            target = conn.execute(
                "SELECT w.*,p.title AS position_title,a.enabled,a.user_id AS agent_owner,a.is_shared "
                "FROM digital_workers w JOIN organization_positions p ON p.position_id=w.position_id "
                "AND p.organization_id=w.organization_id JOIN agents a ON a.agent_id=w.agent_id "
                "WHERE w.worker_id=? AND w.organization_id=?" + (" FOR SHARE OF p,a" if pg else ""),
                (request["target_worker_id"], org),
            ).fetchone()
            if source is None or target is None:
                raise HandoffError("not_found")
            user = conn.execute("SELECT role FROM users WHERE id=?", (actor,)).fetchone()
            if not target["enabled"] or (
                user["role"] != "admin"
                and target["agent_owner"] != actor
                and not target["is_shared"]
            ):
                raise HandoffError("forbidden")
            appointed = conn.execute(
                "SELECT 1 FROM organization_assignments WHERE position_id=? "
                "AND subject_type='agent' AND subject_id=?" + (" FOR SHARE" if pg else ""),
                (target["position_id"], target["agent_id"]),
            ).fetchone()
            if (
                source["version"] != request["expected_source_version"]
                or target["version"] != request["expected_target_version"]
                or source["status"] not in ("active", "completed")
                or source["worker_status"] != "active"
                or target["status"] != "active"
                or source["position_id"] == target["position_id"]
                or appointed is None
            ):
                raise HandoffError("conflict")
            timestamp, goal_id, handoff_id = now_ts(), new_ulid(), new_ulid()
            source_snapshot = {
                key: source[key]
                for key in (
                    "goal_id",
                    "worker_id",
                    "worker_name",
                    "position_id",
                    "agent_id",
                    "title",
                    "description",
                    "success_criteria",
                    "status",
                    "version",
                )
            }
            target_snapshot = {
                key: target[key]
                for key in (
                    "worker_id",
                    "name",
                    "position_id",
                    "position_title",
                    "agent_id",
                    "version",
                )
            }
            updated = conn.execute(
                "UPDATE digital_worker_goals SET version=version+1,updated_at=? "
                "WHERE goal_id=? AND worker_id=? AND version=?",
                (timestamp, source_goal, source_worker, request["expected_source_version"]),
            )
            if updated.rowcount != 1:
                raise HandoffError("conflict")
            conn.execute(
                "INSERT INTO digital_worker_goals(goal_id,worker_id,title,description,success_criteria,"
                "created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    goal_id,
                    target["worker_id"],
                    request["title"],
                    request["description"],
                    request["success_criteria"],
                    actor,
                    timestamp,
                    timestamp,
                ),
            )
            conn.execute(
                "INSERT INTO goal_handoffs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    handoff_id,
                    org,
                    source_goal,
                    source_worker,
                    goal_id,
                    target["worker_id"],
                    actor,
                    request["request_id"],
                    fingerprint,
                    request["reason"],
                    json.dumps(source_snapshot, ensure_ascii=False),
                    json.dumps(target_snapshot, ensure_ascii=False),
                    timestamp,
                ),
            )
            conn.execute(
                "INSERT INTO audit_log(ts,actor,action,target,payload) VALUES(?,?,?,?,?)",
                (
                    timestamp,
                    str(actor),
                    "digital_worker.goal_handoff",
                    handoff_id,
                    json.dumps(
                        {
                            "organization_id": org,
                            "source_goal_id": source_goal,
                            "target_goal_id": goal_id,
                            "from_version": source["version"],
                            "to_version": source["version"] + 1,
                            "target_worker_id": target["worker_id"],
                        }
                    ),
                ),
            )
            return self.decode(
                conn.execute(
                    "SELECT h.*,g.status AS target_goal_status FROM goal_handoffs h "
                    "LEFT JOIN digital_worker_goals g ON g.goal_id=h.target_goal_id AND g.worker_id=h.target_worker_id "
                    "WHERE h.handoff_id=?",
                    (handoff_id,),
                ).fetchone()
            )

    def list(
        self, org: str, actor: int, goal: str | None = None, before: str | None = None
    ) -> dict[str, Any]:
        with self.db.connect() as conn:
            self.authorize(conn, org, actor)
            rows = conn.execute(
                "SELECT h.*,g.status AS target_goal_status,g.version AS target_goal_version "
                "FROM goal_handoffs h LEFT JOIN digital_worker_goals g ON g.goal_id=h.target_goal_id "
                "AND g.worker_id=h.target_worker_id WHERE h.organization_id=? "
                + ("AND (h.source_goal_id=? OR h.target_goal_id=?) " if goal else "")
                + ("AND h.handoff_id<? " if before else "")
                + "ORDER BY h.handoff_id DESC LIMIT 21",
                (org, *((goal, goal) if goal else ()), *((before,) if before else ())),
            ).fetchall()
            self.authorize(conn, org, actor)
        return {
            "items": [self.decode(row) for row in rows[:20]],
            "next_before": rows[19]["handoff_id"] if len(rows) > 20 else None,
        }
