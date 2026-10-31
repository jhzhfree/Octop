"""Agent identity management and native retrieval through the platform-neutral SDK.

Private keys enter protected storage only through trusted management IPC and
never leave the host. Model tool arguments never choose a signing identity.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import shutil
import uuid
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.external_knowledge import ExternalSourceRow
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.knowledge.akb_identity import OctopIdentity
from octop.infra.knowledge.external.service import ExternalKnowledgeService
from octop.infra.knowledge.external.types import (
    ExternalSearchResult,
    ExternalSourceError,
    ExternalSourceSpec,
    base_argument,
    selector_argument,
)
from octop.infra.utils.paths import PathLayout


def _invalid() -> OctopError:
    return OctopError.localized(ErrorCode.AGENT_IDENTITY_INVALID)


def _bridge_script() -> str | None:
    """Locates the bundled host bridge, falling back to a global install.

    The checkout depth differs between a source run and an installed layout, so
    every plausible ancestor is searched instead of one fixed level.
    """
    configured = os.environ.get("OCTOP_AGENT_IDENTITY_SDK_BRIDGE", "").strip()
    if configured:
        return configured if Path(configured).is_file() else None
    for parent in list(Path(__file__).resolve().parents)[:8]:
        candidate = parent / "integrations" / "agent-identity-sdk" / "dist" / "bridge.js"
        if candidate.is_file():
            return str(candidate)
    return shutil.which("akb-agent-identity-bridge")


def _sdk_path() -> tuple[str, str] | None:
    node = shutil.which("node")
    script = _bridge_script()
    return (node, script) if node and script else None


def _sdk_problem() -> str | None:
    """Why the host bridge cannot run, for the UI to explain to the operator."""
    if shutil.which("node") is None:
        return "node-missing"
    if _bridge_script() is None:
        return "bridge-missing"
    return None


def _stderr_hint(stderr: bytes, limit: int = 200) -> str:
    return stderr.decode("utf-8", "replace").strip().replace("\n", " ")[:limit]


_PRIVATE_IPV4 = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
)


def _is_private_host(host: str) -> bool:
    """Private address space, mirroring the SDK's opt-in plain-HTTP range."""
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        return bool(address.is_private or address.is_link_local)
    return any(address in network for network in _PRIVATE_IPV4)


def _sdk_origin(endpoint: str) -> str:
    """Reduce the stored endpoint to the bare origin the SDK needs.

    The SDK appends its own paths (``/api/mcp``, ``/api/v1/external/...``), so a
    pasted MCP endpoint has to be stripped. Plain retrieval already tolerates
    that form, and a source should not bind one way and fail the other.
    """
    parts = urlsplit((endpoint or "").strip())
    host = parts.hostname or ""
    if not parts.scheme or not host:
        return (endpoint or "").strip().rstrip("/")
    authority = f"[{host}]" if ":" in host else host
    if parts.port:
        authority = f"{authority}:{parts.port}"
    return f"{parts.scheme}://{authority}"


def _require_origin(row: ExternalSourceRow) -> None:
    """Reject endpoints the SDK's origin check would refuse anyway.

    The SDK answers those with a bare ``PROTOCOL_UNSUPPORTED``, which tells the
    operator nothing; naming the scheme and host lets them fix the endpoint.
    """
    raw = urlsplit((row.endpoint or "").strip())
    parts = urlsplit(_sdk_origin(row.endpoint))
    host = (parts.hostname or "").lower()
    # Credentials in the URL are a leak by themselves, never silently dropped.
    has_userinfo = bool(raw.username or raw.password)
    if parts.scheme == "https" and not has_userinfo:
        return
    # Trusted LANs often cannot terminate TLS at the AKB host, so plain HTTP is
    # allowed there too — but only when the source explicitly opts in.
    allowed_host = host in {"127.0.0.1", "localhost", "::1"} or _is_private_host(host)
    if parts.scheme == "http" and row.allow_insecure_http and allowed_host and not has_userinfo:
        return
    raise OctopError.localized(
        ErrorCode.AGENT_IDENTITY_UNAVAILABLE,
        details={
            "reason": "origin-unsupported",
            "scheme": parts.scheme or "",
            "host": host,
            "path": raw.path,
        },
    )


