# AKB knowledge integration

Octop connects to AKB through AKB's MCP server. Agent owners can attach the connection using Octop's existing MCP selection flow; AKB remains responsible for knowledge governance, retrieval permissions, and citations.

## Configure the connection

1. In AKB, create an API Key bound to the knowledge base the Agent should use. Grant `READ` only.
2. In Octop, open **Agent → Connectors → Custom MCP → Add AKB Knowledge**.
3. Set the URL to `https://<AKB host>/api/mcp` and add `X-API-Key: <key>`.
4. Save and probe the connection. The probe should list tools such as `knowledge_search` and `knowledge_query`.
5. Attach the saved MCP server to the Agent. The Agent can then use the AKB search tool and return AKB citations.

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

The product-neutral endpoint is `PUT /api/organizations/{organization_id}/digital-workers/{worker_id}/knowledge-sources`. It accepts a provider identifier, MCP server, knowledge-base reference, read-tool allowlist, and optional argument name for the knowledge-base selector. See [Product-neutral knowledge connector contract](knowledge-source-integration.md) for examples. Products that do not expose MCP need a dedicated adapter before they can be connected.

Octop stores custom MCP credentials through its encrypted connector-credential store. Keep AKB keys out of prompts and repository configuration.

## Permission and trace behavior

AKB validates the API Key, knowledge-base binding, and `READ` scope on each retrieval call. A READ-only Key also prevents AKB from advertising the `ingest_text` tool. An Agent cannot use an MCP argument to widen the Key's scope. `knowledgeBaseCode` gives the integration a stable selector while numeric `knowledgeBaseId` remains backward compatible. `permissionTags` narrows retrieval; AKB ACL remains authoritative. AKB records MCP query source, query ID, and citations for audit and replay.

The configured API Key is the identity AKB sees. For user-specific AKB permissions, use a key and connection scoped to that principal or team. A shared broad-scope key does not inherit Octop's user identity automatically.

## Product boundary

Use AKB for formal enterprise knowledge that needs evidence, review, versioning, and lifecycle controls. Use Octop to bind that knowledge to Agents, virtual employees, scenes, and scheduled work. Conversation memory remains a separate capability and should not be published into AKB without the normal evidence and review workflow.
