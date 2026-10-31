"""External knowledge source database access."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, bool_int, map_rows, now_ts

TARGET_TYPES = ("agent", "knowledge_base")


@dataclass(frozen=True)
class ExternalSourceRow:
    id: int
    source_id: str
    owner_user_id: int
    instance_id: str
    kind: str
    display_name: str
    endpoint: str
    ref: str
    top_k: int
    min_score: float
    timeout_ms: int
    allow_insecure_http: bool
    selector_argument: str
    forbidden_arguments: str
    read_tools: str
    tool_name: str
    # JSON map of the extra arguments the chosen tool is called with.
    tool_arguments: str
    enabled: bool
    shared: bool
    credential_blob: bytes | None
    created_at: int
    updated_at: int
    connection_id: str = ""
    auth_type: str = "legacy"
    revision: int = 1
    resource_revision: int = 1
    connection_owner_user_id: int = 0
    connection_shared: bool = False
    connection_enabled: bool = True

    @classmethod
    def from_row(cls, r: DbRow) -> ExternalSourceRow:
        blob = r["connection_credential_blob"]
        return cls(
            id=r["id"],
            source_id=r["source_id"],
            owner_user_id=r["owner_user_id"],
            instance_id=r["connection_instance_id"] or "",
            kind=r["connection_kind"],
            display_name=r["display_name"],
            endpoint=r["connection_endpoint"],
            ref=r["ref"] or "",
            top_k=int(r["top_k"] or 8),
            min_score=float(r["min_score"] or 0),
            timeout_ms=int(r["timeout_ms"] or 10000),
            allow_insecure_http=bool(r["connection_allow_insecure_http"]),
            selector_argument=r["selector_argument"] or "",
            forbidden_arguments=r["forbidden_arguments"] or "",
            read_tools=r["read_tools"] or "",
            tool_name=r["tool_name"] or "",
            tool_arguments=r["tool_arguments"] or "",
            enabled=bool(r["enabled"]),
            shared=bool(r["shared"]),
            credential_blob=bytes(blob) if blob is not None else None,
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            connection_id=r["connection_id"] or r["source_id"],
            auth_type=r["auth_type"],
            resource_revision=int(r["resource_revision"]),
            revision=int(r["revision"]),
            connection_owner_user_id=int(r["connection_owner_user_id"]),
            connection_shared=bool(r["connection_shared"]),
            connection_enabled=bool(r["connection_enabled"]),
        )

    @property
    def has_credentials(self) -> bool:
        return bool(self.credential_blob)


@dataclass(frozen=True)
class ExternalBindingRow:
    source_id: str
    target_type: str
    target_id: str
    enabled: bool
    created_at: int

    @classmethod
    def from_row(cls, r: DbRow) -> ExternalBindingRow:
        return cls(
            source_id=r["source_id"],
            target_type=r["target_type"],
            target_id=r["target_id"],
            enabled=bool(r["enabled"]),
            created_at=r["created_at"],
        )


def is_reference_conflict(exc: Exception) -> bool:
    return isinstance(exc, sqlite3.IntegrityError) or getattr(exc, "sqlstate", None) == "23503"


class ExternalKnowledgeRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    @staticmethod
    def _projection() -> str:
        return (
            "SELECT c.kind AS connection_kind,c.endpoint AS connection_endpoint,c.instance_id AS connection_instance_id,"
            "c.credential_blob AS connection_credential_blob,c.allow_insecure_http AS connection_allow_insecure_http,"
            "c.auth_type,c.revision,c.owner_user_id AS connection_owner_user_id,"
            "c.shared AS connection_shared,c.enabled AS connection_enabled,s.* "
            "FROM external_knowledge_sources s JOIN external_knowledge_connections c ON c.connection_id=s.connection_id "
        )

    def list_connections(self, user_id: int, *, is_admin: bool = False) -> list[dict[str, Any]]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM external_knowledge_connections"
                + ("" if is_admin else " WHERE owner_user_id=? OR shared=1")
                + " ORDER BY display_name",
                () if is_admin else (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_connection(self, values: dict[str, Any]) -> None:
        fields = (
            "connection_id",
            "owner_user_id",
            "kind",
            "display_name",
            "endpoint",
            "instance_id",
            "auth_type",
            "allow_insecure_http",
            "shared",
            "credential_blob",
            "created_at",
            "updated_at",
        )
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO external_knowledge_connections("
                + ",".join(fields)
                + ") VALUES ("
                + ",".join("?" for _ in fields)
                + ")",
                tuple(values[k] for k in fields),
            )

    def update_connection(self, connection_id: str, revision: int, values: dict[str, Any]) -> bool:
        allowed = {
            "kind",
            "display_name",
            "endpoint",
            "auth_type",
            "allow_insecure_http",
            "shared",
            "enabled",
            "credential_blob",
        }
        if set(values) - allowed:
            raise ValueError("invalid connection fields")
        with self._db.transaction() as conn:
            result = conn.execute(
                "UPDATE external_knowledge_connections SET "
                + ",".join(k + "=?" for k in values)
                + ",revision=revision+1,updated_at=? WHERE connection_id=? AND revision=?",
                (*values.values(), now_ts(), connection_id, revision),
            )
        return bool(result.rowcount == 1)

    def connection_resources(self, connection_id: str) -> list[str]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT source_id FROM external_knowledge_sources WHERE connection_id=?",
                (connection_id,),
            ).fetchall()
        return [r["source_id"] for r in rows]

    def delete_connection(self, connection_id: str) -> bool:
        with self._db.transaction() as conn:
            result = conn.execute(
                "DELETE FROM external_knowledge_connections WHERE connection_id=? AND NOT EXISTS (SELECT 1 FROM external_knowledge_sources WHERE connection_id=?)",
                (connection_id, connection_id),
            )
        return bool(result.rowcount == 1)

    def connection(self, connection_id: str) -> dict[str, Any] | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM external_knowledge_connections WHERE connection_id=?",
                (connection_id,),
            ).fetchone()
        return dict(row) if row else None

    def identity(self, row: ExternalSourceRow) -> dict[str, Any]:
        return self.identity_for_connection(row.connection_id)

    def identity_for_connection(self, connection_id: str) -> dict[str, Any]:
        with self._db.connect() as conn:
            connection = conn.execute(
                "SELECT identity_json FROM external_knowledge_connections WHERE connection_id=?",
                (connection_id,),
            ).fetchone()
            agents = conn.execute(
                "SELECT * FROM external_agent_bindings WHERE connection_id=?", (connection_id,)
            ).fetchall()
        result = json.loads(connection["identity_json"]) if connection else {}
        result["agents"] = {
            a["agent_id"]: {
                **json.loads(a["binding_json"]),
                "lastVerifiedAt": a["last_verified_at"],
                "lastVerificationStatus": a["last_verification_status"],
            }
            for a in agents
        }
        return result

    def import_identity(
        self, row: ExternalSourceRow, auth_type: str, credentials: bytes, config: dict[str, Any]
    ) -> None:
        with self._db.transaction() as conn:
            result = conn.execute(
                "UPDATE external_knowledge_connections SET auth_type=?,credential_blob=?,identity_json=?,revision=revision+1 WHERE connection_id=? AND auth_type='legacy'",
                (
                    auth_type,
                    credentials,
                    json.dumps({k: v for k, v in config.items() if k != "agents"}),
                    row.connection_id,
                ),
            )
            if result.rowcount:
                for agent_id, data in dict(config.get("agents", {})).items():
                    conn.execute(
                        "INSERT INTO external_agent_bindings(connection_id,agent_id,binding_json,last_verified_at,last_verification_status) VALUES(?,?,?,?,?) ON CONFLICT(connection_id,agent_id) DO NOTHING",
                        (
                            row.connection_id,
                            agent_id,
                            json.dumps(data),
                            data.get("lastVerifiedAt"),
                            data.get("lastVerificationStatus"),
                        ),
                    )
                conn.execute(
                    "UPDATE external_knowledge_sources SET credential_blob=NULL WHERE connection_id=?",
                    (row.connection_id,),
                )

    def save_identity(
        self,
        row: ExternalSourceRow,
        config: dict[str, Any],
        agent_id: str | None = None,
        *,
        bound: bool = True,
        mount_resource: bool = True,
    ) -> bool:
        with self._db.transaction() as conn:
            previous = conn.execute(
                "SELECT identity_json FROM external_knowledge_connections WHERE connection_id=?",
                (row.connection_id,),
            ).fetchone()
            unchanged_coordinates = previous is not None and json.loads(
                previous["identity_json"]
            ) == {k: v for k, v in config.items() if k != "agents"}
            result = conn.execute(
                "UPDATE external_knowledge_connections SET identity_json=?,revision=revision+1 WHERE connection_id=? AND revision=?",
                (
                    json.dumps({k: v for k, v in config.items() if k != "agents"}),
                    row.connection_id,
                    row.revision,
                ),
            )
            if result.rowcount != 1:
                return False
            if unchanged_coordinates and agent_id is not None:
                # A binding edit changes this Agent only. Preserve other Agents' checks
                # without relabelling the changed Agent's old proof or retrieval result.
                conn.execute(
                    "UPDATE external_connection_checks SET revision=? WHERE connection_id=? AND revision=? AND agent_id<>?",
                    (row.revision + 1, row.connection_id, row.revision, agent_id),
                )
            if agent_id is not None:
                if bound:
                    data = dict(config["agents"])[agent_id]
                    conn.execute(
                        "INSERT INTO external_agent_bindings(connection_id,agent_id,binding_json,last_verified_at,last_verification_status,key_ref) VALUES(?,?,?,?,?,?) ON CONFLICT(connection_id,agent_id) DO UPDATE SET binding_json=excluded.binding_json,last_verified_at=excluded.last_verified_at,last_verification_status=excluded.last_verification_status,key_ref=excluded.key_ref",
                        (
                            row.connection_id,
                            agent_id,
                            json.dumps(data),
                            data.get("lastVerifiedAt"),
                            data.get("lastVerificationStatus"),
                            "agent_identity:" + data["keyReference"]
                            if data.get("keyReference")
                            else None,
                        ),
                    )
                    if mount_resource:
                        conn.execute(
                            "INSERT INTO external_knowledge_bindings(source_id,target_type,target_id,enabled,created_at) VALUES(?,'agent',?,1,?) ON CONFLICT(source_id,target_type,target_id) DO UPDATE SET enabled=1",
                            (row.source_id, agent_id, now_ts()),
                        )
                else:
                    conn.execute(
                        "DELETE FROM external_agent_bindings WHERE connection_id=? AND agent_id=?",
                        (row.connection_id, agent_id),
                    )
                    conn.execute(
                        "DELETE FROM external_knowledge_bindings WHERE source_id IN (SELECT source_id FROM external_knowledge_sources WHERE connection_id=?) AND target_type='agent' AND target_id=?",
                        (row.connection_id, agent_id),
                    )
            else:
                for native, data in dict(config.get("agents", {})).items():
                    conn.execute(
                        "UPDATE external_agent_bindings SET last_verified_at=?,last_verification_status=? WHERE connection_id=? AND agent_id=?",
                        (
                            data.get("lastVerifiedAt"),
                            data.get("lastVerificationStatus"),
                            row.connection_id,
                            native,
                        ),
                    )
        return True

    def record_verification(
        self, connection_id: str, agent_id: str, expected: str, status: str, at: str
    ) -> None:
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT binding_json FROM external_agent_bindings WHERE connection_id=? AND agent_id=?",
                (connection_id, agent_id),
            ).fetchone()
            if row and json.loads(row["binding_json"]).get("externalAgentId") == expected:
                conn.execute(
                    "UPDATE external_agent_bindings SET last_verified_at=?,last_verification_status=? WHERE connection_id=? AND agent_id=?",
                    (at, status, connection_id, agent_id),
                )

    def record_check(
        self,
        connection_id: str,
        resource_id: str,
        agent_id: str,
        stage: str,
        status: str,
        code: str = "",
        *,
        expected_revision: int | None = None,
        expected_resource_revision: int | None = None,
    ) -> None:
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT revision FROM external_knowledge_connections WHERE connection_id=?",
                (connection_id,),
            ).fetchone()
            if row is None or (
                expected_revision is not None and row["revision"] != expected_revision
            ):
                return
            resource = conn.execute(
                "SELECT resource_revision FROM external_knowledge_sources WHERE source_id=? AND connection_id=?",
                (resource_id, connection_id),
            ).fetchone()
            if not resource_id:
                resource = {"resource_revision": 0}
            if resource is None or (
                expected_resource_revision is not None
                and resource["resource_revision"] != expected_resource_revision
            ):
                return
            conn.execute(
                "INSERT INTO external_connection_checks(connection_id,resource_id,agent_id,revision,resource_revision,stage,status,code,checked_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(connection_id,resource_id,agent_id,stage) DO UPDATE SET revision=excluded.revision,resource_revision=excluded.resource_revision,status=excluded.status,code=excluded.code,checked_at=excluded.checked_at",
                (
                    connection_id,
                    resource_id,
                    agent_id,
                    row["revision"],
                    resource["resource_revision"],
                    stage,
                    status,
                    code,
                    now_ts(),
                ),
            )

    def checks(self, connection_id: str, resource_id: str) -> list[dict[str, object]]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT x.agent_id,x.stage,x.status,x.code,x.checked_at FROM external_connection_checks x JOIN external_knowledge_connections c ON x.connection_id=c.connection_id AND x.revision=c.revision LEFT JOIN external_knowledge_sources s ON s.source_id=x.resource_id AND s.resource_revision=x.resource_revision WHERE (x.resource_id='' OR s.source_id IS NOT NULL) AND x.connection_id=? AND x.resource_id=?",
                (connection_id, resource_id),
            ).fetchall()
        return [dict(r) for r in rows]

    def key_reference_used(self, reference: str) -> bool:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM external_agent_bindings WHERE key_ref=? LIMIT 1",
                ("agent_identity:" + reference,),
            ).fetchone()
        return row is not None

    def retire_key(self, reference: str) -> bool:
        name = "agent_identity:" + reference
        try:
            with self._db.transaction() as conn:
                result = conn.execute(
                    "DELETE FROM secrets WHERE k=? AND NOT EXISTS (SELECT 1 FROM external_agent_bindings WHERE key_ref=?)",
                    (name, name),
                )
            return bool(result.rowcount == 1)
        except Exception as exc:
            if is_reference_conflict(exc):
                return False
            raise

    def adopt_legacy_key(
        self, connection_id: str, agent_id: str, external_id: str, reference: str
    ) -> None:
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT binding_json FROM external_agent_bindings WHERE connection_id=? AND agent_id=? AND key_ref IS NULL",
                (connection_id, agent_id),
            ).fetchone()
            if row is None:
                return
            data = json.loads(row["binding_json"])
            if data.get("externalAgentId") != external_id:
                return
            data["keyReference"] = reference
            conn.execute(
                "UPDATE external_agent_bindings SET binding_json=?,key_ref=? WHERE connection_id=? AND agent_id=? AND binding_json=? AND key_ref IS NULL",
                (
                    json.dumps(data),
                    "agent_identity:" + reference,
                    connection_id,
                    agent_id,
                    row["binding_json"],
                ),
            )

    def target_owner(self, target_type: str, target_id: str) -> int | None:
        tables = {
            "agent": ("agents", "agent_id", "user_id"),
            "knowledge_base": ("knowledge_bases", "knowledge_base_id", "owner_user_id"),
        }
        if target_type not in tables:
            return None
        table, identifier, owner = tables[target_type]
        with self._db.connect() as conn:
            row = conn.execute(
                f"SELECT {owner} AS owner FROM {table} WHERE {identifier}=?", (target_id,)
            ).fetchone()
        return int(row["owner"]) if row and row["owner"] is not None else None

    def create(
        self,
        *,
        source_id: str,
        owner_user_id: int,
        kind: str,
        display_name: str,
        endpoint: str,
        instance_id: str = "",
        ref: str = "",
        top_k: int = 8,
        min_score: float = 0.0,
        timeout_ms: int = 10_000,
        allow_insecure_http: bool = False,
        selector_argument: str = "",
        forbidden_arguments: str = "",
        read_tools: str = "",
        tool_name: str = "",
        tool_arguments: str = "",
        shared: bool = False,
        credential_blob: bytes | None = None,
        connection_id: str = "",
        auth_type: str = "legacy",
    ) -> str:
        ts = now_ts()
        with self._db.transaction() as conn:
            if not connection_id:
                connection_id = source_id
                conn.execute(
                    "INSERT INTO external_knowledge_connections(connection_id,owner_user_id,kind,display_name,endpoint,instance_id,auth_type,allow_insecure_http,shared,credential_blob,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        connection_id,
                        owner_user_id,
                        kind,
                        display_name,
                        endpoint,
                        instance_id,
                        auth_type,
                        bool_int(allow_insecure_http),
                        bool_int(shared),
                        credential_blob,
                        ts,
                        ts,
                    ),
                )
            conn.execute(
                "INSERT INTO external_knowledge_sources("
                "source_id, owner_user_id, instance_id, kind, display_name, endpoint, ref, "
                "top_k, min_score, timeout_ms, allow_insecure_http, selector_argument, "
                "forbidden_arguments, read_tools, tool_name, tool_arguments, "
                "enabled, shared, credential_blob, "
                "created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
                (
                    source_id,
                    owner_user_id,
                    instance_id,
                    kind,
                    display_name,
                    endpoint,
                    ref,
                    top_k,
                    min_score,
                    timeout_ms,
                    bool_int(allow_insecure_http),
                    selector_argument,
                    forbidden_arguments,
                    read_tools,
                    tool_name,
                    tool_arguments,
                    bool_int(shared),
                    credential_blob,
                    ts,
                    ts,
                ),
            )
            conn.execute(
                "UPDATE external_knowledge_sources SET connection_id=?,credential_blob=NULL WHERE source_id=?",
                (connection_id, source_id),
            )
        return source_id

    def get(self, source_id: str) -> ExternalSourceRow | None:
        with self._db.connect() as conn:
            r = conn.execute(self._projection() + "WHERE s.source_id = ?", (source_id,)).fetchone()
        return ExternalSourceRow.from_row(r) if r else None

    def list_visible(self, user_id: int, *, is_admin: bool = False) -> list[ExternalSourceRow]:
        with self._db.connect() as conn:
            if is_admin:
                rows = conn.execute(self._projection() + "ORDER BY s.display_name").fetchall()
            else:
                rows = conn.execute(
                    self._projection()
                    + "WHERE s.owner_user_id = ? OR s.shared = 1 ORDER BY s.display_name",
                    (user_id,),
                ).fetchall()
        return map_rows(rows, ExternalSourceRow)

    def update(
        self,
        source_id: str,
        *,
        kind: str | None = None,
        display_name: str | None = None,
        endpoint: str | None = None,
        ref: str | None = None,
        top_k: int | None = None,
        min_score: float | None = None,
        timeout_ms: int | None = None,
        allow_insecure_http: bool | None = None,
        selector_argument: str | None = None,
        forbidden_arguments: str | None = None,
        read_tools: str | None = None,
        tool_name: str | None = None,
        tool_arguments: str | None = None,
        enabled: bool | None = None,
        shared: bool | None = None,
        resource_shared: bool | None = None,
        credential_blob: bytes | None = None,
        replace_credentials: bool = False,
        auth_type: str | None = None,
        expected_revision: int | None = None,
        expected_resource_revision: int | None = None,
    ) -> None:
        assignments: list[str] = []
        values: list[object] = []
        optional: dict[str, object] = {
            "kind": kind,
            "display_name": display_name,
            "endpoint": endpoint,
            "ref": ref,
            "top_k": top_k,
            "min_score": min_score,
            "timeout_ms": timeout_ms,
            "selector_argument": selector_argument,
            "forbidden_arguments": forbidden_arguments,
            "read_tools": read_tools,
            "tool_name": tool_name,
            "tool_arguments": tool_arguments,
        }
        for column, value in optional.items():
            if value is not None:
                assignments.append(f"{column} = ?")
                values.append(value)
        flags: dict[str, bool | None] = {
            "allow_insecure_http": allow_insecure_http,
            "enabled": enabled,
            "shared": resource_shared if resource_shared is not None else shared,
        }
        for column, value in flags.items():
            if value is not None:
                assignments.append(f"{column} = ?")
                values.append(bool_int(value))
        if not assignments and not replace_credentials and auth_type is None:
            return
        assignments.append("resource_revision = resource_revision + 1")
        assignments.append("updated_at = ?")
        values.append(now_ts())
        values.append(source_id)
        with self._db.transaction() as conn:
            target = conn.execute(
                "SELECT connection_id FROM external_knowledge_sources WHERE source_id=?",
                (source_id,),
            ).fetchone()
            if target is None:
                raise ValueError("source no longer exists")
            changes: dict[str, Any] = {
                k: v
                for k, v in {
                    "kind": kind,
                    "endpoint": endpoint,
                    "instance_id": None,
                    "allow_insecure_http": None
                    if allow_insecure_http is None
                    else bool_int(allow_insecure_http),
                    "shared": None if shared is None else bool_int(shared),
                    "auth_type": auth_type,
                }.items()
                if v is not None
            }
            if replace_credentials:
                changes["credential_blob"] = credential_blob
            if changes:
                sets = ",".join(k + "=?" for k in changes)
                query = (
                    "UPDATE external_knowledge_connections SET "
                    + sets
                    + ",revision=revision+1 WHERE connection_id=?"
                )
                args = [*changes.values(), target["connection_id"]]
                if expected_revision is not None:
                    query += " AND revision=?"
                    args.append(expected_revision)
                result = conn.execute(query, tuple(args))
                if result.rowcount != 1:
                    raise ValueError("connection revision conflict")
            query = (
                f"UPDATE external_knowledge_sources SET {', '.join(assignments)} WHERE source_id=?"
            )
            if expected_resource_revision is not None:
                query += " AND resource_revision=?"
                values.append(expected_resource_revision)
            result = conn.execute(query, tuple(values))
            if result.rowcount != 1:
                raise ValueError("resource revision conflict")

    def delete(self, source_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM external_knowledge_sources WHERE source_id=?", (source_id,))

    def list_bindings(self, source_id: str | None = None) -> list[ExternalBindingRow]:
        with self._db.connect() as conn:
            if source_id:
                rows = conn.execute(
                    "SELECT * FROM external_knowledge_bindings WHERE source_id = ?",
                    (source_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM external_knowledge_bindings").fetchall()
        return map_rows(rows, ExternalBindingRow)

    def set_binding(
        self, *, source_id: str, target_type: str, target_id: str, enabled: bool = True
    ) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO external_knowledge_bindings("
                "source_id, target_type, target_id, enabled, created_at"
                ") VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(source_id, target_type, target_id) DO UPDATE SET enabled = excluded.enabled",
                (source_id, target_type, target_id, bool_int(enabled), now_ts()),
            )

    def delete_binding(self, *, source_id: str, target_type: str, target_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM external_knowledge_bindings "
                "WHERE source_id = ? AND target_type = ? AND target_id = ?",
                (source_id, target_type, target_id),
            )

    def replace_bindings_for_target(
        self, target_type: str, target_id: str, source_ids: Sequence[str]
    ) -> None:
        """Bind exactly *source_ids* to one target, dropping everything else.

        One transaction, because an agent's mounted set is edited as a whole:
        a partial write would leave stale sources mounted.
        """
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM external_knowledge_bindings WHERE target_type = ? AND target_id = ?",
                (target_type, target_id),
            )
            for source_id in source_ids:
                conn.execute(
                    "INSERT INTO external_knowledge_bindings("
                    "source_id, target_type, target_id, enabled, created_at"
                    ") VALUES (?, ?, ?, 1, ?) "
                    "ON CONFLICT(source_id, target_type, target_id) DO UPDATE SET enabled = 1",
                    (str(source_id), target_type, target_id, now_ts()),
                )

    def list_source_ids_for_target(self, target_type: str, target_id: str) -> list[str]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT source_id FROM external_knowledge_bindings "
                "WHERE target_type = ? AND target_id = ? AND enabled = 1",
                (target_type, target_id),
            ).fetchall()
        return [str(row["source_id"]) for row in rows]
