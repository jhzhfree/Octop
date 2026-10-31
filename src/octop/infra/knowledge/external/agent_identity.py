"""Agent identity management and native retrieval through the platform-neutral SDK.

Private keys enter protected storage only through trusted management IPC and
never leave the host. Model tool arguments never choose a signing identity.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import uuid
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.external_knowledge import ExternalSourceRow
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.knowledge.akb_identity import OctopIdentity
from octop.infra.knowledge.external.service import ExternalKnowledgeService
from octop.infra.knowledge.external.types import (
    ExternalSearchResult,
    ExternalSourceError,
    ExternalSourceSpec,
)
from octop.infra.utils.paths import PathLayout


def _invalid() -> OctopError:
    return OctopError.localized(ErrorCode.AGENT_IDENTITY_INVALID)


def _sdk_path() -> tuple[str, str] | None:
    node = shutil.which("node")
    configured = os.environ.get("OCTOP_AGENT_IDENTITY_SDK_BRIDGE", "").strip()
    candidate = (
        Path(configured)
        if configured
        else Path(__file__).resolve().parents[5] / "integrations/agent-identity-sdk/dist/bridge.js"
    )
    script = (
        str(candidate)
        if candidate.is_absolute() and candidate.is_file()
        else shutil.which("akb-agent-identity-bridge")
    )
    return (node, script) if node and script else None


async def sdk_call(payload: dict[str, Any], timeout: float = 30) -> dict[str, Any]:
    command = _sdk_path()
    if command is None:
        raise OctopError.localized(ErrorCode.AGENT_IDENTITY_UNAVAILABLE)
    root = str(PathLayout.from_env().root.resolve() / "agent-identity-keys")
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise OctopError.localized(ErrorCode.AGENT_IDENTITY_UNAVAILABLE) from exc
    try:
        stdout, _stderr = await asyncio.wait_for(
            process.communicate(json.dumps({**payload, "root": root}, ensure_ascii=False).encode()),
            timeout=timeout,
        )
    except (TimeoutError, asyncio.CancelledError) as exc:
        if process.returncode is None:
            process.kill()
        await process.wait()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise OctopError.localized(ErrorCode.AGENT_IDENTITY_UNAVAILABLE) from exc
    try:
        if len(stdout) > 3_000_000:
            raise ValueError()
        result = json.loads(stdout)
        if not isinstance(result, dict):
            raise ValueError()
        if process.returncode != 0 or result.get("ok") is not True:
            code = result.get("code")
            if code in {
                "AUTHENTICATION_FAILED",
                "AUTHORIZATION_DENIED",
                "KEY_NOT_REGISTERED",
                "KEY_UNAVAILABLE",
            }:
                raise OctopError.localized(ErrorCode.AGENT_IDENTITY_NOT_READY)
            raise OctopError.localized(ErrorCode.AGENT_IDENTITY_UNAVAILABLE)
        data = result["data"]
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (ValueError, KeyError, TypeError) as exc:
        raise OctopError.localized(ErrorCode.AGENT_IDENTITY_UNAVAILABLE) from exc


def _default(row: ExternalSourceRow, config: dict[str, Any]) -> dict[str, Any]:
    return {
        "platformId": "octop",
        "installationId": "octop-" + row.source_id,
        "workspaceId": row.source_id,
        "issuer": "https://octop.local/agents",
        "audience": "akb-external-api",
        "policyId": "",
        "applicationPrincipalId": "",
        "resource": row.endpoint.rstrip("/"),
        "agents": {},
        **config,
    }


def _binding(config: dict[str, Any], native_id: str) -> tuple[dict[str, str], dict[str, str]]:
    agent = config.get("agents", {}).get(native_id)
    if not isinstance(agent, dict):
        raise OctopError.localized(ErrorCode.AGENT_IDENTITY_NOT_READY)
    owner = {key: str(config[key]) for key in ("platformId", "installationId", "workspaceId")}
    owner["externalAgentId"] = str(agent["externalAgentId"])
    binding = {
        **owner,
        "runtimeAgentKey": str(agent.get("runtimeAgentKey") or native_id),
        "bindingId": str(agent.get("bindingId") or "1"),
        **{
            key: str(config[key])
            for key in ("policyId", "applicationPrincipalId", "issuer", "audience", "resource")
        },
    }
    return owner, binding


def _request(
    row: ExternalSourceRow, config: dict[str, Any], native_id: str, api_key: str
) -> dict[str, Any]:
    owner, binding = _binding(config, native_id)
    return {
        "owner": owner,
        "binding": binding,
        "apiKey": api_key,
        "origin": row.endpoint.rstrip("/"),
        "transport": "mcp" if row.kind == "akb-mcp" else "rest",
        "timeoutMs": row.timeout_ms,
        "allowInsecureLoopback": row.allow_insecure_http,
        "invocation": {"sessionId": "management", "invocationId": str(uuid.uuid4())},
    }


class AgentIdentityService:
    def __init__(self, sources: ExternalKnowledgeService, agents: AgentRepo) -> None:
        self.sources = sources
        self.agents = agents

    def source(
        self, source_id: str, user_id: int, is_admin: bool
    ) -> tuple[ExternalSourceRow, dict[str, Any]]:
        row = self.sources._require_manage(source_id, user_id=user_id, is_admin=is_admin)
        if row.kind not in {"akb-mcp", "akb-rest"}:
            raise _invalid()
        return row, _default(row, self.sources.identity_config(row))

    def agent(self, agent_id: str, user_id: int, is_admin: bool) -> Any:
        row = self.agents.get(agent_id)
        if row is None or (not is_admin and row.user_id != user_id):
            raise OctopError.localized(ErrorCode.KNOWLEDGE_FORBIDDEN)
        return row

    async def describe(self, source_id: str, user_id: int, is_admin: bool) -> dict[str, Any]:
        row, config = self.source(source_id, user_id, is_admin)
        try:
            capabilities = await sdk_call({"op": "capabilities"})
        except (TimeoutError, OctopError, OSError):
            capabilities = {"supported": False}
        ready = bool(capabilities.get("supported"))
        available = self.agents.list_all() if is_admin else self.agents.list_by_user(user_id)
        bound = config["agents"] if config.get("simple") else {}
        result: dict[str, Any] = {"runtimeReady": ready, "agents": []}
        for record in available:
            current = bound.get(record.agent_id)
            active_kid = None
            if current and ready:
                try:
                    state = await sdk_call(
                        {"op": "state", "owner": _binding(config, record.agent_id)[0]}
                    )
                    active_kid = state.get("activeKid")
                except (TimeoutError, OctopError):
                    active_kid = None
            result["agents"].append(
                {
                    "agentId": record.agent_id,
                    "name": record.name,
                    "externalAgentId": current.get("externalAgentId", "") if current else "",
                    "activeKid": active_kid,
                    "lastVerifiedAt": current.get("lastVerifiedAt") if current else None,
                    "lastVerificationStatus": current.get("lastVerificationStatus")
                    if current
                    else None,
                }
            )
        return result

    async def generate(
        self, source_id: str, user_id: int, is_admin: bool, external_id: str
    ) -> dict[str, Any]:
        row, _config = self.source(source_id, user_id, is_admin)
        external_id = external_id or str(uuid.uuid4())
        try:
            if str(uuid.UUID(external_id)) != external_id:
                raise ValueError()
        except ValueError as exc:
            raise _invalid() from exc
        return await sdk_call(
            {"op": "generate", "externalAgentId": external_id, "keyScope": row.source_id}
        )

    async def connect(
        self,
        source_id: str,
        agent_id: str,
        user_id: int,
        is_admin: bool,
        external_id: str,
        private_key: str,
        key_reference: str = "",
    ) -> dict[str, Any]:
        row, config = self.source(source_id, user_id, is_admin)
        self.agent(agent_id, user_id, is_admin)
        try:
            if str(uuid.UUID(external_id)) != external_id:
                raise ValueError()
        except ValueError as exc:
            raise _invalid() from exc
        if any(
            native != agent_id and a.get("externalAgentId") == external_id
            for native, a in config["agents"].items()
        ):
            raise _invalid()
        current = config["agents"].get(agent_id)
        if current and current.get("externalAgentId") != external_id:
            # Rebinding requires explicit unbind, so keys cannot silently change the Agent ID.
            raise _invalid()
        creds = self.sources.credentials_for(row)
        result = await sdk_call(
            {
                "op": "connect",
                "externalAgentId": external_id,
                "privateKey": private_key,
                "keyReference": key_reference,
                "keyScope": row.source_id,
                "apiKey": str(creds.get("api_key") or ""),
                "origin": row.endpoint.rstrip("/"),
                "transport": "mcp" if row.kind == "akb-mcp" else "rest",
                "timeoutMs": row.timeout_ms,
                "allowInsecureLoopback": row.allow_insecure_http,
                "invocation": {
                    "sessionId": f"management:{user_id}",
                    "invocationId": str(uuid.uuid4()),
                },
            },
            max(30, row.timeout_ms / 1000 * 3 + 10),
        )
        binding = result["binding"]
        coordinates = (
            "platformId",
            "installationId",
            "workspaceId",
            "policyId",
            "applicationPrincipalId",
            "issuer",
            "audience",
            "resource",
        )
        if config["agents"] and any(config[k] != binding[k] for k in coordinates):
            raise _invalid()
        config.update({k: binding[k] for k in coordinates})
        config["simple"] = True
        config["agents"][agent_id] = {
            "externalAgentId": external_id,
            "bindingId": binding["bindingId"],
            "runtimeAgentKey": binding["runtimeAgentKey"],
            "lastVerifiedAt": datetime.now(UTC).isoformat(),
            "lastVerificationStatus": "success",
        }
        self.sources.save_identity_config(row, config, agent_id=agent_id)
        return {"status": result["status"], "activeKid": result["activeKid"]}

    def disconnect(self, source_id: str, agent_id: str, user_id: int, is_admin: bool) -> None:
        row, config = self.source(source_id, user_id, is_admin)
        self.agent(agent_id, user_id, is_admin)
        if agent_id not in config.get("agents", {}):
            raise _invalid()
        config["agents"].pop(agent_id, None)
        self.sources.save_identity_config(row, config, agent_id=agent_id, bound=False)

    async def search(
        self, source_id: str, agent_id: str, user_id: int, is_admin: bool, query: str, top_k: int
    ) -> dict[str, Any]:
        row, config = self.source(source_id, user_id, is_admin)
        self.agent(agent_id, user_id, is_admin)
        identity = OctopIdentity(
            str(user_id), agent_id, f"management:{user_id}", workload_only=True
        )
        spec = replace(self.sources.to_spec(row), octop_identity=identity)
        creds = {**self.sources.credentials_for(row), "agent_identity": config}
        try:
            result = await search_as_agent(spec, creds, query, top_k=top_k)
        except (OctopError, ExternalSourceError):
            self._record_verification(source_id, agent_id, config, user_id, is_admin, "failure")
            raise
        self._record_verification(source_id, agent_id, config, user_id, is_admin, "success")
        return {
            "source_id": source_id,
            "chunks": [asdict(chunk) for chunk in result.chunks],
            "warnings": [],
            "trace_ids": result.trace_ids,
        }

    def _record_verification(
        self,
        source_id: str,
        agent_id: str,
        previous: dict[str, Any],
        user_id: int,
        is_admin: bool,
        status: str,
    ) -> None:
        try:
            row, current = self.source(source_id, user_id, is_admin)
            agent = current.get("agents", {}).get(agent_id)
            if not agent or agent != previous.get("agents", {}).get(agent_id):
                return  # An unbind or key change during the request must not be undone.
            agent["lastVerifiedAt"] = datetime.now(UTC).isoformat()
            agent["lastVerificationStatus"] = status
            self.sources.save_identity_config(row, current)
        except (OctopError, ExternalSourceError):
            logging.getLogger(__name__).info(
                "Identity verification state changed concurrently for source=%s", source_id
            )


async def execute_as_agent(
    spec: ExternalSourceSpec, creds: dict[str, Any], query: str, *, top_k: int
) -> dict[str, Any]:
    identity = spec.octop_identity
    if not isinstance(identity, OctopIdentity) or not identity.agent_id:
        raise OctopError.localized(ErrorCode.AGENT_IDENTITY_NOT_READY)
    config = creds["agent_identity"]
    owner, binding = _binding(config, identity.agent_id)
    request: dict[str, Any] = {"operation": "search", "query": query, "topK": top_k}
    if spec.ref:
        request["knowledgeBaseId" if spec.ref.isdecimal() else "knowledgeBaseCode"] = (
            int(spec.ref) if spec.ref.isdecimal() else spec.ref
        )
    return await sdk_call(
        {
            "op": "execute",
            "owner": owner,
            "binding": binding,
            "apiKey": str(creds.get("api_key") or ""),
            "origin": spec.endpoint.rstrip("/"),
            "transport": "mcp" if spec.kind == "akb-mcp" else "rest",
            "request": request,
            "timeoutMs": int(spec.timeout_seconds * 1000),
            "allowInsecureLoopback": spec.allow_insecure_http,
            "invocation": {
                "sessionId": identity.trace_id or f"agent:{identity.agent_id}",
                "invocationId": str(uuid.uuid4()),
            },
        },
        max(30, spec.timeout_seconds * 3 + 10),
    )


async def search_as_agent(
    spec: ExternalSourceSpec, creds: dict[str, Any], query: str, *, top_k: int
) -> ExternalSearchResult:
    from octop.infra.knowledge.external.adapters.akb_mcp import _collect_items
    from octop.infra.knowledge.external.normalize import (
        extract_items,
        filter_chunks,
        normalize_chunks,
    )

    data = await execute_as_agent(spec, creds, query, top_k=top_k)
    items = _collect_items(data) if spec.kind == "akb-mcp" else extract_items(data)
    return ExternalSearchResult(
        chunks=filter_chunks(
            normalize_chunks(items, origin=spec.kind, ref=spec.ref),
            min_score=spec.min_score,
            limit=top_k,
        )
    )
