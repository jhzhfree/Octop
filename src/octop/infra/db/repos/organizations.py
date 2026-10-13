"""Organization structure and workforce assignment table access."""

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


def _is_unique_violation(exc: BaseException) -> bool:
    if isinstance(exc, sqlite3.IntegrityError):
        return "unique" in str(exc).lower()
    return pg_errors is not None and isinstance(exc, pg_errors.UniqueViolation)


def _dict(row: Any) -> dict[str, Any]:
    return dict(row)


def _position_dict(row: Any) -> dict[str, Any]:
    result = _dict(row)
    raw = result.pop("capabilities_json", "[]")
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        value = []
    result["capabilities"] = [str(item) for item in value] if isinstance(value, list) else []
    return result


class OrganizationRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def list_for_user(self, user_id: int) -> list[dict[str, Any]]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT o.*, m.role AS member_role FROM organizations o "
                "JOIN organization_members m ON m.organization_id = o.organization_id "
                "WHERE m.user_id = ? ORDER BY o.name, o.organization_id",
                (user_id,),
            ).fetchall()
        return [_dict(row) for row in rows]

    def list_all(self) -> list[dict[str, Any]]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM organizations ORDER BY name, organization_id"
            ).fetchall()
        return [_dict(row) for row in rows]

    def get(self, organization_id: str) -> dict[str, Any] | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM organizations WHERE organization_id = ?",
                (organization_id,),
            ).fetchone()
        return _dict(row) if row else None

    def create(self, *, owner_user_id: int, name: str, description: str) -> dict[str, Any]:
        public_id = new_ulid()
        now = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO organizations(organization_id, owner_user_id, name, description, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (public_id, owner_user_id, name, description, now, now),
            )
            conn.execute(
                "INSERT INTO organization_members(organization_id, user_id, role, created_at) "
                "VALUES (?, ?, 'owner', ?)",
                (public_id, owner_user_id, now),
            )
            row = conn.execute(
                "SELECT * FROM organizations WHERE organization_id = ?", (public_id,)
            ).fetchone()
        return _dict(row)

    def update(self, organization_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {"name", "description"}
        fields = [(key, value) for key, value in updates.items() if key in allowed]
        if not fields:
            return self.get(organization_id)
        now = now_ts()
        assignments = ", ".join(f"{key} = ?" for key, _ in fields)
        values = [value for _, value in fields]
        with self._db.transaction() as conn:
            conn.execute(
                f"UPDATE organizations SET {assignments}, updated_at = ? "
                "WHERE organization_id = ?",
                (*values, now, organization_id),
            )
        return self.get(organization_id)

    def get_member(self, organization_id: str, user_id: int) -> dict[str, Any] | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT m.organization_id, m.user_id, m.role, m.created_at, "
                "u.username, u.display_name, u.disabled "
                "FROM organization_members m JOIN users u ON u.id = m.user_id "
                "WHERE m.organization_id = ? AND m.user_id = ?",
                (organization_id, user_id),
            ).fetchone()
        return _dict(row) if row else None

    def list_members(self, organization_id: str) -> list[dict[str, Any]]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT m.organization_id, m.user_id, m.role, m.created_at, "
                "u.username, u.display_name, u.disabled "
                "FROM organization_members m JOIN users u ON u.id = m.user_id "
                "WHERE m.organization_id = ? ORDER BY m.role, u.username",
                (organization_id,),
            ).fetchall()
        return [_dict(row) for row in rows]

    def add_member(self, organization_id: str, user_id: int, role: str) -> dict[str, Any] | None:
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    "INSERT INTO organization_members(organization_id, user_id, role, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (organization_id, user_id, role, now_ts()),
                )
        except Exception as exc:
            if _is_unique_violation(exc):
                return None
            raise
        return self.get_member(organization_id, user_id)

    def remove_member(self, organization_id: str, user_id: int) -> bool:
        with self._db.transaction() as conn:
            cur = conn.execute(
                "DELETE FROM organization_members WHERE organization_id = ? AND user_id = ?",
                (organization_id, user_id),
            )
        return int(cur.rowcount or 0) > 0

    def user_has_assignments(self, organization_id: str, user_id: int) -> bool:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM organization_assignments a "
                "JOIN organization_positions p ON p.position_id = a.position_id "
                "WHERE p.organization_id = ? AND a.subject_type = 'user' AND a.subject_id = ? "
                "LIMIT 1",
                (organization_id, str(user_id)),
            ).fetchone()
        return row is not None

    def list_departments(self, organization_id: str) -> list[dict[str, Any]]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM organization_departments WHERE organization_id = ? "
                "ORDER BY sort_order, name, department_id",
                (organization_id,),
            ).fetchall()
        return [_dict(row) for row in rows]

    def get_department(self, department_id: str) -> dict[str, Any] | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM organization_departments WHERE department_id = ?",
                (department_id,),
            ).fetchone()
        return _dict(row) if row else None

    def create_department(
        self,
        *,
        organization_id: str,
        parent_department_id: str | None,
        name: str,
        code: str,
        description: str,
        sort_order: int,
    ) -> dict[str, Any]:
        public_id = new_ulid()
        now = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO organization_departments(department_id, organization_id, "
                "parent_department_id, name, code, description, sort_order, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (public_id, organization_id, parent_department_id, name, code, description,
                 sort_order, now, now),
            )
            row = conn.execute(
                "SELECT * FROM organization_departments WHERE department_id = ?", (public_id,)
            ).fetchone()
        return _dict(row)

    def update_department(
        self, department_id: str, updates: dict[str, Any]
    ) -> dict[str, Any] | None:
        allowed = {"parent_department_id", "name", "code", "description", "sort_order"}
        fields = [(key, value) for key, value in updates.items() if key in allowed]
        if not fields:
            return self.get_department(department_id)
        values = [value for _, value in fields]
        assignments = ", ".join(f"{key} = ?" for key, _ in fields)
        with self._db.transaction() as conn:
            conn.execute(
                f"UPDATE organization_departments SET {assignments}, updated_at = ? "
                "WHERE department_id = ?",
                (*values, now_ts(), department_id),
            )
        return self.get_department(department_id)

    def delete_department(self, department_id: str) -> bool:
        with self._db.transaction() as conn:
            cur = conn.execute(
                "DELETE FROM organization_departments WHERE department_id = ?", (department_id,)
            )
        return int(cur.rowcount or 0) > 0

    def list_positions(self, organization_id: str) -> list[dict[str, Any]]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM organization_positions WHERE organization_id = ? "
                "ORDER BY title, position_id",
                (organization_id,),
            ).fetchall()
        return [_position_dict(row) for row in rows]

    def get_position(self, position_id: str) -> dict[str, Any] | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM organization_positions WHERE position_id = ?", (position_id,)
            ).fetchone()
        return _position_dict(row) if row else None

    def create_position(
        self,
        *,
        organization_id: str,
        department_id: str | None,
        reports_to_position_id: str | None,
        title: str,
        code: str,
        duties: str,
        capabilities: list[str],
    ) -> dict[str, Any]:
        public_id = new_ulid()
        now = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO organization_positions(position_id, organization_id, department_id, "
                "reports_to_position_id, title, code, duties, capabilities_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (public_id, organization_id, department_id, reports_to_position_id, title, code,
                 duties, json.dumps(capabilities, ensure_ascii=False), now, now),
            )
            row = conn.execute(
                "SELECT * FROM organization_positions WHERE position_id = ?", (public_id,)
            ).fetchone()
        return _position_dict(row)

    def update_position(self, position_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {
            "department_id", "reports_to_position_id", "title", "code", "duties", "capabilities"
        }
        fields: list[tuple[str, Any]] = []
        for key, value in updates.items():
            if key not in allowed:
                continue
            if key == "capabilities":
                key, value = "capabilities_json", json.dumps(value, ensure_ascii=False)
            fields.append((key, value))
        if fields:
            values = [value for _, value in fields]
            assignments = ", ".join(f"{key} = ?" for key, _ in fields)
            with self._db.transaction() as conn:
                conn.execute(
                    f"UPDATE organization_positions SET {assignments}, updated_at = ? "
                    "WHERE position_id = ?",
                    (*values, now_ts(), position_id),
                )
        return self.get_position(position_id)

    def delete_position(self, position_id: str) -> bool:
        with self._db.transaction() as conn:
            cur = conn.execute(
                "DELETE FROM organization_positions WHERE position_id = ?", (position_id,)
            )
        return int(cur.rowcount or 0) > 0

    def list_assignments(self, organization_id: str) -> list[dict[str, Any]]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT a.assignment_id, a.position_id, a.subject_type, a.subject_id, a.created_at "
                "FROM organization_assignments a JOIN organization_positions p "
                "ON p.position_id = a.position_id WHERE p.organization_id = ? "
                "ORDER BY a.created_at, a.assignment_id",
                (organization_id,),
            ).fetchall()
        return [_dict(row) for row in rows]

    def add_assignment(
        self, position_id: str, subject_type: str, subject_id: str
    ) -> dict[str, Any] | None:
        public_id = new_ulid()
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    "INSERT INTO organization_assignments(assignment_id, position_id, subject_type, "
                    "subject_id, created_at) VALUES (?, ?, ?, ?, ?)",
                    (public_id, position_id, subject_type, subject_id, now_ts()),
                )
                row = conn.execute(
                    "SELECT assignment_id, position_id, subject_type, subject_id, created_at "
                    "FROM organization_assignments WHERE assignment_id = ?",
                    (public_id,),
                ).fetchone()
        except Exception as exc:
            if _is_unique_violation(exc):
                return None
            raise
        return _dict(row)

    def get_assignment(self, assignment_id: str) -> dict[str, Any] | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT a.assignment_id, a.position_id, a.subject_type, a.subject_id, "
                "a.created_at, p.organization_id FROM organization_assignments a "
                "JOIN organization_positions p ON p.position_id = a.position_id "
                "WHERE a.assignment_id = ?",
                (assignment_id,),
            ).fetchone()
        return _dict(row) if row else None

    def delete_organization(self, organization_id: str) -> bool:
        with self._db.transaction() as conn:
            cur = conn.execute(
                "DELETE FROM organizations WHERE organization_id = ?", (organization_id,)
            )
        return int(cur.rowcount or 0) > 0

    def delete_assignment(self, assignment_id: str) -> bool:
        with self._db.transaction() as conn:
            cur = conn.execute(
                "DELETE FROM organization_assignments WHERE assignment_id = ?", (assignment_id,)
            )
        return int(cur.rowcount or 0) > 0

