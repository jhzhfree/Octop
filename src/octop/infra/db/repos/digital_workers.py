"""Persistence for organization-bound digital worker profiles and goals."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

try:
    from psycopg import errors as pg_errors
except ImportError:  # pragma: no cover - optional PostgreSQL driver
    pg_errors = None  # type: ignore[assignment]

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import now_ts
from octop.infra.utils.ulid import new_ulid


def _legacy_akb_source(source: dict[str, Any]) -> dict[str, Any] | None:
    if source.get("provider") != "akb":
        return None
    base_ref = str(source.get("knowledge_base_ref") or source.get("knowledge_base_code") or "")
    return {
        "provider": "akb",
        "mcp_server": source.get("mcp_server"),
        "knowledge_base_code": base_ref,
        "access": "read",
    }


def _canonical_knowledge_sources(capabilities: dict[str, Any]) -> list[dict[str, Any]]:
    sources = capabilities.get("knowledge_sources")
    legacy_sources = capabilities.get("akb_knowledge_sources", [])
    if not isinstance(sources, list) or (not sources and legacy_sources):
        sources = legacy_sources
    normalized: list[dict[str, Any]] = []
    for source in sources if isinstance(sources, list) else []:
        if not isinstance(source, dict):
            continue
        provider = str(source.get("provider") or "").strip()
        mcp_server = str(source.get("mcp_server") or "").strip()
        base_ref = str(
            source.get("knowledge_base_ref") or source.get("knowledge_base_code") or ""
        ).strip()
        if not provider or not mcp_server or not base_ref:
            continue
        read_tools = source.get("read_tools")
        if not isinstance(read_tools, list):
            read_tools = ["knowledge_query", "knowledge_search"] if provider == "akb" else []
        forbidden_arguments = source.get("forbidden_arguments")
        if not isinstance(forbidden_arguments, list):
            forbidden_arguments = ["knowledgeBaseId"] if provider == "akb" else []
        selector_argument = source.get("selector_argument")
        if selector_argument is None and provider == "akb":
            selector_argument = "knowledgeBaseCode"
        normalized.append(
            {
                "provider": provider,
                "mcp_server": mcp_server,
                "knowledge_base_ref": base_ref,
                "selector_argument": selector_argument,
                "read_tools": read_tools,
                "forbidden_arguments": forbidden_arguments,
                "access": "read",
            }
        )
    return normalized


def _dict(row: Any) -> dict[str, Any]:
    return dict(row)


def _worker_dict(row: Any) -> dict[str, Any]:
    result = _dict(row)
    try:
        capabilities = json.loads(result.pop("capabilities_json", "{}") or "{}")
    except (TypeError, json.JSONDecodeError):
        capabilities = {}
    if not isinstance(capabilities, dict):
        capabilities = {}
    for field in (
        "skills",
        "knowledge",
        "operating_guidance",
        "skill_package_ids",
        "knowledge_base_ids",
        "knowledge_sources",
        "akb_knowledge_sources",
    ):
        if not isinstance(capabilities.get(field), list):
            capabilities[field] = []
    generic_sources = _canonical_knowledge_sources(capabilities)
    capabilities["knowledge_sources"] = generic_sources
    capabilities["akb_knowledge_sources"] = [
        legacy for source in generic_sources if (legacy := _legacy_akb_source(source)) is not None
    ]
    result["capabilities"] = capabilities
    return result


def _is_unique_violation(exc: BaseException) -> bool:
    if isinstance(exc, sqlite3.IntegrityError):
        return "unique" in str(exc).lower()
    return pg_errors is not None and isinstance(exc, pg_errors.UniqueViolation)


class _CapabilityReviewConflict(Exception):
    pass


class DigitalWorkerRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def list_workers(self, organization_id: str) -> list[dict[str, Any]]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT w.*, p.title AS position_title, a.name AS agent_name "
                "FROM digital_workers w "
                "JOIN organization_positions p ON p.position_id = w.position_id "
                "JOIN agents a ON a.agent_id = w.agent_id "
                "WHERE w.organization_id = ? ORDER BY w.updated_at DESC, w.worker_id",
                (organization_id,),
            ).fetchall()
        return [_worker_dict(row) for row in rows]

    def get_worker(self, worker_id: str) -> dict[str, Any] | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT w.*, p.title AS position_title, a.name AS agent_name "
                "FROM digital_workers w "
                "JOIN organization_positions p ON p.position_id = w.position_id "
                "JOIN agents a ON a.agent_id = w.agent_id WHERE w.worker_id = ?",
                (worker_id,),
            ).fetchone()
        return _worker_dict(row) if row else None

    def create_worker(
        self,
        *,
        organization_id: str,
        position_id: str,
        agent_id: str,
        owner_user_id: int,
        name: str,
        responsibilities: str,
    ) -> dict[str, Any] | None:
        worker_id = new_ulid()
        now = now_ts()
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    "INSERT INTO digital_workers(worker_id, organization_id, position_id, agent_id, "
                    "owner_user_id, name, responsibilities, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        worker_id,
                        organization_id,
                        position_id,
                        agent_id,
                        owner_user_id,
                        name,
                        responsibilities,
                        now,
                        now,
                    ),
                )
        except Exception as exc:
            if _is_unique_violation(exc):
                return None
            raise
        return self.get_worker(worker_id)

    def update_worker(
        self, worker_id: str, updates: dict[str, Any], *, expected_version: int
    ) -> dict[str, Any] | None:
        allowed = {"name", "responsibilities", "status"}
        fields = [(key, value) for key, value in updates.items() if key in allowed]
        if not fields:
            return self.get_worker(worker_id)
        assignments = ", ".join(f"{key} = ?" for key, _ in fields)
        values = [value for _, value in fields]
        with self._db.transaction() as conn:
            cur = conn.execute(
                f"UPDATE digital_workers SET {assignments}, version = version + 1, updated_at = ? "
                "WHERE worker_id = ? AND version = ?",
                (*values, now_ts(), worker_id, expected_version),
            )
            if int(cur.rowcount or 0) != 1:
                return None
        return self.get_worker(worker_id)

    def update_knowledge_sources(
        self,
        worker_id: str,
        sources: list[dict[str, Any]],
        *,
        expected_capability_version: int,
    ) -> dict[str, Any] | None:
        """Replace product-neutral knowledge-source bindings with optimistic concurrency."""
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT capabilities_json FROM digital_workers "
                "WHERE worker_id = ? AND capability_version = ?",
                (worker_id, expected_capability_version),
            ).fetchone()
            if row is None:
                return None
            try:
                capabilities = json.loads(row["capabilities_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                capabilities = {}
            if not isinstance(capabilities, dict):
                capabilities = {}
            capabilities["knowledge_sources"] = sources
            capabilities["akb_knowledge_sources"] = [
                legacy for source in sources if (legacy := _legacy_akb_source(source)) is not None
            ]
            cur = conn.execute(
                "UPDATE digital_workers SET capabilities_json = ?, "
                "capability_version = capability_version + 1, updated_at = ? "
                "WHERE worker_id = ? AND capability_version = ?",
                (
                    json.dumps(capabilities, ensure_ascii=False),
                    now_ts(),
                    worker_id,
                    expected_capability_version,
                ),
            )
            if int(cur.rowcount or 0) != 1:
                return None
        return self.get_worker(worker_id)

    def update_memory_sources(
        self,
        worker_id: str,
        sources: list[dict[str, Any]],
        *,
        expected_capability_version: int,
    ) -> dict[str, Any] | None:
        """Replace platform-memory bindings with optimistic concurrency."""
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT capabilities_json FROM digital_workers "
                "WHERE worker_id = ? AND capability_version = ?",
                (worker_id, expected_capability_version),
            ).fetchone()
            if row is None:
                return None
            try:
                capabilities = json.loads(row["capabilities_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                capabilities = {}
            if not isinstance(capabilities, dict):
                capabilities = {}
            capabilities["memory_sources"] = sources
            cur = conn.execute(
                "UPDATE digital_workers SET capabilities_json = ?, "
                "capability_version = capability_version + 1, updated_at = ? "
                "WHERE worker_id = ? AND capability_version = ?",
                (
                    json.dumps(capabilities, ensure_ascii=False),
                    now_ts(),
                    worker_id,
                    expected_capability_version,
                ),
            )
            if int(cur.rowcount or 0) != 1:
                return None
        return self.get_worker(worker_id)

    def update_akb_knowledge_sources(
        self,
        worker_id: str,
        sources: list[dict[str, str]],
        *,
        expected_capability_version: int,
    ) -> dict[str, Any] | None:
        """Backward-compatible wrapper for the former AKB-only binding method."""
        generic = [
            {
                "provider": "akb",
                "mcp_server": source.get("mcp_server", ""),
                "knowledge_base_ref": source.get("knowledge_base_code", ""),
                "selector_argument": "knowledgeBaseCode",
                "read_tools": ["knowledge_query", "knowledge_search"],
                "forbidden_arguments": ["knowledgeBaseId"],
                "access": "read",
            }
            for source in sources
        ]
        return self.update_knowledge_sources(
            worker_id, generic, expected_capability_version=expected_capability_version
        )

    def list_goals(self, worker_id: str) -> list[dict[str, Any]]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM digital_worker_goals WHERE worker_id = ? "
                "ORDER BY period_start, created_at, goal_id",
                (worker_id,),
            ).fetchall()
        return [_dict(row) for row in rows]

    def get_goal(self, goal_id: str) -> dict[str, Any] | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM digital_worker_goals WHERE goal_id = ?", (goal_id,)
            ).fetchone()
        return _dict(row) if row else None

    def create_goal(
        self,
        *,
        worker_id: str,
        title: str,
        description: str,
        success_criteria: str,
        period_start: str | None,
        period_end: str | None,
        created_by: int,
    ) -> dict[str, Any]:
        goal_id = new_ulid()
        now = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO digital_worker_goals(goal_id, worker_id, title, description, "
                "success_criteria, period_start, period_end, created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    goal_id,
                    worker_id,
                    title,
                    description,
                    success_criteria,
                    period_start,
                    period_end,
                    created_by,
                    now,
                    now,
                ),
            )
        return self.get_goal(goal_id) or {}

    def update_goal(self, goal_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {
            "title",
            "description",
            "success_criteria",
            "period_start",
            "period_end",
            "status",
        }
        fields = [(key, value) for key, value in updates.items() if key in allowed]
        if not fields:
            return self.get_goal(goal_id)
        assignments = ", ".join(f"{key} = ?" for key, _ in fields)
        values = [value for _, value in fields]
        with self._db.transaction() as conn:
            conn.execute(
                f"UPDATE digital_worker_goals SET {assignments}, updated_at = ? WHERE goal_id = ?",
                (*values, now_ts(), goal_id),
            )
        return self.get_goal(goal_id)

    def delete_goal(self, goal_id: str) -> bool:
        with self._db.transaction() as conn:
            cur = conn.execute("DELETE FROM digital_worker_goals WHERE goal_id = ?", (goal_id,))
        return int(cur.rowcount or 0) == 1

    @staticmethod
    def _plan(row: Any) -> dict[str, Any]:
        result = _dict(row)
        try:
            result["steps"] = json.loads(result.pop("steps_json"))
        except (TypeError, json.JSONDecodeError):
            result["steps"] = []
        return result

    def list_plans(self, goal_id: str) -> list[dict[str, Any]]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM digital_worker_plans WHERE goal_id = ? ORDER BY created_at, plan_id",
                (goal_id,),
            ).fetchall()
        return [self._plan(row) for row in rows]

    def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM digital_worker_plans WHERE plan_id = ?", (plan_id,)
            ).fetchone()
        return self._plan(row) if row else None

    def create_plan(
        self,
        *,
        goal_id: str,
        title: str,
        approach: str,
        steps: list[dict[str, str]],
        created_by: int,
    ) -> dict[str, Any]:
        plan_id = new_ulid()
        now = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO digital_worker_plans(plan_id, goal_id, title, approach, steps_json, "
                "created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    plan_id,
                    goal_id,
                    title,
                    approach,
                    json.dumps(steps, ensure_ascii=False),
                    created_by,
                    now,
                    now,
                ),
            )
        return self.get_plan(plan_id) or {}

    def update_plan(
        self,
        plan_id: str,
        updates: dict[str, Any],
        *,
        expected_version: int,
    ) -> dict[str, Any] | None:
        allowed = {"title", "approach", "steps", "status"}
        fields = [(key, value) for key, value in updates.items() if key in allowed]
        if not fields:
            return self.get_plan(plan_id)
        assignments: list[str] = []
        values: list[Any] = []
        for key, value in fields:
            if key == "steps":
                key, value = "steps_json", json.dumps(value, ensure_ascii=False)
            assignments.append(f"{key} = ?")
            values.append(value)
        with self._db.transaction() as conn:
            cur = conn.execute(
                f"UPDATE digital_worker_plans SET {', '.join(assignments)}, "
                "version = version + 1, updated_at = ? WHERE plan_id = ? AND version = ?",
                (*values, now_ts(), plan_id, expected_version),
            )
            if int(cur.rowcount or 0) != 1:
                return None
        return self.get_plan(plan_id)

    def delete_plan(self, plan_id: str) -> bool:
        with self._db.transaction() as conn:
            cur = conn.execute("DELETE FROM digital_worker_plans WHERE plan_id = ?", (plan_id,))
        return int(cur.rowcount or 0) == 1

    @staticmethod
    def _proposal(row: Any) -> dict[str, Any]:
        result = _dict(row)
        try:
            result["proposed_changes"] = json.loads(result.pop("proposed_changes_json"))
        except (TypeError, json.JSONDecodeError):
            result["proposed_changes"] = {"skills": [], "knowledge": [], "operating_guidance": []}
        return result

    def list_capability_proposals(self, worker_id: str) -> list[dict[str, Any]]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM digital_worker_capability_proposals WHERE worker_id = ? "
                "ORDER BY created_at DESC, proposal_id",
                (worker_id,),
            ).fetchall()
        return [self._proposal(row) for row in rows]

    def get_capability_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM digital_worker_capability_proposals WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
        return self._proposal(row) if row else None

    def create_capability_proposal(
        self,
        *,
        proposal_id: str,
        worker_id: str,
        source_run_id: str,
        source_summary: str,
        observed_gaps: str,
        proposed_changes: dict[str, list[str]],
        expected_benefit: str,
        risk_notes: str,
        base_capability_version: int,
        created_by: int,
    ) -> dict[str, Any] | None:
        now = now_ts()
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    "INSERT INTO digital_worker_capability_proposals("
                    "proposal_id, worker_id, source_run_id, source_summary, observed_gaps, "
                    "proposed_changes_json, expected_benefit, risk_notes, base_capability_version, "
                    "created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        proposal_id,
                        worker_id,
                        source_run_id,
                        source_summary,
                        observed_gaps,
                        json.dumps(proposed_changes, ensure_ascii=False),
                        expected_benefit,
                        risk_notes,
                        base_capability_version,
                        created_by,
                        now,
                        now,
                    ),
                )
        except Exception as exc:
            if _is_unique_violation(exc):
                return None
            raise
        return self.get_capability_proposal(proposal_id)

    def review_capability_proposal(
        self,
        proposal_id: str,
        *,
        reviewer_id: int,
        approve: bool,
        review_note: str,
    ) -> dict[str, Any] | None:
        """Atomically publish an approved proposal against its capability base version."""
        try:
            with self._db.transaction() as conn:
                proposal_row = conn.execute(
                    "SELECT * FROM digital_worker_capability_proposals WHERE proposal_id = ?",
                    (proposal_id,),
                ).fetchone()
                if proposal_row is None:
                    return None
                proposal = self._proposal(proposal_row)
                if proposal["status"] != "submitted":
                    return None
                if approve:
                    worker_row = conn.execute(
                        "SELECT capabilities_json, capability_version FROM digital_workers WHERE worker_id = ?",
                        (proposal["worker_id"],),
                    ).fetchone()
                    if worker_row is None or int(worker_row["capability_version"]) != int(
                        proposal["base_capability_version"]
                    ):
                        return None
                    try:
                        current = json.loads(worker_row["capabilities_json"] or "{}")
                    except (TypeError, json.JSONDecodeError):
                        current = {}
                    for field in (
                        "skills",
                        "knowledge",
                        "operating_guidance",
                        "skill_package_ids",
                        "knowledge_base_ids",
                    ):
                        merged = list(current.get(field, []))
                        for value in proposal["proposed_changes"].get(field, []):
                            if value not in merged:
                                merged.append(value)
                        current[field] = merged
                    updated = conn.execute(
                        "UPDATE digital_workers SET capabilities_json = ?, "
                        "capability_version = capability_version + 1, updated_at = ? "
                        "WHERE worker_id = ? AND capability_version = ?",
                        (
                            json.dumps(current, ensure_ascii=False),
                            now_ts(),
                            proposal["worker_id"],
                            proposal["base_capability_version"],
                        ),
                    )
                    if int(updated.rowcount or 0) != 1:
                        return None
                status = "published" if approve else "rejected"
                updated_proposal = conn.execute(
                    "UPDATE digital_worker_capability_proposals SET status = ?, reviewed_by = ?, "
                    "review_note = ?, updated_at = ? WHERE proposal_id = ? AND status = 'submitted'",
                    (status, reviewer_id, review_note, now_ts(), proposal_id),
                )
                if int(updated_proposal.rowcount or 0) != 1:
                    raise _CapabilityReviewConflict
        except _CapabilityReviewConflict:
            return None
        return self.get_capability_proposal(proposal_id)

    @staticmethod
    def _memory_candidate(row: Any) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        if not item.get("scope_code"):
            item["scope_code"] = None
        return item

    def create_memory_candidate(
        self,
        *,
        candidate_id: str,
        organization_id: str,
        worker_id: str,
        source_run_id: str,
        provider: str,
        mcp_server: str,
        scope_type: str,
        scope_code: str | None,
        content: str,
        created_by: int,
    ) -> dict[str, Any] | None:
        now = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO digital_worker_memory_candidates("
                "candidate_id, organization_id, worker_id, source_run_id, provider, mcp_server, "
                "scope_type, scope_code, content, created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source_run_id, provider, mcp_server, scope_type, scope_code) DO NOTHING",
                (
                    candidate_id,
                    organization_id,
                    worker_id,
                    source_run_id,
                    provider,
                    mcp_server,
                    scope_type,
                    scope_code or "",
                    content,
                    created_by,
                    now,
                    now,
                ),
            )
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM digital_worker_memory_candidates WHERE source_run_id = ? "
                "AND provider = ? AND mcp_server = ? AND scope_type = ? AND scope_code = ?",
                (source_run_id, provider, mcp_server, scope_type, scope_code or ""),
            ).fetchone()
        return self._memory_candidate(row)

    def list_memory_candidates(
        self,
        organization_id: str,
        *,
        worker_id: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM digital_worker_memory_candidates WHERE organization_id = ?"
        params: list[Any] = [organization_id]
        if worker_id is not None:
            sql += " AND worker_id = ?"
            params.append(worker_id)
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC, candidate_id LIMIT ?"
        params.append(max(1, min(limit, 500)))
        with self._db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        candidates: list[dict[str, Any]] = []
        for row in rows:
            candidate = self._memory_candidate(row)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    def get_memory_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM digital_worker_memory_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
        return self._memory_candidate(row)

    def claim_memory_candidate(
        self,
        candidate_id: str,
        *,
        reviewer_id: int,
        review_note: str,
        content: str,
    ) -> bool:
        with self._db.transaction() as conn:
            cur = conn.execute(
                "UPDATE digital_worker_memory_candidates SET status = 'writing', content = ?, "
                "reviewed_by = ?, review_note = ?, error_summary = NULL, updated_at = ? "
                "WHERE candidate_id = ? AND (status IN ('pending','failed') OR "
                "(status = 'writing' AND updated_at < ?))",
                (content, reviewer_id, review_note, now_ts(), candidate_id, now_ts() - 300),
            )
        return int(cur.rowcount or 0) == 1

    def reject_memory_candidate(
        self, candidate_id: str, *, reviewer_id: int, review_note: str
    ) -> bool:
        with self._db.transaction() as conn:
            cur = conn.execute(
                "UPDATE digital_worker_memory_candidates SET status = 'rejected', "
                "reviewed_by = ?, review_note = ?, updated_at = ? "
                "WHERE candidate_id = ? AND status IN ('pending','failed')",
                (reviewer_id, review_note, now_ts(), candidate_id),
            )
        return int(cur.rowcount or 0) == 1

    def finish_memory_candidate(
        self,
        candidate_id: str,
        *,
        status: str,
        memory_code: str | None = None,
        error_summary: str | None = None,
    ) -> bool:
        if status not in {"written", "failed"}:
            raise ValueError("invalid terminal memory-candidate status")
        with self._db.transaction() as conn:
            cur = conn.execute(
                "UPDATE digital_worker_memory_candidates SET status = ?, memory_code = ?, "
                "error_summary = ?, updated_at = ? WHERE candidate_id = ? AND status = 'writing'",
                (status, memory_code, error_summary, now_ts(), candidate_id),
            )
        return int(cur.rowcount or 0) == 1
