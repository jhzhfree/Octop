"""Agent identity management and native retrieval through the platform-neutral SDK.

No private key crosses the SDK process boundary. The backend supplies trusted
runtime context; model tool arguments never choose a signing identity.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import uuid
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.external_knowledge import ExternalSourceRow
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.knowledge.akb_identity import OctopIdentity
from octop.infra.knowledge.external.service import ExternalKnowledgeService
from octop.infra.knowledge.external.types import ExternalSearchResult, ExternalSourceSpec
from octop.infra.utils.paths import PathLayout

V2 = "AGENT_KEY_RS256_V2"


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
        "mode": "LEGACY",
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
        "runtimeAgentKey": native_id,
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
        available = self.agents.list_all() if is_admin else self.agents.list_by_user(user_id)
        result = {key: value for key, value in config.items() if key != "agents"}
        result.update(
            {
                "runtimeReady": bool(capabilities.get("supported")),
                "sdkVersion": capabilities.get("version", ""),
                "agents": [],
            }
        )
        for record in available:
            current = config["agents"].get(record.agent_id)
            state: dict[str, Any] = {"activeKid": None, "keys": []}
            if current and capabilities.get("supported"):
                try:
                    state = await sdk_call(
                        {"op": "state", "owner": _binding(config, record.agent_id)[0]}
                    )
                except (TimeoutError, OctopError):
                    state["unavailable"] = True
            result["agents"].append(
                {
                    "agentId": record.agent_id,
                    "name": record.name,
                    "externalAgentId": current.get("externalAgentId", "") if current else "",
                    "bindingId": current.get("bindingId", "") if current else "",
                    **state,
                }
            )
        return result

    def configure(
        self, source_id: str, user_id: int, is_admin: bool, values: dict[str, str]
    ) -> None:
        row, config = self.source(source_id, user_id, is_admin)
        for name in ("policyId", "applicationPrincipalId"):
            value = values.get(name, "").strip()
            if value and not re.fullmatch(r"[1-9][0-9]{0,18}", value):
                raise _invalid()
        parsed = urlsplit(values.get("issuer", ""))
        endpoint = urlsplit(row.endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise _invalid()
        if (
            not endpoint.hostname
            or endpoint.path not in {"", "/"}
            or endpoint.query
            or endpoint.fragment
            or endpoint.username
            or endpoint.password
        ):
            raise _invalid()
        if endpoint.scheme != "https" and not (
            row.allow_insecure_http
            and endpoint.scheme == "http"
            and endpoint.hostname in {"127.0.0.1", "localhost", "::1"}
        ):
            raise _invalid()
        if values.get("mode") not in {V2, "LEGACY"} or not values.get("audience", "").strip():
            raise _invalid()
        if values["mode"] == V2 and not all(
            values.get(key) for key in ("policyId", "applicationPrincipalId")
        ):
            raise _invalid()
        config.update(
            {
                key: value.strip()
                for key, value in values.items()
                if key in {"mode", "policyId", "applicationPrincipalId", "issuer", "audience"}
            }
        )
        config["resource"] = row.endpoint.rstrip("/")
        try:
            if endpoint.port == (443 if endpoint.scheme == "https" else 80):
                raise _invalid()
        except ValueError as exc:
            raise _invalid() from exc
        self.sources.save_identity_config(row, config)

    async def prepare(
        self, source_id: str, agent_id: str, user_id: int, is_admin: bool, rotate: bool
    ) -> dict[str, Any]:
        row, config = self.source(source_id, user_id, is_admin)
        self.agent(agent_id, user_id, is_admin)
        if agent_id not in config["agents"]:
            config["agents"][agent_id] = {"externalAgentId": str(uuid.uuid4()), "bindingId": ""}
            self.sources.save_identity_config(row, config)
        owner = {
            **{key: str(config[key]) for key in ("platformId", "installationId", "workspaceId")},
            "externalAgentId": config["agents"][agent_id]["externalAgentId"],
        }
        return await sdk_call({"op": "rotate" if rotate else "initialize", "owner": owner})

    async def prove(
        self,
        source_id: str,
        agent_id: str,
        user_id: int,
        is_admin: bool,
        kid: str,
        challenge: dict[str, Any],
    ) -> dict[str, Any]:
        row, config = self.source(source_id, user_id, is_admin)
        self.agent(agent_id, user_id, is_admin)
        owner, binding = _binding(config, agent_id)
        binding_id = challenge.get("bindingId")
        if not isinstance(binding_id, str) or not re.fullmatch(r"[1-9][0-9]{0,18}", binding_id):
            raise _invalid()
        binding["bindingId"] = binding_id
        result = await sdk_call(
            {
                "op": "registration",
                "owner": owner,
                "binding": binding,
                "kid": kid,
                "challenge": challenge,
            }
        )
        config["agents"][agent_id]["bindingId"] = binding_id
        self.sources.save_identity_config(row, config)
        return result

    async def verify(
        self, source_id: str, agent_id: str, user_id: int, is_admin: bool, kid: str | None
    ) -> dict[str, Any]:
        row, config = self.source(source_id, user_id, is_admin)
        self.agent(agent_id, user_id, is_admin)
        creds = self.sources.credentials_for(row)
        payload = {
            **_request(row, config, agent_id, str(creds.get("api_key") or "")),
            "op": "activate" if kid else "probe",
            "kid": kid,
        }
        payload["invocation"]["sessionId"] = f"management:{user_id}"
        return await sdk_call(payload, max(30, row.timeout_ms / 1000 * 3 + 10))

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
        result = await search_as_agent(spec, creds, query, top_k=top_k)
        return {
            "source_id": source_id,
            "chunks": [asdict(chunk) for chunk in result.chunks],
            "warnings": [],
            "trace_ids": result.trace_ids,
        }


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
