"""Source-owner management of Agent identity and protected host key imports."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from octop.api.deps import current_user, get_server
from octop.api.routers.external_knowledge import _fail, _service
from octop.infra.knowledge.external.agent_identity import AgentIdentityService
from octop.infra.knowledge.external.types import ExternalSourceError


class SensitiveIdentityRoute(APIRoute):
    """Validation failures must never echo a submitted private key."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except RequestValidationError as exc:
                errors = [{k: e[k] for k in ("loc", "type", "msg") if k in e} for e in exc.errors()]
                raise HTTPException(status_code=422, detail=errors) from None

        return handler


router = APIRouter(
    route_class=SensitiveIdentityRoute,
    prefix="/external-knowledge/sources/{source_id}/identity",
    tags=["knowledge"],
)


def _identity(server: Any) -> AgentIdentityService:
    return AgentIdentityService(_service(server), server.services.agent_repo)


class SimpleBindingBody(BaseModel):
    external_agent_id: str = Field(alias="externalAgentId", pattern=r"^[0-9a-f-]{36}$")
    private_key: str = Field(
        default="",
        alias="privateKey",
        max_length=16384,
        repr=False,
        description="PEM private key imported into protected host storage. Never sent to AKB or returned. Empty reuses the selected key.",
    )


class IdentitySearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=3, ge=1, le=50)


@router.get(
    "",
    summary="查看 Agent 身份接入状态",
    description="仅连接拥有者或管理员；返回绑定状态与组件可用性，不返回私钥和服务 Key。",
)
async def state(
    source_id: str, user: Any = Depends(current_user), server: Any = Depends(get_server)
) -> dict[str, Any]:
    try:
        return await _identity(server).describe(source_id, user.id, user.role == "admin")
    except ExternalSourceError as exc:
        raise _fail(exc) from exc


@router.post(
    "/agents/{agent_id}/connect",
    summary="绑定系统 Agent 并验证密钥",
    description="仅连接和 Agent 拥有者或管理员。私钥仅经管理请求进入 Octop 安全存储，不发送给 AKB；认证成功后自动绑定检索。",
)
async def connect(
    source_id: str,
    agent_id: str,
    body: SimpleBindingBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    try:
        return await _identity(server).connect(
            source_id,
            agent_id,
            user.id,
            user.role == "admin",
            body.external_agent_id,
            body.private_key,
        )
    except ExternalSourceError as exc:
        raise _fail(exc) from exc


@router.delete(
    "/agents/{agent_id}/connect",
    summary="解除 Agent 身份及检索绑定",
    description="保留受保护密钥文件以便恢复；AKB 公钥需在 AKB 单独撤销。",
)
async def disconnect(
    source_id: str,
    agent_id: str,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, bool]:
    try:
        _identity(server).disconnect(source_id, agent_id, user.id, user.role == "admin")
        return {"ok": True}
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
