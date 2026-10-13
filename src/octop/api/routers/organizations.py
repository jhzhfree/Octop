"""Organization chart, positions, and human/Agent appointments."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from octop.api.deps import current_user, get_server
from octop.infra.organizations import OrganizationError, OrganizationService

router = APIRouter()


class OrganizationCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)


class OrganizationPatchBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)


class OrganizationMemberCreateBody(BaseModel):
    user_id: int = Field(gt=0)
    role: Literal["manager", "member"] = "member"


class DepartmentCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    code: str = Field(default="", max_length=80)
    description: str = Field(default="", max_length=2000)
    parent_department_id: str | None = None
    sort_order: int = 0


class DepartmentPatchBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    code: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    parent_department_id: str | None = None
    sort_order: int | None = None


class PositionCreateBody(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    code: str = Field(default="", max_length=80)
    department_id: str | None = None
    reports_to_position_id: str | None = None
    duties: str = Field(default="", max_length=8000)
    capabilities: list[str] = Field(default_factory=list, max_length=50)


class PositionPatchBody(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    code: str | None = Field(default=None, max_length=80)
    department_id: str | None = None
    reports_to_position_id: str | None = None
    duties: str | None = Field(default=None, max_length=8000)
    capabilities: list[str] | None = Field(default=None, max_length=50)


class AppointmentCreateBody(BaseModel):
    subject_type: Literal["user", "agent"]
    subject_id: str = Field(min_length=1, max_length=128)


def _service(server: Any, user: Any) -> OrganizationService:
    services = getattr(server, "services", None)
    if services is None:
        raise HTTPException(status_code=503, detail="database is not ready")
    return OrganizationService(services.repos, user)


def _updates(body: BaseModel, nullable: set[str] | None = None) -> dict[str, Any]:
    values = body.model_dump(exclude_unset=True)
    nullable_fields = nullable or set()
    invalid = [key for key, value in values.items() if value is None and key not in nullable_fields]
    if invalid:
        raise HTTPException(status_code=422, detail="null is not allowed for: " + ", ".join(invalid))
    return values


def _call(callback: Any) -> Any:
    try:
        return callback()
    except OrganizationError as exc:
        raise HTTPException(
            status_code=exc.status,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


@router.get("/organizations", summary="List organizations available to the current user")
async def list_organizations(
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> list[dict[str, Any]]:
    return _service(server, user).list()


@router.post("/organizations", status_code=201, summary="Create an organization")
async def create_organization(
    body: OrganizationCreateBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    return _service(server, user).create(name=body.name, description=body.description)


@router.get("/organizations/{organization_id}/chart", summary="Get an organization chart")
async def organization_chart(
    organization_id: str,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    service = _service(server, user)
    return _call(lambda: service.get_chart(organization_id))


@router.patch("/organizations/{organization_id}", summary="Update organization details")
async def update_organization(
    organization_id: str,
    body: OrganizationPatchBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    service = _service(server, user)
    return _call(
        lambda: service.update_organization(
            organization_id, _updates(body)
        )
    )


@router.delete("/organizations/{organization_id}", status_code=204, summary="Delete an organization")
async def delete_organization(
    organization_id: str,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> None:
    service = _service(server, user)
    _call(lambda: service.delete_organization(organization_id))


@router.post(
    "/organizations/{organization_id}/members",
    status_code=201,
    summary="Add a user to an organization",
)
async def add_organization_member(
    organization_id: str,
    body: OrganizationMemberCreateBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    service = _service(server, user)
    return _call(
        lambda: service.add_member(organization_id, body.user_id, body.role)
    )


@router.delete(
    "/organizations/{organization_id}/members/{user_id}",
    status_code=204,
    summary="Remove a user from an organization",
)
async def remove_organization_member(
    organization_id: str,
    user_id: int,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> None:
    service = _service(server, user)
    _call(lambda: service.remove_member(organization_id, user_id))


@router.post(
    "/organizations/{organization_id}/departments",
    status_code=201,
    summary="Create a department",
)
async def create_department(
    organization_id: str,
    body: DepartmentCreateBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    service = _service(server, user)
    return _call(
        lambda: service.create_department(organization_id, **body.model_dump())
    )


@router.patch(
    "/organizations/{organization_id}/departments/{department_id}",
    summary="Update a department",
)
async def update_department(
    organization_id: str,
    department_id: str,
    body: DepartmentPatchBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    service = _service(server, user)
    return _call(
        lambda: service.update_department(
            organization_id, department_id, _updates(body, {"parent_department_id"})
        )
    )


@router.delete(
    "/organizations/{organization_id}/departments/{department_id}",
    status_code=204,
    summary="Delete a department",
)
async def delete_department(
    organization_id: str,
    department_id: str,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> None:
    service = _service(server, user)
    _call(lambda: service.delete_department(organization_id, department_id))


@router.post(
    "/organizations/{organization_id}/positions",
    status_code=201,
    summary="Create a position",
)
async def create_position(
    organization_id: str,
    body: PositionCreateBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    service = _service(server, user)
    return _call(lambda: service.create_position(organization_id, **body.model_dump()))


@router.patch(
    "/organizations/{organization_id}/positions/{position_id}",
    summary="Update a position",
)
async def update_position(
    organization_id: str,
    position_id: str,
    body: PositionPatchBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    service = _service(server, user)
    return _call(
        lambda: service.update_position(
            organization_id, position_id, _updates(body, {"department_id", "reports_to_position_id"})
        )
    )


@router.delete(
    "/organizations/{organization_id}/positions/{position_id}",
    status_code=204,
    summary="Delete a position",
)
async def delete_position(
    organization_id: str,
    position_id: str,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> None:
    service = _service(server, user)
    _call(lambda: service.delete_position(organization_id, position_id))


@router.post(
    "/organizations/{organization_id}/positions/{position_id}/appointments",
    status_code=201,
    summary="Assign a user or Agent to a position",
)
async def add_appointment(
    organization_id: str,
    position_id: str,
    body: AppointmentCreateBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    service = _service(server, user)
    return _call(
        lambda: service.add_assignment(
            organization_id,
            position_id,
            subject_type=body.subject_type,
            subject_id=body.subject_id,
        )
    )


@router.delete(
    "/organizations/{organization_id}/appointments/{assignment_id}",
    status_code=204,
    summary="Remove a position appointment",
)
async def remove_appointment(
    organization_id: str,
    assignment_id: str,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> None:
    service = _service(server, user)
    _call(lambda: service.remove_assignment(organization_id, assignment_id))
