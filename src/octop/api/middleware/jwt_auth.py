"""Require JWT for all /api/* routes except an explicit allowlist.

Validated users are cached on ``request.state.octop_user`` so route-level
``Depends(current_user)`` can reuse the result without re-decoding.

Ordinary requests never renew tokens. Renewal is an explicit authenticated action.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from octop.api.deps import (
    authenticate_request,
    is_jwt_exempt_request,
)
from octop.infra.errors import OctopError

_INSTALL_ATTR = "_octop_jwt_auth_installed"
logger = logging.getLogger(__name__)


def install(app: Any, server: Any) -> None:
    if getattr(app, _INSTALL_ATTR, False):
        return
    setattr(app, _INSTALL_ATTR, True)

    @app.middleware("http")  # type: ignore[untyped-decorator]
    async def _jwt_auth(
        request: Request,
        call_next: Callable[[Request], Awaitable[Any]],
    ) -> Any:
        path = request.url.path
        if not path.startswith("/api/") or is_jwt_exempt_request(request):
            return await call_next(request)

        try:
            request.state.octop_user = authenticate_request(request, server)
        except OctopError as exc:
            # Middleware returns directly (skips FastAPI exception handlers).
            if exc.status >= 500:
                logger.error(
                    "OctopError %s in %s: %s",
                    exc.code.value,
                    request.url.path,
                    exc.message,
                    exc_info=exc,
                )
            return JSONResponse(status_code=exc.status, content=exc.to_envelope())

        return await call_next(request)
