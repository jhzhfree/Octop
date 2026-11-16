"""Isolated composition only; deliberately not mounted by build_app.

Default service is unavailable. No user JWT dependency or production provider.
"""

from __future__ import annotations

import json
import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from octop.infra.db.repos.mattermost_ingress import IntakeError
from octop.infra.digital_workers.mattermost_ingress import MattermostIngressService, public_intake


class CancellationBody(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    operation_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_revision: int = Field(ge=1, le=8)


def create_router(service: MattermostIngressService | None = None) -> APIRouter:
    router = APIRouter()
    path = "/api/integrations/mattermost/v1/intakes"

    async def dispatch(request: Request, key: str | None, cancel: bool = False) -> JSONResponse:
        headers = {"Cache-Control": "no-store"}
        try:
            if service is None:
                raise IntakeError(503, "BROKER_ISOLATION_UNCONFIGURED")
            if request.url.query or key is not None and not re.fullmatch(r"[a-f0-9]{64}", key):
                raise IntakeError(422, "INTAKE_REQUEST_INVALID")
            body = bytearray()
            async for part in request.stream():
                if len(body) + len(part) > (1024 if cancel else 32768):
                    raise IntakeError(413, "BROKER_BODY_TOO_LARGE")
                body.extend(part)
            raw = bytes(body)
            mapping = service.authenticate(request.method, request.url.path, raw, request.headers)
            status = 200
            if request.method == "GET":
                if raw or key is None:
                    raise IntakeError(422, "INTAKE_REQUEST_INVALID")
                row = service.repo.inspect(mapping, key)
            elif cancel:
                if request.headers.get("content-type") != "application/json" or key is None:
                    raise IntakeError(415, "INTAKE_CONTENT_TYPE")

                def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
                    value = dict(pairs)
                    if len(value) != len(pairs):
                        raise ValueError
                    return value

                data = CancellationBody.model_validate(json.loads(raw, object_pairs_hook=unique))
                row = await service.cancel(mapping, key, data.expected_revision, data.operation_id)
            else:
                if request.headers.get("content-type") != "application/octet-stream":
                    raise IntakeError(415, "INTAKE_CONTENT_TYPE")
                row, created = await service.accept(mapping, raw)
                status = 201 if created else 200
            return JSONResponse(public_intake(row), status_code=status, headers=headers)
        except IntakeError as exc:
            return JSONResponse({"code": exc.code}, status_code=exc.status, headers=headers)
        except (ValueError, ValidationError):
            return JSONResponse(
                {"code": "INTAKE_REQUEST_INVALID"}, status_code=422, headers=headers
            )
        except Exception:
            # A commit error may mean committed but acknowledgement lost. No retries.
            return JSONResponse(
                {"code": "INTAKE_OUTCOME_UNKNOWN"}, status_code=503, headers=headers
            )

    @router.post(path)
    async def accept(request: Request) -> JSONResponse:
        return await dispatch(request, None)

    @router.get(path + "/{delivery_key}")
    async def inspect(request: Request, delivery_key: str) -> JSONResponse:
        return await dispatch(request, delivery_key)

    @router.post(path + "/{delivery_key}/cancellations")
    async def cancel(request: Request, delivery_key: str) -> JSONResponse:
        return await dispatch(request, delivery_key, True)

    return router
