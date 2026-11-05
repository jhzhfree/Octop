"""SQL-only persistence and compare-and-swap transitions for enterprise workflows."""

from __future__ import annotations

import json
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import now_ts


def _decode(row: Any) -> dict[str, Any]:
    item = dict(row)
    for key in ("config", "snapshot", "evidence"):
        if key + "_json" in item:
            item[key] = json.loads(item.pop(key + "_json"))
    return item


class EnterpriseAssistantRepo:
    def __init__(self, db: DatabasePool) -> None:
        self.db = db

    def resources(self, owner: int, category: str) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM enterprise_assistant_resources WHERE owner_id=? AND category=? "
                "ORDER BY created_at, id",
                (owner, category),
            ).fetchall()
        return [_decode(r) for r in rows]

    def resource(self, resource_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM enterprise_assistant_resources WHERE id=?", (resource_id,)
            ).fetchone()
        return _decode(row) if row else None

    def save(
        self,
        resource_id: str,
        owner: int,
        category: str,
        config: dict[str, Any],
        secret: str,
        expected: int | None,
        next_due: int | None,
    ) -> bool:
        now = now_ts()
        with self.db.transaction() as conn:
            if expected is None:
                conn.execute(
                    "INSERT INTO enterprise_assistant_resources "
                    "(id,owner_id,category,config_json,secret_blob,next_due,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (resource_id, owner, category, json.dumps(config), secret, next_due, now, now),
                )
                return True
            cur = conn.execute(
                "UPDATE enterprise_assistant_resources SET config_json=?,secret_blob=?,next_due=?,"
                "version=version+1,updated_at=? WHERE id=? AND owner_id=? AND category=? AND version=?",
                (json.dumps(config), secret, next_due, now, resource_id, owner, category, expected),
            )
            return bool(cur.rowcount == 1)

    def due(self, now: int) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM enterprise_assistant_resources WHERE category='job' "
                "AND next_due<=? ORDER BY next_due LIMIT 50",
                (now,),
            ).fetchall()
        return [_decode(r) for r in rows]

    def advance(self, resource_id: str, version: int, due: int, next_due: int | None) -> bool:
        with self.db.transaction() as conn:
            cur = conn.execute(
                "UPDATE enterprise_assistant_resources SET next_due=? WHERE id=? AND version=? AND next_due=?",
                (next_due, resource_id, version, due),
            )
            return bool(cur.rowcount == 1)

    def enqueue(
        self, run_id: str, owner: int, job_id: str, trigger: str, snapshot: dict[str, Any]
    ) -> bool:
        # ON CONFLICT includes trigger dedupe and the partial active-run index.
        now = now_ts()
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO enterprise_assistant_runs "
                "(id,owner_id,job_id,trigger_key,status,snapshot_json,created_at,updated_at) "
                "VALUES (?,?,?,?,'queued',?,?,?) ON CONFLICT DO NOTHING",
                (run_id, owner, job_id, trigger, json.dumps(snapshot), now, now),
            )
            return bool(cur.rowcount == 1)

    def run(self, run_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM enterprise_assistant_runs WHERE id=?", (run_id,)
            ).fetchone()
        return _decode(row) if row else None

    def runs(self, owner: int) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM enterprise_assistant_runs WHERE owner_id=? ORDER BY seq DESC LIMIT 100",
                (owner,),
            ).fetchall()
        return [_decode(r) for r in rows]

    def latest_for_job(self, owner: int, job_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM enterprise_assistant_runs WHERE owner_id=? AND job_id=? ORDER BY seq DESC LIMIT 1",
                (owner, job_id),
            ).fetchone()
        return _decode(row) if row else None

    def pending(self) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM enterprise_assistant_runs WHERE status IN ('queued','approved') "
                "ORDER BY seq LIMIT 20",
            ).fetchall()
        return [_decode(r) for r in rows]

    def transition(
        self,
        run_id: str,
        expected: int,
        old: str,
        new: str,
        *,
        content: str | None = None,
        content_hash: str | None = None,
        evidence: list[dict[str, Any]] | None = None,
        error: str = "",
    ) -> bool:
        fields: dict[str, Any] = {"status": new, "error_code": error, "updated_at": now_ts()}
        if content is not None:
            fields["content"] = content
        if content_hash is not None:
            fields["content_hash"] = content_hash
        if evidence is not None:
            fields["evidence_json"] = json.dumps(evidence, ensure_ascii=False)
        assignments = ",".join(f"{k}=?" for k in fields)
        with self.db.transaction() as conn:
            cur = conn.execute(
                f"UPDATE enterprise_assistant_runs SET {assignments},version=version+1 "
                "WHERE id=? AND version=? AND status=?",
                (*fields.values(), run_id, expected, old),
            )
            return bool(cur.rowcount == 1)

    def recover(self) -> None:
        # A process died in a request: never automatically repeat a possible write.
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE enterprise_assistant_runs SET status=CASE WHEN status='executing' THEN 'unknown' ELSE 'failed' END,"
                "error_code='PROCESS_INTERRUPTED',"
                "version=version+1 WHERE status IN ('running','executing') AND updated_at<?",
                (now_ts() - 600,),
            )
