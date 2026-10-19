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
