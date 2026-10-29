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

Enable it only after AKB turns on the matching server-side check (`EXTERNAL_OCTOP_IDENTITY_ENABLED`); Octop sends the header only when both `enabled` and `instance_id` are set, and silently falls back to credential-only access otherwise.

```json
{
  "akb_identity": {
    "enabled": true,
    "issuer": "octop",
    "audience": "akb",
    "instance_id": "octop-prod"
  }
}
```

Environment overrides: `OCTOP_AKB_IDENTITY_ENABLED`, `OCTOP_AKB_IDENTITY_ISSUER`, `OCTOP_AKB_IDENTITY_AUDIENCE`, `OCTOP_AKB_IDENTITY_INSTANCE_ID`.

The assertion is HS256-signed with a dedicated secret (`secrets` row `akb-identity`, created on first use) and carries `iss`, `aud`, `sub` (`user:<id>`), `octop_instance_id`, `iat`, `exp` (5 minutes) and a one-time `jti`. Only AKB sources (`akb-mcp`, `akb-rest`) receive the header.

## Knowledge base discovery

`GET /external-knowledge/sources/{source_id}/refs` lists the knowledge bases the stored credential may read — AKB's `GET /api/v1/external/bases` over REST, or the `list_knowledge_bases` MCP tool. A probe without a configured `ref` reports reachability and returns the same list, so the binding never has to guess a base code.

## Error mapping

Upstream rejections keep their meaning instead of collapsing into one generic failure: `UNAUTHORIZED`/401 → `INVALID_CREDENTIALS`, `FORBIDDEN`/403 (and MCP `-32001`) → `FORBIDDEN`, `KB_NOT_FOUND`/404 → `NOT_FOUND`, `RESOURCE_EXHAUSTED`/429 → `RATE_LIMITED`. Upstream messages are never surfaced; only the sanitized code and the HTTP status are.