def _log_bridge_failure(stderr: bytes, reason: str) -> None:
    """Surface why the bridge failed; the UI only reports it as unavailable.

    A bridge that cannot even start (a missing dependency, a wrong Node
    version) writes the real cause to stderr, which the caller never sees.
    """
    detail = _stderr_hint(stderr, 500)
    logging.getLogger(__name__).warning(
        "agent identity bridge failed (%s)%s",
        reason,
        f": {detail}" if detail else "",
    )


async def sdk_call(payload: dict[str, Any], timeout: float = 30) -> dict[str, Any]:
    command = _sdk_path()
    if command is None:
        raise OctopError.localized(
            ErrorCode.AGENT_IDENTITY_UNAVAILABLE,
            details={"reason": _sdk_problem() or "sdk-missing"},
        )
    root = str(PathLayout.from_env().root.resolve() / "agent-identity-keys")
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        _log_bridge_failure(b"", f"cannot execute bridge: {exc}")
        raise OctopError.localized(
            ErrorCode.AGENT_IDENTITY_UNAVAILABLE,
            details={"reason": "bridge-launch-failed", "detail": str(exc)},
        ) from exc
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(json.dumps({**payload, "root": root}, ensure_ascii=False).encode()),
            timeout=timeout,
        )
    except (TimeoutError, asyncio.CancelledError) as exc:
        if process.returncode is None:
            process.kill()
        await process.wait()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise OctopError.localized(
            ErrorCode.AGENT_IDENTITY_UNAVAILABLE,
            details={"reason": "bridge-timeout", "timeoutSeconds": timeout},
        ) from exc
    try:
        if len(stdout) > 3_000_000:
            raise ValueError()
        result = json.loads(stdout)
        if not isinstance(result, dict):
            raise ValueError()
        if process.returncode != 0 or result.get("ok") is not True:
            _log_bridge_failure(stderr, f"exit={process.returncode}")
            code = result.get("code")
            message = result.get("message")
            failure = {
                "reason": "bridge-error",
                "bridgeCode": str(code or ""),
                "bridgeMessage": str(message or "")[:400],
                "detail": _stderr_hint(stderr),
            }
            if code in {
                "AUTHENTICATION_FAILED",
                "AUTHORIZATION_DENIED",
                "KEY_NOT_REGISTERED",
                "KEY_UNAVAILABLE",
            }:
                raise OctopError.localized(ErrorCode.AGENT_IDENTITY_NOT_READY, details=failure)
            raise OctopError.localized(ErrorCode.AGENT_IDENTITY_UNAVAILABLE, details=failure)
        data = result["data"]
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (ValueError, KeyError, TypeError) as exc:
        _log_bridge_failure(stderr, f"unreadable bridge response (exit={process.returncode})")
        raise OctopError.localized(
            ErrorCode.AGENT_IDENTITY_UNAVAILABLE,
            details={
                "reason": "bridge-unreadable",
                "exit": process.returncode,
                "detail": _stderr_hint(stderr),
            },
        ) from exc


