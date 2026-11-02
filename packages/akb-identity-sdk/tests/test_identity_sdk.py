from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import jwt
import pytest
from akb_identity_sdk import (
    HEADER,
    AkbError,
    AkbMcpClient,
    MemoryReplayStore,
    create_request_proof,
    generate_key_pair,
    generate_uuid,
    public_jwk,
    sign_data,
    tool_payload,
    validate_uuid,
    verify_request_proof,
    verify_signature,
)


@pytest.fixture(scope="module")
def pair():
    return generate_key_pair()


@pytest.fixture
def binding():
    return {
        "issuer": "https://akb.example/agent-identity",
        "audience": "akb-external-api",
        "resource": "https://akb.example",
        "policyId": "17",
        "applicationPrincipalId": "123",
        "externalAgentId": "e21cce9c-1b16-4ab8-a76a-bb761df919b8",
        "runtimeAgentKey": "build",
        "platformId": "akb-service",
        "installationId": "installation",
        "workspaceId": "workspace",
        "bindingId": "21",
    }


def test_uuid_keys_signatures_and_public_exports(pair):
    assert validate_uuid(generate_uuid())
    with pytest.raises(ValueError):
        validate_uuid("not-a-uuid")
    assert pair.kid == public_jwk(pair.public_pem)["kid"] == public_jwk(pair.private_pem)["kid"]
    assert "PRIVATE KEY" not in repr(pair)
    body = "Agent build + UUID 中文🙂".encode()
    signature = sign_data(body, pair.private_pem)
    assert verify_signature(body, signature, pair.public_jwk)
    assert verify_signature(body, signature, pair.public_pem)
    assert not verify_signature(body + b"tamper", signature, pair.public_jwk)
    assert not verify_signature(body, "invalid!", pair.public_jwk)
    with pytest.raises(ValueError):
        generate_key_pair(2048)


def options(pair, binding):
    return {
        "binding": binding,
        "private_pem": pair.private_pem,
        "service_key": "synthetic-key",
        "method": "POST",
        "url": "https://akb.example/gateway/api/mcp",
        "body": "中文🙂".encode(),
        "session_id": "session",
        "invocation_id": generate_uuid(),
        "terminal_agent": "build",
    }


def verifier(pair, binding, **changes):
    return dict(
        public_material=pair.public_jwk,
        binding=binding,
        service_key="synthetic-key",
        method="POST",
        url="https://akb.example/gateway/api/mcp",
        body="中文🙂".encode(),
        replay_store=MemoryReplayStore(),
        **changes,
    )


def test_proof_roundtrip_exact_bytes_ttl_binding_and_replay(pair, binding):
    token = create_request_proof(**options(pair, binding))
    verify = verifier(pair, binding)
    claims = verify_request_proof(token, **verify)
    assert claims["ver"] == 2
    assert claims["terminal_agent"] == "build"
    assert claims["exp"] - claims["iat"] == 60
    with pytest.raises(ValueError, match="replay"):
        verify_request_proof(token, **verify)
    for field, value in [
        ("body", b"changed"),
        ("service_key", "different"),
        ("method", "GET"),
        ("url", "https://akb.example/api/mcp"),
    ]:
        with pytest.raises(ValueError):
            verify_request_proof(token, **{**verifier(pair, binding), field: value})
    for ttl in (0, 61, True):
        with pytest.raises(ValueError):
            create_request_proof(**options(pair, binding), ttl=ttl)
    with pytest.raises(jwt.ExpiredSignatureError):
        verify_request_proof(
            create_request_proof(**options(pair, binding), now=int(time.time()) - 120),
            **verifier(pair, binding),
        )


def test_profile_binding_and_algorithm_cannot_be_downgraded(pair, binding):
    token = create_request_proof(**options(pair, binding))
    claims = jwt.decode(token, options={"verify_signature": False})
    for field, value in [
        ("ver", 1),
        ("azp", "other"),
        ("runtime_agent", "other"),
        ("sub", "agent:" + generate_uuid()),
    ]:
        modified = jwt.encode(
            {**claims, field: value},
            pair.private_pem,
            algorithm="RS256",
            headers={"typ": "akb-agent-proof+jwt", "kid": pair.kid},
        )
        with pytest.raises(ValueError):
            verify_request_proof(modified, **verifier(pair, binding))
    wrong_type = jwt.encode(
        claims,
        pair.private_pem,
        algorithm="RS256",
        headers={"typ": "agent-proof+jwt", "kid": pair.kid},
    )
    with pytest.raises(ValueError):
        verify_request_proof(wrong_type, **verifier(pair, binding))


def test_replay_store_is_atomic_and_fails_closed_at_capacity(pair, binding):
    token = create_request_proof(**options(pair, binding))
    verify = verifier(pair, binding)

    def run(_):
        try:
            verify_request_proof(token, **verify)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(run, range(8))) == 1
    store = MemoryReplayStore(capacity=1)
    assert store.claim("a", time.time() + 60)
    assert not store.claim("b", time.time() + 60)


def test_shared_node_java_vector_is_rejected_as_expired():
    vector = json.loads((Path(__file__).parent / "fixtures/akb-v2-vector.json").read_text())
    with pytest.raises(jwt.ExpiredSignatureError):
        verify_request_proof(
            vector["proof"],
            public_material=vector["publicJwk"],
            binding=vector["binding"],
            service_key=vector["serviceKey"],
            method="POST",
            url=vector["binding"]["resource"] + vector["target"],
            body=vector["body"].encode(),
            replay_store=MemoryReplayStore(),
        )


