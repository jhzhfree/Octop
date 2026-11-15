"""Local, immutable publication proposals and independent approvals; no dispatch.

SQL transactions recheck identities and bindings. SQLite serializes writes; PG
locks existing authority and source rows, not global membership/permission epochs.
Nothing in this table grants permission for future external network operations.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import now_ts
from octop.infra.db.repos.scene_acceptance import AcceptanceStoreError, snapshot_hash


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


class MattermostPublicationRepo:
    def __init__(self, db: DatabasePool) -> None:
        self.db = db

    def _access(self, conn: Any, org: str, run_id: str, actor: int) -> dict[str, Any]:
        lock = " FOR UPDATE" if self.db.dialect == "postgresql" else ""
        user = conn.execute("SELECT * FROM users WHERE id=?" + lock, (actor,)).fetchone()
        member = conn.execute(
            "SELECT * FROM organization_members WHERE organization_id=? AND user_id=?" + lock,
            (org, actor),
        ).fetchone()
        run_row = conn.execute(
            "SELECT * FROM enterprise_scene_runs WHERE organization_id=? AND run_id=?" + lock,
            (org, run_id),
        ).fetchone()
        if run_row is None:
            raise AcceptanceStoreError("not_found", "publication unavailable")
        run = dict(run_row)
        agent = conn.execute(
            "SELECT * FROM agents WHERE agent_id=?" + lock, (run["agent_id"],)
        ).fetchone()
        flags_row = conn.execute(
            "SELECT value FROM settings WHERE key=?" + lock,
            ("enterprise_assistant.features.v1",),
        ).fetchone()
        flags = json.loads(flags_row["value"]) if flags_row else {}
        admin = user is not None and user["role"] == "admin"
        if (
            not user
            or user["disabled"]
            or not agent
            or not agent["enabled"]
            or not (admin or member and member["role"] in {"owner", "manager"})
            or not (admin or agent["user_id"] == actor or agent["is_shared"])
            or not (admin or "tasks" in json.loads(user["permissions"] or "[]"))
            or not all(flags.get(k) is True for k in ("enabled", "reports", "inspection"))
            or not (admin or actor in {run["requested_by"], run["approved_by"]})
        ):
            raise AcceptanceStoreError("forbidden", "publication access denied")
        run["_actor_admin"] = admin
        return run

    def _snapshot(self, conn: Any, run: dict[str, Any]) -> tuple[dict[str, Any], str]:
        lock = " FOR UPDATE" if self.db.dialect == "postgresql" else ""
        acceptance = conn.execute(
            "SELECT * FROM scene_run_acceptances WHERE run_id=?" + lock, (run["run_id"],)
        ).fetchone()
        report = conn.execute(
            "SELECT * FROM scene_inspection_batches WHERE run_id=?" + lock, (run["run_id"],)
        ).fetchone()
        if (
            run["status"] != "succeeded"
            or run["report_attachment_sha256"]
            or not acceptance
            or acceptance["decision"] != "accepted"
            or acceptance["organization_id"] != run["organization_id"]
            or acceptance["reviewer_id"] == run["requested_by"]
            or acceptance["snapshot_sha256"] != snapshot_hash(run)
            or not report
            or report["status"] != "reported"
            or not report["content"]
            or len(report["content"].encode()) > 65536
            or report["content_sha256"] != run["inspection_report_sha256"]
            or hashlib.sha256(report["content"].encode()).hexdigest() != report["content_sha256"]
            or acceptance["evidence_sha256"] != report["content_sha256"]
            or acceptance["evidence_length"] != len(report["content"])
        ):
            raise AcceptanceStoreError("stale", "accepted same-run inspection report required")
        # Historical acceptance is not permission to publish after a principal is
        # disabled, removed, demoted or loses access. No cached JWT role is used.
        agent = conn.execute(
            "SELECT * FROM agents WHERE agent_id=?" + lock, (run["agent_id"],)
        ).fetchone()
        for participant in sorted({run["requested_by"], acceptance["reviewer_id"]}):
            user = conn.execute("SELECT * FROM users WHERE id=?" + lock, (participant,)).fetchone()
            member = conn.execute(
                "SELECT * FROM organization_members WHERE organization_id=? AND user_id=?" + lock,
                (run["organization_id"], participant),
            ).fetchone()
            if (
                not user
                or user["disabled"]
                or not agent
                or not agent["enabled"]
                or not (
                    user["role"] == "admin"
                    or member
                    and member["role"] in {"owner", "manager"}
                    and "tasks" in json.loads(user["permissions"] or "[]")
                    and (agent["user_id"] == participant or agent["is_shared"])
                )
            ):
                raise AcceptanceStoreError("forbidden", "publication participant access changed")
        plan = json.loads(run["plan_snapshot_json"] or "{}")
        worker_id = (plan.get("worker") or {}).get("worker_id")
        worker = conn.execute(
            "SELECT * FROM digital_workers WHERE worker_id=?" + lock, (worker_id,)
        ).fetchone()
        intent = conn.execute(
            "SELECT * FROM worker_mattermost_intents WHERE worker_id=?" + lock, (worker_id,)
        ).fetchone()
        if (
            not worker
            or worker["organization_id"] != run["organization_id"]
            or worker["agent_id"] != run["agent_id"]
            or worker["position_id"] != run["position_id"]
            or worker["status"] != "active"
            or not intent
            or intent["status"] != "draft"
            or intent["organization_id"] != run["organization_id"]
            or intent["worker_version"] != worker["version"]
        ):
            raise AcceptanceStoreError("stale", "worker or destination changed")
        assignment = conn.execute(
            "SELECT x.assignment_id FROM organization_assignments x "
            "JOIN organization_positions p ON p.position_id=x.position_id "
            "WHERE p.organization_id=? AND x.position_id=? AND x.subject_type='agent' "
            "AND x.subject_id=?" + lock,
            (run["organization_id"], run["position_id"], run["agent_id"]),
        ).fetchone()
        configuration = conn.execute(
            "SELECT * FROM mattermost_instances WHERE organization_id=? AND instance_key=?" + lock,
            (run["organization_id"], intent["instance_key"]),
        ).fetchone()
        if (
            not assignment
            or not configuration
            or configuration["status"] != "configured"
            or configuration["bot_id"] != intent["bot_id"]
        ):
            raise AcceptanceStoreError("stale", "configured destination required")
        snapshot = {
            "organization_id": run["organization_id"],
            "run_id": run["run_id"],
            "worker_id": worker_id,
            "worker_version": worker["version"],
            "intent_revision": intent["revision"],
            "configuration_revision": configuration["revision"],
            "instance_key": intent["instance_key"],
            "team_id": intent["team_id"],
            "channel_id": intent["channel_id"],
            "bot_id": intent["bot_id"],
            "report_sha256": report["content_sha256"],
            "proposed_by": run["requested_by"],
            "acceptance_reviewer_id": acceptance["reviewer_id"],
            "run_snapshot_sha256": acceptance["snapshot_sha256"],
            "configuration_sha256": _digest(dict(configuration)),
        }
        return snapshot, str(report["content"])

    @staticmethod
    def _audit(conn: Any, actor: int, action: str, row: dict[str, Any]) -> None:
        conn.execute(
            "INSERT INTO audit_log(ts,actor,action,target,payload) VALUES(?,?,?,?,?)",
            (
                now_ts(),
                str(actor),
                "scene.mattermost_publication." + action,
                row["run_id"],
                json.dumps(
                    {k: row[k] for k in ("organization_id", "publication_sha256", "status")}
                ),
            ),
        )

    @staticmethod
    def _view(row: dict[str, Any], content: str) -> dict[str, Any]:
        snapshot = json.loads(row["snapshot_json"])
        for key in ("acceptance_reviewer_id", "run_snapshot_sha256", "configuration_sha256"):
            snapshot.pop(key)
        return {
            **snapshot,
            **{
                k: row[k]
                for k in ("publication_sha256", "approved_by", "status", "created_at", "updated_at")
            },
            "content": content,
            "connected": False,
            "dispatch_enabled": False,
            "execution_authorized": False,
        }

    def candidate(self, org: str, run_id: str, actor: int) -> dict[str, Any]:
        """Read preview preconditions only; no publication or audit mutation."""
        with self.db.transaction() as conn:
            run = self._access(conn, org, run_id, actor)
            if actor != run["requested_by"]:
                raise AcceptanceStoreError("forbidden", "requesting owner must propose publication")
            snapshot, _ = self._snapshot(conn, run)
            existing = conn.execute(
                "SELECT * FROM scene_mattermost_publications WHERE run_id=?", (run_id,)
            ).fetchone()
            if existing:
                row = dict(existing)
                if row["status"] not in {"pending_approval", "blocked_not_connected"}:
                    raise AcceptanceStoreError("conflict", "publication is terminal")
                self._assert_snapshot(row, snapshot)
                self._check_approver(conn, row, snapshot)
            return {
                "expected_" + key: snapshot[key]
                for key in (
                    "report_sha256",
                    "worker_version",
                    "intent_revision",
                    "configuration_revision",
                )
            }

    def preview(
        self, org: str, run_id: str, actor: int, expected: dict[str, Any]
    ) -> dict[str, Any]:
        with self.db.transaction() as conn:
            run = self._access(conn, org, run_id, actor)
            if actor != run["requested_by"]:
                raise AcceptanceStoreError("forbidden", "requesting owner must propose publication")
            snapshot, content = self._snapshot(conn, run)
            if any(snapshot[key] != value for key, value in expected.items()):
                raise AcceptanceStoreError("stale", "publication source or destination changed")
            digest = _digest(snapshot)
            existing = conn.execute(
                "SELECT * FROM scene_mattermost_publications WHERE run_id=?", (run_id,)
            ).fetchone()
            if existing:
                row = dict(existing)
                if row["publication_sha256"] != digest or row["status"] not in {
                    "pending_approval",
                    "blocked_not_connected",
                }:
                    raise AcceptanceStoreError("conflict", "publication is immutable or terminal")
                self._assert_snapshot(row, snapshot)
                self._check_approver(conn, row, snapshot)
                return self._view(row, content)
            now = now_ts()
            row = {
                "run_id": run_id,
                "organization_id": org,
                "snapshot_json": json.dumps(snapshot, sort_keys=True),
                "publication_sha256": digest,
                "proposed_by": actor,
                "approved_by": None,
                "status": "pending_approval",
                "created_at": now,
                "updated_at": now,
            }
            conn.execute(
                "INSERT INTO scene_mattermost_publications ("
                + ",".join(row)
                + ") VALUES ("
                + ",".join("?" for _ in row)
                + ")",
                tuple(row.values()),
            )
            self._audit(conn, actor, "proposed", row)
            return self._view(row, content)

    def get(self, org: str, run_id: str, actor: int) -> dict[str, Any]:
        with self.db.transaction() as conn:
            run = self._access(conn, org, run_id, actor)
            row = self._existing(conn, org, run_id)
            snapshot, content = self._snapshot(conn, run)
            self._assert_snapshot(row, snapshot)
            self._check_approver(conn, row, snapshot)
            return self._view(row, content)

    @staticmethod
    def _existing(conn: Any, org: str, run_id: str) -> dict[str, Any]:
        row = conn.execute(
            "SELECT * FROM scene_mattermost_publications WHERE organization_id=? AND run_id=?",
            (org, run_id),
        ).fetchone()
        if row is None:
            raise AcceptanceStoreError("not_found", "publication unavailable")
        return dict(row)

    @staticmethod
    def _assert_snapshot(row: dict[str, Any], snapshot: dict[str, Any]) -> None:
        if (
            row["publication_sha256"] != _digest(snapshot)
            or json.loads(row["snapshot_json"]) != snapshot
        ):
            raise AcceptanceStoreError("stale", "publication source or destination changed")

    def _check_approver(self, conn: Any, row: dict[str, Any], snapshot: dict[str, Any]) -> None:
        if row["approved_by"] is None:
            if row["status"] == "blocked_not_connected":
                raise AcceptanceStoreError("stale", "publication approval missing")
            return
        approver = row["approved_by"]
        current = self._access(conn, row["organization_id"], row["run_id"], approver)
        if (
            approver == row["proposed_by"]
            or approver == current["requested_by"]
            or not (current["_actor_admin"] or approver == snapshot["acceptance_reviewer_id"])
        ):
            raise AcceptanceStoreError("forbidden", "publication approval access changed")

    def decide(
        self, org: str, run_id: str, actor: int, digest: str, *, revoke: bool
    ) -> dict[str, Any]:
        with self.db.transaction() as conn:
            run = self._access(conn, org, run_id, actor)
            row = self._existing(conn, org, run_id)
            if row["publication_sha256"] != digest:
                raise AcceptanceStoreError("stale", "publication changed")
            # Cancellation may proceed after source/configuration or earlier
            # participants were revoked. Never expose stale report content here.
            content = ""
            if not revoke:
                snapshot, content = self._snapshot(conn, run)
                self._assert_snapshot(row, snapshot)
                self._check_approver(conn, row, snapshot)
                if (
                    actor == row["proposed_by"]
                    or actor == run["requested_by"]
                    or not (run["_actor_admin"] or actor == snapshot["acceptance_reviewer_id"])
                ):
                    raise AcceptanceStoreError("forbidden", "independent reviewer required")
            desired = "revoked" if revoke else "blocked_not_connected"
            if row["status"] == desired and (revoke or row["approved_by"] == actor):
                return self._view(row, content)
            if row["status"] not in (
                {"pending_approval", "blocked_not_connected"} if revoke else {"pending_approval"}
            ):
                raise AcceptanceStoreError(
                    "conflict", "publication is terminal or already approved"
                )
            row.update(status=desired, updated_at=now_ts())
            if not revoke:
                row["approved_by"] = actor
            conn.execute(
                "UPDATE scene_mattermost_publications SET status=?,approved_by=?,updated_at=? WHERE run_id=?",
                (row["status"], row["approved_by"], row["updated_at"], run_id),
            )
            self._audit(conn, actor, "revoked" if revoke else "approved_locally", row)
            return self._view(row, content)
