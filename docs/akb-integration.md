# AKB knowledge and memory integration

Octop connects to AKB through AKB's MCP server. Agent owners can attach the connection using Octop's existing MCP selection flow; AKB remains responsible for knowledge governance, retrieval permissions, and citations. The built-in external-knowledge adapters can also send a signed, short-lived Octop identity assertion when the AKB identity integration is enabled.

Virtual workers can also use AKB's platform memory as run context. This binding is read-only: Octop pins the memory scope, requires `memory_recall` before the planned work, and blocks `memory_capture` and `memory_forget` during the run.

## Configure the connection

1. In AKB, create an API Key bound to the knowledge base the Agent should use. Grant `READ` only.
2. In Octop, open **Agent → Connectors → Custom MCP → Add AKB Knowledge**.
3. Set the URL to `https://<AKB host>/api/mcp` and add `X-API-Key: <key>`.
4. Save and probe the connection. The probe should list tools such as `knowledge_search` and `knowledge_query`.
5. Attach the saved MCP server to the Agent. The Agent can then use the AKB search tool and return AKB citations.

### Optional Octop user identity binding

AKB and Octop may use different identity providers. When AKB has registered Octop's issuer, audience, public signing key, and Octop instance ID, configure these variables on the Octop backend to enable delegated identity for the built-in external-knowledge REST/MCP adapters:

| Variable | Purpose |
| --- | --- |
| `OCTOP_AKB_IDENTITY_ENABLED=true` | Enables server-selected `AGENT_DELEGATED_USER` on interactive turns. |
| `OCTOP_AKB_IDENTITY_ISSUER` | Issuer registered in AKB for this Octop identity provider. |
| `OCTOP_AKB_IDENTITY_AUDIENCE` | AKB audience expected by the identity assertion validator. |
| `OCTOP_AKB_INSTANCE_ID` | Stable Octop instance identifier registered in AKB. |
| `OCTOP_AKB_IDENTITY_PRIVATE_KEY` | RS256 signing key; mount through the deployment secret mechanism, never commit it. Newlines may be encoded as `\\n`. |
| `OCTOP_AKB_IDENTITY_KEY_ID` | Optional key ID (`kid`) for signing-key rotation. |

Octop signs a fresh JWT for each AKB request with a 60-second lifetime, unique `jti`, the authenticated Octop user ID as `sub`, the server-selected Agent ID, instance ID, and a thread/run trace reference. It sends the assertion only in `X-Octop-Identity`, alongside the existing `X-API-Key`; it never forwards the Octop login token. AKB still validates the key, principal binding, scopes, resource ACL, assertion signature, expiry, audience, and one-time `jti`. A delegated key must be bound in AKB to the matching Octop user principal and Agent relationship.

When identity signing is disabled, existing API-key-only behavior remains. With identity enabled, interactive built-in knowledge retrieval uses `AGENT_DELEGATED_USER`; cron retrieval uses the service key without a user assertion (`AGENT_SERVICE`). Manager-approved memory writes use the candidate owner's delegated identity for non-cron runs and service identity for cron runs. Incomplete signing configuration fails closed for requests that require delegation.

This assertion is currently attached by Octop's built-in external-knowledge REST/MCP adapters and approved memory-capture REST client. Octop's generic saved MCP connector transport continues to use its configured static headers; it does not yet inject per-request user assertions. For that path AKB sees the configured service identity until dynamic connector authentication is implemented.

For platform memory, create an AKB API key with `MEMORY_READ` and `MEMORY_WRITE`. Set its `memoryNamespace` to the intended sharing boundary and `memoryAgentCode` to a stable virtual-worker identity. `MEMORY_WRITE` is used only by Octop's manager-approved candidate workflow; run-time Agent tools remain read-only. Keep the key in the encrypted connector store. The key's namespace and Agent identity are authoritative; Octop cannot override them.

Bind a memory scope to the virtual worker after the connector is listed by an enabled scene:

```http
PUT /api/organizations/{organization_id}/digital-workers/{worker_id}/memory-sources
```

```json
{
  "expected_capability_version": 4,
  "sources": [
    {
      "provider": "akb",
      "mcp_server": "akb__1",
      "scope_type": "TASK",
      "scope_code": "weekly-service-report"
    }
  ]
}
```

For an Agent-wide memory scope, use `"scope_type": "AGENT"` and omit `scope_code`. Other scopes require an explicit code. The API only accepts the standardized `memory_recall` tool and records the change in the organization audit log.