class FakeAKB:
    def __init__(self, pair, binding, agent=True):
        self.pair, self.binding, self.agent = pair, binding, agent
        self.requests = []
        self.nonces = set()
        self.bad_identity = False

    def __call__(self, method, url, headers, body, timeout):
        assert headers["X-API-Key"] == "synthetic-key"
        self.requests.append((url, headers, body))
        if url.endswith("/connection"):
            return {
                "success": True,
                "data": {"connected": True, "agentIdentityRequired": self.agent},
            }
        if "/configuration?" in url:
            return {"success": True, "data": self.binding}
        if HEADER in headers:
            key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(self.pair.public_jwk))
            claims = jwt.decode(
                headers[HEADER],
                key,
                algorithms=["RS256"],
                audience=self.binding["audience"],
                issuer=self.binding["issuer"],
            )
            assert (
                claims["request_binding"]["body_sha256"] == hashlib.sha256(body or b"").hexdigest()
            )
            assert claims["request_binding"]["target"] == "/gateway" + url.split("/gateway", 1)[1]
            assert claims["jti"] not in self.nonces
            self.nonces.add(claims["jti"])
        identity = {
            "agentId": self.binding["externalAgentId"],
            "kid": "wrong" if self.bad_identity else self.pair.kid,
            "keyStatus": "ACTIVE",
            "proofProfile": "AGENT_KEY_RS256_V2",
            "policyId": 17,
        }
        if url.endswith("/status"):
            return {"success": True, "data": identity}
        rpc = json.loads(body)
        if rpc["method"] == "notifications/initialized":
            return None
        if rpc["method"] == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}}
        elif rpc["method"] == "tools/list":
            result = {"tools": [{"name": "knowledge_search"}]}
        else:
            assert (HEADER in headers) == self.agent
            result = {
                "structuredContent": identity
                if rpc["params"]["name"] == "get_agent_identity"
                else rpc["params"]["arguments"]
            }
        return {"jsonrpc": "2.0", "id": rpc["id"], "result": result}


def test_mcp_client_discovers_without_agent_but_executes_with_bound_identity(pair, binding):
    upstream = FakeAKB(pair, binding)
    client = AkbMcpClient(
        "https://akb.example/gateway/api/mcp", "synthetic-key", transport=upstream
    )
    client.connect()
    assert client.list_tools()["tools"]
    with pytest.raises(AkbError, match="AGENT_NOT_AUTHENTICATED"):
        client.search("KB_TEST", "test")
    client.authenticate_agent(binding["externalAgentId"], pair.private_pem, agent_name="build")
    result = tool_payload(client.search("KB_TEST", "你好🙂"))
    assert result["knowledgeBaseId"] == "KB_TEST"
    assert result["query"] == "你好🙂"
    assert len(upstream.nonces) == 3
    upstream.bad_identity = True
    with pytest.raises(AkbError):
        client.authenticate_agent(binding["externalAgentId"], pair.private_pem)
    with pytest.raises(AkbError, match="AGENT_NOT_AUTHENTICATED"):
        client.call_tool("knowledge_search", {})


def test_service_mode_and_input_boundaries(pair, binding):
    upstream = FakeAKB(pair, binding, agent=False)
    client = AkbMcpClient(
        "https://akb.example/gateway", "synthetic-key", auth_type="service", transport=upstream
    )
    client.connect()
    assert tool_payload(client.search("KB_TEST", "test"))["topK"] == 5
    with pytest.raises(ValueError):
        client.search("6", "test")
    with pytest.raises(ValueError):
        client.call_tool("knowledge_search", {"knowledgeBaseCode": 6})
    with pytest.raises(ValueError):
        AkbMcpClient("http://192.168.1.10/api/mcp", "synthetic-key")
    with pytest.raises(ValueError):
        AkbMcpClient("https://user:password@akb.example", "synthetic-key")


def test_standard_transport_sends_exact_unicode_bytes(pair, binding):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    upstream = None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.answer()

        def do_POST(self):
            self.answer()

        def answer(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            headers = {"X-API-Key": self.headers.get("X-API-Key")}
            if self.headers.get(HEADER):
                headers[HEADER] = self.headers[HEADER]
            result = upstream(self.command, resource + self.path, headers, raw or None, 10)
            body = json.dumps(result, ensure_ascii=False).encode() if result is not None else b""
            self.send_response(200 if result is not None else 202)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    resource = f"http://127.0.0.1:{server.server_port}"
    upstream = FakeAKB(pair, {**binding, "resource": resource})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = AkbMcpClient(resource + "/gateway/api/mcp", "synthetic-key")
        client.connect()
        client.authenticate_agent(binding["externalAgentId"], pair.private_pem)
        result = tool_payload(client.search("KB_TEST", "你好🙂"))
        assert result["query"] == "你好🙂"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_standard_transport_never_follows_credential_redirects():
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from akb_identity_sdk.client import http_transport

    paths = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            paths.append(self.path)
            self.send_response(302)
            self.send_header("Location", "/must-not-follow")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(AkbError) as error:
            http_transport(
                "GET",
                f"http://127.0.0.1:{server.server_port}/start",
                {"X-API-Key": "synthetic-key"},
                None,
                2,
            )
        assert error.value.status == 302
        assert paths == ["/start"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
