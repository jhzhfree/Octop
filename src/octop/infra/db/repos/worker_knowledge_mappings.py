"""Draft mapping persistence. No record in this table authorizes retrieval.

Revoked rows survive re-drafting, but parent-resource deletion cascades to all
mapping rows. This table is not a permanent audit archive.
"""

from __future__ import annotations

from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import now_ts
from octop.infra.utils.ulid import new_ulid


class WorkerKnowledgeMappingRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def get(self, organization_id: str, worker_id: str, mapping_id: str) -> dict[str, Any] | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM worker_knowledge_source_mappings WHERE organization_id=? AND worker_id=? AND mapping_id=?",
                (organization_id, worker_id, mapping_id),
            ).fetchone()
        return dict(row) if row else None

    def list_page(
        self, organization_id: str, worker_id: str, *, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM worker_knowledge_source_mappings "
                "WHERE organization_id=? AND worker_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
                (organization_id, worker_id, limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_draft(self, values: dict[str, Any], actor_id: int) -> str | None:
        """Atomically insert once; stale source/worker versions cannot be persisted."""
        mapping_id = new_ulid()
        fields = tuple(values)
        ts = now_ts()
        with self._db.transaction() as conn:
            result = conn.execute(
                "INSERT INTO worker_knowledge_source_mappings (mapping_id,"
                + ",".join(fields)
                + ",created_by,updated_by,created_at,updated_at) SELECT "
                + ",".join("?" for _ in range(len(fields) + 5))
                + " WHERE EXISTS (SELECT 1 FROM digital_workers WHERE worker_id=? AND organization_id=? AND version=? AND capability_version=?)"
                + " AND EXISTS (SELECT 1 FROM external_knowledge_sources WHERE source_id=? AND connection_id=? AND resource_revision=? AND enabled=1)"
                + " AND EXISTS (SELECT 1 FROM external_knowledge_connections WHERE connection_id=? AND revision=? AND enabled=1)"
                + " ON CONFLICT(worker_id,capability_key) WHERE status='draft' DO NOTHING",
                (
                    mapping_id,
                    *values.values(),
                    actor_id,
                    actor_id,
                    ts,
                    ts,
                    values["worker_id"],
                    values["organization_id"],
                    values["worker_version"],
                    values["capability_version"],
                    values["external_source_id"],
                    values["expected_connection_id"],
                    values["resource_revision"],
                    values["expected_connection_id"],
                    values["connection_revision"],
                ),
            )
            return mapping_id if result.rowcount == 1 else None

    def revoke(
        self, organization_id: str, worker_id: str, mapping_id: str, revision: int, actor_id: int
    ) -> bool:
        with self._db.transaction() as conn:
            result = conn.execute(
                "UPDATE worker_knowledge_source_mappings SET status='revoked',revision=revision+1,updated_by=?,updated_at=? "
                "WHERE organization_id=? AND worker_id=? AND mapping_id=? AND revision=? AND status='draft'",
                (actor_id, now_ts(), organization_id, worker_id, mapping_id, revision),
            )
            return bool(result.rowcount == 1)