Each virtual-worker memory binding can also set `read_enabled` and `capture_enabled` (both default to `true` for existing clients). `read_enabled` controls whether the worker may recall that scope. `capture_enabled` controls whether a successful run may stage a candidate for that scope; it never permits direct Agent writes. At plan-run request time, `use_memory` and `capture_memory` can further disable recall or candidate generation for that run. These run-level switches only reduce the worker's configured permissions and are stored in the run snapshot.

For backward compatibility, an AKB source can still be bound through `PUT /api/organizations/{organization_id}/digital-workers/{worker_id}/akb-knowledge-sources`:

```json
{
  "expected_capability_version": 1,
  "sources": [
    {"mcp_server": "akb__1", "knowledge_base_code": "HR-POLICY"}
  ]
}
```

The named MCP connection must already be allowed by an enterprise scene for the worker's Agent and position. When a plan runs, the selected scene must include that connector; Octop records the source in the plan snapshot, checks it at approval, and checks it again before execution. During an approved run, Octop's tool middleware permits only the configured read tools and injects the bound `knowledgeBaseCode`; it rejects a different code. Use `GET .../authorized-resources` to review current bindings. Octop's local `knowledge_base_ids` remain reserved for Octop-managed knowledge bases and must not contain AKB identifiers. AKB continues to enforce the key's knowledge-base binding and `READ` scope.

The product-neutral endpoint is `PUT /api/organizations/{organization_id}/digital-workers/{worker_id}/knowledge-sources`. It accepts a provider identifier, MCP server, knowledge-base reference, read-tool allowlist, and optional argument name for the knowledge-base selector. See [Product-neutral knowledge connector contract](knowledge-source-integration.md) for examples. Octop includes a REST-to-MCP adapter for RAGFlow; other products can be added through the gateway adapter contract.

Managers can run `POST /api/organizations/{organization_id}/digital-workers/{worker_id}/knowledge-sources/diagnose` to test each bound upstream connection and see credential expiry or rotation reminders. The dashboard exposes this check on the worker capability card. Diagnostic responses never include secrets or raw upstream error bodies.

Memory integrations use the corresponding `PUT .../memory-sources` endpoint and the common `memory_recall` arguments (`query`, `scopeType`, and `scopeCode`). AKB uses the platform key to resolve namespace and Agent identity. Other memory products can implement the same MCP tool contract while applying their own tenant and identity controls.

Octop stores custom MCP credentials through its encrypted connector-credential store. Keep AKB keys out of prompts and repository configuration.

## Permission and trace behavior

AKB validates the API Key, knowledge-base binding, and `READ` scope on each retrieval call. A READ-only Key also prevents AKB from advertising the `ingest_text` tool. An Agent cannot use an MCP argument to widen the Key's scope. `knowledgeBaseCode` gives the integration a stable selector while numeric `knowledgeBaseId` remains backward compatible. `permissionTags` narrows retrieval; AKB ACL remains authoritative. AKB records MCP query source, query ID, and citations for audit and replay.

For memory, Octop snapshots the worker's scope binding and run switches when a run is requested, checks the binding again at approval and execution, injects the approved `scopeType`/`scopeCode`, and rejects changes to that binding. Runs can use only `memory_recall`. After a successful scheduled run, Octop stages a bounded candidate from the visible final response only for scopes with candidate generation enabled. An organization owner or manager other than the requester can review, edit, approve, or reject it:

```http
GET  /api/organizations/{organization_id}/memory-candidates?status=pending
POST /api/organizations/{organization_id}/memory-candidates/{candidate_id}/review
```

Approval is the only path that writes memory. Octop uses the requester's encrypted AKB connector, retains the run and reviewer provenance, and sends a stable idempotency key so a retry does not create duplicate memory. The candidate must still match both the successful run snapshot and the worker's current memory binding. Write credentials are never exposed to the model. Rejection, failure, and successful AKB `memoryCode` receipts remain visible in the candidate list and audit log.

For generic saved MCP connectors, the configured API Key remains the identity AKB sees until dynamic connector authentication is implemented. For user-specific AKB permissions on those connectors, use a key and connection scoped to that principal or team. Assertion-backed built-in adapters pass the authenticated Octop user separately; AKB still requires the API Key and assertion principal binding to agree.

## Product boundary

Use AKB for formal enterprise knowledge that needs evidence, review, versioning, and lifecycle controls. Use Octop to bind that knowledge to Agents, virtual employees, scenes, and scheduled work. Conversation memory remains a separate capability and should not be published into AKB without the normal evidence and review workflow.
