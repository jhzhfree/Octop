"""Immutable human delivery reviews. Acceptance and minimal audit commit together."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import now_ts


class AcceptanceStoreError(Exception):
    """Storage precondition failed; transport mapping belongs to the caller."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def snapshot_hash(run: dict[str, Any]) -> str:
    """Bind review to the run, not the currently editable plan or scene."""
    snapshot = run.get("plan_snapshot")
    if "plan_snapshot_json" in run:
        snapshot = json.loads(run["plan_snapshot_json"] or "null")
    value = {
        key: run.get(key)
        for key in (
            "run_id",
            "organization_id",
            "scene_version",
            "plan_id",
            "cron_id",
            "requested_by",
            "approved_by",
            "status",
            "updated_at",
        )
    }
    value["plan_snapshot"] = snapshot
    # Preserve old hashes when no attachment exists (including pre-migration reviews).
    if run.get("report_attachment_sha256"):
        value["report_attachment_sha256"] = run["report_attachment_sha256"]
    if run.get("inspection_report_sha256"):
        value["inspection_report_sha256"] = run["inspection_report_sha256"]
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


class SceneAcceptanceRepo:
    def __init__(self, db: DatabasePool) -> None:
        self.db = db

    def get(self, organization_id: str, run_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM scene_run_acceptances WHERE organization_id=? AND run_id=?",
                (organization_id, run_id),
            ).fetchone()
        return dict(row) if row else None

    def check_reviewer(self, organization_id: str, run_id: str, reviewer_id: int) -> None:
        """Recheck current identity for sensitive read paths, without cached admin claims."""
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT u.disabled,u.role AS user_role,m.role AS member_role,"
                "r.requested_by,r.approved_by FROM enterprise_scene_runs r "
                "JOIN users u ON u.id=? LEFT JOIN organization_members m "
                "ON m.organization_id=r.organization_id AND m.user_id=u.id "
                "WHERE r.organization_id=? AND r.run_id=?",
                (reviewer_id, organization_id, run_id),
            ).fetchone()
        if row is None:
            raise AcceptanceStoreError("not_found", "scene run or reviewer not found")
        admin = row["user_role"] == "admin"
        if (
            row["disabled"]
            or reviewer_id == row["requested_by"]
            or not (admin or row["member_role"] in {"owner", "manager"})
            or not (admin or reviewer_id == row["approved_by"])
        ):
            raise AcceptanceStoreError("forbidden", "independent authorized reviewer required")

    def decide(
        self,
        *,
        organization_id: str,
        run_id: str,
        reviewer_id: int,
        decision: str,
        evidence_sha256: str,
        evidence_length: int,
        expected_snapshot_hash: str,
    ) -> dict[str, Any]:
        if decision not in {"accepted", "changes_requested"}:
            raise AcceptanceStoreError("invalid", "invalid acceptance decision")
        lock = " FOR UPDATE" if self.db.dialect == "postgresql" else ""
        with self.db.transaction() as conn:
            # Lock identities before decision, including on idempotent retries.
            user = conn.execute("SELECT * FROM users WHERE id=?" + lock, (reviewer_id,)).fetchone()
            member = conn.execute(
                "SELECT * FROM organization_members WHERE organization_id=? AND user_id=?" + lock,
                (organization_id, reviewer_id),
            ).fetchone()
            run_row = conn.execute(
                "SELECT * FROM enterprise_scene_runs WHERE organization_id=? AND run_id=?" + lock,
                (organization_id, run_id),
            ).fetchone()
            if not run_row:
                raise AcceptanceStoreError("not_found", "scene run not found")
            run = dict(run_row)
            admin = user is not None and user["role"] == "admin"
            if (
                user is None
                or user["disabled"]
                or not (admin or member and member["role"] in {"owner", "manager"})
                or not (admin or reviewer_id == run["approved_by"])
                or reviewer_id == run["requested_by"]
            ):
                raise AcceptanceStoreError("forbidden", "independent authorized reviewer required")
            if run["status"] != "succeeded" or snapshot_hash(run) != expected_snapshot_hash:
                raise AcceptanceStoreError("stale", "run changed or has not succeeded")
            existing = conn.execute(
                "SELECT * FROM scene_run_acceptances WHERE run_id=?", (run_id,)
            ).fetchone()
            if existing:
                if all(
                    existing[k] == v
                    for k, v in {
                        "reviewer_id": reviewer_id,
                        "decision": decision,
                        "evidence_sha256": evidence_sha256,
                        "snapshot_sha256": expected_snapshot_hash,
                    }.items()
                ):
                    return dict(existing)
                raise AcceptanceStoreError("conflict", "run already reviewed")
            now = now_ts()
            conn.execute(
                "INSERT INTO scene_run_acceptances(run_id,organization_id,decision,reviewer_id,"
                "reviewed_at,evidence_sha256,evidence_length,snapshot_sha256,scene_version) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    organization_id,
                    decision,
                    reviewer_id,
                    now,
                    evidence_sha256,
                    evidence_length,
                    expected_snapshot_hash,
                    run["scene_version"],
                ),
            )
            conn.execute(
                "INSERT INTO audit_log(ts,actor,action,target,payload) VALUES (?,?,?,?,?)",
                (
                    now,
                    str(reviewer_id),
                    "scene.run.delivery_reviewed",
                    run_id,
                    json.dumps(
                        {
                            "organization_id": organization_id,
                            "decision": decision,
                            "evidence_sha256": evidence_sha256,
                            "snapshot_sha256": expected_snapshot_hash,
                        }
                    ),
                ),
            )
            return dict(
                conn.execute(
                    "SELECT * FROM scene_run_acceptances WHERE run_id=?", (run_id,)
                ).fetchone()
            )
