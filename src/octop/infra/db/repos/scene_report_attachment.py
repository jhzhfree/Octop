"""One immutable, owner-shared report snapshot per scene run; SQL persistence only."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import now_ts
from octop.infra.db.repos.scene_acceptance import AcceptanceStoreError


def attachment_hash(record: dict[str, Any]) -> str:
    value = {
        key: record[key]
        for key in (
            "run_id",
            "organization_id",
            "report_run_id",
            "report_version",
            "report_content_hash",
            "content",
            "attached_by",
        )
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


class SceneReportAttachmentRepo:
    def __init__(self, db: DatabasePool) -> None:
        self.db = db

    def get(self, organization_id: str, run_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM scene_report_attachments WHERE organization_id=? AND run_id=?",
                (organization_id, run_id),
            ).fetchone()
        return dict(row) if row else None

    def check_access(
        self, organization_id: str, run_id: str, actor_id: int, *, owner_only: bool = False
    ) -> None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT u.disabled,u.role,m.role AS member_role,r.requested_by,r.approved_by "
                "FROM enterprise_scene_runs r JOIN users u ON u.id=? "
                "LEFT JOIN organization_members m ON m.organization_id=r.organization_id "
                "AND m.user_id=u.id WHERE r.organization_id=? AND r.run_id=?",
                (actor_id, organization_id, run_id),
            ).fetchone()
        if row is None:
            raise AcceptanceStoreError("not_found", "run or actor unavailable")
        owner = actor_id == row["requested_by"] and (row["role"] == "admin" or row["member_role"])
        reviewer = actor_id != row["requested_by"] and (
            row["role"] == "admin"
            or row["member_role"] in {"owner", "manager"}
            and actor_id == row["approved_by"]
        )
        if row["disabled"] or not (owner or not owner_only and reviewer):
            raise AcceptanceStoreError("forbidden", "report attachment access denied")

    def attach(
        self, *, organization_id: str, run_id: str, actor_id: int, report: dict[str, Any]
    ) -> dict[str, Any]:
        lock = " FOR UPDATE" if self.db.dialect == "postgresql" else ""
        with self.db.transaction() as conn:
            # Same identity -> membership -> scene lock order as acceptance.decide.
            user = conn.execute("SELECT * FROM users WHERE id=?" + lock, (actor_id,)).fetchone()
            member = conn.execute(
                "SELECT * FROM organization_members WHERE organization_id=? AND user_id=?" + lock,
                (organization_id, actor_id),
            ).fetchone()
            run = conn.execute(
                "SELECT * FROM enterprise_scene_runs WHERE organization_id=? AND run_id=?" + lock,
                (organization_id, run_id),
            ).fetchone()
            if run is None:
                raise AcceptanceStoreError("not_found", "scene run unavailable")
            if (
                not user
                or user["disabled"]
                or run["requested_by"] != actor_id
                or not (user["role"] == "admin" or member)
            ):
                raise AcceptanceStoreError("forbidden", "current requesting owner required")
            flags_row = conn.execute(
                "SELECT value FROM settings WHERE key=?" + lock,
                ("enterprise_assistant.features.v1",),
            ).fetchone()
            flags = json.loads(flags_row["value"]) if flags_row else {}
            if (
                user["role"] != "admin"
                and "tasks" not in json.loads(user["permissions"] or "[]")
                or not all(flags.get(key) is True for key in ("enabled", "reports", "inspection"))
            ):
                raise AcceptanceStoreError("forbidden", "current report permission required")
            if run["status"] != "succeeded":
                raise AcceptanceStoreError("stale", "scene execution must succeed first")
            if (
                run["report_attachment_sha256"]
                or conn.execute(
                    "SELECT run_id FROM scene_run_acceptances WHERE run_id=?", (run_id,)
                ).fetchone()
            ):
                raise AcceptanceStoreError("conflict", "run already attached or reviewed")
            source = conn.execute(
                "SELECT * FROM enterprise_assistant_runs WHERE id=?" + lock, (report["id"],)
            ).fetchone()
            agent = conn.execute(
                "SELECT * FROM agents WHERE agent_id=?" + lock, (run["agent_id"],)
            ).fetchone()
            if not agent or not agent["enabled"] or agent["user_id"] != actor_id:
                raise AcceptanceStoreError("forbidden", "owned active agent required")
            if not source or source["owner_id"] != actor_id:
                raise AcceptanceStoreError("not_found", "report unavailable")
            if (
                source["version"] != report["version"]
                or source["content_hash"] != report["content_hash"]
                or source["content"] != report["content"]
                or source["status"] != "awaiting_approval"
                or json.loads(source["snapshot_json"])["job"]["agent_id"] != run["agent_id"]
            ):
                raise AcceptanceStoreError("stale", "report changed before sharing")
            record = {
                "run_id": run_id,
                "organization_id": organization_id,
                "report_run_id": source["id"],
                "report_version": source["version"],
                "report_content_hash": source["content_hash"],
                "attached_by": actor_id,
                "attached_at": now_ts(),
                "content": source["content"],
            }
            record["attachment_sha256"] = attachment_hash(record)
            keys = list(record)
            conn.execute(
                "INSERT INTO scene_report_attachments ("
                + ",".join(keys)
                + ") VALUES ("
                + ",".join("?" for _ in keys)
                + ")",
                tuple(record.values()),
            )
            conn.execute(
                "UPDATE enterprise_scene_runs SET report_attachment_sha256=? WHERE run_id=?",
                (record["attachment_sha256"], run_id),
            )
            conn.execute(
                "INSERT INTO audit_log(ts,actor,action,target,payload) VALUES (?,?,?,?,?)",
                (
                    record["attached_at"],
                    str(actor_id),
                    "scene.run.report_attached",
                    run_id,
                    json.dumps(
                        {
                            key: record[key]
                            for key in (
                                "organization_id",
                                "report_run_id",
                                "report_version",
                                "attachment_sha256",
                            )
                        }
                    ),
                ),
            )
            return record
