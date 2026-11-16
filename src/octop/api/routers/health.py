"""Liveness probe (no auth)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from octop.api.deps import get_server

router = APIRouter()


@router.get("", summary="Liveness check (not model or integration readiness)")
async def health(server: Any = Depends(get_server)) -> dict[str, Any]:
    """Liveness with database binding and loaded counts, not dependency probes.

    No auth required. An ok response also permits an unfinished setup with no
    model. It does not verify model inference, knowledge access or integrations.
    """
    bound = server.database_bound
    users = server.user_manager.count() if server.user_manager is not None else 0
    agents = (
        len(server.app_runtime.agent_registry.list_rows()) if server.app_runtime is not None else 0
    )
    return {
        "ok": True,
        "started_at": server._started_at,
        "db": bound,
        "users_loaded": users,
        "agents_running": agents,
    }
