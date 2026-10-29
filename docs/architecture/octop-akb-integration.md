# Octop × AKB integration: first executable contract

## Scope

The read-only `octop.infra.knowledge.akb_client` adapter targets the AKB repository's actual versioned external endpoints:

- `POST /api/v1/external/search`: retrieval without answer generation.
- `POST /api/v1/external/query`: answer generation with citations/trace from AKB.

Payload: `knowledgeBaseId` (AKB numeric ID), `question`, `topK`, `permissionTags`.
Authentication: `X-API-Key`. Result: AKB's `data`, `traceId`, and the selected KB ID.

## Isolation and security

Each client is explicitly configured with a single HTTPS origin, API key, and numeric AKB knowledge-base ID. The adapter refuses HTTP, URL-embedded credentials, redirects, oversized responses, invalid JSON and unsuccessful responses. It does not print upstream response bodies or credentials. AKB itself remains the authority for API key scopes, knowledge-base restrictions, permission tags, and audit.

An Octop Agent appointment **does not** grant AKB rights. Before exposing this client to an Agent, add organization-to-AKB identity mapping, a secret-backed connector configuration, per-request authorization and audit, and a scoped tool wrapper. Never let Agent prompts choose arbitrary base URLs, keys, or knowledge-base IDs.

## Integration status

AKB knowledge is connected through its MCP server and Octop's product-neutral worker binding. AKB platform memory is available through read-only `memory_recall`; successful scheduled runs can stage a candidate for manager review before an idempotent write. The configured API key remains authoritative for AKB namespace, Agent identity, permissions, and auditing. The AKB service must be available for live end-to-end checks.

Other knowledge products use the same worker binding. Octop currently includes a read-only RAGFlow REST adapter that fixes every retrieval request to the worker's approved dataset. On-demand source diagnostics test connector visibility and upstream health, and report expiry or 90-day rotation reminders without exposing secrets. Broader roadmap work includes run recovery and reliability, evidence propagation, memory retention controls, and multi-step orchestration.

## Delegated identity (X-Octop-Identity)

AKB can run with an *application* API key plus a delegated caller identity. Octop then signs a short-lived assertion so AKB can intersect the key's grants with the Octop instance and user behind the call.

Enable it only after AKB turns on the matching server-side check (`EXTERNAL_OCTOP_IDENTITY_ENABLED`). Octop signs only when the deployment provisions the signing material below, and stays on credential-only access otherwise.

| Variable | Purpose |
| --- | --- |
| `OCTOP_AKB_IDENTITY_ENABLED=true` | Enables server-selected `AGENT_DELEGATED_USER` on interactive turns. |
| `OCTOP_AKB_IDENTITY_ISSUER` | Issuer registered in AKB for this Octop identity provider. |
| `OCTOP_AKB_IDENTITY_AUDIENCE` | AKB audience expected by the assertion validator. |
| `OCTOP_AKB_INSTANCE_ID` | Stable Octop instance identifier registered in AKB. |
| `OCTOP_AKB_IDENTITY_PRIVATE_KEY` | RS256 signing key; mount through the deployment secret mechanism, never commit it. Newlines may be encoded as `\n`. |
| `OCTOP_AKB_IDENTITY_KEY_ID` | Optional key ID (`kid`) for signing-key rotation. |

Octop signs a fresh assertion per request: RS256, a 60-second lifetime, a one-time `jti`, the authenticated Octop user as `sub`, the server-selected Agent ID, the instance ID, and a trace reference. Only AKB sources (`akb-mcp`, `akb-rest`) and the AKB memory client receive the header, always alongside `X-API-Key`; the Octop login token is never forwarded. An Agent ID is required — a request that cannot name a trusted Agent fails closed instead of sending an unscoped assertion.

### Public key set (JWKS)

AKB verifies assertions through `NimbusJwtDecoder.withJwkSetUri`, so Octop publishes the matching public key:

```
GET /.well-known/jwks.json
```

Set AKB's `EXTERNAL_OCTOP_IDENTITY_JWK_SET_URI` to this URL on the Octop host. The endpoint sits outside `/api/`, where the JWT middleware does not apply — it is unauthenticated by design, carries only public keys, and answers `{"keys": []}` when no signing key is provisioned. Every assertion carries its `kid`: the configured key id when set, otherwise the RFC 7638 thumbprint of the key, so AKB can tell an old key from a new one during rotation and the two sides never disagree.

Because AKB rejects a replayed `jti`, each upstream HTTP request gets its own assertion; an MCP exchange that initializes and then calls a tool signs twice.

## Knowledge base discovery

`GET /external-knowledge/sources/{source_id}/refs` lists the knowledge bases the stored credential may read — AKB's `GET /api/v1/external/bases` over REST, or the `list_knowledge_bases` MCP tool. A probe without a configured `ref` reports reachability and returns the same list, so the binding never has to guess a base code.

## Error mapping

Upstream rejections keep their meaning instead of collapsing into one generic failure: `UNAUTHORIZED`/401 → `INVALID_CREDENTIALS`, `FORBIDDEN`/403 (and MCP `-32001`) → `FORBIDDEN`, `KB_NOT_FOUND`/404 → `NOT_FOUND`, `RESOURCE_EXHAUSTED`/429 → `RATE_LIMITED`. Upstream messages are never surfaced; only the sanitized code and the HTTP status are.
