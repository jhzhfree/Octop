# AKB knowledge integration

Octop connects to AKB through AKB's MCP server. Agent owners can attach the connection using Octop's existing MCP selection flow; AKB remains responsible for knowledge governance, retrieval permissions, and citations.

## Configure the connection

1. In AKB, create an API Key bound to the knowledge base the Agent should use. Grant `READ` only.
2. In Octop, open **Agent → Connectors → Custom MCP → Add AKB Knowledge**.
3. Set the URL to `https://<AKB host>/api/mcp` and add `X-API-Key: <key>`.
4. Save and probe the connection. The probe should list tools such as `knowledge_search` and `knowledge_query`.
5. Attach the saved MCP server to the Agent. The Agent can then use the AKB search tool and return AKB citations.

Octop stores custom MCP credentials through its encrypted connector-credential store. Keep AKB keys out of prompts and repository configuration.

## Permission and trace behavior

AKB validates the API Key and its knowledge-base binding on each tool call. An Agent cannot use an MCP argument to widen the Key's scope. `permissionTags` narrows retrieval; AKB ACL remains authoritative. AKB records MCP query source, query ID, and citations for audit and replay.

The configured API Key is the identity AKB sees. For user-specific AKB permissions, use a key and connection scoped to that principal or team. A shared broad-scope key does not inherit Octop's user identity automatically.

## Product boundary

Use AKB for formal enterprise knowledge that needs evidence, review, versioning, and lifecycle controls. Use Octop to bind that knowledge to Agents, virtual employees, scenes, and scheduled work. Conversation memory remains a separate capability and should not be published into AKB without the normal evidence and review workflow.
