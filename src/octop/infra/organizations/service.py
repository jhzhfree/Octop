"""Organization hierarchy, access, and human/Agent appointment rules."""

from __future__ import annotations

from typing import Any

from octop.infra.db.services import RepoBundle
from octop.infra.users.identity import User


class OrganizationError(Exception):
    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class OrganizationService:
    def __init__(self, repos: RepoBundle, actor: User) -> None:
        self.repos = repos
        self.actor = actor

    def list(self) -> list[dict[str, Any]]:
        if self.actor.is_admin:
            return self.repos.organization_repo.list_all()
        return self.repos.organization_repo.list_for_user(self.actor.id)

    def create(self, *, name: str, description: str = "") -> dict[str, Any]:
        return self.repos.organization_repo.create(
            owner_user_id=self.actor.id,
            name=name.strip(),
            description=description.strip(),
        )

    def _organization(self, organization_id: str) -> dict[str, Any]:
        row = self.repos.organization_repo.get(organization_id)
        if row is None:
            raise OrganizationError("ORGANIZATION_NOT_FOUND", "organization not found", 404)
        return row

    def _role(self, organization_id: str) -> str:
        if self.actor.is_admin:
            return "owner"
        member = self.repos.organization_repo.get_member(organization_id, self.actor.id)
        if member is None:
            raise OrganizationError("ORGANIZATION_FORBIDDEN", "organization access denied", 403)
        return str(member["role"])

    def _require_manager(self, organization_id: str) -> str:
        role = self._role(organization_id)
        if role not in {"owner", "manager"}:
            raise OrganizationError("ORGANIZATION_FORBIDDEN", "organization manager required", 403)
        return role

    def _require_owner(self, organization_id: str) -> None:
        if self._require_manager(organization_id) != "owner":
            raise OrganizationError("ORGANIZATION_FORBIDDEN", "organization owner required", 403)

    def get_chart(self, organization_id: str) -> dict[str, Any]:
        organization = self._organization(organization_id)
        organization["member_role"] = self._role(organization_id)
        members = self.repos.organization_repo.list_members(organization_id)
        departments = self.repos.organization_repo.list_departments(organization_id)
        positions = self.repos.organization_repo.list_positions(organization_id)
        assignments = self.repos.organization_repo.list_assignments(organization_id)
        by_position: dict[str, list[dict[str, Any]]] = {str(p["position_id"]): [] for p in positions}
        for assignment in assignments:
            subject_type = str(assignment["subject_type"])
            subject_id = str(assignment["subject_id"])
            if subject_type == "user":
                try:
                    user = self.repos.user_repo.get(int(subject_id))
                except ValueError:
                    user = None
                label = (user.display_name or user.username) if user else "Unknown user"
            else:
                agent = self.repos.agent_repo.get(subject_id)
                label = agent.name if agent else "Unknown Agent"
            item = dict(assignment)
            item["label"] = label
            by_position[str(assignment["position_id"])].append(item)
        for position in positions:
            position["assignments"] = by_position[str(position["position_id"])]
        return {
            "organization": organization,
            "members": members,
            "departments": departments,
            "positions": positions,
        }

    def update_organization(
        self, organization_id: str, updates: dict[str, Any]
    ) -> dict[str, Any]:
        row = self._organization(organization_id)
        self._require_owner(organization_id)
        result = self.repos.organization_repo.update(organization_id, updates)
        return result or row

    def delete_organization(self, organization_id: str) -> None:
        self._organization(organization_id)
        self._require_owner(organization_id)
        if not self.repos.organization_repo.delete_organization(organization_id):
            raise OrganizationError("ORGANIZATION_NOT_FOUND", "organization not found", 404)

    def add_member(self, organization_id: str, user_id: int, role: str) -> dict[str, Any]:
        self._organization(organization_id)
        self._require_owner(organization_id)
        if role not in {"manager", "member"}:
            raise OrganizationError("ORGANIZATION_MEMBER_ROLE_INVALID", "invalid member role", 422)
        user = self.repos.user_repo.get(user_id)
        if user is None or user.disabled:
            raise OrganizationError("ORGANIZATION_MEMBER_INVALID", "active user not found", 404)
        if self.repos.organization_repo.get_member(organization_id, user_id) is not None:
            raise OrganizationError("ORGANIZATION_MEMBER_EXISTS", "user is already a member", 409)
        result = self.repos.organization_repo.add_member(organization_id, user_id, role)
        if result is None:
            raise OrganizationError("ORGANIZATION_MEMBER_EXISTS", "user is already a member", 409)
        return result

    def remove_member(self, organization_id: str, user_id: int) -> None:
        self._organization(organization_id)
        self._require_owner(organization_id)
        member = self.repos.organization_repo.get_member(organization_id, user_id)
        if member is None:
            raise OrganizationError("ORGANIZATION_MEMBER_NOT_FOUND", "member not found", 404)
        if member["role"] == "owner":
            raise OrganizationError("ORGANIZATION_OWNER_REQUIRED", "cannot remove owner", 409)
        if self.repos.organization_repo.user_has_assignments(organization_id, user_id):
            raise OrganizationError(
                "ORGANIZATION_MEMBER_ASSIGNED", "remove member appointments first", 409
            )
        self.repos.organization_repo.remove_member(organization_id, user_id)

    def _department(self, organization_id: str, department_id: str) -> dict[str, Any]:
        row = self.repos.organization_repo.get_department(department_id)
        if row is None or row["organization_id"] != organization_id:
            raise OrganizationError("ORGANIZATION_DEPARTMENT_NOT_FOUND", "department not found", 404)
        return row

    def _validate_department_parent(
        self, organization_id: str, parent_id: str | None, *, department_id: str | None = None
    ) -> None:
        if parent_id is None:
            return
        seen: set[str] = set()
        current_id: str | None = parent_id
        while current_id:
            if current_id == department_id:
                raise OrganizationError(
                    "ORGANIZATION_HIERARCHY_CYCLE", "department hierarchy cannot contain a cycle", 409
                )
            if current_id in seen:
                raise OrganizationError(
                    "ORGANIZATION_HIERARCHY_CYCLE", "department hierarchy already contains a cycle", 409
                )
            seen.add(current_id)
            current = self._department(organization_id, current_id)
            current_id = str(current["parent_department_id"]) if current["parent_department_id"] else None

    def create_department(
        self,
        organization_id: str,
        *,
        name: str,
        code: str = "",
        description: str = "",
        parent_department_id: str | None = None,
        sort_order: int = 0,
    ) -> dict[str, Any]:
        self._organization(organization_id)
        self._require_manager(organization_id)
        self._validate_department_parent(organization_id, parent_department_id)
        return self.repos.organization_repo.create_department(
            organization_id=organization_id,
            parent_department_id=parent_department_id,
            name=name.strip(),
            code=code.strip(),
            description=description.strip(),
            sort_order=sort_order,
        )

    def update_department(
        self, organization_id: str, department_id: str, updates: dict[str, Any]
    ) -> dict[str, Any]:
        self._organization(organization_id)
        self._require_manager(organization_id)
        current = self._department(organization_id, department_id)
        if "parent_department_id" in updates:
            self._validate_department_parent(
                organization_id, updates["parent_department_id"], department_id=department_id
            )
        for key in ("name", "code", "description"):
            if key in updates and updates[key] is not None:
                updates[key] = updates[key].strip()
        return self.repos.organization_repo.update_department(department_id, updates) or current

    def delete_department(self, organization_id: str, department_id: str) -> None:
        self._organization(organization_id)
        self._require_manager(organization_id)
        self._department(organization_id, department_id)
        self.repos.organization_repo.delete_department(department_id)

    def _position(self, organization_id: str, position_id: str) -> dict[str, Any]:
        row = self.repos.organization_repo.get_position(position_id)
        if row is None or row["organization_id"] != organization_id:
            raise OrganizationError("ORGANIZATION_POSITION_NOT_FOUND", "position not found", 404)
        return row

    def _validate_position_links(
        self,
        organization_id: str,
        *,
        department_id: str | None,
        reports_to_position_id: str | None,
        position_id: str | None = None,
    ) -> None:
        if department_id is not None:
            self._department(organization_id, department_id)
        current_id = reports_to_position_id
        seen: set[str] = set()
        while current_id:
            if current_id == position_id or current_id in seen:
                raise OrganizationError(
                    "ORGANIZATION_HIERARCHY_CYCLE", "position reporting line cannot contain a cycle", 409
                )
            seen.add(current_id)
            current = self._position(organization_id, current_id)
            current_id = (
                str(current["reports_to_position_id"])
                if current["reports_to_position_id"]
                else None
            )

    def create_position(
        self,
        organization_id: str,
        *,
        title: str,
        code: str = "",
        department_id: str | None = None,
        reports_to_position_id: str | None = None,
        duties: str = "",
        capabilities: list[str] | None = None,
    ) -> dict[str, Any]:
        self._organization(organization_id)
        self._require_manager(organization_id)
        self._validate_position_links(
            organization_id,
            department_id=department_id,
            reports_to_position_id=reports_to_position_id,
        )
        return self.repos.organization_repo.create_position(
            organization_id=organization_id,
            department_id=department_id,
            reports_to_position_id=reports_to_position_id,
            title=title.strip(),
            code=code.strip(),
            duties=duties.strip(),
            capabilities=[item.strip() for item in capabilities or [] if item.strip()],
        )

    def update_position(
        self, organization_id: str, position_id: str, updates: dict[str, Any]
    ) -> dict[str, Any]:
        self._organization(organization_id)
        self._require_manager(organization_id)
        current = self._position(organization_id, position_id)
        department_id = updates.get("department_id", current["department_id"])
        reports_to = updates.get("reports_to_position_id", current["reports_to_position_id"])
        self._validate_position_links(
            organization_id,
            department_id=department_id,
            reports_to_position_id=reports_to,
            position_id=position_id,
        )
        for key in ("title", "code", "duties"):
            if key in updates and updates[key] is not None:
                updates[key] = updates[key].strip()
        if "capabilities" in updates:
            updates["capabilities"] = [
                str(item).strip() for item in updates["capabilities"] if str(item).strip()
            ]
        return self.repos.organization_repo.update_position(position_id, updates) or current

    def delete_position(self, organization_id: str, position_id: str) -> None:
        self._organization(organization_id)
        self._require_manager(organization_id)
        self._position(organization_id, position_id)
        self.repos.organization_repo.delete_position(position_id)

    def add_assignment(
        self, organization_id: str, position_id: str, *, subject_type: str, subject_id: str
    ) -> dict[str, Any]:
        self._organization(organization_id)
        self._require_manager(organization_id)
        self._position(organization_id, position_id)
        subject_id = subject_id.strip()
        if subject_type == "user":
            try:
                target_user_id = int(subject_id)
            except ValueError as exc:
                raise OrganizationError("ORGANIZATION_ASSIGNEE_INVALID", "invalid user id", 422) from exc
            if self.repos.organization_repo.get_member(organization_id, target_user_id) is None:
                raise OrganizationError(
                    "ORGANIZATION_ASSIGNEE_INVALID", "user must be an organization member", 409
                )
            if self.repos.user_repo.get(target_user_id) is None:
                raise OrganizationError("ORGANIZATION_ASSIGNEE_INVALID", "user not found", 404)
        elif subject_type == "agent":
            agent = self.repos.agent_repo.get(subject_id)
            if agent is None:
                raise OrganizationError("ORGANIZATION_ASSIGNEE_INVALID", "Agent not found", 404)
            if not self.actor.is_admin and agent.user_id != self.actor.id and not agent.is_shared:
                raise OrganizationError(
                    "ORGANIZATION_ASSIGNEE_FORBIDDEN", "Agent access is not granted", 403
                )
        else:
            raise OrganizationError("ORGANIZATION_ASSIGNEE_INVALID", "invalid assignee type", 422)
        result = self.repos.organization_repo.add_assignment(
            position_id, subject_type, subject_id
        )
        if result is None:
            raise OrganizationError(
                "ORGANIZATION_ASSIGNMENT_EXISTS", "assignee already holds this position", 409
            )
        return result

    def remove_assignment(self, organization_id: str, assignment_id: str) -> None:
        self._organization(organization_id)
        self._require_manager(organization_id)
        assignment = self.repos.organization_repo.get_assignment(assignment_id)
        if assignment is None or assignment["organization_id"] != organization_id:
            raise OrganizationError("ORGANIZATION_ASSIGNMENT_NOT_FOUND", "assignment not found", 404)
        self.repos.organization_repo.delete_assignment(assignment_id)