def _default(row: ExternalSourceRow, config: dict[str, Any]) -> dict[str, Any]:
    return {
        "platformId": "octop",
        "installationId": "octop-" + row.source_id,
        "workspaceId": row.source_id,
        "issuer": "https://octop.local/agents",
        "audience": "akb-external-api",
        "policyId": "",
        "applicationPrincipalId": "",
        "resource": _sdk_origin(row.endpoint),
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
    _require_origin(row)
    owner, binding = _binding(config, native_id)
    return {
        "owner": owner,
        "binding": binding,
        "apiKey": api_key,
        "origin": _sdk_origin(row.endpoint),
        "transport": "mcp" if row.kind == "akb-mcp" else "rest",
        "timeoutMs": row.timeout_ms,
        "allowInsecureLoopback": row.allow_insecure_http,
        "allowPrivateHttp": row.allow_insecure_http,
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
        if row.kind not in {"akb-mcp", "akb-rest"} or self.sources.auth_type(row) != "agent":
            raise _invalid()
        return row, _default(row, self.sources.identity_config(row))

    def agent(self, agent_id: str, user_id: int, is_admin: bool) -> Any:
        row = self.agents.get(agent_id)
        if row is None or (not is_admin and row.user_id != user_id):
            raise OctopError.localized(ErrorCode.KNOWLEDGE_FORBIDDEN)
        return row

    def _service_key_saved(self, row: ExternalSourceRow) -> bool:
        try:
            return bool(self.sources.credentials_for(row).get("api_key"))
        except ExternalSourceError:
            return False

    async def describe(self, source_id: str, user_id: int, is_admin: bool) -> dict[str, Any]:
        row, config = self.source(source_id, user_id, is_admin)
        problem = _sdk_problem()
        try:
            capabilities = await sdk_call({"op": "capabilities"})
        except (TimeoutError, OctopError, OSError):
            capabilities = {"supported": False}
            problem = problem or "bridge-failed"
        ready = bool(capabilities.get("supported"))
        available = self.agents.list_all() if is_admin else self.agents.list_by_user(user_id)
        bound = config["agents"] if config.get("simple") else {}
        result: dict[str, Any] = {
            "runtimeReady": ready,
            "runtimeReason": None if ready else (problem or "unsupported"),
            "serviceKeySaved": self._service_key_saved(row),
            "agents": [],
        }
        for record in available:
            current = bound.get(record.agent_id)
            active_kid = None
            public_jwk = None
            if current and ready:
                try:
                    state = await sdk_call(
                        {"op": "state", "owner": _binding(config, record.agent_id)[0]}
                    )
                    active_kid = state.get("activeKid")
                    # Public keys are not secret, so a saved one is returned for display.
                    public_jwk = next(
                        (
                            key.get("publicJwk")
                            for key in state.get("keys") or []
                            if isinstance(key, dict) and key.get("kid") == active_kid
                        ),
                        None,
                    )
                except (TimeoutError, OctopError):
                    active_kid = None
            result["agents"].append(
                {
                    "agentId": record.agent_id,
                    "name": record.name,
                    "externalAgentId": current.get("externalAgentId", "") if current else "",
                    "activeKid": active_kid,
                    "publicJwk": public_jwk,
                    "keySaved": bool(public_jwk),
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
        _require_origin(row)
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
        if (
            current
            and current.get("externalAgentId") != external_id
            and not (private_key or key_reference)
        ):
            # Moving to another Agent ID requires fresh key material (or an explicit
            # unbind), so a live binding cannot silently point at another identity.
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
                "origin": _sdk_origin(row.endpoint),
                "transport": "mcp" if row.kind == "akb-mcp" else "rest",
                "timeoutMs": row.timeout_ms,
                "allowInsecureLoopback": row.allow_insecure_http,
                "allowPrivateHttp": row.allow_insecure_http,
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

    async def list_bases(
        self, source_id: str, agent_id: str, user_id: int, is_admin: bool
    ) -> list[dict[str, str]]:
        """Knowledge bases this binding may read, for the ref picklist."""
        row, config = self.source(source_id, user_id, is_admin)
        self.agent(agent_id, user_id, is_admin)
        identity = OctopIdentity(
            str(user_id), agent_id, f"management:{user_id}", workload_only=True
        )
        spec = replace(self.sources.to_spec(row), octop_identity=identity)
        creds = {**self.sources.credentials_for(row), "agent_identity": config}
        return await list_bases_as_agent(spec, creds)

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
    # A base is named here the same way plain retrieval names it, and travels in
    # the shape AKB's tools accept rather than being relabelled into a code.
    request: dict[str, Any] = {
        "operation": "search",
        "query": query,
        "topK": top_k,
        **base_argument(spec),
    }
    return await sdk_call(
        {
            "op": "execute",
            "owner": owner,
            "binding": binding,
            "apiKey": str(creds.get("api_key") or ""),
            "origin": _sdk_origin(spec.endpoint),
            "transport": "mcp" if spec.kind == "akb-mcp" else "rest",
            "request": request,
            "timeoutMs": int(spec.timeout_seconds * 1000),
            "allowInsecureLoopback": spec.allow_insecure_http,
            "allowPrivateHttp": spec.allow_insecure_http,
            "invocation": {
                "sessionId": identity.trace_id or f"agent:{identity.agent_id}",
                "invocationId": str(uuid.uuid4()),
            },
        },
        max(30, spec.timeout_seconds * 3 + 10),
    )


def _items(data: Any, kind: str) -> list[Any]:
    """Unwrap whatever the SDK returned, whichever channel produced it."""
    if kind == "akb-mcp":
        from octop.infra.knowledge.external.adapters.akb_mcp import _collect_items

        return _collect_items(data)
    from octop.infra.knowledge.external.normalize import extract_items

    return extract_items(data)


async def search_as_agent(
    spec: ExternalSourceSpec, creds: dict[str, Any], query: str, *, top_k: int
) -> ExternalSearchResult:
    from octop.infra.knowledge.external.normalize import (
        filter_chunks,
        normalize_chunks,
    )

    data = await execute_as_agent(spec, creds, query, top_k=top_k)
    items = _items(data, spec.kind)
    return ExternalSearchResult(
        chunks=filter_chunks(
            normalize_chunks(items, origin=spec.kind, ref=spec.ref),
            min_score=spec.min_score,
            limit=top_k,
        )
    )


async def list_bases_as_agent(
    spec: ExternalSourceSpec, creds: dict[str, Any]
) -> list[dict[str, str]]:
    """Knowledge bases the bound Agent may read, listed over its own channel.

    A source managed by an Agent binding refuses the legacy credential calls,
    so a picklist for it has to travel through the binding itself.
    """
    identity = spec.octop_identity
    if not isinstance(identity, OctopIdentity) or not identity.agent_id:
        raise OctopError.localized(ErrorCode.AGENT_IDENTITY_NOT_READY)
    config = creds["agent_identity"]
    owner, binding = _binding(config, identity.agent_id)
    data = await sdk_call(
        {
            "op": "execute",
            "owner": owner,
            "binding": binding,
            "apiKey": str(creds.get("api_key") or ""),
            "origin": _sdk_origin(spec.endpoint),
            "transport": "mcp" if spec.kind == "akb-mcp" else "rest",
            "request": {"operation": "bases"},
            "timeoutMs": int(spec.timeout_seconds * 1000),
            "allowInsecureLoopback": spec.allow_insecure_http,
            "allowPrivateHttp": spec.allow_insecure_http,
            "invocation": {
                "sessionId": identity.trace_id or f"agent:{identity.agent_id}",
                "invocationId": str(uuid.uuid4()),
            },
        },
        max(30, spec.timeout_seconds * 3 + 10),
    )
    refs: list[dict[str, str]] = []
    for item in _items(data, spec.kind):
        if not isinstance(item, dict):
            continue
        code = (
            item.get(selector_argument(spec))
            or item.get("baseCode")
            or item.get("knowledgeBaseCode")
            or item.get("id")
        )
        name = item.get("name") or item.get("displayName") or ""
        if isinstance(code, (str, int)) and str(code).strip():
            refs.append({"ref": str(code).strip(), "name": str(name)})
    return refs
