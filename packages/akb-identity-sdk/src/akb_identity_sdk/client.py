"""Small synchronous MCP client using Python's standard HTTP transport."""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Callable, Mapping
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .crypto import generate_uuid, public_jwk, validate_uuid
from .proof import HEADER, PROFILE, create_request_proof, origin, validate_binding


class AkbError(RuntimeError):
    def __init__(self, code: str, *, status: int | None = None, trace_id: str = ""):
        self.code = code
        self.status = status
        self.trace_id = trace_id
        super().__init__(code + (f" (HTTP {status})" if status else ""))


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def http_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
) -> Any:
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise AkbError("RESPONSE_TOO_LARGE")
            if response.status in (202, 204) and not raw:
                return None
            try:
                return json.loads(raw)
            except (ValueError, UnicodeDecodeError) as exc:
                raise AkbError("INVALID_JSON") from exc
    except HTTPError as exc:
        trace = exc.headers.get("x-trace-id", "")
        trace = trace if len(trace) <= 128 and all(c.isalnum() or c in "_-" for c in trace) else ""
        status = exc.code
        exc.close()
        raise AkbError("HTTP_REJECTED", status=status, trace_id=trace) from None


Transport = Callable[[str, str, dict[str, str], bytes | None, float], Any]


class AkbMcpClient:
    """One trusted Agent/service connection. Construct one client per bound Agent.

    Agent credentials may discover tools without proof but execution requires
    authenticate_agent(). Ordinary service credentials use auth_type='service'.
    Keys stay in the calling platform; UUID/name must come from its host context.
    """

    def __init__(
        self,
        endpoint: str,
        service_key: str,
        *,
        auth_type: str = "agent",
        allow_private_http: bool = False,
        timeout: float = 10,
        transport: Transport = http_transport,
    ):
        parsed = urlsplit(endpoint)
        origin(endpoint)
        if (
            parsed.query
            or parsed.fragment
            or auth_type not in ("agent", "service")
            or not 0 < timeout <= 120
        ):
            raise ValueError("Invalid AKB client configuration")
        if parsed.scheme == "http":
            try:
                address = ipaddress.ip_address(parsed.hostname or "")
                allowed = address.is_loopback or (allow_private_http and address.is_private)
            except ValueError:
                allowed = parsed.hostname == "localhost"
            if not allowed:
                raise ValueError("HTTPS required; opt in explicitly for private IP HTTP")
        if (
            not service_key
            or len(service_key) > 512
            or any(ord(c) < 33 or ord(c) > 126 for c in service_key)
        ):
            raise ValueError("Invalid service credential")
        self._base = endpoint.rstrip("/")
        for suffix in ("/api/mcp", "/api/v1/external"):
            if self._base.endswith(suffix):
                self._base = self._base[: -len(suffix)]
                break
        self._credential = service_key
        self._auth_type = auth_type
        self._timeout = timeout
        self._transport = transport
        self._binding: dict[str, str] | None = None
        self._private = ""
        self._session = generate_uuid()
        self._agent_name = ""
        self._protocol = "2025-06-18"

    def _request(
        self, path: str, body: Mapping[str, Any] | None = None, *, signed: bool = False
    ) -> Any:
        url = self._base + path
        raw = (
            json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
            if body is not None
            else None
        )
        method = "GET" if body is None else "POST"
        headers = {"X-API-Key": self._credential, "Accept": "application/json"}
        if raw is not None:
            headers["Content-Type"] = "application/json"
        if path == "/api/mcp":
            headers["MCP-Protocol-Version"] = self._protocol
        if signed:
            if not self._binding or not self._private:
                raise AkbError("AGENT_NOT_AUTHENTICATED")
            headers[HEADER] = create_request_proof(
                binding=self._binding,
                private_pem=self._private,
                service_key=self._credential,
                method=method,
                url=url,
                body=raw or b"",
                session_id=self._session,
                invocation_id=generate_uuid(),
                terminal_agent=self._agent_name or None,
            )
        return self._transport(method, url, headers, raw, self._timeout)

    def _rpc(self, method: str, params: Mapping[str, Any], *, signed: bool = False) -> Any:
        request_id = generate_uuid()
        response = self._request(
            "/api/mcp",
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params)},
            signed=signed,
        )
        if (
            not isinstance(response, dict)
            or response.get("jsonrpc") != "2.0"
            or response.get("id") != request_id
        ):
            raise AkbError("INVALID_RPC_RESPONSE")
        if response.get("error"):
            # Never echo upstream raw error messages containing request/credential data.
            raise AkbError("MCP_RPC_REJECTED")
        result = response.get("result")
        if isinstance(result, dict) and result.get("isError"):
            raise AkbError("MCP_TOOL_REJECTED")
        return result

    def connect(self) -> Any:
        connection = self._request("/api/v1/external/connection")
        if (
            not isinstance(connection, dict)
            or connection.get("success") is not True
            or connection.get("data", {}).get("connected") is not True
        ):
            raise AkbError("CONNECTION_REJECTED")
        required = connection["data"].get("agentIdentityRequired")
        if required is not None and required != (self._auth_type == "agent"):
            raise AkbError("CREDENTIAL_TYPE_MISMATCH")
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": self._protocol,
                "capabilities": {},
                "clientInfo": {"name": "akb-identity-sdk", "version": "0.1.0"},
            },
        )
        if (
            not isinstance(result, dict)
            or result.get("protocolVersion") not in ("2025-03-26", "2025-06-18", "2025-11-25")
            or not isinstance(result.get("capabilities", {}).get("tools"), dict)
        ):
            raise AkbError("UNSUPPORTED_MCP_PROTOCOL")
        self._protocol = result["protocolVersion"]
        self._request(
            "/api/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        )
        return connection["data"]

    def list_tools(self) -> Any:
        return self._rpc("tools/list", {})

    def authenticate_agent(
        self,
        agent_id: str,
        private_pem: str,
        *,
        session_id: str | None = None,
        agent_name: str = "",
    ) -> Any:
        if self._auth_type != "agent":
            raise AkbError("AGENT_CREDENTIAL_REQUIRED")
        # Discard previous identity before attempting another binding; failure never reuses it.
        self._binding = None
        self._private = ""
        validate_uuid(agent_id)
        response = self._request(
            "/api/v1/external/identity/configuration?" + urlencode({"agentId": agent_id})
        )
        if not isinstance(response, dict) or response.get("success") is not True:
            raise AkbError("BINDING_REJECTED")
        binding = validate_binding(response["data"], self._base, agent_id)
        kid = public_jwk(private_pem)["kid"]
        self._binding, self._private = binding, private_pem
        self._session, self._agent_name = session_id or generate_uuid(), agent_name
        try:
            status = self._request("/api/v1/external/identity/status", signed=True)
            data = (
                status.get("data", {})
                if isinstance(status, dict) and status.get("success") is True
                else {}
            )
            if (
                data.get("agentId") != agent_id
                or data.get("kid") != kid
                or data.get("keyStatus") not in ("ACTIVE", "RETIRING")
                or data.get("proofProfile") != PROFILE
                or str(data.get("policyId")) != binding["policyId"]
            ):
                raise AkbError("VERIFIED_IDENTITY_MISMATCH")
            identity = self.call_tool("get_agent_identity", {})
            described = tool_payload(identity)
            if (
                not isinstance(described, dict)
                or described.get("agentId") != agent_id
                or described.get("kid") != kid
            ):
                raise AkbError("MCP_IDENTITY_MISMATCH")
            return data
        except Exception:
            self._binding, self._private = None, ""
            raise

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Any:
        if any(
            field in arguments
            for field in ("knowledgeBaseCode", "identity", "privateKey", "serviceKey")
        ):
            raise ValueError("Identity comes from trusted client state; use only knowledgeBaseId")
        return self._rpc(
            "tools/call",
            {"name": name, "arguments": dict(arguments)},
            signed=self._auth_type == "agent",
        )

    def list_knowledge_bases(self) -> Any:
        return self.call_tool("list_knowledge_bases", {})

    def search(self, knowledge_base_id: str, query: str, *, top_k: int = 5) -> Any:
        import re

        if (
            not re.fullmatch(r"KB_[A-Za-z0-9]{1,125}", knowledge_base_id)
            or not query.strip()
            or len(query) > 4000
            or type(top_k) is not int
            or not 1 <= top_k <= 50
        ):
            raise ValueError("KB_ internal ID, nonempty query and top_k 1..50 required")
        return self.call_tool(
            "knowledge_search",
            {"knowledgeBaseId": knowledge_base_id, "query": query, "topK": top_k},
        )


def tool_payload(result: Any) -> Any:
    if not isinstance(result, dict):
        raise AkbError("INVALID_TOOL_RESULT")
    if "structuredContent" in result:
        return result["structuredContent"]
    for item in result.get("content", []):
        if isinstance(item, dict) and item.get("type") == "text":
            try:
                return json.loads(item["text"])
            except (ValueError, KeyError):
                continue
    raise AkbError("INVALID_TOOL_RESULT")
