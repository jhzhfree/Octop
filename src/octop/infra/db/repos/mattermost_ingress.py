"""SQL-only broker nonce and pending-intake transactions; never creates runnable work."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from octop.infra.db.pool import DatabasePool


class IntakeError(Exception):
    def __init__(self, status: int, code: str) -> None:
        self.status = status
        self.code = code
        super().__init__(code)


class MattermostIngressRepo:
    def __init__(self, db: DatabasePool) -> None:
        self.db = db
        self.lock = " FOR UPDATE" if db.dialect == "postgresql" else ""

    def mapping(self, broker: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM mattermost_brokers WHERE broker_id=?", (broker,)
            ).fetchone()
        if not row or not row["enabled"]:
            raise IntakeError(401, "BROKER_UNAUTHENTICATED")
        return dict(row)

    def _authorize(self, conn: Any, expected: dict[str, Any]) -> None:
        current = conn.execute(
            "SELECT * FROM mattermost_brokers WHERE broker_id=?" + self.lock,
            (expected["broker_id"],),
        ).fetchone()
        if not current or not current["enabled"] or dict(current) != expected:
            raise IntakeError(403, "BROKER_AUTHORITY_CHANGED")
        user = conn.execute(
            "SELECT * FROM users WHERE id=?" + self.lock, (expected["actor_id"],)
        ).fetchone()
        worker = conn.execute(
            "SELECT * FROM digital_workers WHERE worker_id=?" + self.lock, (expected["worker_id"],)
        ).fetchone()
        member = conn.execute(
            "SELECT * FROM organization_members WHERE organization_id=? AND user_id=?" + self.lock,
            (expected["organization_id"], expected["actor_id"]),
        ).fetchone()
        if (
            not user
            or user["disabled"]
            or not worker
            or worker["status"] != "active"
            or worker["organization_id"] != expected["organization_id"]
            or worker["version"] != expected["worker_version"]
        ):
            raise IntakeError(403, "BROKER_TARGET_UNAVAILABLE")
        if user["role"] != "admin" and (
            not member
            or member["role"] not in {"owner", "manager"}
            or "tasks" not in json.loads(user["permissions"] or "[]")
        ):
            raise IntakeError(403, "BROKER_TARGET_FORBIDDEN")
        agent = conn.execute(
            "SELECT * FROM agents WHERE agent_id=?" + self.lock, (worker["agent_id"],)
        ).fetchone()
        assignment = conn.execute(
            "SELECT * FROM organization_assignments WHERE position_id=? AND subject_type='agent' AND subject_id=?"
            + self.lock,
            (worker["position_id"], worker["agent_id"]),
        ).fetchone()
        if (
            not agent
            or not agent["enabled"]
            or not assignment
            or (
                user["role"] != "admin"
                and agent["user_id"] != user["id"]
                and not agent["is_shared"]
            )
        ):
            raise IntakeError(403, "BROKER_TARGET_FORBIDDEN")

    def consume_nonce(
        self, mapping: dict[str, Any], nonce: str, issued: int, clock: Callable[[], int]
    ) -> None:
        with self.db.transaction() as conn:
            self._authorize(conn, mapping)
            now = clock()
            if not issued <= now < issued + 30:
                raise IntakeError(401, "BROKER_AUTH_EXPIRED")
            if conn.execute(
                "SELECT 1 FROM mattermost_broker_nonces WHERE broker_id=? AND nonce=?",
                (mapping["broker_id"], nonce),
            ).fetchone():
                raise IntakeError(409, "BROKER_AUTH_REPLAY")
            if (
                conn.execute(
                    "SELECT COUNT(*) FROM mattermost_broker_nonces WHERE broker_id=?",
                    (mapping["broker_id"],),
                ).fetchone()[0]
                >= 4096
            ):
                raise IntakeError(429, "BROKER_NONCE_CAPACITY")
            conn.execute(
                "INSERT INTO mattermost_broker_nonces VALUES (?,?,?)",
                (mapping["broker_id"], nonce, now),
            )

    @staticmethod
    def _audit(conn: Any, row: dict[str, Any], action: str, now: int, operation: str = "") -> None:
        conn.execute(
            "INSERT INTO audit_log(ts,actor,action,target,payload) VALUES(?,?,?,?,?)",
            (
                now,
                "broker:" + row["broker_id"],
                "mattermost.intake." + action,
                row["delivery_key"],
                json.dumps(
                    {
                        "fingerprint": row["fingerprint"],
                        "revision": row["revision"],
                        "source_version": str(row["source_version"]),
                        "operation_id": operation,
                        "execution_authorized": False,
                    }
                ),
            ),
        )

    def _load(self, conn: Any, mapping: dict[str, Any], key: str) -> dict[str, Any]:
        row = conn.execute(
            "SELECT * FROM mattermost_intakes WHERE delivery_key=?" + self.lock, (key,)
        ).fetchone()
        if not row:
            raise IntakeError(404, "INTAKE_NOT_VISIBLE")
        result = dict(row)
        if any(
            result[k] != mapping[k]
            for k in ("broker_id", "organization_id", "worker_id", "actor_id", "binding_json")
        ):
            raise IntakeError(403, "INTAKE_SCOPE_CHANGED")
        if conn.execute(
            "SELECT 1 FROM mattermost_retired_intakes WHERE delivery_key=?", (key,)
        ).fetchone():
            raise IntakeError(410, "INTAKE_RETIRED")
        return result

    def inspect(self, mapping: dict[str, Any], key: str) -> dict[str, Any]:
        with self.db.transaction() as conn:
            self._authorize(conn, mapping)
            return self._load(conn, mapping, key)

    @staticmethod
    def _advance_observation(conn: Any, row: dict[str, Any], version: int, now: int) -> None:
        if version < row["observed_source_version"]:
            raise IntakeError(409, "SOURCE_VERSION_REWOUND")
        if version == row["observed_source_version"]:
            return
        conn.execute(
            "UPDATE mattermost_intakes SET observed_source_version=? WHERE delivery_key=?",
            (version, row["delivery_key"]),
        )
        row["observed_source_version"] = version
        conn.execute(
            "INSERT INTO audit_log(ts,actor,action,target,payload) VALUES(?,?,?,?,?)",
            (
                now,
                "broker:" + row["broker_id"],
                "mattermost.source_observed",
                row["delivery_key"],
                json.dumps(
                    {
                        "fingerprint": row["fingerprint"],
                        "observed_source_version": str(version),
                        "state_source_version": str(row["source_version"]),
                        "revision": row["revision"],
                        "execution_authorized": False,
                    }
                ),
            ),
        )

    def observe_source(
        self,
        mapping: dict[str, Any],
        identity: dict[str, Any],
        version: int,
        clock: Callable[[], int],
    ) -> None:
        """Commit matching source evidence separately from a requested business action.

        A later action may fail CAS or be denied by source state. Neither erases a
        committed observation. Never creates a new identity or authorizes execution.
        """
        with self.db.transaction() as conn:
            self._authorize(conn, mapping)
            if not conn.execute(
                "SELECT 1 FROM mattermost_intakes WHERE delivery_key=?", (identity["delivery_key"],)
            ).fetchone():
                return
            row = self._load(conn, mapping, identity["delivery_key"])
            if any(
                row[k] != identity[k]
                for k in ("fingerprint", "receipt", "commitment", "source_expires_at")
            ):
                raise IntakeError(409, "INTAKE_IDENTITY_CONFLICT")
            self._advance_observation(conn, row, version, clock())

    def accept(
        self,
        mapping: dict[str, Any],
        values: dict[str, Any],
        *,
        issued: int,
        expires: int,
        clock: Callable[[], int],
    ) -> tuple[dict[str, Any], bool]:
        with self.db.transaction() as conn:
            self._authorize(conn, mapping)
            now = clock()
            if not issued <= now < expires:
                raise IntakeError(422, "PROOF_EXPIRED")
            exists = conn.execute(
                "SELECT 1 FROM mattermost_intakes WHERE delivery_key=?", (values["delivery_key"],)
            ).fetchone()
            if exists:
                row = self._load(conn, mapping, values["delivery_key"])
                if any(
                    row[k] != values[k]
                    for k in ("fingerprint", "receipt", "commitment", "source_expires_at")
                ):
                    raise IntakeError(409, "INTAKE_IDENTITY_CONFLICT")
                if (
                    row["state"] in {"cancelled", "expired"}
                    or now * 1000 >= row["source_expires_at"]
                ):
                    raise IntakeError(410, "INTAKE_TERMINAL")
                self._advance_observation(conn, row, values["source_version"], now)
                return row, False
            # Serialize capacity by locked broker row. Terminal identities are never deleted.
            if (
                conn.execute(
                    "SELECT COUNT(*) FROM mattermost_intakes WHERE broker_id=?",
                    (mapping["broker_id"],),
                ).fetchone()[0]
                >= 128
            ):
                raise IntakeError(429, "INTAKE_CAPACITY")
            row = (
                values
                | {
                    k: mapping[k]
                    for k in (
                        "broker_id",
                        "organization_id",
                        "worker_id",
                        "actor_id",
                        "binding_json",
                    )
                }
                | {
                    "observed_source_version": values["source_version"],
                    "state": "pending_authorization",
                    "revision": 1,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            columns = (
                "delivery_key",
                "fingerprint",
                "broker_id",
                "organization_id",
                "worker_id",
                "actor_id",
                "binding_json",
                "receipt",
                "commitment",
                "source_expires_at",
                "source_version",
                "observed_source_version",
                "state",
                "revision",
                "created_at",
                "updated_at",
            )
            conn.execute(
                "INSERT INTO mattermost_intakes ("
                + ",".join(columns)
                + ") VALUES ("
                + ",".join("?" for _ in columns)
                + ")",
                tuple(row[k] for k in columns),
            )
            self._audit(conn, row, "accepted_pending", now)
            return row, True

    def cancel(
        self,
        mapping: dict[str, Any],
        observed: dict[str, Any],
        expected_revision: int,
        operation: str,
        source_version: int,
        clock: Callable[[], int],
    ) -> dict[str, Any]:
        with self.db.transaction() as conn:
            self._authorize(conn, mapping)
            row = self._load(conn, mapping, observed["delivery_key"])
            if (
                any(
                    row[k] != observed[k]
                    for k in ("fingerprint", "commitment", "receipt", "source_expires_at")
                )
                or source_version < row["observed_source_version"]
            ):
                raise IntakeError(409, "INTAKE_IDENTITY_CONFLICT")
            self._advance_observation(conn, row, source_version, clock())
            if row["state"] in {"cancelled", "expired"}:
                return row
            now = clock()
            if source_version <= row["source_version"]:
                raise IntakeError(409, "SOURCE_VERSION_NOT_ADVANCED")
            if row["revision"] != expected_revision:
                raise IntakeError(409, "INTAKE_REVISION_CONFLICT")
            state = "expired" if now * 1000 >= row["source_expires_at"] else "cancelled"
            conn.execute(
                "UPDATE mattermost_intakes SET state=?,revision=revision+1,source_version=?,updated_at=? WHERE delivery_key=? AND revision=?",
                (state, source_version, now, row["delivery_key"], expected_revision),
            )
            row.update(
                state=state,
                revision=row["revision"] + 1,
                source_version=source_version,
                updated_at=now,
            )
            self._audit(conn, row, state, now, operation)
            return row
