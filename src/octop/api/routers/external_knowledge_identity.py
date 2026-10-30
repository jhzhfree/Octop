"""Source-owner management of public Agent keys, through the common SDK."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from octop.api.deps import current_user, get_server
from octop.api.routers.external_knowledge import _fail, _service
from octop.infra.knowledge.external.agent_identity import AgentIdentityService
from octop.infra.knowledge.external.types import ExternalSourceError

router = APIRouter(prefix="/external-knowledge/sources/{source_id}/identity", tags=["knowledge"])


def _identity(server: Any) -> AgentIdentityService:
    return AgentIdentityService(_service(server), server.services.agent_repo)


class IdentityConfiguration(BaseModel):
    mode: Literal["LEGACY", "AGENT_KEY_RS256_V2"]
    policy_id: str = Field(default="", alias="policyId", max_length=19)
    application_principal_id: str = Field(default="", alias="applicationPrincipalId", max_length=19)
    issuer: str = Field(min_length=1, max_length=255)
    audience: str = Field(min_length=1, max_length=128)


class PrepareBody(BaseModel):
    rotate: bool = False


class ProveBody(BaseModel):
    kid: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    challenge: dict[str, Any] = Field(
        description="AKB administrator's public registration challenge; never a private key"
    )


class ActivateBody(BaseModel):
    kid: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")


class IdentitySearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=3, ge=1, le=50)


@router.get(
    "",
    summary="查看 Agent 身份接入状态",
    description="仅连接拥有者或管理员；返回公开身份、公钥及组件状态，不返回私钥和服务 Key。",
)
async def state(
    source_id: str, user: Any = Depends(current_user), server: Any = Depends(get_server)
) -> dict[str, Any]:
    try:
        return await _identity(server).describe(source_id, user.id, user.role == "admin")
    except ExternalSourceError as exc:
        raise _fail(exc) from exc


@router.put(
    "",
    summary="保存 AKB V2 身份策略",
    description="保留服务端生成的 Agent 身份映射；切换模式不会自动登记公钥或扩大授权。",
)
async def configure(
    source_id: str,
    body: IdentityConfiguration,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, bool]:
    try:
        _identity(server).configure(
            source_id, user.id, user.role == "admin", body.model_dump(by_alias=True)
        )
        return {"ok": True}
    except ExternalSourceError as exc:
        raise _fail(exc) from exc


@router.post(
    "/agents/{agent_id}/prepare",
    summary="生成或准备轮换 Agent 密钥",
    description="检查连接和 Agent 的所有权，私钥由 SDK 保存在受保护宿主目录；响应只含公钥。",
)
async def prepare(
    source_id: str,
    agent_id: str,
    body: PrepareBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    try:
        return await _identity(server).prepare(
            source_id, agent_id, user.id, user.role == "admin", body.rotate
        )
    except ExternalSourceError as exc:
        raise _fail(exc) from exc


@router.post(
    "/agents/{agent_id}/proof",
    summary="签署 AKB 登记挑战",
    description="需要拥有当前连接和 Agent；只签与已保存身份、策略和 kid 一致的专用挑战。",
)
async def prove(
    source_id: str,
    agent_id: str,
    body: ProveBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    try:
        return await _identity(server).prove(
            source_id, agent_id, user.id, user.role == "admin", body.kid, body.challenge
        )
    except ExternalSourceError as exc:
        raise _fail(exc) from exc


@router.post(
    "/agents/{agent_id}/activate",
    summary="验证并选中 Agent 公钥",
    description="先由 AKB 验证候选密钥归属和 ACTIVE 状态，成功后才切换本地签名密钥。",
)
async def activate(
    source_id: str,
    agent_id: str,
    body: ActivateBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    try:
        return await _identity(server).verify(
            source_id, agent_id, user.id, user.role == "admin", body.kid
        )
    except ExternalSourceError as exc:
        raise _fail(exc) from exc


@router.post(
    "/agents/{agent_id}/probe",
    summary="验证当前 Agent 身份",
    description="管理操作仅验证选中 Agent 的身份，不伪造模型运行上下文。",
)
async def probe(
    source_id: str,
    agent_id: str,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    try:
        return await _identity(server).verify(
            source_id, agent_id, user.id, user.role == "admin", None
        )
    except ExternalSourceError as exc:
        raise _fail(exc) from exc


@router.post(
    "/agents/{agent_id}/search",
    summary="以获准 Agent 测试知识库",
    description="管理测试检查连接和 Agent 所有权，审计会话标为 management；普通模型检索仍从宿主上下文取得身份。",
)
async def search(
    source_id: str,
    agent_id: str,
    body: IdentitySearchBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    try:
        return await _identity(server).search(
            source_id, agent_id, user.id, user.role == "admin", body.query, body.top_k
        )
    except ExternalSourceError as exc:
        raise _fail(exc) from exc
