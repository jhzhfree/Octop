"""SQL-only one-shot inspection claims; no automatic retry after an uncertain attempt."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import uuid4

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import now_ts


class SceneInspectionRepo:
    def __init__(self, db: DatabasePool) -> None:
        self.db = db

    @staticmethod
    def _check_worker(conn: Any, run: Any, snapshot: dict[str, Any], lock: str) -> None:
        worker_id = (snapshot.get("worker") or {}).get("worker_id")
        worker = conn.execute(
            "SELECT * FROM digital_workers WHERE worker_id=?" + lock, (worker_id,)
        ).fetchone()
        goal = conn.execute(
            "SELECT * FROM digital_worker_goals WHERE goal_id=?" + lock,
            ((snapshot.get("goal") or {}).get("goal_id"),),
        ).fetchone()
        if (
            not worker
            or worker["status"] != "active"
            or worker["organization_id"] != run["organization_id"]
            or worker["agent_id"] != run["agent_id"]
            or not goal
            or goal["status"] != "active"
            or goal["worker_id"] != worker_id
        ):
            raise ValueError("inspection worker or goal changed")
        assignment = conn.execute(
            "SELECT assignment_id FROM organization_assignments WHERE position_id=? AND subject_type='agent' AND subject_id=?"
            + lock,
            (worker["position_id"], run["agent_id"]),
        ).fetchone()
        if not assignment or worker["position_id"] != run["position_id"]:
            raise ValueError("inspection appointment changed")

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM scene_inspection_batches WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["sources"] = json.loads(result.pop("sources_json"))
        result["counts"] = json.loads(result.pop("counts_json"))
        return result

    def claim(self, run_id: str, cron_id: str, actor_id: int, agent_id: str) -> dict[str, Any]:
        lock = " FOR UPDATE" if self.db.dialect == "postgresql" else ""
        now = now_ts()
        with self.db.transaction() as conn:
            user = conn.execute("SELECT * FROM users WHERE id=?" + lock, (actor_id,)).fetchone()
            identity = conn.execute(
                "SELECT organization_id FROM enterprise_scene_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if identity is None:
                raise ValueError("inspection run unavailable")
            member = conn.execute(
                "SELECT * FROM organization_members WHERE organization_id=? AND user_id=?" + lock,
                (identity["organization_id"], actor_id),
            ).fetchone()
            run = conn.execute(
                "SELECT * FROM enterprise_scene_runs WHERE run_id=?" + lock, (run_id,)
            ).fetchone()
            if (
                run is None
                or run["status"] != "queued"
                or run["cron_id"] != cron_id
                or run["requested_by"] != actor_id
                or run["agent_id"] != agent_id
                or run["approved_by"] in (None, actor_id)
            ):
                raise ValueError("inspection execution is not approved")
            flags_row = conn.execute(
                "SELECT value FROM settings WHERE key=?" + lock,
                ("enterprise_assistant.features.v1",),
            ).fetchone()
            flags = json.loads(flags_row["value"]) if flags_row else {}
            agent = conn.execute(
                "SELECT * FROM agents WHERE agent_id=?" + lock, (agent_id,)
            ).fetchone()
            if (
                not user
                or user["disabled"]
                or not (user["role"] == "admin" or member)
                or (
                    user["role"] != "admin"
                    and "tasks" not in json.loads(user["permissions"] or "[]")
                )
                or not all(flags.get(k) is True for k in ("enabled", "inspection", "reports"))
                or not agent
                or not agent["enabled"]
                or agent["user_id"] != actor_id
            ):
                raise ValueError("inspection authority changed")
            snapshot = json.loads(run["plan_snapshot_json"] or "{}")
            self._check_worker(conn, run, snapshot, lock)
            scope = snapshot.get("inspection") or {}
            sources = scope.get("sources")
            if (
                scope.get("share_results") is not True
                or not isinstance(sources, list)
                or not 1 <= len(sources) <= 10
            ):
                raise ValueError("inspection scope invalid")
            if conn.execute(
                "SELECT run_id FROM scene_inspection_batches WHERE run_id=?", (run_id,)
            ).fetchone():
                raise ValueError("inspection already attempted; submit a new run")
            prepared = []
            for source in sources:
                job = conn.execute(
                    "SELECT * FROM enterprise_assistant_resources WHERE id=?" + lock,
                    (source["job_id"],),
                ).fetchone()
                connection = conn.execute(
                    "SELECT * FROM enterprise_assistant_resources WHERE id=?" + lock,
                    (source["connection_id"],),
                ).fetchone()
                if (
                    not job
                    or not connection
                    or job["owner_id"] != actor_id
                    or connection["owner_id"] != actor_id
                    or job["category"] != "job"
                    or connection["category"] != "connection"
                    or job["version"] != source["job_version"]
                    or connection["version"] != source["connection_version"]
                ):
                    raise ValueError("inspection configuration changed")
                config = json.loads(job["config_json"])
                conn_config = json.loads(connection["config_json"])
                if (
                    config.get("kind") != "inspection"
                    or config.get("agent_id") != agent_id
                    or config.get("connection_id") != source["connection_id"]
                    or conn_config.get("kind") != "inspection"
                    or conn_config.get("inspection_transport", "http") != "http"
                ):
                    raise ValueError("inspection scope mismatch")
                prepared.append(
                    {
                        "job_id": job["id"],
                        "run_id": str(uuid4()),
                        "name": config["name"],
                        "snapshot": {
                            "job": config,
                            "job_version": job["version"],
                            "connection": conn_config,
                            "connection_version": connection["version"],
                        },
                    }
                )
            public = [{k: item[k] for k in ("job_id", "run_id", "name")} for item in prepared]
            conn.execute(
                "INSERT INTO scene_inspection_batches(run_id,status,sources_json,created_at,updated_at) VALUES (?,'running',?,?,?)",
                (run_id, json.dumps(public), now, now),
            )
            for item in prepared:
                conn.execute(
                    "INSERT INTO enterprise_assistant_runs(id,owner_id,job_id,trigger_key,status,snapshot_json,created_at,updated_at) VALUES (?,?,?,?,'queued',?,?,?)",
                    (
                        item["run_id"],
                        actor_id,
                        item["job_id"],
                        "scene:" + run_id,
                        json.dumps(item["snapshot"]),
                        now,
                        now,
                    ),
                )
                conn.execute(
                    "INSERT INTO scene_inspection_sources(source_run_id,scene_run_id) VALUES (?,?)",
                    (item["run_id"], run_id),
                )
        return self.get(run_id) or {}

    def finish(
        self,
        run_id: str,
        content: str,
        counts: dict[str, int],
        *,
        expected_flags: str,
        expected_sources: dict[str, int],
    ) -> None:
        digest = hashlib.sha256(content.encode()).hexdigest()
        lock = " FOR UPDATE" if self.db.dialect == "postgresql" else ""
        with self.db.transaction() as conn:
            identity = conn.execute(
                "SELECT requested_by,organization_id,agent_id FROM enterprise_scene_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if identity is None:
                raise ValueError("inspection run unavailable")
            actor_id = identity["requested_by"]
            user = conn.execute("SELECT * FROM users WHERE id=?" + lock, (actor_id,)).fetchone()
            member = conn.execute(
                "SELECT * FROM organization_members WHERE organization_id=? AND user_id=?" + lock,
                (identity["organization_id"], actor_id),
            ).fetchone()
            run = conn.execute(
                "SELECT * FROM enterprise_scene_runs WHERE run_id=?" + lock, (run_id,)
            ).fetchone()
            flags_row = conn.execute(
                "SELECT value FROM settings WHERE key=?" + lock,
                ("enterprise_assistant.features.v1",),
            ).fetchone()
            flags = json.loads(flags_row["value"]) if flags_row else {}
            agent = conn.execute(
                "SELECT * FROM agents WHERE agent_id=?" + lock, (identity["agent_id"],)
            ).fetchone()
            if (
                not user
                or user["disabled"]
                or not (user["role"] == "admin" or member)
                or (
                    user["role"] != "admin"
                    and "tasks" not in json.loads(user["permissions"] or "[]")
                )
                or not all(flags.get(k) is True for k in ("enabled", "inspection", "reports"))
                or not agent
                or not agent["enabled"]
                or agent["user_id"] != actor_id
                or not run
                or run["status"] != "queued"
            ):
                raise ValueError("inspection publication authority changed")
            if not flags_row or flags_row["value"] != expected_flags:
                raise ValueError("inspection publication settings changed")
            scope = json.loads(run["plan_snapshot_json"])["inspection"]
            for source in scope["sources"]:
                for resource_id, version in (
                    (source["job_id"], source["job_version"]),
                    (source["connection_id"], source["connection_version"]),
                ):
                    resource = conn.execute(
                        "SELECT owner_id,version FROM enterprise_assistant_resources WHERE id=?"
                        + lock,
                        (resource_id,),
                    ).fetchone()
                    if (
                        not resource
                        or resource["owner_id"] != actor_id
                        or resource["version"] != version
                    ):
                        raise ValueError("inspection publication source changed")
            self._check_worker(conn, run, json.loads(run["plan_snapshot_json"] or "{}"), lock)
            # Publish only from the exact terminal source versions used to render.
            # Lock source rows in the same transaction as report and scene hashes;
            # a late transition cannot silently substitute different evidence.
            batch = conn.execute(
                "SELECT status,sources_json FROM scene_inspection_batches WHERE run_id=?" + lock,
                (run_id,),
            ).fetchone()
            if not batch or batch["status"] != "running":
                raise ValueError("inspection completion conflict")
            sources = json.loads(batch["sources_json"])
            if not sources or {s["run_id"] for s in sources} != set(expected_sources):
                raise ValueError("inspection publication source set changed")
            for source in sorted(sources, key=lambda s: s["run_id"]):
                child = conn.execute(
                    "SELECT r.* FROM enterprise_assistant_runs r "
                    "JOIN scene_inspection_sources s ON s.source_run_id=r.id "
                    "WHERE r.id=? AND s.scene_run_id=?" + lock,
                    (source["run_id"], run_id),
                ).fetchone()
                if (
                    not child
                    or child["owner_id"] != actor_id
                    or child["job_id"] != source["job_id"]
                    or child["status"] not in {"succeeded", "failed"}
                    or child["version"] != expected_sources[source["run_id"]]
                ):
                    raise ValueError("inspection publication evidence changed")
            cursor = conn.execute(
                "UPDATE scene_inspection_batches SET status='reported',content=?,content_sha256=?,counts_json=?,updated_at=? WHERE run_id=? AND status='running'",
                (content, digest, json.dumps(counts), now_ts(), run_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("inspection completion conflict")
            cursor = conn.execute(
                "UPDATE enterprise_scene_runs SET inspection_report_sha256=? WHERE run_id=? AND status='queued'",
                (digest, run_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("inspection run changed")

    def fail(self, run_id: str) -> None:
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT r.status,r.evidence_json FROM enterprise_assistant_runs r JOIN scene_inspection_sources s ON s.source_run_id=r.id WHERE s.scene_run_id=?",
                (run_id,),
            ).fetchall()
            attempted = sum(
                any(e.get("stage") == "collection_started" for e in json.loads(r["evidence_json"]))
                for r in rows
            )
            counts = {
                "sources": len(rows),
                "attempted": attempted,
                "succeeded": sum(r["status"] == "succeeded" for r in rows),
                "failed": sum(r["status"] != "succeeded" for r in rows),
                "unknown": sum(r["status"] in ("running", "unknown") for r in rows),
                "health_unknown": 0,
                "not_started": len(rows) - attempted,
                "reports_persisted": 0,
                "scene_bound": 0,
            }
            conn.execute(
                "UPDATE scene_inspection_batches SET status='failed',counts_json=?,updated_at=? WHERE run_id=? AND status='running'",
                (json.dumps(counts), now_ts(), run_id),
            )
            conn.execute(
                "UPDATE enterprise_assistant_runs SET status='failed',error_code='SCENE_INSPECTION_INTERRUPTED',version=version+1 WHERE status IN ('queued','running') AND id IN (SELECT source_run_id FROM scene_inspection_sources WHERE scene_run_id=?)",
                (run_id,),
            )
