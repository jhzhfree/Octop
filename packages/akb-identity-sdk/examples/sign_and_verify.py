"""Offline signature roundtrip. Private key stays in memory; not an AKB registration bypass."""

from akb_identity_sdk import (
    MemoryReplayStore,
    create_request_proof,
    generate_key_pair,
    generate_uuid,
    sign_data,
    verify_request_proof,
    verify_signature,
)

pair = generate_key_pair()
agent_id = generate_uuid()
body = '{"query":"中文检索","knowledgeBaseId":"KB_DEMO"}'.encode()
assert verify_signature(body, sign_data(body, pair.private_pem), pair.public_jwk)
binding = {
    "issuer": "https://akb.example/agent-identity",
    "audience": "akb-external-api",
    "resource": "https://akb.example",
    "policyId": "17",
    "applicationPrincipalId": "123",
    "externalAgentId": agent_id,
    "runtimeAgentKey": agent_id,
    "platformId": "akb-service",
    "installationId": "installation",
    "workspaceId": "workspace",
}
# Synthetic binding/key for local verification; live binding must come from AKB.
proof = create_request_proof(
    binding=binding,
    private_pem=pair.private_pem,
    service_key="synthetic_local_key",
    method="POST",
    url="https://akb.example/api/mcp",
    body=body,
    session_id="host-session",
    invocation_id=generate_uuid(),
    terminal_agent="third-party-agent",
)
verified = verify_request_proof(
    proof,
    public_material=pair.public_jwk,
    binding=binding,
    service_key="synthetic_local_key",
    method="POST",
    url="https://akb.example/api/mcp",
    body=body,
    replay_store=MemoryReplayStore(),  # keep one store for the verifier lifetime
)
print("Signature verified:", verified["sub"])
