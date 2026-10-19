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

This is a reusable read-only adapter with offline contract tests, **not** a deployed AKB connector, automatic retrieval, persistent memory, or production-ready integration. AKB's service must be running and its external API enabled for a real end-to-end test.

Remaining: P3 timeout/idempotency/recovery and audit, P4 step state and handoff orchestration, P5 credential provisioning, scoped retrieval, citation/evidence propagation, memory retention and review.
