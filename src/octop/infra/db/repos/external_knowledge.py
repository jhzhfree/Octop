"""External knowledge source database access."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

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
    enabled: bool
    shared: bool
    credential_blob: bytes | None
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, r: DbRow) -> ExternalSourceRow:
        blob = r["credential_blob"]
        return cls(
            id=r["id"],
            source_id=r["source_id"],
            owner_user_id=r["owner_user_id"],
            instance_id=r["instance_id"] or "",
            kind=r["kind"],
            display_name=r["display_name"],
            endpoint=r["endpoint"],
            ref=r["ref"] or "",
            top_k=int(r["top_k"] or 8),
            min_score=float(r["min_score"] or 0),
            timeout_ms=int(r["timeout_ms"] or 10000),
            allow_insecure_http=bool(r["allow_insecure_http"]),
            selector_argument=r["selector_argument"] or "",
            forbidden_arguments=r["forbidden_arguments"] or "",
            read_tools=r["read_tools"] or "",
            enabled=bool(r["enabled"]),
            shared=bool(r["shared"]),
            credential_blob=bytes(blob) if blob is not None else None,
            created_at=r["created_at"],
            updated_at=r["updated_at"],
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


class ExternalKnowledgeRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def compare_replace_credentials(self, source_id: str, old: bytes | None, new: bytes) -> bool:
        with self._db.connect() as conn:
            if old is None:
                result = conn.execute(
                    "UPDATE external_knowledge_sources SET credential_blob=?,updated_at=? WHERE source_id=? AND credential_blob IS NULL",
                    (new, now_ts(), source_id),
                )
            else:
                result = conn.execute(
                    "UPDATE external_knowledge_sources SET credential_blob=?,updated_at=? WHERE source_id=? AND credential_blob=?",
                    (new, now_ts(), source_id, old),
                )
            return bool(result.rowcount == 1)

    def compare_replace_identity_binding(
        self, source_id: str, old: bytes | None, new: bytes, agent_id: str, *, bound: bool
    ) -> bool:
        """Commit encrypted identity and its retrieval mount together, with CAS protection."""
        with self._db.transaction() as conn:
            predicate = "credential_blob IS NULL" if old is None else "credential_blob=?"
            values: tuple[object, ...] = (new, now_ts(), source_id)
            if old is not None:
                values += (old,)
            result = conn.execute(
                "UPDATE external_knowledge_sources SET credential_blob=?,updated_at=? "
                f"WHERE source_id=? AND {predicate}",
                values,
            )
            if result.rowcount != 1:
                return False
            if bound:
                conn.execute(
                    "INSERT INTO external_knowledge_bindings(source_id,target_type,target_id,enabled,created_at) "
                    "VALUES (?,'agent',?,1,?) ON CONFLICT(source_id,target_type,target_id) "
                    "DO UPDATE SET enabled=1",
                    (source_id, agent_id, now_ts()),
                )
            else:
                conn.execute(
                    "DELETE FROM external_knowledge_bindings WHERE source_id=? AND target_type='agent' AND target_id=?",
                    (source_id, agent_id),
                )
            return True

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
        shared: bool = False,
        credential_blob: bytes | None = None,
    ) -> str:
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO external_knowledge_sources("
                "source_id, owner_user_id, instance_id, kind, display_name, endpoint, ref, "
                "top_k, min_score, timeout_ms, allow_insecure_http, selector_argument, "
                "forbidden_arguments, read_tools, enabled, shared, credential_blob, "
                "created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
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
                    bool_int(shared),
                    credential_blob,
                    ts,
                    ts,
                ),
            )
        return source_id

    def get(self, source_id: str) -> ExternalSourceRow | None:
        with self._db.connect() as conn:
            r = conn.execute(
                "SELECT * FROM external_knowledge_sources WHERE source_id = ?", (source_id,)
            ).fetchone()
        return ExternalSourceRow.from_row(r) if r else None

    def list_visible(self, user_id: int, *, is_admin: bool = False) -> list[ExternalSourceRow]:
        with self._db.connect() as conn:
            if is_admin:
                rows = conn.execute(
                    "SELECT * FROM external_knowledge_sources ORDER BY display_name"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM external_knowledge_sources "
                    "WHERE owner_user_id = ? OR shared = 1 ORDER BY display_name",
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
        enabled: bool | None = None,
        shared: bool | None = None,
        credential_blob: bytes | None = None,
        replace_credentials: bool = False,
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
        }
        for column, value in optional.items():
            if value is not None:
                assignments.append(f"{column} = ?")
                values.append(value)
        flags: dict[str, bool | None] = {
            "allow_insecure_http": allow_insecure_http,
            "enabled": enabled,
            "shared": shared,
        }
        for column, value in flags.items():
            if value is not None:
                assignments.append(f"{column} = ?")
                values.append(bool_int(value))
        if replace_credentials:
            assignments.append("credential_blob = ?")
            values.append(credential_blob)
        if not assignments:
            return
        assignments.append("updated_at = ?")
        values.append(now_ts())
        values.append(source_id)
        with self._db.transaction() as conn:
            conn.execute(
                f"UPDATE external_knowledge_sources SET {', '.join(assignments)} "
                "WHERE source_id = ?",
                tuple(values),
            )

    def delete(self, source_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM external_knowledge_sources WHERE source_id = ?", (source_id,))

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
