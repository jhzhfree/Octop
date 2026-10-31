"""Agent identity management and native retrieval through the platform-neutral SDK.

Private keys enter protected storage only through trusted management IPC and
never leave the host. Model tool arguments never choose a signing identity.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import uuid
from dataclasses import asdict, replace
from datetime import UTC, datetime
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


def _sdk_problem() -> str | None:
    return None


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
    from octop.infra.knowledge.external.http import drop_api_prefix

    base = (endpoint or "").strip().rstrip("/")
    for prefix in ("/api/mcp", "/api/v1/external"):
        base = drop_api_prefix(base, prefix)
    return base


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


async def sdk_call(
    payload: dict[str, Any], timeout: float = 30, *, secret_repo: Any = None
) -> dict[str, Any]:
    from octop.infra.knowledge.external.native_identity import call

    root = str(PathLayout.from_env().root.resolve() / "agent-identity-keys")
    try:
        async with asyncio.timeout(timeout):
            return await call({**payload, "root": root}, secret_repo)
    except TimeoutError as exc:
        raise OctopError.localized(
            ErrorCode.AGENT_IDENTITY_UNAVAILABLE, details={"reason": "identity-timeout"}
        ) from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise OctopError.localized(ErrorCode.AGENT_IDENTITY_INVALID) from exc


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
        "keyReference": str(agent.get("keyReference") or ""),
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

    async def _call(self, payload: dict[str, Any], timeout: float = 30) -> dict[str, Any]:
        return await sdk_call(payload, timeout, secret_repo=self.sources._secret_repo)

    def source(
        self, source_id: str, user_id: int, is_admin: bool
    ) -> tuple[ExternalSourceRow, dict[str, Any]]:
        row = self.sources._require_use(source_id, user_id=user_id, is_admin=is_admin)
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
            capabilities = await self._call({"op": "capabilities"})
        except (TimeoutError, OctopError, OSError):
            capabilities = {"supported": False}
            problem = problem or "native-unavailable"
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
                    state = await self._call(
                        {
                            "op": "state",
                            "owner": _binding(config, record.agent_id)[0],
                            "keyReference": current.get("keyReference"),
                        }
                    )
                    active_kid = state.get("activeKid")
                    if state.get("keyReference") and not current.get("keyReference"):
                        self.sources._repo.adopt_legacy_key(
                            row.connection_id,
                            record.agent_id,
                            current["externalAgentId"],
                            state["keyReference"],
                        )
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
        return await self._call(
            {
                "op": "generate",
                "externalAgentId": external_id,
                "keyScope": row.connection_id + ":" + str(user_id),
            }
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
        old_reference = str(current.get("keyReference") or "") if current else ""
        if current is None and not (private_key or key_reference):
            raise OctopError.localized(ErrorCode.AGENT_IDENTITY_NOT_READY)
        if (
            current
            and current.get("externalAgentId") != external_id
            and not (private_key or key_reference)
        ):
            # Moving to another Agent ID requires fresh key material (or an explicit
            # unbind), so a live binding cannot silently point at another identity.
            raise _invalid()
        creds = self.sources.credentials_for(row)
        result = await self._call(
            {
                "op": "connect",
                "externalAgentId": external_id,
                "privateKey": private_key,
                "reuseExisting": current is not None,
                "existingKeyReference": old_reference,
                "keyReference": key_reference,
                "keyScope": row.connection_id + ":" + str(user_id),
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
            "keyReference": result.get("keyReference"),
            "runtimeAgentKey": binding["runtimeAgentKey"],
            "lastVerifiedAt": datetime.now(UTC).isoformat(),
            "lastVerificationStatus": "success",
        }
        self.sources.save_identity_config(row, config, agent_id=agent_id)
        self.sources._repo.record_check(
            row.connection_id, row.source_id, agent_id, "identity", "success"
        )
        if key_reference:
            self.sources._secret_repo.delete(
                "agent_identity:generated:"
                + row.connection_id
                + ":"
                + str(user_id)
                + ":"
                + key_reference
            )
        if old_reference and old_reference != result.get("keyReference"):
            self._retire(old_reference)
        return {"status": result["status"], "activeKid": result["activeKid"]}

    def disconnect(self, source_id: str, agent_id: str, user_id: int, is_admin: bool) -> None:
        row, config = self.source(source_id, user_id, is_admin)
        self.agent(agent_id, user_id, is_admin)
        if agent_id not in config.get("agents", {}):
            raise _invalid()
        removed = config["agents"].pop(agent_id, {})
        self.sources.save_identity_config(row, config, agent_id=agent_id, bound=False)
        self._retire(str(removed.get("keyReference") or ""))

    def _retire(self, reference: str) -> None:
        if reference and not self.sources._repo.key_reference_used(reference):
            from octop.infra.knowledge.external.native_identity import KeyStore, retire_reference

            retire_reference(
                KeyStore(
                    self.sources._secret_repo,
                    str(PathLayout.from_env().root / "agent-identity-keys"),
                ),
                reference,
                self.sources._repo,
            )

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
        except (OctopError, ExternalSourceError) as exc:
            self._record_verification(
                source_id, agent_id, config, user_id, is_admin, "failure", str(exc.code)
            )
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
        code: str = "",
    ) -> None:
        try:
            row, current = self.source(source_id, user_id, is_admin)
            agent = current.get("agents", {}).get(agent_id)
            if not agent or agent != previous.get("agents", {}).get(agent_id):
                return  # An unbind or key change during the request must not be undone.
            self.sources._repo.record_verification(
                row.connection_id,
                agent_id,
                agent["externalAgentId"],
                status,
                datetime.now(UTC).isoformat(),
            )
            self.sources._repo.record_check(
                row.connection_id, row.source_id, agent_id, "retrieval", status, code
            )
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
            "spec": {k: v for k, v in asdict(spec).items() if k != "octop_identity"},
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
        max(1, spec.timeout_seconds),
        secret_repo=creds.get("_secret_repo"),
    )


def _items(data: Any, kind: str) -> list[Any]:
    """Unwrap whatever the SDK returned, whichever channel produced it."""
    if isinstance(data, dict) and isinstance(data.get("chunks"), list):
        return list(data["chunks"])
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
    if data.get("keyReference") and isinstance(spec.octop_identity, OctopIdentity):
        creds["agent_identity"]["agents"][spec.octop_identity.agent_id]["keyReference"] = data[
            "keyReference"
        ]
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
            "spec": {k: v for k, v in asdict(spec).items() if k != "octop_identity"},
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
        max(1, spec.timeout_seconds),
        secret_repo=creds.get("_secret_repo"),
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
